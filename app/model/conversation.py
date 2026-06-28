"""
会话 ORM 模型
"""
from datetime import datetime

from sqlalchemy import Integer, String, DateTime, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Conversation(Base):
    __tablename__ = "t_conversation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, default="default_user", comment="用户ID")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新对话", comment="会话标题")

    create_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间"
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} title={self.title}>"
