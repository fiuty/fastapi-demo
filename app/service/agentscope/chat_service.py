"""
ChatService: Agent 流式对话服务
整合会话管理、消息持久化、Agent 流式调用
使用全局 Agent + RedisStorage 持久化 AgentState 实现多会话隔离

支持对话中断与恢复:
- 中断运行中的回复: 取消承载 reply_stream 的 asyncio.Task, Agent 内部捕获
  CancelledError 后清理上下文并产出 INTERRUPTED 结束事件, 上下文保持一致。
- 中断暂停中的回复(等待用户确认/外部执行): 通过 UserInterruptEvent 清理待处理工具调用。
- 恢复: 携带 conversation_id 再次调用 chat_stream 即可, 历史消息与状态从
  Redis/DB 恢复, 不会丢失。
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
    ReplyEndReason,
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

from app.model.conversation import Conversation
from app.model.message import Message
from app.service.agentscope.agent_service import AgentService

logger = logging.getLogger("agentscope")


class ChatService:

    # 会话 ID -> 正在运行的 reply 任务, 供中断接口取消
    _running_tasks: dict[str, asyncio.Task] = {}

    def __init__(self, db: Session):
        self.db = db

    # ======================== 运行任务注册表 ========================

    @classmethod
    def _register_task(cls, session_id: str, task: asyncio.Task) -> None:
        """注册流式任务, 供中断接口取消"""
        cls._running_tasks[session_id] = task

    @classmethod
    def _unregister_task(cls, session_id: str) -> None:
        cls._running_tasks.pop(session_id, None)

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

    # ======================== 流式对话 ========================

    async def chat_stream(
        self,
        user_message: str,
        conversation_id: str | None = None,
        user_id: str = "default_user",
    ) -> AsyncGenerator[dict, None]:
        conversation = self._resolve_conversation(
            conversation_id, user_message[:30], user_id,
        )
        session_id = conversation.id

        # 若同会话存在尚未结束的回复, 取消并等待其清理完成后再继续
        await ChatService._cancel_running_task(session_id)

        # 保存用户消息到 DB
        self._save_message(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
        )

        # 从 Redis 加载会话状态; 过期/不存在则从 DB 恢复历史消息后重建
        state = await AgentService.load_state(session_id)
        if state is None:
            state = AgentState(
                session_id=session_id,
                permission_context=PermissionContext(mode=PermissionMode.BYPASS),
            )
            history = self._load_history(conversation.id)
            if history:
                agent_temp = AgentService.get_agent()
                agent_temp.state = state
                await agent_temp.observe(history)
                logger.info(
                    "从 DB 恢复历史消息 | session_id=%s | count=%d",
                    session_id, len(history),
                )

        # 创建独立 Agent (不复用全局单例, 避免并发会话 state 互相覆盖)
        agent = AgentService.create_agent_with_state(state)

        # 自动确认上一轮遗留的 pending ASKING 工具调用, 避免统一会话一直等到工具调用结果
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

        yield {"event": "conversation_id", "data": {"conversation_id": conversation.id}}

        # 只传新消息, agent.state.context 会自动累积
        user_msg = UserMsg(name=user_id, content=user_message)

        # 注册当前请求任务, 供中断接口通过 task.cancel() 取消。
        # 直接迭代 agent.reply_stream, CancelledError 会被 Agent 内部
        # (model._stream / _reply_impl) 捕获并产出 INTERRUPTED 结束事件,
        # 随后 async for 正常结束, 进入 finally 持久化。
        current_task = asyncio.current_task()
        if current_task is not None:
            ChatService._register_task(session_id, current_task)

        full_response: list[dict] = []
        assistant_msg: Msg | None = None

        try:
            async for event in agent.reply_stream(user_msg):
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
            logger.info("对话被中断(CancelledError 传播到 chat_stream) | session_id=%s", session_id)
        except Exception as e:
            logger.exception("reply_stream 异常")
            yield {"event": "error", "data": {"message": str(e)}}
        finally:
            ChatService._unregister_task(session_id)
            # 持久化 state 与(部分)助手消息。
            # asyncio.shield 防止取消信号打断 Redis 写入; shield 内部任务
            # 即使外层被取消也会在后台完成。
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
            try:
                self._save_assistant_message(
                    conversation.id, full_response, assistant_msg,
                )
                # 显式提交, 不依赖 get_db() 的延迟提交。
                # 中断后请求可能被取消, get_db() 的 commit 不会执行反而可能
                # rollback, 导致已 flush 的消息丢失。
                self.db.commit()
            except Exception:
                logger.exception("保存助手消息/提交失败")
                try:
                    self.db.rollback()
                except Exception:
                    pass

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
            dict: 中断结果, 含 status / conversation_id / reason。
        """
        # 1. 取消正在运行的回复任务, 等待其 finally 持久化完成
        task = ChatService._running_tasks.get(conversation_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                # 直接迭代模式下 CancelledError 会快速传播到 Agent,
                # Agent 内部捕获后 finally 持久化 state/消息, 通常 < 1s。
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                logger.warning(
                    "等待中断清理超时或异常 | conversation_id=%s",
                    conversation_id,
                )
            # 在 await 之后才注销, 避免"继续"请求在 finally 未完成时就
            # 进入, 导致 DB 中 conversation 未提交而创建重复记录。
            ChatService._unregister_task(conversation_id)
            logger.info("已中断运行中的对话 | conversation_id=%s", conversation_id)
            return {
                "status": "interrupted",
                "conversation_id": conversation_id,
                "reason": "task_cancelled",
            }

        # 2. 中断暂停中的智能体(等待用户确认/外部执行)
        state = await AgentService.load_state(conversation_id)
        if state is None:
            return {
                "status": "noop",
                "conversation_id": conversation_id,
                "reason": "no_active_session",
            }

        agent = AgentService.create_agent_with_state(state)
        if not state.has_awaiting_tool_calls(agent.name):
            return {
                "status": "noop",
                "conversation_id": conversation_id,
                "reason": "not_parked",
            }

        reply_id = state.reply_id
        full_response: list[dict] = []
        assistant_msg: Msg | None = None
        try:
            async for event in agent.reply_stream(
                UserInterruptEvent(reply_id=reply_id),
            ):
                if isinstance(event, ReplyStartEvent):
                    assistant_msg = AssistantMsg(
                        name=event.name, content=[], id=event.reply_id,
                    )
                elif assistant_msg is not None:
                    assistant_msg.append_event(event)

                mapped = self._map_event(event)
                if mapped is not None:
                    full_response.append(mapped)

            await AgentService.save_state(conversation_id, agent.state)
            self._save_assistant_message(conversation_id, full_response, assistant_msg)
            try:
                self.db.commit()
            except Exception:
                logger.exception("parked_interrupt 提交失败")
                try:
                    self.db.rollback()
                except Exception:
                    pass
            logger.info("已中断暂停中的对话 | conversation_id=%s", conversation_id)
            return {
                "status": "interrupted",
                "conversation_id": conversation_id,
                "reason": "parked_interrupted",
            }
        except Exception as e:
            logger.exception("中断暂停中的智能体失败")
            return {
                "status": "error",
                "conversation_id": conversation_id,
                "message": str(e),
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
        conversation = self._resolve_conversation(
            conversation_id,
            (user_message or "tool confirmation")[:30],
            user_id,
        )
        session_id = conversation.id

        # 仅当有新消息时保存用户消息
        if user_message:
            self._save_message(
                conversation_id=conversation.id,
                role="user",
                content=user_message,
            )
        elif confirm_results:
            self._save_message(
                conversation_id=conversation.id,
                role="user",
                content=json.dumps(confirm_results, ensure_ascii=False),
                event_type="tool_confirmation",
            )

        # 加载状态，使用 DEFAULT 模式（需要权限确认）
        state = await AgentService.load_state(session_id)
        if state is None:
            state = AgentState(
                session_id=session_id,
                permission_context=PermissionContext(mode=PermissionMode.DEFAULT),
            )
            history = self._load_history(conversation.id)
            if history:
                agent_temp = AgentService.get_agent()
                agent_temp.state = state
                await agent_temp.observe(history)
                logger.info(
                    "从 DB 恢复历史消息 | session_id=%s | count=%d",
                    session_id,
                    len(history),
                )

        agent = AgentService.set_agent_state(state)

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
        _open_thinking_blocks: set[str] = set()
        _pending_events: list[dict] = []

        try:
            async for event in agent.reply_stream(agent_input):
                if isinstance(event, ReplyStartEvent):
                    assistant_msg = AssistantMsg(
                        name=event.name, content=[], id=event.reply_id,
                    )
                elif assistant_msg is not None:
                    assistant_msg.append_event(event)

                mapped = self._map_event(event)
                if mapped is None:
                    continue

                for ordered in self._reorder_event(
                    mapped, _open_thinking_blocks, _pending_events,
                ):
                    full_response.append(ordered)
                    yield ordered
        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}
            return
        finally:
            await AgentService.save_state(session_id, agent.state)
            self._save_assistant_message(
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
            return ChatService._build_confirm_input(agent, confirm_results)

        if user_message:
            return ChatService._build_user_input(agent, user_message, user_id)

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

    def _resolve_conversation(
        self,
        conversation_id: str | None,
        title: str,
        user_id: str,
    ) -> Conversation:
        """根据 conversation_id (主键) 查找会话, 不存在则创建"""
        if conversation_id is not None:
            conv = self.db.get(Conversation, conversation_id)
            if conv is not None:
                return conv
        # 不存在或未传 conversation_id, 创建新会话 (id 由模型 default 自动生成 UUID)
        conv = Conversation(user_id=user_id, title=title)
        self.db.add(conv)
        self.db.flush()
        self.db.refresh(conv)
        return conv

    def _save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        event_type: str | None = None,
        extra_meta: str | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            event_type=event_type,
            extra_meta=extra_meta,
        )
        self.db.add(msg)
        self.db.flush()
        self.db.refresh(msg)
        return msg

    def _save_assistant_message(
        self,
        conversation_id: str,
        full_response: list[dict],
        assistant_msg: Msg | None,
    ) -> str | None:
        """持久化助手回复事件列表, 返回 message_id"""
        if not full_response:
            return None
        try:
            db_msg = self._save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=json.dumps(full_response, ensure_ascii=False),
                extra_meta=assistant_msg.model_dump_json() if assistant_msg else None,
            )
            return db_msg.id
        except Exception as ex:
            logger.warning("保存助手回复失败 | conversation_id=%s | error=%s", conversation_id, ex)
            return None

    def _load_history(self, conversation_id: str) -> list[Msg]:
        from sqlalchemy import select
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
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
    def _reorder_event(
        mapped: dict | None,
        open_thinking_blocks: set[str],
        pending_events: list[dict],
    ) -> list[dict]:
        """对事件进行重排序: 在 thinking block 未关闭之前, 缓冲 text block 事件;
        待 thinking_block_end 到达且所有 thinking block 关闭后, 按序输出缓冲区内容."""
        if mapped is None:
            return []

        event_type: str = mapped["event"]
        block_id: str | None = (mapped.get("data") or {}).get("block_id")

        if event_type == "thinking_block_start":
            if block_id:
                open_thinking_blocks.add(block_id)
            return [mapped]

        if event_type == "thinking_block_end":
            if block_id:
                open_thinking_blocks.discard(block_id)
            if not open_thinking_blocks and pending_events:
                result = [mapped]
                result.extend(pending_events)
                pending_events.clear()
                return result
            return [mapped]

        if open_thinking_blocks and event_type in (
            "text_block_start",
            "text_block_delta",
            "text_block_end",
        ):
            pending_events.append(mapped)
            return []

        return [mapped]

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
