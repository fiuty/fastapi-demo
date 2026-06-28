"""
消息 DAO
"""
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.model.message import Message


class MessageDAO:

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, message_id: int) -> Optional[Message]:
        return self.db.get(Message, message_id)

    def list_by_conversation(
        self,
        conversation_id: int,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Message], int]:
        conditions = [Message.conversation_id == conversation_id]

        list_stmt = (
            select(Message)
            .where(*conditions)
            .order_by(Message.create_time.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
        records = list(self.db.execute(list_stmt).scalars().all())

        count_stmt = select(func.count(Message.id)).where(*conditions)
        total = self.db.execute(count_stmt).scalar_one()

        return records, total

    def insert(self, message: Message) -> Message:
        self.db.add(message)
        self.db.flush()
        self.db.refresh(message)
        return message

    def update(self, message: Message) -> Message:
        self.db.flush()
        self.db.refresh(message)
        return message

    def delete_by_id(self, message_id: int) -> bool:
        msg = self.get_by_id(message_id)
        if msg is None:
            return False
        self.db.delete(msg)
        self.db.flush()
        return True

    def delete_by_conversation_id(self, conversation_id: int) -> int:
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        messages = list(self.db.execute(stmt).scalars().all())
        count = len(messages)
        for msg in messages:
            self.db.delete(msg)
        self.db.flush()
        return count
