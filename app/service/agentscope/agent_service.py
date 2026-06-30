"""
AgentService: Agent 创建服务
分离 model 创建与 agent 创建，调用方可自定义 model 后传入
内置全局 Agent 单例，通过 AgentState 实现多会话隔离
"""
import logging
from typing import Optional

from agentscope.agent import Agent, ModelConfig, ReActConfig
from agentscope.credential import (
    DashScopeCredential,
    OpenAICredential,
)
from agentscope.message import Msg
from agentscope.model import (
    ChatModelBase,
    DashScopeChatModel,
    OpenAIChatModel,
)
from agentscope.state import AgentState
from agentscope.tool import Toolkit

from app.config import settings

logger = logging.getLogger("agentscope")


class AgentService:
    """Agent 工厂 + 全局 Agent 管理

    AgentScope 2.0 支持一个 Agent 实例处理多个会话，通过 AgentState 隔离:
    - 每个 conversation_id 对应一个独立的 AgentState
    - 调用前切换 agent.state 即可实现会话间互不影响

    Usage:
        # 获取全局 agent (自动切换到对应会话的 state)
        agent = AgentService.get_agent(session_id="123")
        async for event in agent.reply_stream(user_msg):
            ...

        # 也可以单独创建 agent (不走全局单例)
        agent = AgentService.create_agent_with_default_model(name="助手")
    """

    # 全局 Agent 单例
    _agent: Optional[Agent] = None
    # 会话状态池: session_id -> AgentState
    _states: dict[str, AgentState] = {}

    # ======================== Model 创建 ========================

    @staticmethod
    def create_model(
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        stream: bool = True,
        max_retries: int = 3,
    ) -> OpenAIChatModel:
        """创建默认模型 (OpenAIChatModel)

        默认从 config.py 读取 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME,
        传入参数可覆盖默认值。

        Args:
            api_key: API Key (默认使用 config.LLM_API_KEY)
            base_url: API 地址 (默认使用 config.LLM_BASE_URL)
            model_name: 模型名称 (默认使用 config.LLM_MODEL_NAME)
            stream: 是否流式输出
            max_retries: 最大重试次数

        Returns:
            OpenAIChatModel 实例
        """
        credential = OpenAICredential(
            api_key=api_key or settings.LLM_API_KEY,
            base_url=base_url or settings.LLM_BASE_URL,
        )
        return OpenAIChatModel(
            credential=credential,
            model=model_name or settings.LLM_MODEL_NAME,
            stream=stream,
            max_retries=max_retries,
        )

    @staticmethod
    def create_dashscope_model(
        api_key: str,
        model_name: str = "qwen-plus",
        stream: bool = True,
        max_retries: int = 3,
    ) -> DashScopeChatModel:
        """创建阿里云 DashScope 模型

        Args:
            api_key: DashScope API Key
            model_name: 模型名称 (默认 qwen-plus)
            stream: 是否流式输出
            max_retries: 最大重试次数

        Returns:
            DashScopeChatModel 实例
        """
        credential = DashScopeCredential(api_key=api_key)
        return DashScopeChatModel(
            credential=credential,
            model=model_name,
            stream=stream,
            max_retries=max_retries,
        )

    @staticmethod
    def create_openai_model(
        api_key: str,
        model_name: str = "gpt-4o",
        base_url: str = "",
        stream: bool = True,
        max_retries: int = 3,
    ) -> OpenAIChatModel:
        """创建 OpenAI 兼容模型

        Args:
            api_key: API Key
            model_name: 模型名称 (默认 gpt-4o)
            base_url: 自定义 API 地址
            stream: 是否流式输出
            max_retries: 最大重试次数

        Returns:
            OpenAIChatModel 实例
        """
        credential = OpenAICredential(
            api_key=api_key,
            base_url=base_url,
        )
        return OpenAIChatModel(
            credential=credential,
            model=model_name,
            stream=stream,
            max_retries=max_retries,
        )

    # ======================== Agent 创建 ========================

    @staticmethod
    def create_agent(
        name: str,
        system_prompt: str,
        model: ChatModelBase,
        tools: Optional[list] = None,
        max_retries: int = 3,
        max_iters: int = 20,
    ) -> Agent:
        """创建 Agent (核心方法)

        Args:
            name: Agent 名称
            system_prompt: 系统提示词
            model: ChatModelBase 实例 (需调用方先创建)
            tools: 工具列表
            max_retries: 模型调用最大重试次数
            max_iters: ReAct 循环最大迭代次数

        Returns:
            Agent 实例
        """
        toolkit = Toolkit(tools=tools) if tools else None

        return Agent(
            name=name,
            system_prompt=system_prompt,
            model=model,
            toolkit=toolkit,
            model_config=ModelConfig(max_retries=max_retries),
            react_config=ReActConfig(max_iters=max_iters),
        )

    @staticmethod
    def create_agent_with_default_model(
        name: str,
        system_prompt: str = "You are a helpful assistant.",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        stream: bool = True,
        tools: Optional[list] = None,
        max_retries: int = 3,
        max_iters: int = 20,
    ) -> Agent:
        """使用默认模型创建 Agent (便捷方法)

        默认从 config.py 读取模型配置，传入参数可覆盖。

        Args:
            name: Agent 名称
            system_prompt: 系统提示词
            api_key: API Key (覆盖 config)
            base_url: API 地址 (覆盖 config)
            model_name: 模型名称 (覆盖 config)
            stream: 是否流式输出
            tools: 工具列表
            max_retries: 最大重试次数
            max_iters: ReAct 最大迭代次数

        Returns:
            Agent 实例
        """
        model = AgentService.create_model(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            stream=stream,
            max_retries=max_retries,
        )
        return AgentService.create_agent(
            name=name,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            max_retries=max_retries,
            max_iters=max_iters,
        )

    # ======================== 全局 Agent 管理 ========================

    DEFAULT_NAME = "assistant"
    DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

    @classmethod
    def get_agent(cls, session_id: str) -> Agent:
        """获取全局 Agent，并切换到指定 session 的状态

        AgentScope 2.0 中 Agent 本身是无状态的调度器，真正的会话上下文
        存放在 AgentState 中。通过切换 agent.state 即可让同一个 agent
        服务多个互不干扰的会话。

        Args:
            session_id: 会话标识 (一般用 conversation_id 的字符串)

        Returns:
            全局 Agent 实例 (state 已切换到对应会话)
        """
        if cls._agent is None:
            cls._agent = cls._create_default_agent()
            logger.info("全局 Agent 创建完成")

        if session_id not in cls._states:
            cls._states[session_id] = AgentState(session_id=session_id)
            logger.info("创建会话状态 | session_id=%s", session_id)

        cls._agent.state = cls._states[session_id]
        return cls._agent

    @classmethod
    def has_state(cls, session_id: str) -> bool:
        """判断指定会话的状态是否已存在 (内存中)"""
        return session_id in cls._states

    @classmethod
    def load_history_to_state(cls, session_id: str, messages: list[Msg]) -> None:
        """将历史消息加载到会话状态中 (用于服务重启后恢复上下文)

        仅在状态上下文为空时加载，避免重复。

        Args:
            session_id: 会话标识
            messages: 历史消息列表 (Msg 对象)
        """
        agent = cls.get_agent(session_id)
        if agent.state.context:
            return
        if messages:
            agent.observe(messages)
            logger.info("加载历史消息到状态 | session_id=%s | count=%d", session_id, len(messages))

    @classmethod
    def remove_state(cls, session_id: str) -> None:
        """移除指定会话的状态 (会话删除时调用)"""
        if session_id in cls._states:
            del cls._states[session_id]
            logger.info("移除会话状态 | session_id=%s", session_id)

    @classmethod
    def _create_default_agent(cls) -> Agent:
        """创建默认全局 Agent"""
        model = cls.create_model()
        return cls.create_agent(
            name=cls.DEFAULT_NAME,
            system_prompt=cls.DEFAULT_SYSTEM_PROMPT,
            model=model,
        )
