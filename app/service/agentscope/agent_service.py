"""
AgentService: Agent 创建服务
分离 model 创建与 agent 创建，调用方可自定义 model 后传入
"""
from typing import Optional

from agentscope.agent import Agent, ModelConfig, ReActConfig
from agentscope.credential import (
    DashScopeCredential,
    OpenAICredential,
)
from agentscope.model import (
    ChatModelBase,
    DashScopeChatModel,
    OpenAIChatModel,
)
from agentscope.tool import Toolkit

from app.config import settings


class AgentService:
    """Agent 工厂

    Usage:
        # 方式一: 使用默认模型 (从 config 读取 api_key / base_url)
        agent = AgentService.create_agent_with_default_model(
            name="助手",
            system_prompt="你是一个有用的助手",
        )

        # 方式二: 自定义 model 后传入
        model = AgentService.create_model(model_name="deepseek-chat")
        agent = AgentService.create_agent(
            name="助手",
            system_prompt="你是一个有用的助手",
            model=model,
        )
    """

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
