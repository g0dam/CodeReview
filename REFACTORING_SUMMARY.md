# LangGraph 架构重构总结

## 概述

本次重构将现有的 Agent 代码迁移到 LangGraph 最新架构标准，摒弃过时的 Legacy LangChain 写法，确保代码符合生产级标准。

## 重构对照清单

### ✅ 1. 输入/输出与状态管理 (I/O & State)

#### 问题
- ❌ State 中没有使用 `Annotated[list, add_messages]` 来管理消息历史
- ❌ 没有使用 LangChain 的 Message 类型（HumanMessage, AIMessage, ToolMessage）
- ❌ 依赖注入通过 metadata 传递，不是标准做法

#### 重构方案
- ✅ **添加 messages 字段**：在 `ReviewState` 中添加 `messages: Annotated[List[BaseMessage], add_messages]`
  - 位置：`core/state.py`
  - 说明：使用 `add_messages` 确保消息追加而非重写，这是 LangGraph 标准做法
- ✅ **初始化 messages**：在 `run_multi_agent_workflow` 中初始化 `messages: []`
  - 位置：`agents/workflow.py`
  - 说明：确保 state 始终包含 messages 字段

**重构代码示例**：
```python
# core/state.py
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class ReviewState(TypedDict, total=False):
    # LangGraph 标准：消息历史管理（必须）
    messages: Annotated[List[BaseMessage], add_messages]
    # ... 其他字段
```

---

### ✅ 2. 链与节点逻辑 (Chains as Nodes)

#### 问题
- ❌ 节点内部直接调用 `llm_provider.generate()`，没有使用 LCEL 语法
- ❌ 没有使用 `prompt | llm | parser` 模式
- ❌ 节点逻辑不符合 LangGraph 节点规范

#### 重构方案
- ✅ **创建 LangChain LLM 适配器**：`core/langchain_llm.py`
  - 将现有的 `LLMProvider` 包装成 LangChain 的 `BaseChatModel`
  - 支持 LCEL 语法和工具绑定
- ✅ **重构节点使用 LCEL 语法**：`agents/nodes/intent_analysis.py`
  - 使用 `ChatPromptTemplate` 创建提示
  - 使用 `JsonOutputParser` 解析响应
  - 使用 `prompt | llm | parser` 链式调用

**重构代码示例**：
```python
# agents/nodes/intent_analysis.py
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

# 使用 LCEL 语法：prompt | llm | parser
prompt_template = ChatPromptTemplate.from_messages([
    ("system", "You are an expert code reviewer..."),
    ("human", prompt_text)
])
json_parser = JsonOutputParser()
chain = prompt_template | llm_adapter.with_config({"temperature": 0.3}) | json_parser
parsed_response = await chain.ainvoke({})
```

---

### ✅ 3. 记忆模块 (Memory)

#### 问题
- ❌ 没有使用 LangGraph 的 checkpointer
- ❌ 对话历史没有存储在 State 的 messages 键中
- ❌ 没有使用 `thread_id` 进行会话隔离

#### 重构方案
- ✅ **添加 MemorySaver checkpointer**：`agents/workflow.py`
  - 使用 `MemorySaver` 替代传统的 `ConversationBufferMemory`
  - 支持通过 `thread_id` 进行会话隔离
  - 对话历史存储在 State 的 `messages` 键中

**重构代码示例**：
```python
# agents/workflow.py
from langgraph.checkpoint.memory import MemorySaver

# 创建 checkpointer 用于记忆持久化
checkpointer = MemorySaver() if enable_checkpointing else None

# 编译工作流时传入 checkpointer
compiled = workflow.compile(checkpointer=checkpointer)

# 使用时传入 thread_id 进行会话隔离
result = await app.ainvoke(state, config={"configurable": {"thread_id": "session-1"}})
```

---

### ✅ 4. 工具使用 (Tools)

#### 问题
- ❌ 工具不是使用 `@tool` 装饰器定义的
- ❌ 没有使用 `llm.bind_tools()` 绑定工具
- ❌ 工具调用通过手动解析 LLM 响应实现，没有使用 `ToolNode`

#### 重构方案
- ✅ **创建 LangChain 标准工具**：`tools/langchain_tools.py`
  - 使用 `@tool` 装饰器定义工具
  - 包含详细的 docstring（用于 LLM 意图识别）
  - 包含类型注解
- ✅ **工具绑定支持**：在 workflow 中注入 `langchain_tools`
  - 支持使用 `llm.bind_tools(tools)` 绑定工具
  - 预留 `ToolNode` 支持（当前工作流中工具调用主要在 expert_execution_node 中手动处理）

**重构代码示例**：
```python
# tools/langchain_tools.py
from langchain_core.tools import tool

@tool
async def fetch_repo_map(asset_key: Optional[str] = None) -> Dict[str, Any]:
    """获取仓库结构映射。
    
    此工具从存储层加载仓库映射资产，返回项目结构的摘要字符串。
    代理可以使用此工具来"感知"代码库结构。
    
    Args:
        asset_key: 仓库映射的资产键。如果为 None，则使用默认的 'repo_map'。
    
    Returns:
        包含 summary, file_count, files, error 的字典。
    """
    # ... 实现
```

