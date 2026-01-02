"""专家分析子图。
使用 LangGraph 子图模式实现专家智能体的工具调用循环。
"""

import logging
from typing import List, Optional, Any
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from core.state import RiskItem, ExpertState
from core.langchain_llm import LangChainLLMAdapter
from langchain_core.tools import BaseTool
from agents.prompts import render_prompt_template

logger = logging.getLogger(__name__)


def create_langchain_tools(
    workspace_root: Optional[str] = None,
    asset_key: Optional[str] = None
) -> List[BaseTool]:
    """创建 LangChain 工具列表。
    
    统一使用 langchain_tools.create_tools_with_context 创建标准工具。
    
    Args:
        workspace_root: 工作区根目录（用于工具上下文）。
        asset_key: 仓库映射的资产键（用于 fetch_repo_map）。
    
    Returns:
        LangChain 工具列表：fetch_repo_map, read_file, run_grep。
    """
    from tools.langchain_tools import create_tools_with_context
    from pathlib import Path
    
    if workspace_root:
        workspace_path = Path(workspace_root)
        return create_tools_with_context(
            workspace_root=workspace_path,
            asset_key=asset_key
        )
    else:
        # 如果没有 workspace_root，仍然创建工具（使用默认值）
        return create_tools_with_context(
            workspace_root=None,
            asset_key=asset_key
        )


def tools_condition(state: ExpertState) -> str:
    """条件路由函数：根据最后一条消息是否包含工具调用来决定路由。
    
    Args:
        state: 专家子图状态。
    
    Returns:
        "tools" 如果最后一条消息包含工具调用，否则 "end"。
    """
    messages = state.get("messages", [])
    if not messages:
        return "end"
    
    last_message = messages[-1]
    # 检查最后一条消息是否包含工具调用
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    
    return "end"


def build_expert_graph(
    llm: LangChainLLMAdapter,
    tools: List[BaseTool],
) -> Any:
    """构建专家分析子图。
    
    子图结构：
    1. reasoner 节点：调用 LLM 进行分析
    2. tools 节点：执行工具调用（如果 LLM 返回工具调用）
    3. 条件路由：根据是否有工具调用决定继续或结束
    
    Args:
        llm: LangChain LLM 适配器。
        tools: LangChain 工具列表。
        risk_type_str: 风险类型字符串（用于渲染提示词模板）。
    
    Returns:
        编译后的 LangGraph 子图。
    """
    # 绑定工具到 LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # 创建工具节点
    tool_node = ToolNode(tools)
    
    # 创建 Pydantic 解析器
    parser = PydanticOutputParser(pydantic_object=RiskItem)
    format_instructions = parser.get_format_instructions()
    
    # 格式化可用工具描述
    tool_descriptions = []
    for tool in tools:
        desc = getattr(tool, 'description', f'Tool: {tool.name}')
        tool_descriptions.append(f"- **{tool.name}**: {desc}")
    available_tools_text = "\n".join(tool_descriptions)
    
    # 定义 reasoner 节点（异步）
    async def reasoner(state: ExpertState) -> ExpertState:
        """推理节点：调用 LLM 进行分析。
        
        第一轮动态构建包含完整上下文的 SystemMessage，后续轮次直接使用历史消息。
        """
        messages = state.get("messages", [])
        risk_context = state.get("risk_context")
        diff_context = state.get("diff_context", "")
        file_content = state.get("file_content", "")
        risk_type_str = risk_context.risk_type.value
    
        # 获取基础系统提示词
        try:
            base_system_prompt = render_prompt_template(
                f"expert_{risk_type_str}",
                risk_type=risk_type_str,
                available_tools=available_tools_text,
                validation_logic_examples=""
            )
        except FileNotFoundError:
            # 回退到通用提示词
            base_system_prompt = render_prompt_template(
                "expert_generic",
                risk_type=risk_type_str,
                available_tools=available_tools_text
            )
        
        # 构建完整的 SystemMessage 内容
        system_content = f"""{base_system_prompt}
            ## 当前任务锚点
            风险类型: {risk_context.risk_type.value}
            文件路径: {risk_context.file_path}
            行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
            描述: {risk_context.description}"""

        if file_content:
            system_content += f"""
            ## 文件完整内容
            以下是该缺陷所在文件的完整内容。**严禁对当前文件{risk_context.file_path}调用 read_file 工具**，请直接使用以下内容：

            {file_content}"""

        system_content += f"""
            ## 输出格式要求
            {format_instructions}
            """
        
        system_msg = SystemMessage(content=system_content)
        
        if not messages:
            # 构建初始 UserMessage
            user_msg_content = "请分析上述风险项。如果需要更多信息，请调用工具。分析完成后，请输出最终的 JSON 结果。"
            user_msg = HumanMessage(content=user_msg_content)
            new_messages = [system_msg, user_msg]
        else:
            # 后续轮次：直接使用历史消息（SystemMessage 已在第一轮添加）
            new_messages = [system_msg, *messages]
        
        # 调用 LLM（异步）
        response = await llm_with_tools.ainvoke(new_messages)
        
        return {
            "messages": [response]
        }
    
    # 构建图
    graph = StateGraph(ExpertState)
    
    # 添加节点
    graph.add_node("reasoner", reasoner)
    graph.add_node("tools", tool_node)
    
    # 设置入口点
    graph.set_entry_point("reasoner")
    
    # 添加条件边
    graph.add_conditional_edges(
        "reasoner",
        tools_condition,
        {
            "tools": "tools",
            "end": END
        }
    )
    
    # 工具执行后回到 reasoner
    graph.add_edge("tools", "reasoner")
    
    # 编译图
    return graph.compile()


async def run_expert_analysis(
    graph: Any,
    risk_item: RiskItem,
    diff_context: Optional[str] = None,
    file_content: Optional[str] = None
) -> Optional[dict]:
    """运行专家分析子图。
    
    Args:
        graph: 编译后的专家子图。
        risk_item: 待分析的风险项。
        risk_type_str: 风险类型字符串（用于渲染提示词模板）。
        diff_context: 文件的 diff 上下文（可选）。
        file_content: 文件的完整内容（可选）。
    
    Returns:
        包含 'result' 和 'messages' 的字典，如果失败则返回 None。
        - result: 最终验证结果（RiskItem 对象）
        - messages: 对话历史（消息列表）
    """
    try:
        # 创建 Pydantic 解析器
        parser = PydanticOutputParser(pydantic_object=RiskItem)
        
        # 初始化状态
        initial_state: ExpertState = {
            "messages": [],
            "risk_context": risk_item,
            "final_result": None,
            "diff_context": diff_context,
            "file_content": file_content
        }
        
        # 运行子图
        final_state = await graph.ainvoke(initial_state)
        
        # 从消息中提取最后一条消息的文本内容
        messages = final_state.get("messages", [])
        if not messages:
            logger.warning("No messages in final state")
            return None
        
        # 获取最后一条消息的文本内容
        last_message = messages[-1]
        response_text = last_message.content if hasattr(last_message, "content") else str(last_message)
        
        # 使用 PydanticOutputParser 解析结果
        try:
            result: RiskItem = parser.parse(response_text)
        except Exception as e:
            logger.warning(f"PydanticOutputParser failed to parse response: {e}")
            logger.warning(f"Response text (first 500 chars): {response_text[:500]}")
            return None
        
        return {
            "result": result,
            "messages": messages
        }
        
    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else type(e).__name__
        error_traceback = traceback.format_exception(type(e), e, e.__traceback__)
        logger.error(f"Error running expert analysis: {error_msg}")
        logger.error(f"Traceback:\n{''.join(error_traceback)}")
        return None

