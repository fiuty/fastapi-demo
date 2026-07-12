"""
学生 ORM 模型
对应 MyBatis-Plus 中的 @TableName("t_student") Student 实体
"""
from datetime import datetime

from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Student(Base):
    """
    学生表
    SQLAlchemy 2.0 风格: 用 Mapped[] + mapped_column() 做类型注解
    等价于 Java 的:
        @Data
        @TableName("t_student")
        public class Student {
            @TableId(type = IdType.AUTO)
            private Long id;
            private String name;
            private String studentNo;
            private Integer age;
            private String gender;
            private String clazz;
            @TableField(fill = FieldFill.INSERT)
            private LocalDateTime createTime;
            @TableField(fill = FieldFill.INSERT_UPDATE)
            private LocalDateTime updateTime;
        }
    """
    __tablename__ = "t_student"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="姓名")
    student_no: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False, comment="学号")
    age: Mapped[int] = mapped_column(Integer, nullable=False, comment="年龄")
    gender: Mapped[str] = mapped_column(String(10), nullable=False, default="未知", comment="性别")
    clazz: Mapped[str] = mapped_column(String(50), nullable=False, comment="班级")
    email: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="邮箱")

    create_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间"
    )

    def __repr__(self) -> str:
        return f"<Student id={self.id} name={self.name} student_no={self.student_no}>"
