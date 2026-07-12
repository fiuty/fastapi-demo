"""
model 层: SQLAlchemy ORM 模型
"""
from app.pojo.student import Student
from app.pojo.teacher import Teacher
from app.pojo.conversation import Conversation
from app.pojo.message import Message

__all__ = ["Student", "Teacher", "Conversation", "Message"]