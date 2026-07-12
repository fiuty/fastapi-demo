"""
pojo 层: Pydantic 数据校验/序列化模型
对应 Java 中的 DTO/VO/Form
- StudentCreate:  新增学生表单 (类似 @RequestBody AddStudentForm)
- StudentUpdate:  修改学生表单 (类似 @RequestBody UpdateStudentForm)
- StudentVO:      返回给前端的视图对象 (类似 VO/Resp)
- StudentQuery:   查询条件 (类似 Query 类的字段)
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class StudentBase(BaseModel):
    """学生基础字段 (Create 和 Update 共用)"""
    name: str = Field(..., min_length=1, max_length=50, description="姓名")
    student_no: str = Field(..., min_length=1, max_length=20, description="学号")
    age: int = Field(..., ge=1, le=200, description="年龄")
    gender: str = Field(default="未知", max_length=10, description="性别")
    clazz: str = Field(..., min_length=1, max_length=50, description="班级")
    email: Optional[str] = Field(default=None, max_length=100, description="邮箱")


class StudentCreate(StudentBase):
    """新增学生请求体"""
    pass


class StudentUpdate(BaseModel):
    """修改学生请求体 (所有字段可选, 局部更新)"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    student_no: Optional[str] = Field(default=None, min_length=1, max_length=20)
    age: Optional[int] = Field(default=None, ge=1, le=200)
    gender: Optional[str] = Field(default=None, max_length=10)
    clazz: Optional[str] = Field(default=None, min_length=1, max_length=50)
    email: Optional[str] = Field(default=None, max_length=100)


class StudentVO(StudentBase):
    """学生响应 VO"""
    id: int
    create_time: datetime
    update_time: datetime

    # 允许从 ORM 对象直接创建 (类似 Spring 的 BeanUtils.copyProperties)
    # model_config 取代了 Pydantic v1 的 class Config
    model_config = ConfigDict(from_attributes=True)


class StudentQuery(BaseModel):
    """学生分页查询条件"""
    name: Optional[str] = Field(default=None, description="姓名(模糊)")
    student_no: Optional[str] = Field(default=None, description="学号(精确)")
    clazz: Optional[str] = Field(default=None, description="班级(精确)")
    page: int = Field(default=1, ge=1, description="页码, 从1开始")
    size: int = Field(default=10, ge=1, le=100, description="每页条数")
