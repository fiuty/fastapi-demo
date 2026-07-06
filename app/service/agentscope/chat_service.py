"""
ChatService: Agent 流式对话服务
整合会话管理、消息持久化、Agent 流式调用
使用全局 Agent + RedisStorage 持久化 AgentState 实现多会话隔离
"""
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
)
from agentscope.message import Msg, UserMsg, AssistantMsg
from agentscope.message._block import ToolCallState
from agentscope.state import AgentState

from app.model.conversation import Conversation
from app.model.message import Message
from app.service.agentscope.agent_service import AgentService

logger = logging.getLogger("agentscope")


class ChatService:

    def __init__(self, db: Session):
        self.db = db

    async def chat_stream(
        self,
        user_message: str,
        conversation_id: str | None = None,
        user_id: str = "default_user",
    ) -> AsyncGenerator[dict, None]:
        full_response: list[dict] = []
        conversation = self._resolve_conversation(conversation_id, user_message[:30], user_id)
        session_id = conversation.id

        # 保存用户消息到 DB
        self._save_message(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
        )

        # 从 Redis 加载会话状态; 过期/不存在则从 DB 恢复历史消息后重建
        state = await AgentService.load_state(session_id)
        if state is None:
            state = AgentState(session_id=session_id, permission_context=PermissionContext(mode=PermissionMode.BYPASS))
            history = self._load_history(conversation.id)
            if history:
                agent_temp = AgentService.get_agent()
                agent_temp.state = state
                await agent_temp.observe(history)
                logger.info("从 DB 恢复历史消息 | session_id=%s | count=%d", session_id, len(history))

        # 将 state 挂载到全局 Agent
        agent = AgentService.set_agent_state(state)

        # # 自动确认上一轮遗留的 pending ASKING 工具调用，避免统一会话一直等到工具调用结果
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

        assistant_msg = None
        _open_thinking_blocks: set[str] = set()
        _pending_events: list[dict] = []

        try:
            async for event in agent.reply_stream(user_msg):
                if isinstance(event, ReplyStartEvent):
                    assistant_msg = AssistantMsg(name=event.name, content=[], id=event.reply_id)
                elif assistant_msg is not None:
                    assistant_msg.append_event(event)

                mapped = self._map_event(event)
                if mapped is None:
                    continue

                for ordered in self._reorder_event(mapped, _open_thinking_blocks, _pending_events):
                    full_response.append(ordered)
                    yield ordered
        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}
            return
        finally:
            await AgentService.save_state(session_id, agent.state)
            self._save_assistant_message(conversation.id, full_response, assistant_msg,)

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
            case ReplyStartEvent() | ReplyEndEvent():
                return {"event": event_name, "data": {"reply_id": event.reply_id}}

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
