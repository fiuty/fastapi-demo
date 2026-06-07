"""
自定义业务异常
对应 Java 中继承 RuntimeException 的 BizException
"""


class BizException(Exception):
    """
    业务异常, 在 service / dao 抛出后会被全局异常处理器捕获
    用法:
        raise BizException(code=400, message="学号已存在")
    """

    def __init__(self, code: int = 500, message: str = "业务异常", data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)