**工具绑定示例**（在需要工具调用的节点中）：
```python
# 绑定工具到模型
bound_llm = llm_adapter.bind_tools(langchain_tools)

# 调用模型（模型可以返回工具调用）
response = await bound_llm.ainvoke(messages)

# 使用 ToolNode 执行工具调用
from langgraph.prebuilt import ToolNode
tool_node = ToolNode(langchain_tools)
tool_results = await tool_node.ainvoke(response.tool_calls)
```

---

## 文件变更清单

### 新增文件

1. **`core/langchain_llm.py`**
   - LangChain LLM 适配器
   - 将 `LLMProvider` 包装成 `BaseChatModel`
   - 支持 LCEL 语法和工具绑定

2. **`tools/langchain_tools.py`**
   - 使用 `@tool` 装饰器定义的标准工具
   - `fetch_repo_map` 和 `read_file` 工具
   - 支持依赖注入（workspace_root, asset_key）

3. **`REFACTORING_SUMMARY.md`**
   - 本文档，详细说明重构内容

### 修改文件

1. **`core/state.py`**
   - 添加 `messages: Annotated[List[BaseMessage], add_messages]` 字段
   - 导入 `BaseMessage` 和 `add_messages`

2. **`agents/workflow.py`**
   - 添加 `MemorySaver` checkpointer 支持
   - 创建并传递 `LangChainLLMAdapter`
   - 创建并传递 `langchain_tools`
   - 初始化 `messages` 字段
   - 更新依赖注入逻辑

3. **`agents/nodes/intent_analysis.py`**
   - 重构为使用 LCEL 语法
   - 使用 `ChatPromptTemplate` 和 `JsonOutputParser`
   - 使用 `prompt | llm | parser` 链式调用
   - 保持向后兼容（支持文本解析回退）

## 向后兼容性

本次重构保持了向后兼容性：

1. **保留原有工具类**：`FetchRepoMapTool` 和 `ReadFileTool` 仍然可用
2. **保留原有 LLMProvider**：`LLMProvider` 仍然可用，通过适配器包装
3. **metadata 注入**：仍然通过 metadata 传递依赖（虽然不是最佳实践，但保持兼容）
4. **State 字段**：所有原有字段保持不变，仅添加 `messages` 字段

## 工具绑定示例

虽然当前工作流中工具调用主要在 `expert_execution_node` 中手动处理，但我们已经创建了示例文件展示如何使用标准方式：

**文件**：`agents/nodes/expert_execution_refactored_example.py`

**关键代码**：
```python
# 绑定工具到模型
bound_llm = llm_adapter.bind_tools(langchain_tools)

# 创建 ToolNode
tool_node = ToolNode(langchain_tools)

# 调用模型（可以返回工具调用）
response = await bound_llm.ainvoke(messages)

# 如果有工具调用，使用 ToolNode 执行
if hasattr(response, "tool_calls") and response.tool_calls:
    tool_messages = await tool_node.ainvoke(response.tool_calls)
    messages.extend(tool_messages)
```

## 下一步建议

虽然本次重构已经完成了核心的 LangGraph 标准化，但还有一些可以进一步优化的地方：

1. **完全迁移到 ToolNode**：
   - 将 `expert_execution_node` 中的手动工具调用解析改为使用 `ToolNode`
   - 使用 `llm.bind_tools()` 绑定工具
   - 参考 `expert_execution_refactored_example.py` 中的示例

2. **改进依赖注入**：
   - 考虑使用 LangGraph 的 `RunnableConfig` 传递依赖
   - 或者使用闭包/工厂函数创建节点

3. **添加更多节点使用 LCEL**：
   - `manager_node` 和 `reporter_node` 也可以重构为使用 LCEL 语法
   - `expert_execution_node` 中的 LLM 调用也可以使用 LCEL

4. **使用 Pydantic 输出解析器**：
   - 为结构化输出创建 Pydantic 模型
   - 使用 `PydanticOutputParser` 替代 `JsonOutputParser`

## 测试建议

重构后建议进行以下测试：

1. **功能测试**：确保工作流仍然正常工作
2. **消息历史测试**：验证 `messages` 字段正确追加
3. **工具调用测试**：验证工具绑定和调用正常工作
4. **记忆持久化测试**：验证 checkpointer 正常工作

## 总结

本次重构成功将代码迁移到 LangGraph 最新架构标准：

- ✅ 添加了消息历史管理（`messages` 字段）
- ✅ 实现了 LCEL 语法支持
- ✅ 添加了 checkpointer 支持
- ✅ 创建了标准工具定义（`@tool` 装饰器）
- ✅ 保持了向后兼容性

代码现在符合 LangGraph 生产级标准，可以更好地利用 LangGraph 的功能和生态系统。
