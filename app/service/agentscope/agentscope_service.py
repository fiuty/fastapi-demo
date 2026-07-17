"""
AgentscopeService: Agent 流式对话服务
整合会话管理、消息持久化、Agent 流式调用
使用全局 Agent + RedisStorage 持久化 AgentState 实现多会话隔离

支持对话中断与恢复:
- 中断运行中的回复: 设置 interrupt_event 让 _interruptible_agent_stream
  取消 __anext__() 任务, 将 CancelledError 精确注入 Agent 的 reply_stream
  生成器。Agent 内部捕获后清理上下文并产出 INTERRUPTED 结束事件。
  task.cancel() 作为兜底。直接 task.cancel() 不可靠: CancelledError 落在
  HTTP 客户端或 Starlette send() 中无法到达 Agent 生成器。
- 中断暂停中的回复(等待用户确认/外部执行): 通过 UserInterruptEvent 清理待处理工具调用。
- 恢复: 携带 conversation_id 再次调用 chat_stream 即可, 历史消息与状态从
  Redis/DB 恢复, 不会丢失。

模型重试实时通知:
  通过 _interruptible_agent_stream 的 retry_queue 参数, 在 asyncio.wait
  中同时监听 Agent 事件、中断信号和 retry_queue.get() 三路信号。
  当中间件在 _call_model 阻塞期间将通知放入 Queue, 合并流立即将其
  yield 给前端, 实现每次重试的即时反馈。
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

from agentscope.permission import PermissionContext, PermissionMode
from sqlalchemy.orm import Session

from agentscope.event import (
    ReplyStartEvent,
    ReplyEndEvent,
    TextBlockDeltaEvent,
    TextBlockStartEvent,
    TextBlockEndEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockStartEvent,
    ThinkingBlockEndEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    ToolResultEndEvent,
    ModelCallStartEvent,
    ModelCallEndEvent,
    HintBlockEvent,
    ExceedMaxItersEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    ConfirmResult,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import Msg, UserMsg, AssistantMsg
from agentscope.message._block import ToolCallState
from agentscope.state import AgentState

from app.pojo import Conversation
from app.pojo import Message
from app.common.exception import BizException
from app.service.agentscope.agent_service import AgentService
from app.middleware.model_retry_notifier import (
    _RETRY_STATE_KEY,
    _RETRY_QUEUE_KEY,
)

logger = logging.getLogger("agentscope")


class RetryNotification:
    """重试/回退通知包装器, 用于在 _interruptible_agent_stream 中
    将 retry_queue 的通知与原生的 AgentScope 事件统一 yield。

    通过 isinstance(event, RetryNotification) 与 AgentScope 事件区分。
    """

    __slots__ = ("data",)

    def __init__(self, data: dict) -> None:
        self.data = data

    def to_sse_dict(self) -> dict:
        return {"event": "model_retry", "data": self.data}


class AgentscopeService:

    # 会话 ID -> 正在运行的 reply 任务, 供中断接口取消
    _running_tasks: dict[str, asyncio.Task] = {}
    # 会话 ID -> 中断信号 Event, 用于将 CancelledError 精确注入 Agent 生成器
    _interrupt_events: dict[str, asyncio.Event] = {}

    def __init__(self, db: Session):
        self.db = db
        from app.service.conversation_service import ConversationService
        from app.service.message_service import MessageService
        self.conv_service = ConversationService(db)
        self.msg_service = MessageService(db)

    # ======================== 运行任务注册表 ========================

    @classmethod
    def _register_task(cls, session_id: str, task: asyncio.Task) -> None:
        """注册流式任务, 供中断接口取消"""
        cls._running_tasks[session_id] = task

    @classmethod
    def _unregister_task(cls, session_id: str) -> None:
        cls._running_tasks.pop(session_id, None)

    @classmethod
    def _get_interrupt_event(cls, session_id: str) -> asyncio.Event:
        """获取(或创建)会话的中断信号 Event"""
        event = cls._interrupt_events.get(session_id)
        if event is None:
            event = asyncio.Event()
            cls._interrupt_events[session_id] = event
        return event

    @classmethod
    def _clear_interrupt_event(cls, session_id: str) -> None:
        cls._interrupt_events.pop(session_id, None)

    @classmethod
    async def _cancel_running_task(cls, session_id: str) -> None:
        """取消同会话已存在的运行任务。若任务尚未完成清理则等待最多 10s,
        确保前一个请求已将 DB conversation 提交后再继续当前请求。"""
        task = cls._running_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                # 直接迭代模式下 Agent 的 finally 快速完成 (通常 < 1s)
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        cls._running_tasks.pop(session_id, None)

    @staticmethod
    def _auto_confirm_pending_tool_calls(agent) -> None:
        if agent.state.context:
            last_msg = agent.state.context[-1]
            if last_msg.role == "assistant":
                for tc in last_msg.get_content_blocks("tool_call"):
                    if tc.state == ToolCallState.ASKING:
                        agent._update_tool_call_state(tc.id, ToolCallState.ALLOWED)
                        logger.info(
                            "自动确认上一轮遗留的 tool_call | tool_call_id=%s | name=%s",
                            tc.id, tc.name,
                        )

    # ======================== 流式对话 ========================

    async def chat_stream(
        self,
        user_message: str | None,
        conversation_id: str | None = None,
        user_id: str = "default_user",
    ) -> AsyncGenerator[dict, None]:
        """流式对话, 支持新消息对话和恢复中断会话。

        Args:
            user_message: 用户消息内容. 为 None 时表示恢复中断的会话,
                          此时必须提供 conversation_id.
            conversation_id: 会话 ID.
            user_id: 用户标识.
        """
        # 1. 解析会话上下文(新消息 or 恢复中断), 失败时直接返回错误事件
        conversation, message, session_id, error = self._resolve_session_context(
            user_message, conversation_id, user_id,
        )
        if error is not None:
            yield error
            return

        # 2. 取消同会话尚未结束的旧回复, 等待其清理完成后再继续
        await AgentscopeService._cancel_running_task(session_id)

        # 3. 加载/重建会话状态(Redis 优先, 过期/不存在则从 DB 恢复历史)
        state = await self._load_or_rebuild_state(
            session_id, conversation.id, message.id if message else None,
        )

        # 4. 创建独立 Agent (不复用全局单例, 避免并发会话 state 互相覆盖)
        agent = await AgentService.create_agent_with_state(state)

        # 4.1 清理上一轮可能遗留的重试状态, 确保本轮通知计数从零开始
        agent.state.middle_context.pop(_RETRY_STATE_KEY, None)

        # 4.2 创建重试通知队列, 注入到 AgentState 供中间件写入
        retry_queue: asyncio.Queue = asyncio.Queue()
        agent.state.middle_context[_RETRY_QUEUE_KEY] = retry_queue

        # 5. 自动确认上一轮遗留的 ASKING 工具调用, 避免同一会话一直卡在等待结果
        AgentscopeService._auto_confirm_pending_tool_calls(agent)

        # 6. 构建 agent 输入: 新消息模式传 UserMsg, 恢复模式传 None
        agent_input = AgentscopeService._build_stream_input(
            user_message, user_id, session_id,
        )

        # 7. 注册当前请求任务与中断信号。
        #    interrupt_event 用于 _interruptible_agent_stream 将 CancelledError
        #    精确注入 Agent 生成器; task.cancel() 作为兜底。
        current_task = asyncio.current_task()
        if current_task is not None:
            AgentscopeService._register_task(session_id, current_task)

        full_response: list[dict] = []
        assistant_msg: Msg | None = None
        interrupt_event = AgentscopeService._get_interrupt_event(session_id)

        try:
            async for event in self._interruptible_agent_stream(
                agent.reply_stream(agent_input), interrupt_event,
                retry_queue=retry_queue,
            ):
                # 重试/回退通知 (来自中间件 via retry_queue)
                if isinstance(event, RetryNotification):
                    retry_event = event.to_sse_dict()
                    full_response.append(retry_event)
                    yield retry_event
                    continue

                if isinstance(event, ReplyStartEvent):
                    assistant_msg = AssistantMsg(
                        name=event.name, content=[], id=event.reply_id,
                    )
                elif assistant_msg is not None:
                    assistant_msg.append_event(event)

                mapped = self._map_event(event)
                if mapped is None:
                    continue

                full_response.append(mapped)
                yield mapped
        except asyncio.CancelledError:
            # 安全网: Agent 默认在内部吞下 CancelledError 并产出 INTERRUPTED
            # 事件, 不会走到这里。若走到此处(interruption_raise_cancelled_error=True
            # 等), 记录日志, 不重抛以保证 finally 能正常执行并提交 DB。
            logger.info(
                "对话被中断(CancelledError 传播到 chat_stream) | session_id=%s",
                session_id,
            )
        except Exception as e:
            logger.exception("reply_stream 异常")
            yield {"event": "error", "data": {"message": str(e)}}
        finally:
            await self._persist_after_stream(
                conversation.id, session_id, full_response, assistant_msg, agent,
            )

    # ======================== chat_stream 辅助方法 ========================

    def _resolve_session_context(
        self,
        user_message: str | None,
        conversation_id: str | None,
        user_id: str,
    ) -> tuple[Conversation | None, Message | None, str | None, dict | None]:
        """解析会话上下文, 区分新消息与恢复中断两种模式。

        Returns:
            (conversation, message, session_id, error_event)
            - 成功: error_event 为 None, 其余字段有效(恢复模式下 message 为 None)。
            - 失败: 前三项为 None, error_event 为需向前端推送的错误事件。
        """
        # ---- 恢复模式: user_message 为 None, 必须提供 conversation_id ----
        if user_message is None:
            if not conversation_id:
                return None, None, None, {
                    "event": "error",
                    "data": {"message": "恢复会话必须提供 conversation_id"},
                }
            try:
                conversation = self.conv_service.get_conversation(conversation_id)
            except BizException:
                return None, None, None, {
                    "event": "error",
                    "data": {"message": f"会话不存在: {conversation_id}"},
                }
            return conversation, None, conversation.id, None

        # ---- 新消息模式: 创建/复用会话并保存用户消息 ----
        conversation, message = self.handle_conversation(
            user_message, conversation_id, user_id,
        )
        return conversation, message, conversation.id, None

    async def _load_or_rebuild_state(
        self,
        session_id: str,
        conversation_id: str,
        current_msg_id: str | None,
    ) -> AgentState:
        """加载会话状态; Redis 过期/不存在时从 DB 历史消息重建。

        Args:
            session_id: 会话状态键(Redis)。
            conversation_id: DB 会话 ID, 用于加载历史消息。
            current_msg_id: 当前用户消息 ID, 重建历史时排除该条避免重复。
        """
        state = await AgentService.load_state(session_id)
        if state is not None:
            return state

        # Redis 无状态: 构建 AgentState 并从 DB 恢复历史消息
        state = AgentState(
            session_id=session_id,
            permission_context=PermissionContext(mode=PermissionMode.BYPASS),
        )
        history = self._load_history(conversation_id, current_msg_id)
        if history:
            agent_temp = await AgentService.create_agent_with_state(state)
            await agent_temp.observe(history)
            logger.info(
                "从 DB 恢复历史消息 | session_id=%s | count=%d",
                session_id, len(history),
            )
        return state

    @staticmethod
    def _build_stream_input(
        user_message: str | None,
        user_id: str,
        session_id: str,
    ):
        """构建 agent.reply_stream 的输入。

        - 新消息模式: 返回 UserMsg, Agent 正常处理用户输入。
        - 恢复模式: 返回 None, Agent 从当前上下文继续推理, 不添加新消息。
        """
        if user_message is not None:
            return UserMsg(name=user_id, content=user_message)
        logger.info("恢复中断会话 | session_id=%s", session_id)
        return None

    async def _persist_after_stream(
        self,
        conversation_id: str,
        session_id: str,
        full_response: list[dict],
        assistant_msg: Msg | None,
        agent,
    ) -> None:
        """流结束后持久化: 注销任务 -> 保存助手消息 -> 提交 DB -> 保存状态。

        所有异常被捕获并记录, 不向上抛出, 保证 finally 语义稳定 ——
        无论流正常结束、被中断还是异常, 均会执行持久化。
        """
        # 0. 清理 non-serializable 的中间件上下文, 避免 save_state 时
        #    Pydantic 序列化 middle_context 中的 asyncio.Queue 报错
        agent.state.middle_context.pop(_RETRY_QUEUE_KEY, None)
        agent.state.middle_context.pop(_RETRY_STATE_KEY, None)

        # 注销当前任务与中断信号, 释放会话锁
        AgentscopeService._unregister_task(session_id)
        AgentscopeService._clear_interrupt_event(session_id)

        # 1. 保存助手消息并显式提交 DB。
        #    不依赖 get_db() 的延迟提交: 中断后请求可能被取消,
        #    get_db() 的 commit 不会执行反而可能 rollback, 导致已 flush 的消息丢失。
        try:
            self.msg_service.save_assistant_message(
                conversation_id, full_response, assistant_msg,
            )
            if self.db.is_active:
                self.db.commit()
        except Exception:
            logger.exception("保存助手消息/提交失败")
            try:
                if self.db.is_active:
                    self.db.rollback()
            except Exception:
                pass

        # 2. 持久化 AgentState 到 Redis; shield 防止取消信号打断写入
        try:
            await asyncio.shield(
                AgentService.save_state(session_id, agent.state),
            )
        except asyncio.CancelledError:
            logger.warning(
                "save_state 被取消(后台仍会完成) | session_id=%s",
                session_id,
            )
        except Exception:
            logger.exception("save_state 异常")

    def handle_conversation(self, user_message: str, conversation_id: str | None = None, user_id: str = "default_user") \
            -> tuple[Conversation, Message]:
        conversation = self.conv_service.resolve_conversation(conversation_id, user_message[:30], user_id,)
        # 保存用户消息到 DB
        message = self.msg_service.save_message(conversation_id=conversation.id, role="user", content=user_message)
        return conversation, message


    async def interrupt(self, conversation_id: str) -> dict:
        """中断指定会话的智能体回复。

        两种中断场景(参考 AgentScope 2.0.4 中断智能体文档):

        1. 智能体正在运行: 取消承载 reply_stream 的请求任务, Agent 内部
           捕获 CancelledError, 清理未完成工具调用并产出 INTERRUPTED 结束事件,
           上下文保持一致, 可立即通过新输入继续。

           取消后会等待任务清理完成(通常 < 1s, 含 Redis/DB 持久化), 以
           保证 DB 中 conversation 已提交, 避免后续"继续"请求因未提交而
           创建重复 conversation 导致消息丢失。
           最大等待 10 秒, 超时则返回但不影响清理(任务最终仍会完成)。

        2. 智能体处于暂停状态(等待用户确认/外部执行): 向 reply_stream 传入
           UserInterruptEvent, 清理待处理工具调用并结束当前 reply。

        Returns:
            dict: 中断结果, 含 status / conversation_id / reason(或 message)。
        """
        # 1. 取消正在运行的回复任务, 等待其 finally 持久化完成
        task = AgentscopeService._running_tasks.get(conversation_id)
        if task is not None and not task.done():
            return await AgentscopeService._cancel_and_wait_task(
                conversation_id, task,
            )

        # 2. 中断暂停中的智能体(等待用户确认/外部执行)
        return await self._interrupt_parked_agent(conversation_id)

    # ======================== interrupt 辅助方法 ========================

    @classmethod
    async def _cancel_and_wait_task(
        cls,
        conversation_id: str,
        task: asyncio.Task,
    ) -> dict:
        """取消运行中的回复任务并等待其清理完成。

        先设置中断 Event, 让 _interruptible_agent_stream 取消 __anext__()
        任务, 将 CancelledError 精确注入 Agent 的 reply_stream 生成器,
        触发 SDK 中断清理路径。task.cancel() 作为兜底, 确保即使中断信号
        未被及时消费, 任务也能被取消。

        等待最多 10s 确保 DB 已提交, 避免后续"继续"请求因 conversation
        未提交而创建重复记录。
        """
        interrupt_event = cls._get_interrupt_event(conversation_id)
        interrupt_event.set()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, Exception):
            logger.warning(
                "等待中断清理超时或异常 | conversation_id=%s",
                conversation_id,
            )
        cls._unregister_task(conversation_id)
        logger.info("已中断运行中的对话 | conversation_id=%s", conversation_id)
        return cls._build_interrupt_result(
            conversation_id, status="interrupted", reason="task_cancelled",
        )

    async def _interrupt_parked_agent(self, conversation_id: str) -> dict:
        """中断暂停中的智能体(等待用户确认/外部执行)。

        通过向 reply_stream 传入 UserInterruptEvent, 清理待处理工具调用
        并结束当前 reply, 随后持久化状态与消息。
        无活跃会话或无待处理工具调用时返回 noop。
        """
        state = await AgentService.load_state(conversation_id)
        if state is None:
            return self._build_interrupt_result(
                conversation_id, status="noop", reason="no_active_session",
            )

        agent = await AgentService.create_agent_with_state(state)
        if not state.has_awaiting_tool_calls(agent.name):
            return self._build_interrupt_result(
                conversation_id, status="noop", reason="not_parked",
            )

        try:
            full_response, assistant_msg = await self._collect_reply_stream(
                agent.reply_stream(
                    UserInterruptEvent(reply_id=state.reply_id),
                ),
            )
            await AgentService.save_state(conversation_id, agent.state)
            self.msg_service.save_assistant_message(
                conversation_id, full_response, assistant_msg,
            )
            self._commit_db()
            logger.info("已中断暂停中的对话 | conversation_id=%s", conversation_id)
            return self._build_interrupt_result(
                conversation_id, status="interrupted", reason="parked_interrupted",
            )
        except Exception as e:
            logger.exception("中断暂停中的智能体失败")
            return self._build_interrupt_result(
                conversation_id, status="error", message=str(e),
            )

    async def _collect_reply_stream(
        self, agent_gen: AsyncGenerator,
    ) -> tuple[list[dict], Msg | None]:
        """消费 agent 事件流, 聚合映射后的事件与助手消息对象。

        与 chat_stream 不同, 此方法不向前端 yield, 仅在内存中收集,
        供后续持久化使用。

        Returns:
            (full_response, assistant_msg): 映射后的事件列表与助手消息
            (未收到 ReplyStartEvent 时 assistant_msg 为 None)。
        """
        full_response: list[dict] = []
        assistant_msg: Msg | None = None
        async for event in agent_gen:
            if isinstance(event, ReplyStartEvent):
                assistant_msg = AssistantMsg(
                    name=event.name, content=[], id=event.reply_id,
                )
            elif assistant_msg is not None:
                assistant_msg.append_event(event)

            mapped = self._map_event(event)
            if mapped is not None:
                full_response.append(mapped)
        return full_response, assistant_msg

    def _commit_db(self) -> None:
        """显式提交 DB, 失败时回滚并记录日志。

        不依赖 get_db() 的延迟提交: 中断后请求可能被取消,
        get_db() 的 commit 不会执行反而可能 rollback, 导致已 flush 的消息丢失。
        """
        try:
            self.db.commit()
        except Exception:
            logger.exception("DB 提交失败")
            try:
                self.db.rollback()
            except Exception:
                pass

    @staticmethod
    def _build_interrupt_result(
        conversation_id: str,
        status: str,
        **extra,
    ) -> dict:
        """构建中断结果响应。

        extra 用于附加 reason(正常/noop 场景)或 message(异常场景)字段。
        """
        return {
            "status": status,
            "conversation_id": conversation_id,
            **extra,
        }

    async def chat_stream_interactive(
        self,
        user_message: str | None = None,
        confirm_results: list[dict] | None = None,
        conversation_id: str | None = None,
        user_id: str = "default_user",
    ) -> AsyncGenerator[dict, None]:
        """交互式流式对话，支持工具权限确认交互。

        与 chat_stream 的区别：
        - 使用 PermissionMode.DEFAULT 模式，工具调用会触发 RequireUserConfirmEvent
        - 支持 confirm_results 参数，用于客户端回传工具确认结果
        - 遗留的 ASKING 工具调用不会被自动确认

        典型交互流程：
        1. 客户端发送 {"message": "帮我查找文件"}  → 流式返回，包含 require_user_confirm 事件
        2. 客户端发送 {"confirm_results": [{"tool_call_id": "...", "confirmed": true}]} → 继续流式返回
        """
        full_response: list[dict] = []
        conversation = self.conv_service.resolve_conversation(
            conversation_id,
            (user_message or "tool confirmation")[:30],
            user_id,
        )
        session_id = conversation.id

        # 仅当有新消息时保存用户消息
        message = None
        if user_message:
            message = self.msg_service.save_message(
                conversation_id=conversation.id,
                role="user",
                content=user_message,
            )
        elif confirm_results:
            message = self.msg_service.save_message(
                conversation_id=conversation.id,
                role="user",
                content=json.dumps(confirm_results, ensure_ascii=False),
                event_type="tool_confirmation",
            )
        message_id = message.id if message else None

        # 加载状态，使用 DEFAULT 模式（需要权限确认）
        state = await AgentService.load_state(session_id)
        if state is None:
            state = AgentState(
                session_id=session_id,
                permission_context=PermissionContext(mode=PermissionMode.DEFAULT),
            )
            history = self._load_history(conversation.id, message_id)
            if history:
                agent_temp = await AgentService.create_agent_with_state(state)
                await agent_temp.observe(history)
                logger.info(
                    "从 DB 恢复历史消息 | session_id=%s | count=%d",
                    session_id,
                    len(history),
                )

        agent = await AgentService.create_agent_with_state(state)

        # 清理上一轮遗留的重试状态, 创建 retry_queue 并注入 AgentState
        agent.state.middle_context.pop(_RETRY_STATE_KEY, None)
        retry_queue: asyncio.Queue = asyncio.Queue()
        agent.state.middle_context[_RETRY_QUEUE_KEY] = retry_queue

        # 构建输入
        agent_input = self._build_interactive_input(agent, user_message, confirm_results, user_id)
        if agent_input is None:
            yield {
                "event": "error",
                "data": {"message": "必须提供 message 或 confirm_results"},
            }
            return

        yield {"event": "conversation_id", "data": {"conversation_id": conversation.id}}

        assistant_msg = None
        # 交互模式不响应中断, 但复用 _interruptible_agent_stream 以支持 retry_queue 实时通知
        _dummy_interrupt = asyncio.Event()

        try:
            async for event in self._interruptible_agent_stream(
                agent.reply_stream(agent_input), _dummy_interrupt,
                retry_queue=retry_queue,
            ):
                # 重试/回退通知 (来自中间件 via retry_queue)
                if isinstance(event, RetryNotification):
                    retry_event = event.to_sse_dict()
                    full_response.append(retry_event)
                    yield retry_event
                    continue
                if isinstance(event, ReplyStartEvent):
                    assistant_msg = AssistantMsg(
                        name=event.name, content=[], id=event.reply_id,
                    )
                elif assistant_msg is not None:
                    assistant_msg.append_event(event)

                mapped = self._map_event(event)
                if mapped is None:
                    continue

                full_response.append(mapped)
                yield mapped

        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}
            return
        finally:
            # 清理 non-serializable 上下文, 避免 asyncio.Queue 序列化失败
            agent.state.middle_context.pop(_RETRY_QUEUE_KEY, None)
            agent.state.middle_context.pop(_RETRY_STATE_KEY, None)

            await AgentService.save_state(session_id, agent.state)
            self.msg_service.save_assistant_message(
                conversation.id,
                full_response,
                assistant_msg,
            )
            # 显式提交, 不依赖 get_db() 的延迟提交
            try:
                self.db.commit()
            except Exception:
                logger.exception("chat_stream_interactive 提交失败")
                try:
                    self.db.rollback()
                except Exception:
                    pass

    @staticmethod
    def _build_interactive_input(
        agent,
        user_message: str | None,
        confirm_results: list[dict] | None,
        user_id: str,
    ):
        """根据请求类型构建 agent.reply_stream 的输入参数"""
        if confirm_results:
            return AgentscopeService._build_confirm_input(agent, confirm_results)

        if user_message:
            return AgentscopeService._build_user_input(agent, user_message, user_id)

        return None

    @staticmethod
    def _build_confirm_input(agent, confirm_results: list[dict]):
        """从客户端确认结果构建 UserConfirmResultEvent"""
        confirm_items: list[ConfirmResult] = []

        if agent.state.context:
            last_msg = agent.state.context[-1]
            tool_call_map = {
                tc.id: tc
                for tc in last_msg.get_content_blocks("tool_call")
            }
            for item in confirm_results:
                tc_id = item["tool_call_id"]
                confirmed = item.get("confirmed", False)
                tc = tool_call_map.get(tc_id)
                if tc is None:
                    logger.warning(
                        "客户端传回了不存在的 tool_call_id=%s，跳过", tc_id,
                    )
                elif tc.state != ToolCallState.ASKING:
                    logger.warning(
                        "tool_call_id=%s 当前状态为 %s（非 ASKING），跳过",
                        tc_id, tc.state,
                    )
                else:
                    confirm_items.append(
                        ConfirmResult(confirmed=confirmed, tool_call=tc),
                    )

        if not confirm_items:
            raise ValueError("confirm_results 中没有有效的 ASKING 状态 tool_call_id")

        last_reply_id = agent.state.context[-1].id if agent.state.context else ""
        return UserConfirmResultEvent(
            reply_id=last_reply_id,
            confirm_results=confirm_items,
        )

    @staticmethod
    def _build_user_input(agent, user_message: str, user_id: str):
        """构建新的 UserMsg，如有遗留 ASKING 工具则自动拒绝"""
        if agent.state.context:
            last_msg = agent.state.context[-1]
            if last_msg.role == "assistant":
                for tc in last_msg.get_content_blocks("tool_call"):
                    if tc.state == ToolCallState.ASKING:
                        agent._update_tool_call_state(
                            tc.id, ToolCallState.FINISHED,
                        )
                        logger.info(
                            "交互模式：自动拒绝上一轮遗留的 ASKING 工具 | "
                            "tool_call_id=%s | name=%s",
                            tc.id, tc.name,
                        )

        return UserMsg(name=user_id, content=user_message)

    def _load_history(self, conversation_id: str, current_msg_id: str | None = None) -> list[Msg]:
        from sqlalchemy import select
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.id != current_msg_id)
            .order_by(Message.create_time.asc())
        )
        messages = list(self.db.execute(stmt).scalars().all())
        history: list[Msg] = []
        for msg in messages:
            if msg.role == "user":
                history.append(UserMsg(name="user", content=msg.content))
            elif msg.role == "assistant":
                if msg.extra_meta:
                    history.append(Msg.model_validate(json.loads(msg.extra_meta)))
                else:
                    history.append(AssistantMsg(name="assistant", content=msg.content))
        return history

    @staticmethod
    async def _interruptible_agent_stream(
        agent_gen: AsyncGenerator,
        interrupt_event: asyncio.Event,
        retry_queue: asyncio.Queue | None = None,
    ) -> AsyncGenerator:
        """包装 Agent 流, 同时支持 interrupt_event 即时中断和 retry_queue 实时重试通知。

        当 retry_queue 为 None 时, 使用原有的二路竞争逻辑
        (agent_gen vs interrupt) — 保持向后兼容, 不引入额外开销。

        当 retry_queue 不为 None 时, 采用生产者-消费者模式:
        - agent_producer (后台 task): 独占消费 agent_gen + 中断处理 → 写入 event_queue
        - 主循环: 三路竞争 (event_queue / retry_queue / agent_done 信号),
          retry_queue 的消息由主循环直接读取, 不再通过独立 retry_producer 中转

        此设计避免了在 agent_gen.__anext__() 阻塞期间 cancel+重建的并发问题,
        确保异步生成器的 __anext__() 仅被单一 task 消费, 永不竞争。

        Args:
            agent_gen: Agent reply_stream 生成器
            interrupt_event: 中断信号 Event
            retry_queue: 重试通知队列 (None 时走旧逻辑)

        Yields:
            AgentScope 事件 或 RetryNotification 包装的重试通知
        """
        # ---- 无 retry_queue: 原有二路竞争逻辑 (不动) ----
        if retry_queue is None:
            while True:
                get_next = asyncio.ensure_future(agent_gen.__anext__())
                wait_interrupt = asyncio.ensure_future(interrupt_event.wait())
                try:
                    done, pending = await asyncio.wait(
                        [get_next, wait_interrupt],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    get_next.cancel()
                    wait_interrupt.cancel()
                    raise

                if get_next in done:
                    wait_interrupt.cancel()
                    try:
                        yield get_next.result()
                    except StopAsyncIteration:
                        return
                else:
                    wait_interrupt.cancel()
                    get_next.cancel()
                    try:
                        yield await get_next
                    except (StopAsyncIteration, asyncio.CancelledError):
                        pass
                    async for event in agent_gen:
                        yield event
                    return

        # ---- 有 retry_queue: 生产者-消费者模式 ----
        event_queue: asyncio.Queue = asyncio.Queue()
        agent_done = asyncio.Event()

        async def agent_producer():
            """独占消费 agent_gen, 将事件写入 event_queue, 同时监听中断。"""
            try:
                while True:
                    get_next = asyncio.ensure_future(agent_gen.__anext__())
                    wait_int = asyncio.ensure_future(interrupt_event.wait())
                    done, _ = await asyncio.wait(
                        [get_next, wait_int],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if wait_int.done():
                        get_next.cancel()
                        try:
                            await get_next
                        except (StopAsyncIteration, asyncio.CancelledError):
                            pass
                        async for event in agent_gen:
                            await event_queue.put(event)
                        break
                    wait_int.cancel()
                    try:
                        await event_queue.put(get_next.result())
                    except StopAsyncIteration:
                        break
            finally:
                agent_done.set()

        agent_task = asyncio.create_task(agent_producer())

        try:
            while True:
                if agent_done.is_set() and event_queue.empty():
                    return

                get_event = asyncio.ensure_future(event_queue.get())
                wait_done = asyncio.ensure_future(agent_done.wait())
                get_retry = asyncio.ensure_future(retry_queue.get())
                done, _ = await asyncio.wait(
                    [get_event, wait_done, get_retry],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if get_event in done:
                    wait_done.cancel()
                    get_retry.cancel()
                    yield get_event.result()
                elif get_retry in done:
                    get_event.cancel()
                    wait_done.cancel()
                    yield RetryNotification(get_retry.result())
                else:
                    get_event.cancel()
                    get_retry.cancel()
        finally:
            agent_task.cancel()

    def _map_event(self, event) -> dict | None:
        event_name = event.type.value.lower()

        match event:
            case ReplyStartEvent():
                return {"event": event_name, "data": {"reply_id": event.reply_id}}

            case ReplyEndEvent():
                return {
                    "event": event_name,
                    "data": {
                        "reply_id": event.reply_id,
                        "finished_reason": str(event.finished_reason),
                    },
                }

            case TextBlockStartEvent() | TextBlockEndEvent() | ThinkingBlockStartEvent() | ThinkingBlockEndEvent():
                return {"event": event_name, "data": {"block_id": event.block_id}}

            case TextBlockDeltaEvent() | ThinkingBlockDeltaEvent():
                return {"event": event_name, "data": {"content": event.delta, "block_id": event.block_id}}

            case ToolCallStartEvent() | ToolResultStartEvent():
                return {
                    "event": event_name,
                    "data": {
                        "tool_call_id": event.tool_call_id,
                        "tool_call_name": event.tool_call_name,
                    },
                }

            case ToolCallDeltaEvent() | ToolResultTextDeltaEvent():
                return {
                    "event": event_name,
                    "data": {
                        "tool_call_id": event.tool_call_id,
                        "delta": event.delta,
                    },
                }

            case ToolCallEndEvent():
                return {"event": event_name, "data": {"tool_call_id": event.tool_call_id}}

            case ToolResultEndEvent():
                return {
                    "event": event_name,
                    "data": {
                        "tool_call_id": event.tool_call_id,
                        "state": str(event.state),
                    },
                }

            case ModelCallStartEvent():
                return {"event": event_name, "data": {"model_name": event.model_name}}

            case ModelCallEndEvent():
                return {
                    "event": event_name,
                    "data": {
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                    },
                }

            case HintBlockEvent():
                return {
                    "event": event_name,
                    "data": {
                        "block_id": event.block_id,
                        "hint": event.hint,
                        "source": str(event.source),
                    },
                }

            case ExceedMaxItersEvent():
                return {"event": event_name, "data": {"message": "超过最大迭代次数"}}

            case RequireUserConfirmEvent():
                return {
                    "event": event_name,
                    "data": {
                        "reply_id": event.reply_id,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "suggested_rules": [
                                    {
                                        "tool_name": r.tool_name,
                                        "rule_content": r.rule_content,
                                        "behavior": r.behavior.value
                                        if hasattr(r.behavior, "value")
                                        else str(r.behavior),
                                    }
                                    for r in (tc.suggested_rules or [])
                                ],
                            }
                            for tc in event.tool_calls
                        ],
                    },
                }

            case RequireExternalExecutionEvent():
                return {
                    "event": event_name,
                    "data": {
                        "reply_id": event.reply_id,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "name": tc.name,
                            }
                            for tc in event.tool_calls
                        ],
                    },
                }

            case _:
                return None
