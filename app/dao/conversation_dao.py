"""
会话 DAO
"""
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.model.conversation import Conversation


class ConversationDAO:

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, conversation_id: str) -> Optional[Conversation]:
        return self.db.get(Conversation, conversation_id)

    def page_query(
        self,
        user_id: Optional[str] = None,
        page: int = 1,
        size: int = 10,
    ) -> tuple[list[Conversation], int]:
        conditions = []
        if user_id:
            conditions.append(Conversation.user_id == user_id)

        list_stmt = (
            select(Conversation)
            .where(*conditions)
            .order_by(Conversation.update_time.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        records = list(self.db.execute(list_stmt).scalars().all())

        count_stmt = select(func.count(Conversation.id)).where(*conditions)
        total = self.db.execute(count_stmt).scalar_one()

        return records, total

    def insert(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        self.db.flush()
        self.db.refresh(conversation)
        return conversation

    def update(self, conversation: Conversation) -> Conversation:
        self.db.flush()
        self.db.refresh(conversation)
        return conversation

    def delete_by_id(self, conversation_id: str) -> bool:
        conv = self.get_by_id(conversation_id)
        if conv is None:
            return False
        self.db.delete(conv)
        self.db.flush()
        return True
