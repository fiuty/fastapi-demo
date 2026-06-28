"""
dao 层: 数据访问对象
"""
from app.dao.student_dao import StudentDAO
from app.dao.teacher_dao import TeacherDAO
from app.dao.conversation_dao import ConversationDAO
from app.dao.message_dao import MessageDAO

__all__ = ["StudentDAO", "TeacherDAO", "ConversationDAO", "MessageDAO"]
