"""
统一响应封装
对应 Java 中的 R<T> / Result<T> / ApiResponse<T>

    {
        "code": 200,
        "message": "success",
        "data": ...
    }
"""
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Response(BaseModel, Generic[T]):
    """统一响应结构"""
    code: int = Field(default=200, description="状态码, 200 表示成功")
    message: str = Field(default="success", description="提示信息")
    data: Optional[T] = Field(default=None, description="业务数据")

    @classmethod
    def ok(cls, data: T | None = None, message: str = "success") -> "Response[T]":
        """成功响应"""
        return cls(code=200, message=message, data=data)

    @classmethod
    def fail(cls, code: int = 500, message: str = "fail", data: Any = None) -> "Response[T]":
        """失败响应"""
        return cls(code=code, message=message, data=data)


class PageData(BaseModel, Generic[T]):
    """分页数据, 类似 MyBatis-Plus 的 IPage<T>"""
    records: list[T] = Field(default_factory=list, description="数据列表")
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, description="当前页")
    size: int = Field(default=10, description="每页条数")

    @classmethod
    def of(cls, records: list[T], total: int, page: int, size: int) -> "PageData[T]":
        return cls(records=records, total=total, page=page, size=size)
