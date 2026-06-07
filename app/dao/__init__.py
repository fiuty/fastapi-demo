"""
dao 层: 数据访问对象
对应 Java 中的 Mapper 接口 + BaseMapper<T> (MyBatis-Plus)
每个 DAO 接收一个 Session (类似 SqlSession), 不持有状态
"""
from app.dao.student_dao import StudentDAO
from app.dao.teacher_dao import TeacherDAO

__all__ = ["StudentDAO", "TeacherDAO"]
