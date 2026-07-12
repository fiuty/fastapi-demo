"""
消息业务层
"""
import json
import logging

from sqlalchemy.orm import Session

from app.dao.message_dao import MessageDAO
from app.model.message import Message
from agentscope.message import Msg

logger = logging.getLogger("agentscope")


class MessageService:

    def __init__(self, db: Session):
        self.db = db
        self.message_dao = MessageDAO(db)

    def save_message(
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
        return self.message_dao.insert(msg)

    def save_assistant_message(
        self,
        conversation_id: str,
        full_response: list[dict],
        assistant_msg: Msg | None,
    ) -> str | None:
        if not full_response:
            return None
        try:
            db_msg = self.save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=json.dumps(full_response, ensure_ascii=False),
                extra_meta=assistant_msg.model_dump_json() if assistant_msg else None,
            )
            return db_msg.id
        except Exception as ex:
            logger.error(
                "保存助手回复失败 | conversation_id=%s | error=%s",
                conversation_id, ex,
            )
            return None
