"""
会话业务层
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.common.exception import BizException
from app.dao.conversation_dao import ConversationDAO
from app.dao.message_dao import MessageDAO
from app.model.conversation import Conversation
from app.model.message import Message
from app.pojo.conversation import ConversationCreate, ConversationUpdate, ConversationDetailVO, ConversationVO
from app.pojo.message import MessageVO


class ConversationService:

    def __init__(self, db: Session):
        self.db = db
        self.conversation_dao = ConversationDAO(db)
        self.message_dao = MessageDAO(db)

    def create_conversation(self, form: ConversationCreate, user_id: str = "default_user") -> Conversation:
        conv = Conversation(user_id=user_id, title=form.title)
        return self.conversation_dao.insert(conv)

    def get_conversation(self, conversation_id: int) -> Conversation:
        conv = self.conversation_dao.get_by_id(conversation_id)
        if conv is None:
            raise BizException(code=404, message=f"会话(id={conversation_id})不存在")
        return conv

    def get_conversation_detail(self, conversation_id: int) -> ConversationDetailVO:
        conv = self.get_conversation(conversation_id)
        messages, _ = self.message_dao.list_by_conversation(conversation_id, page=1, size=1000)
        return ConversationDetailVO(
            id=conv.id,
            user_id=conv.user_id,
            title=conv.title,
            create_time=conv.create_time,
            update_time=conv.update_time,
            messages=[MessageVO.model_validate(m) for m in messages],
        )

    def page_conversations(
        self,
        user_id: Optional[str] = None,
        page: int = 1,
        size: int = 10,
    ) -> tuple[list[Conversation], int]:
        return self.conversation_dao.page_query(user_id=user_id, page=page, size=size)

    def update_conversation(self, conversation_id: int, form: ConversationUpdate) -> Conversation:
        conv = self.get_conversation(conversation_id)
        update_data = form.model_dump(exclude_unset=True)
        if not update_data:
            return conv
        for key, value in update_data.items():
            setattr(conv, key, value)
        return self.conversation_dao.update(conv)

    def delete_conversation(self, conversation_id: int) -> None:
        if not self.conversation_dao.delete_by_id(conversation_id):
            raise BizException(code=404, message=f"会话(id={conversation_id})不存在")
