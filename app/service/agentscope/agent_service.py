"""
AgentService: Agent 创建服务
分离 model 创建与 agent 创建，调用方可自定义 model 后传入
内置全局 Agent 单例，通过 AgentState + RedisStorage 实现多会话隔离与持久化
"""
import logging
from typing import Optional

from agentscope.agent import Agent, ContextConfig, ModelConfig, ReActConfig
from agentscope.app.storage import AgentData, AgentRecord, RedisStorage, SessionConfig
from agentscope.credential import (
    DashScopeCredential,
    DeepSeekCredential,
    OpenAICredential,
)
from agentscope.middleware import MiddlewareBase
from agentscope.model import (
    ChatModelBase,
    DashScopeChatModel,
    DeepSeekChatModel,
    OpenAIChatModel,
)
from agentscope.state import AgentState
from agentscope.tool import Toolkit

from app.config import settings
from app.middleware import ModelRetryNotifierMiddleware
from app.service.agentscope.tool_kit_service import ToolkitService

logger = logging.getLogger("agentscope")


class AgentService:
    """Agent 工厂 + 全局 Agent 管理

    使用 AgentScope 原生 RedisStorage 持久化 AgentState:
    - 每个 conversation.id 对应一个 Redis session (key_ttl=1天滑动过期)
    - 对话前从 Redis 加载 state, 过期则从 DB 恢复历史消息后重建
    - 对话后写回 Redis, 刷新 TTL
    - 多实例部署共享同一 Redis, 上下文不丢失
    """

    # 全局 Agent 单例
    _agent: Optional[Agent] = None
    # RedisStorage 单例 (app 启动时初始化)
    _storage: Optional[RedisStorage] = None
    # 全局中间件列表 (Agent 模板创建时设置, 供 create_agent_with_state 复用)
    _middlewares: Optional[list[MiddlewareBase]] = None
    # 固定的 user_id / agent_id (当前无用户体系, 写死)
    USER_ID = "default_user"
    AGENT_ID = "global_assistant"

    # ======================== Model 创建 ========================

    @staticmethod
    def create_model(
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        stream: bool = True,
        max_retries: int = 3,
    ) -> ChatModelBase:
        """创建默认模型

        根据 base_url 自动选择最合适的模型实现:
        - DeepSeek (api.deepseek.com): 使用 DeepSeekChatModel + DeepSeekChatFormatter,
          后者会在多轮对话中将 ThinkingBlock 作为 reasoning_content 回传给 API,
          避免思考模型因 reasoning_content 丢失而报 400。
        - 其它: 使用 OpenAIChatModel。

        默认从 config.py 读取 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME,
        传入参数可覆盖默认值。

        Args:
            api_key: API Key (默认使用 config.LLM_API_KEY)
            base_url: API 地址 (默认使用 config.LLM_BASE_URL)
            model_name: 模型名称 (默认使用 config.LLM_MODEL_NAME)
            stream: 是否流式输出
            max_retries: 最大重试次数

        Returns:
            ChatModelBase 实例
        """
        actual_base_url = base_url or settings.LLM_BASE_URL
        actual_api_key = api_key or settings.LLM_API_KEY
        actual_model = model_name or settings.LLM_MODEL_NAME

        # DeepSeek 思考模型要求多轮对话回传 reasoning_content, 必须使用
        # DeepSeekChatFormatter (OpenAIChatFormatter 会丢弃 ThinkingBlock)
        if "deepseek.com" in actual_base_url:
            credential = DeepSeekCredential(
                api_key=actual_api_key,
                base_url=actual_base_url,
            )
            return DeepSeekChatModel(
                credential=credential,
                model=actual_model,
                stream=stream,
                max_retries=max_retries,
            )

        credential = OpenAICredential(
            api_key=actual_api_key,
            base_url=actual_base_url,
        )
        return OpenAIChatModel(
            credential=credential,
            model=actual_model,
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
        toolkit: Optional[Toolkit] = None,
        max_retries: int = 3,
        max_iters: int = 20,
        context_config: Optional[ContextConfig] = None,
        react_config: Optional[ReActConfig] = None,
        middlewares: Optional[list[MiddlewareBase]] = None,
    ) -> Agent:
        """创建 Agent (核心方法)

        Args:
            name: Agent 名称
            system_prompt: 系统提示词
            model: ChatModelBase 实例 (需调用方先创建)
            toolkit: 工具列表
            max_retries: 模型调用最大重试次数
            max_iters: ReAct 循环最大迭代次数
            context_config: 上下文压缩配置 (None 时从 config 读取默认值)
            react_config: ReAct 配置 (None 时使用 max_iters 创建默认值)
            middlewares: AgentScope 中间件列表 (默认包含 ModelRetryNotifierMiddleware)

        Returns:
            Agent 实例
        """

        if context_config is None:
            context_config = ContextConfig(
                trigger_ratio=settings.CONTEXT_TRIGGER_RATIO,
                reserve_ratio=settings.CONTEXT_RESERVE_RATIO,
            )

        if react_config is None:
            react_config = ReActConfig(max_iters=max_iters)

        if middlewares is None:
            middlewares = [ModelRetryNotifierMiddleware()]

        back_model = AgentService.create_model(api_key=settings.BACK_LLM_API_KEY, base_url=settings.BACK_LLM_BASE_URL,
                                               model_name=settings.BACK_LLM_MODEL_NAME)
        return Agent(
            name=name,
            system_prompt=system_prompt,
            model=model,
            toolkit=toolkit,
            middlewares=middlewares,
            model_config=ModelConfig(max_retries=max_retries, fallback_model=back_model),
            react_config=react_config,
            context_config=context_config,
        )

    @staticmethod
    def create_agent_with_default_model(
        name: str,
        system_prompt: str = "You are a helpful assistant.",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        stream: bool = True,
        toolkit: Optional[Toolkit] = None,
        max_retries: int = 3,
        max_iters: int = 20,
        context_config: Optional[ContextConfig] = None,
        middlewares: Optional[list[MiddlewareBase]] = None,
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
            toolkit: 工具列表
            max_retries: 最大重试次数
            max_iters: ReAct 最大迭代次数
            context_config: 上下文压缩配置 (None 时从 config 读取默认值)
            middlewares: AgentScope 中间件列表

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
            toolkit=toolkit,
            max_retries=max_retries,
            max_iters=max_iters,
            context_config=context_config,
            middlewares=middlewares,
        )

    # ======================== 全局 Agent 管理 ========================

    DEFAULT_NAME = "assistant"
    DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

    @classmethod
    async def init_storage(cls) -> None:
        """初始化 RedisStorage (app 启动时调用, 仅初始化连接)"""
        cls._storage = RedisStorage(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            key_ttl=settings.STATE_KEY_TTL,
        )
        await cls._storage.__aenter__()
        logger.info(
            "RedisStorage 初始化完成 | host=%s:%s",
            settings.REDIS_HOST, settings.REDIS_PORT,
        )

    @classmethod
    async def close_storage(cls) -> None:
        """关闭 RedisStorage (app 关闭时调用)"""
        if cls._storage is not None:
            await cls._storage.__aexit__(None, None, None)
            cls._storage = None
            logger.info("RedisStorage 已关闭")

    @classmethod
    def get_storage(cls) -> RedisStorage:
        if cls._storage is None:
            raise RuntimeError("RedisStorage 未初始化, 请先调用 AgentService.init_storage()")
        return cls._storage

    @classmethod
    def get_agent(cls) -> Agent:
        """获取全局 Agent 模板 (不含 state)

        懒加载, 首次调用时从配置创建 model / toolkit / 默认配置。
        该实例仅作为模板复用, 所有需要 state 的场景必须使用
        create_agent_with_state() 创建独立实例。
        """
        if cls._agent is None:
            cls._agent = cls._create_default_agent()
            logger.info("全局 Agent 模板创建完成")
        return cls._agent

    @classmethod
    async def create_agent_with_state(cls, state: AgentState) -> Agent:
        """从 Redis Agent 记录创建绑定指定 state 的独立 Agent。

        1. 从 Redis 获取 AgentRecord (name / system_prompt / context_config / react_config)
        2. 首次对话时 Agent 记录不存在则从配置创建并持久化到 Redis
        3. 复用全局 Agent 模板的 model / toolkit / middlewares, 构建独立 Agent 实例并绑定 state

        多个并发会话各自调用本方法获取独立 Agent 实例, 不会互相覆盖 state。
        """
        storage = cls.get_storage()
        agent_record = await storage.get_agent(cls.USER_ID, cls.AGENT_ID)

        if agent_record is None:
            default_context = ContextConfig(
                trigger_ratio=settings.CONTEXT_TRIGGER_RATIO,
                reserve_ratio=settings.CONTEXT_RESERVE_RATIO,
            )
            default_react = ReActConfig(max_iters=20)
            agent_record = AgentRecord(
                id=cls.AGENT_ID,
                user_id=cls.USER_ID,
                data=AgentData(
                    id=cls.AGENT_ID,
                    name=cls.DEFAULT_NAME,
                    system_prompt=cls.DEFAULT_SYSTEM_PROMPT,
                    context_config=default_context,
                    react_config=default_react,
                ),
            )
            await storage.upsert_agent(cls.USER_ID, agent_record)
            logger.info("创建并持久化 Agent 记录 | agent_id=%s", cls.AGENT_ID)

        data = agent_record.data
        template = cls.get_agent()
        return Agent(
            name=data.name,
            system_prompt=data.system_prompt,
            model=template.model,
            toolkit=template.toolkit,
            middlewares=cls._middlewares,
            model_config=template.model_config,
            react_config=data.react_config,
            context_config=data.context_config,
            state=state,
        )

    @classmethod
    async def load_state(cls, session_id: str) -> Optional[AgentState]:
        """从 Redis 加载会话状态

        Returns:
            AgentState: Redis 命中时返回; None 表示过期/不存在
        """
        storage = cls.get_storage()
        record = await storage.get_session(cls.USER_ID, cls.AGENT_ID, session_id)
        if record is not None:
            logger.info("Redis 命中会话状态 | session_id=%s", session_id)
            return record.state
        logger.info("Redis 未命中会话状态 (可能已过期) | session_id=%s", session_id)
        return None

    @classmethod
    async def save_state(cls, session_id: str, state: AgentState) -> None:
        """将会话状态写回 Redis (刷新滑动 TTL)"""
        storage = cls.get_storage()
        # 先尝试 update, 不存在则 create
        try:
            await storage.update_session_state(
                cls.USER_ID, cls.AGENT_ID, session_id, state,
            )
        except KeyError:
            config = SessionConfig(workspace_id="local", name=session_id)
            await storage.upsert_session(
                user_id=cls.USER_ID,
                agent_id=cls.AGENT_ID,
                config=config,
                state=state,
                session_id=session_id,
            )
        logger.info("会话状态已写回 Redis | session_id=%s", session_id)

    @classmethod
    async def create_state(cls, session_id: str, state: AgentState | None = None) -> AgentState:
        """在 Redis 中创建新会话状态"""
        storage = cls.get_storage()
        agent_state = state or AgentState(session_id=session_id)
        config = SessionConfig(workspace_id="local", name=session_id)
        await storage.upsert_session(
            user_id=cls.USER_ID,
            agent_id=cls.AGENT_ID,
            config=config,
            state=agent_state,
            session_id=session_id,
        )
        logger.info("创建会话状态 | session_id=%s", session_id)
        return agent_state

    @classmethod
    async def remove_state(cls, session_id: str) -> None:
        """删除 Redis 中的会话状态 (会话删除时调用)"""
        storage = cls.get_storage()
        await storage.delete_session(cls.USER_ID, cls.AGENT_ID, session_id)
        logger.info("删除会话状态 | session_id=%s", session_id)

    @classmethod
    def _create_default_agent(cls) -> Agent:
        """创建默认全局 Agent"""
        model = cls.create_model()
        middlewares = [ModelRetryNotifierMiddleware()]
        cls._middlewares = middlewares
        return cls.create_agent(
            name=cls.DEFAULT_NAME,
            system_prompt=cls.DEFAULT_SYSTEM_PROMPT,
            model=model,
            toolkit=ToolkitService.create_default_toolkit(),
            middlewares=middlewares,
        )
