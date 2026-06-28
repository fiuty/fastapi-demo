"""
ChatService: Agent 流式对话服务
整合会话管理、消息持久化、Agent 流式调用
"""
import json
from typing import AsyncGenerator

from sqlalchemy.orm import Session

from agentscope.agent import Agent
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
)
from agentscope.message import Msg, UserMsg, AssistantMsg

from app.model.conversation import Conversation
from app.model.message import Message


class ChatService:

    def __init__(self, db: Session):
        self.db = db

    async def chat_stream(
        self,
        agent: Agent,
        user_message: str,
        conversation_id: int | None = None,
        user_id: str = "default_user",
    ) -> AsyncGenerator[dict, None]:
        full_response = ""
        conversation = self._resolve_conversation(conversation_id, user_message[:30], user_id)

        self._save_message(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
        )

        history = self._load_history(conversation.id)
        history.append(UserMsg(name=user_id, content=user_message))

        yield {"event": "conversation_id", "data": {"conversation_id": conversation.id}}

        try:
            async for event in agent.reply_stream(history):
                mapped = self._map_event(event)
                if mapped is not None:
                    if mapped["event"] in ("text_delta",):
                        full_response += mapped["data"].get("content", "")
                    yield mapped
        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}
            return

        if full_response:
            assistant_msg = self._save_message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_response,
            )
            yield {
                "event": "assistant_message_id",
                "data": {"message_id": assistant_msg.id},
            }

    def _resolve_conversation(self, conversation_id: int | None, title: str, user_id: str) -> Conversation:
        if conversation_id is not None:
            conv = self.db.get(Conversation, conversation_id)
            if conv is None:
                conv = Conversation(user_id=user_id, title=title)
                self.db.add(conv)
                self.db.flush()
                self.db.refresh(conv)
            return conv
        conv = Conversation(user_id=user_id, title=title)
        self.db.add(conv)
        self.db.flush()
        self.db.refresh(conv)
        return conv

    def _save_message(
        self,
        conversation_id: int,
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

    def _load_history(self, conversation_id: int) -> list[Msg]:
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
                history.append(AssistantMsg(name="assistant", content=msg.content))
        return history

    def _map_event(self, event) -> dict | None:
        event_type = event.type if hasattr(event, "type") else type(event).__name__

        if isinstance(event, ReplyStartEvent):
            return {"event": "reply_start", "data": {"reply_id": event.reply_id}}

        if isinstance(event, ReplyEndEvent):
            return {"event": "reply_end", "data": {"reply_id": event.reply_id}}

        if isinstance(event, TextBlockStartEvent):
            return {"event": "text_start", "data": {"block_id": event.block_id}}

        if isinstance(event, TextBlockDeltaEvent):
            return {"event": "text_delta", "data": {"content": event.delta, "block_id": event.block_id}}

        if isinstance(event, TextBlockEndEvent):
            return {"event": "text_end", "data": {"block_id": event.block_id}}

        if isinstance(event, ThinkingBlockStartEvent):
            return {"event": "thinking_start", "data": {"block_id": event.block_id}}

        if isinstance(event, ThinkingBlockDeltaEvent):
            return {"event": "thinking_delta", "data": {"content": event.delta, "block_id": event.block_id}}

        if isinstance(event, ThinkingBlockEndEvent):
            return {"event": "thinking_end", "data": {"block_id": event.block_id}}

        if isinstance(event, ToolCallStartEvent):
            return {
                "event": "tool_call_start",
                "data": {
                    "tool_call_id": event.tool_call_id,
                    "tool_call_name": event.tool_call_name,
                },
            }

        if isinstance(event, ToolCallDeltaEvent):
            return {
                "event": "tool_call_delta",
                "data": {
                    "tool_call_id": event.tool_call_id,
                    "delta": getattr(event, "delta", ""),
                },
            }

        if isinstance(event, ToolCallEndEvent):
            return {"event": "tool_call_end", "data": {"tool_call_id": event.tool_call_id}}

        if isinstance(event, ToolResultStartEvent):
            return {
                "event": "tool_result_start",
                "data": {
                    "tool_call_id": event.tool_call_id,
                    "tool_call_name": event.tool_call_name,
                },
            }

        if isinstance(event, ToolResultTextDeltaEvent):
            return {
                "event": "tool_result_delta",
                "data": {
                    "tool_call_id": event.tool_call_id,
                    "content": event.delta,
                },
            }

        if isinstance(event, ToolResultEndEvent):
            return {
                "event": "tool_result_end",
                "data": {
                    "tool_call_id": event.tool_call_id,
                    "state": str(event.state) if hasattr(event, "state") else None,
                },
            }

        if isinstance(event, ModelCallStartEvent):
            return {"event": "model_call_start", "data": {"model_name": event.model_name}}

        if isinstance(event, ModelCallEndEvent):
            return {
                "event": "model_call_end",
                "data": {
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                },
            }

        if isinstance(event, HintBlockEvent):
            return {
                "event": "hint",
                "data": {
                    "block_id": event.block_id,
                    "hint": event.hint,
                    "source": str(event.source) if hasattr(event, "source") else None,
                },
            }

        if isinstance(event, ExceedMaxItersEvent):
            return {"event": "error", "data": {"message": "超过最大迭代次数"}}

        return None
