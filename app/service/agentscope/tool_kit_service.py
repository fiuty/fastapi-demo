"""
ToolkitService: Agent 工具能力服务
统一管理 Agent 的 tools (自定义函数工具)、MCP (Model Context Protocol)、skills (技能)

三种扩展能力的区别:
- Tool:  自定义 Python 函数, 用 @service_tool 装饰器注册, Agent 可直接调用
- MCP:   标准化外部工具协议 (Stdio / HTTP), 连接外部 MCP Server
- Skill: 指令+脚本+资源的集合, Agent 通过 skill_viewer 工具读取后按指令执行

Usage:
    toolkit = ToolkitService.create_toolkit(
        tools=[weather_tool, calculator_tool],
        mcps=[mcp_client],
        skills_or_loaders=["./skills"],
    )
    agent = AgentService.create_agent(name="助手", model=model, toolkit=toolkit)
"""
import logging
from typing import Callable, Optional, Sequence

from agentscope.mcp import MCPClient, HttpMCPConfig, StdioMCPConfig
from agentscope.skill import LocalSkillLoader, Skill
from agentscope.tool import FunctionTool, Toolkit, ToolBase

logger = logging.getLogger("agentscope")


class ToolkitService:
    """工具/MCP/Skill 管理服务"""

    # ======================== Tool 创建 ========================

    @staticmethod
    def create_tool(
        func: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_concurrency_safe: bool = True,
        is_read_only: bool = False,
    ) -> FunctionTool:
        """将普通 Python 函数包装为 AgentScope FunctionTool

        函数需有完整的 docstring (用于 LLM 理解工具用途), 参数需有类型注解。

        Args:
            func: Python 函数 (同步/异步均可)
            name: 工具名称 (默认用函数名)
            description: 工具描述 (默认从 docstring 提取)
            is_concurrency_safe: 是否并发安全
            is_read_only: 是否只读 (无副作用)

        Returns:
            FunctionTool 实例

        Example:
            def get_weather(city: str) -> str:
                '''查询天气

                Args:
                    city: 城市名称
                '''
                return f"{city}今天晴"
            tool = ToolkitService.create_tool(get_weather)
        """
        tool = FunctionTool(
            func=func,
            name=name,
            description=description,
            is_concurrency_safe=is_concurrency_safe,
            is_read_only=is_read_only,
        )
        logger.info("创建函数工具 | name=%s", tool.name)
        return tool

    @staticmethod
    def create_tools(funcs: list[Callable]) -> list[FunctionTool]:
        """批量创建函数工具"""
        return [ToolkitService.create_tool(f) for f in funcs]

    # ======================== MCP 创建 ========================

    @staticmethod
    def create_stdio_mcp(
        name: str,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        enable_tools: Optional[list[str]] = None,
        disable_tools: Optional[list[str]] = None,
        execution_timeout: Optional[float] = None,
    ) -> MCPClient:
        """创建 Stdio MCP 客户端 (通过子进程通信)

        Args:
            name: MCP 实例名称 (工具名前缀, 如 mcp__{name}__tool)
            command: 启动命令 (如 "npx", "python")
            args: 命令参数
            env: 环境变量
            cwd: 工作目录
            enable_tools: 仅启用指定工具 (None=全部启用)
            disable_tools: 禁用指定工具
            execution_timeout: 工具执行超时 (秒)

        Returns:
            MCPClient 实例

        Example:
            mcp = ToolkitService.create_stdio_mcp(
                name="filesystem",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            )
        """
        mcp_config = StdioMCPConfig(
            type="stdio_mcp",
            command=command,
            args=args,
            env=env,
            cwd=cwd,
        )
        client = MCPClient(
            name=name,
            mcp_config=mcp_config,
            enable_tools=enable_tools,
            disable_tools=disable_tools,
            execution_timeout=execution_timeout,
        )
        logger.info("创建 Stdio MCP | name=%s | command=%s %s", name, command, " ".join(args or []))
        return client

    @staticmethod
    def create_http_mcp(
        name: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        enable_tools: Optional[list[str]] = None,
        disable_tools: Optional[list[str]] = None,
        execution_timeout: Optional[float] = None,
    ) -> MCPClient:
        """创建 HTTP MCP 客户端 (通过 HTTP/SSE 通信)

        Args:
            name: MCP 实例名称
            url: MCP Server 的 HTTP 地址 (如 "http://localhost:8080/mcp")
            headers: 请求头 (如认证 token)
            timeout: 连接超时 (秒)
            enable_tools: 仅启用指定工具
            disable_tools: 禁用指定工具
            execution_timeout: 工具执行超时 (秒)

        Returns:
            MCPClient 实例

        Example:
            mcp = ToolkitService.create_http_mcp(
                name="web-search",
                url="http://localhost:8080/mcp",
                headers={"Authorization": "Bearer xxx"},
            )
        """
        mcp_config = HttpMCPConfig(
            type="http_mcp",
            url=url,
            headers=headers,
            timeout=timeout,
        )
        client = MCPClient(
            name=name,
            mcp_config=mcp_config,
            enable_tools=enable_tools,
            disable_tools=disable_tools,
            execution_timeout=execution_timeout,
        )
        logger.info("创建 HTTP MCP | name=%s | url=%s", name, url)
        return client

    # ======================== Skill 创建 ========================

    @staticmethod
    def create_skill_loader(directory: str, scan_subdir: bool = False) -> LocalSkillLoader:
        """创建本地 Skill 加载器

        扫描指定目录下的 skill (每个 skill 是一个子目录, 含 SKILL.md 等文件)。
        Agent 会通过 skill_viewer 工具读取 skill 指令后按需使用。

        Args:
            directory: skill 根目录路径
            scan_subdir: 是否递归扫描子目录

        Returns:
            LocalSkillLoader 实例

        Example:
            loader = ToolkitService.create_skill_loader("./skills")
        """
        loader = LocalSkillLoader(directory=directory, scan_subdir=scan_subdir)
        logger.info("创建 Skill 加载器 | directory=%s | scan_subdir=%s", directory, scan_subdir)
        return loader

    # ======================== Toolkit 组装 ========================

    @staticmethod
    def create_toolkit(
        tools: Optional[list[ToolBase]] = None,
        mcps: Optional[list[MCPClient]] = None,
        skills_or_loaders: Optional[Sequence[str | Skill | LocalSkillLoader]] = None,
    ) -> Toolkit:
        """组装 Toolkit (Agent 的工具容器)

        将 tools、MCP clients、skills 合并为一个 Toolkit 实例, 传给 Agent。

        Args:
            tools: 自定义函数工具列表 (FunctionTool)
            mcps: MCP 客户端列表
            skills_or_loaders: Skill 路径/Skill 对象/SkillLoader 列表

        Returns:
            Toolkit 实例

        Example:
            toolkit = ToolkitService.create_toolkit(
                tools=[weather_tool],
                mcps=[mcp_client],
                skills_or_loaders=["./skills"],
            )
            agent = AgentService.create_agent(
                name="助手", system_prompt="...", model=model, toolkit=toolkit,
            )
        """
        toolkit = Toolkit(
            tools=tools,
            mcps=mcps,
            skills_or_loaders=skills_or_loaders,
        )
        tool_count = len(tools) if tools else 0
        mcp_count = len(mcps) if mcps else 0
        skill_count = len(skills_or_loaders) if skills_or_loaders else 0
        logger.info(
            "组装 Toolkit | tools=%d | mcps=%d | skills=%d",
            tool_count, mcp_count, skill_count,
        )
        return toolkit

    @staticmethod
    def create_empty_toolkit() -> Toolkit:
        """创建空 Toolkit (无工具)"""
        return Toolkit()
