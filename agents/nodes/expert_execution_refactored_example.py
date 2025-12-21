"""Expert Execution Node 重构示例。

此文件展示了如何将 expert_execution_node 重构为使用 LangGraph 标准做法：
1. 使用 llm.bind_tools() 绑定工具
2. 使用 ToolNode 执行工具调用
3. 使用 LCEL 语法

注意：这是一个示例文件，展示重构方向。实际重构需要更仔细的测试。
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from core.state import ReviewState, RiskItem
from core.langchain_llm import LangChainLLMAdapter
from agents.prompts import render_prompt_template

logger = logging.getLogger(__name__)


async def expert_execution_node_refactored(state: ReviewState) -> Dict[str, Any]:
    """重构后的 Expert Execution Node（示例）。
    
    重构说明：
    1. 使用 llm.bind_tools() 绑定工具到模型
    2. 使用 ToolNode 执行工具调用（替代手动解析）
    3. 使用 messages 字段管理对话历史（LangGraph 标准）
    4. 使用 LCEL 语法进行提示和解析
    
    Args:
        state: Current workflow state with expert_tasks.
    
    Returns:
        Dictionary with 'expert_results' key.
    """
    print("\n" + "="*80)
    print("🔬 [节点3] Expert Execution (重构版) - 使用 LangGraph 标准工具调用")
    print("="*80)
    
    # 获取依赖
    llm_adapter: LangChainLLMAdapter = state.get("metadata", {}).get("llm_adapter")
    langchain_tools = state.get("metadata", {}).get("langchain_tools", [])
    
    if not llm_adapter:
        logger.error("LLM adapter not found in metadata")
        return {"expert_results": {}}
    
    # 重构说明：使用 ToolNode 执行工具调用
    tool_node = ToolNode(langchain_tools)
    
    # 重构说明：使用 llm.bind_tools() 绑定工具
    bound_llm = llm_adapter.bind_tools(langchain_tools)
    
    expert_tasks_dicts = state.get("expert_tasks", {})
    if not expert_tasks_dicts:
        return {"expert_results": {}}
    
    # 转换任务
    from core.state import RiskItem
    expert_tasks = {
        risk_type: [RiskItem(**item) if isinstance(item, dict) else item for item in items]
        for risk_type, items in expert_tasks_dicts.items()
    }
    
    # 处理每个专家组
    expert_results = {}
    for risk_type_str, risk_items in expert_tasks.items():
        results = await _process_expert_group_refactored(
            risk_type_str=risk_type_str,
            tasks=risk_items,
            state=state,
            bound_llm=bound_llm,
            tool_node=tool_node
        )
        expert_results[risk_type_str] = results
    
    # 转换结果
    expert_results_dicts = {
        risk_type: [item.model_dump() for item in items]
        for risk_type, items in expert_results.items()
    }
    
    return {"expert_results": expert_results_dicts}


async def _process_expert_group_refactored(
    risk_type_str: str,
    tasks: List[RiskItem],
    state: ReviewState,
    bound_llm: Any,
    tool_node: ToolNode
) -> List[RiskItem]:
    """处理专家组任务（重构版）。
    
    重构说明：
    - 使用 bound_llm 调用模型（模型可以返回工具调用）
    - 使用 tool_node 执行工具调用
    - 使用 messages 字段管理对话历史
    
    Args:
        risk_type_str: Risk type string.
        tasks: List of RiskItem objects.
        state: Global workflow state.
        bound_llm: LLM with tools bound.
        tool_node: ToolNode for executing tool calls.
    
    Returns:
        List of validated RiskItem objects.
    """
    results = []
    
    for task in tasks:
        try:
            # 创建初始提示
            initial_prompt = render_prompt_template(
                f"expert_{risk_type_str}",
                risk_item=task.model_dump(),
                file_path=task.file_path,
                line_number=task.line_number,
                description=task.description,
                diff_context=state.get("diff_context", ""),
                available_tools=", ".join([tool.name for tool in langchain_tools])
            )
            
            # 重构说明：使用 messages 字段管理对话历史
            messages = state.get("messages", [])
            messages.append(HumanMessage(content=initial_prompt))
            
            # 重构说明：使用 bound_llm 调用模型（可以返回工具调用）
            max_iterations = 10
            for iteration in range(max_iterations):
                # 调用模型
                response = await bound_llm.ainvoke(messages)
                messages.append(response)
                
                # 检查是否有工具调用
                if hasattr(response, "tool_calls") and response.tool_calls:
                    # 重构说明：使用 ToolNode 执行工具调用
                    tool_messages = await tool_node.ainvoke(response.tool_calls)
                    messages.extend(tool_messages)
                    # 继续循环，让模型基于工具结果继续分析
                else:
                    # 没有工具调用，这是最终答案
                    break
            
            # 解析最终响应
            validated_item = _parse_final_response(response.content, task)
            results.append(validated_item)
            
        except Exception as e:
            logger.error(f"Error processing task: {e}")
            continue
    
    return results


def _parse_final_response(response: str, original_item: RiskItem) -> RiskItem:
    """解析最终响应。
    
    重构说明：
    - 可以使用 PydanticOutputParser 替代手动解析
    - 或者使用结构化输出（如果 LLM 支持）
    
    Args:
        response: LLM response string.
        original_item: Original risk item.
    
    Returns:
        Validated RiskItem.
    """
    # 这里可以使用 PydanticOutputParser 或结构化输出
    # 为了简化，这里保持原有的解析逻辑
    import json
    try:
        data = json.loads(response)
        return RiskItem(
            risk_type=RiskType(data.get("risk_type", original_item.risk_type.value)),
            file_path=data.get("file_path", original_item.file_path),
            line_number=data.get("line_number", original_item.line_number),
            description=data.get("description", original_item.description),
            confidence=float(data.get("confidence", original_item.confidence)),
            severity=data.get("severity", original_item.severity),
            suggestion=data.get("suggestion", original_item.suggestion)
        )
    except:
        return original_item
