"""
消息 ORM 模型
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "t_message"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()), comment="主键ID(UUID)"
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("t_conversation.id", ondelete="CASCADE"), nullable=False, index=True, comment="会话ID"
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, comment="角色: user/assistant/system/tool")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="消息内容")
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="事件类型")
    extra_meta: Mapped[str | None] = mapped_column(Text, nullable=True, comment="附加元数据(JSON)")

    create_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="创建时间"
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role} conversation_id={self.conversation_id}>"
