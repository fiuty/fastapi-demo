"""
教师 Pydantic Schema
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class TeacherBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="姓名")
    teacher_no: str = Field(..., min_length=1, max_length=20, description="工号")
    age: int = Field(..., ge=1, le=200, description="年龄")
    gender: str = Field(default="未知", max_length=10, description="性别")
    subject: str = Field(..., min_length=1, max_length=50, description="所教学科")
    salary: float = Field(default=0, ge=0, description="薪资")
    email: Optional[str] = Field(default=None, max_length=100, description="邮箱")


class TeacherCreate(TeacherBase):
    pass


class TeacherUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    teacher_no: Optional[str] = Field(default=None, min_length=1, max_length=20)
    age: Optional[int] = Field(default=None, ge=1, le=200)
    gender: Optional[str] = Field(default=None, max_length=10)
    subject: Optional[str] = Field(default=None, min_length=1, max_length=50)
    salary: Optional[float] = Field(default=None, ge=0)
    email: Optional[str] = Field(default=None, max_length=100)


class TeacherVO(TeacherBase):
    id: int
    create_time: datetime
    update_time: datetime

    model_config = ConfigDict(from_attributes=True)


class TeacherQuery(BaseModel):
    name: Optional[str] = Field(default=None, description="姓名(模糊)")
    teacher_no: Optional[str] = Field(default=None, description="工号(精确)")
    subject: Optional[str] = Field(default=None, description="学科(精确)")
    page: int = Field(default=1, ge=1, description="页码, 从1开始")
    size: int = Field(default=10, ge=1, le=100, description="每页条数")
