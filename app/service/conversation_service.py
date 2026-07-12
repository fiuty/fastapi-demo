"""
会话业务层
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.common.exception import BizException
from app.dao.conversation_dao import ConversationDAO
from app.dao.message_dao import MessageDAO
from app.model.conversation import Conversation
from app.pojo.conversation import ConversationCreate, ConversationUpdate, ConversationDetailVO, ConversationVO
from app.pojo.message import MessageVO
from app.service.agentscope.agent_service import AgentService


class ConversationService:

    def __init__(self, db: Session):
        self.db = db
        self.conversation_dao = ConversationDAO(db)
        self.message_dao = MessageDAO(db)

    def create_conversation(self, form: ConversationCreate, user_id: str = "default_user") -> Conversation:
        conv = Conversation(user_id=user_id, title=form.title)
        return self.conversation_dao.insert(conv)

    def get_conversation(self, conversation_id: str) -> Conversation:
        conv = self.conversation_dao.get_by_id(conversation_id)
        if conv is None:
            raise BizException(code=404, message=f"会话(id={conversation_id})不存在")
        return conv

    def get_conversation_detail(self, conversation_id: str) -> ConversationDetailVO:
        conv = self.get_conversation(conversation_id)
        messages, _ = self.message_dao.list_by_conversation(conv.id, page=1, size=1000)
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

    def update_conversation(self, conversation_id: str, form: ConversationUpdate) -> Conversation:
        conv = self.get_conversation(conversation_id)
        update_data = form.model_dump(exclude_unset=True)
        if not update_data:
            return conv
        for key, value in update_data.items():
            setattr(conv, key, value)
        return self.conversation_dao.update(conv)

    def resolve_conversation(
        self,
        conversation_id: str | None,
        title: str,
        user_id: str,
    ) -> Conversation:
        """根据 conversation_id 查找会话，不存在则创建"""
        if conversation_id is not None:
            conv = self.conversation_dao.get_by_id(conversation_id)
            if conv is not None:
                return conv
        conv = Conversation(user_id=user_id, title=title)
        return self.conversation_dao.insert(conv)

    async def delete_conversation(self, conversation_id: str) -> None:
        conv = self.get_conversation(conversation_id)
        await AgentService.remove_state(conv.id)
        if not self.conversation_dao.delete_by_id(conv.id):
            raise BizException(code=404, message=f"会话(id={conversation_id})不存在")
