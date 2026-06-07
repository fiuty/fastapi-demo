"""
model 层: SQLAlchemy ORM 模型
对应 Java 中的 Entity / DO (与数据库表结构一一对应)
注意: 该层不要直接返回给前端, 与前端交互请使用 pojo 层的 Schema
"""
from app.model.student import Student
from app.model.teacher import Teacher

__all__ = ["Student", "Teacher"]
