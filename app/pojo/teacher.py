"""
教师 ORM 模型
"""
from datetime import datetime

from sqlalchemy import Integer, String, DateTime, func, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Teacher(Base):
    """
    教师表
    """
    __tablename__ = "t_teacher"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="姓名")
    teacher_no: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False, comment="工号")
    age: Mapped[int] = mapped_column(Integer, nullable=False, comment="年龄")
    gender: Mapped[str] = mapped_column(String(10), nullable=False, default="未知", comment="性别")
    subject: Mapped[str] = mapped_column(String(50), nullable=False, comment="所教学科")
    salary: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0, comment="薪资")
    email: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="邮箱")

    create_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间"
    )

    def __repr__(self) -> str:
        return f"<Teacher id={self.id} name={self.name} teacher_no={self.teacher_no}>"
