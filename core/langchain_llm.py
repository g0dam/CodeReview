"""LangChain LLM 适配器。

重构说明：
- 将现有的 LLMProvider 包装成 LangChain 的 LLM 接口
- 支持 LCEL (LangChain Expression Language) 语法：prompt | llm | parser
- 这是 LangGraph 标准做法，替代直接调用 llm_provider.generate()
"""

from typing import Any, Optional, List, Dict
from pydantic import PrivateAttr
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.outputs import ChatGeneration, ChatResult
from core.llm import LLMProvider
from core.config import LLMConfig


class LangChainLLMAdapter(BaseChatModel):
    """将 LLMProvider 适配为 LangChain 的 BaseChatModel。
    
    重构说明：
    - 此类将现有的 LLMProvider 包装成 LangChain 兼容的接口
    - 允许使用 LCEL 语法：prompt | llm | parser
    - 支持工具绑定：llm.bind_tools(tools)
    """
    
    # 重构说明：在 Pydantic v2 中，使用 PrivateAttr 存储不应该被序列化的属性
    # 这样可以避免 Pydantic 验证和序列化问题
    _llm_provider: LLMProvider = PrivateAttr()
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        **kwargs: Any
    ):
        """初始化适配器。
        
        Args:
            llm_provider: 现有的 LLMProvider 实例。
            **kwargs: 传递给父类的其他参数。
        """
        # 重构说明：先调用父类初始化
        super().__init__(**kwargs)
        # 然后设置私有属性
        self._llm_provider = llm_provider
    
    @property
    def _llm_type(self) -> str:
        """返回 LLM 类型标识符。"""
        # 重构说明：通过私有属性访问 llm_provider
        return f"adapter_{self._llm_provider.provider}"
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成聊天响应（同步版本）。
        
        注意：此方法应该被 _agenerate 覆盖，因为 LLMProvider 是异步的。
        这里提供同步版本是为了兼容性，但实际应该使用异步版本。
        """
        import asyncio
        return asyncio.run(self._agenerate(messages, stop, run_manager, **kwargs))
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成聊天响应（异步版本）。
        
        重构说明：
        - 将 LangChain 的 BaseMessage 列表转换为字符串提示
        - 调用底层的 LLMProvider.generate() 方法
        - 将响应包装为 ChatResult 对象
        
        Args:
            messages: LangChain 消息列表。
            stop: 可选的停止词列表。
            run_manager: 回调管理器。
            **kwargs: 其他参数（如 temperature）。
        
        Returns:
            ChatResult 对象，包含生成的响应。
        """
        # 将消息列表转换为提示字符串
        prompt_parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                prompt_parts.append(f"System: {msg.content}")
            elif isinstance(msg, HumanMessage):
                prompt_parts.append(f"Human: {msg.content}")
            elif isinstance(msg, AIMessage):
                prompt_parts.append(f"Assistant: {msg.content}")
            else:
                prompt_parts.append(str(msg.content))
        
        prompt = "\n\n".join(prompt_parts)
        
        # 从 kwargs 中提取参数
        temperature = kwargs.get("temperature", 0.7)
        
        # 调用底层的 LLMProvider
        # 重构说明：通过私有属性访问 llm_provider
        response_text = await self._llm_provider.generate(
            prompt,
            temperature=temperature,
            **{k: v for k, v in kwargs.items() if k != "temperature"}
        )
        
        # 创建 AIMessage
        ai_message = AIMessage(content=response_text)
        
        # 创建 ChatGeneration
        generation = ChatGeneration(message=ai_message)
        
        # 返回 ChatResult
        return ChatResult(generations=[generation])
    
    @classmethod
    def from_config(cls, config: LLMConfig) -> "LangChainLLMAdapter":
        """从配置创建适配器实例。
        
        重构说明：
        - 提供便捷的工厂方法，从配置直接创建适配器
        - 这是依赖注入的标准做法
        
        Args:
            config: LLM 配置对象。
        
        Returns:
            LangChainLLMAdapter 实例。
        """
        llm_provider = LLMProvider(config)
        return cls(llm_provider=llm_provider)
