"""
会话 Pydantic Schema
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", max_length=200, description="会话标题")


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200, description="会话标题")


class ConversationVO(BaseModel):
    id: int
    user_id: str
    title: str
    create_time: datetime
    update_time: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetailVO(ConversationVO):
    messages: list["MessageVO"] = Field(default_factory=list, description="消息列表")
