"""
消息 Pydantic Schema
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class MessageCreate(BaseModel):
    content: str = Field(..., description="消息内容")
    role: str = Field(default="user", description="角色: user/assistant/system/tool")
    event_type: Optional[str] = Field(default=None, description="事件类型")
    extra_meta: Optional[str] = Field(default=None, description="附加元数据(JSON)")


class MessageVO(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    event_type: Optional[str] = None
    extra_meta: Optional[str] = None
    create_time: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = Field(default=None, description="会话ID, 不传则创建新会话")
    message: str = Field(..., min_length=1, description="用户消息")


class ConfirmResultItem(BaseModel):
    """工具确认结果项，客户端对 RequireUserConfirmEvent 的回应"""
    tool_call_id: str = Field(..., description="工具调用ID")
    tool_call_name: str = Field(..., description="工具名称")
    confirmed: bool = Field(..., description="是否允许执行")


class ChatInteractiveRequest(BaseModel):
    """交互式对话请求：支持发送新消息或对工具调用进行确认/拒绝"""
    message: Optional[str] = Field(default=None, description="用户新消息（与 confirm_results 二选一）")
    conversation_id: Optional[str] = Field(default=None, description="会话ID，不传则创建新会话")
    confirm_results: Optional[list[ConfirmResultItem]] = Field(default=None, description="工具确认结果列表")
