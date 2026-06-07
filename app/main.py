"""
FastAPI 应用入口
对应 Spring Boot 的 Application.java (含 main 方法 + @SpringBootApplication)

启动方式:
    uvicorn app.main:app --reload
或者:
    python -m app.main
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.exception import BizException
from app.common.response import Response
from app.config import settings
from app.controller.student_controller import router as student_router
from app.controller.teacher_controller import router as teacher_router
from app.database import init_db

# 日志 (类似 Spring 的 slf4j)
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("app")


# ===================== 应用初始化 =====================
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
## 一个面向 Java 开发者 的 FastAPI 示例项目

实现学生 / 教师表的增删改查, 涵盖:

- 分层架构: **Controller → Service → DAO → Model**
- 全局异常处理 (统一返回格式)
- 自动生成 OpenAPI (Swagger) 文档

### 接口文档
- **Swagger UI**:  [/docs](/docs)
- **ReDoc**:       [/redoc](/redoc)
- **OpenAPI JSON**: [/openapi.json](/openapi.json)

### 统一响应格式
```json
{
  "code": 200,
  "message": "success",
  "data": { ... }
}
```
""",
    docs_url="/docs",       # Swagger UI
    redoc_url="/redoc",     # ReDoc
    openapi_url="/openapi.json",
)


# ===================== 启动事件 =====================
@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库 (类似 Spring 的 CommandLineRunner)"""
    init_db()
    logger.info("数据库表初始化完成")


# ===================== 注册路由 =====================
app.include_router(student_router)
app.include_router(teacher_router)


# ===================== 全局异常处理器 =====================
# 对应 Java 的 @ControllerAdvice + @ExceptionHandler
# ---------------------------------------------------------------
# Spring 中:
#   @RestControllerAdvice
#   public class GlobalExceptionHandler {
#       @ExceptionHandler(BizException.class)
#       public R<?> handleBiz(BizException e) { ... }
#
#       @ExceptionHandler(MethodArgumentNotValidException.class)
#       public R<?> handleValid(MethodArgumentNotValidException e) { ... }
#
#       @ExceptionHandler(Exception.class)
#       public R<?> handleAll(Exception e) { ... }
#   }
# ---------------------------------------------------------------


@app.exception_handler(BizException)
async def biz_exception_handler(request: Request, exc: BizException) -> JSONResponse:
    """业务异常: 主动抛出的 BizException"""
    logger.warning("业务异常 | path=%s | code=%s | msg=%s", request.url.path, exc.code, exc.message)
    return JSONResponse(
        status_code=200,  # 业务异常仍返回 200, 错误码放在 body
        content=Response.fail(code=exc.code, message=exc.message, data=exc.data).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """参数校验异常: Pydantic 校验失败 (类似 @Valid 抛出的 BindException)"""
    errors = exc.errors()
    # 取第一个错误作为提示, 完整错误放在 data
    first = errors[0] if errors else {}
    loc = ".".join(str(x) for x in first.get("loc", []))
    msg = first.get("msg", "参数校验失败")
    message = f"{loc}: {msg}" if loc else msg
    logger.warning("参数校验失败 | path=%s | errors=%s", request.url.path, errors)
    return JSONResponse(
        status_code=422,
        content=Response.fail(code=400, message=message, data=errors).model_dump(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTP 异常: 404/405 等"""
    logger.warning("HTTP异常 | path=%s | status=%s | detail=%s",
                   request.url.path, exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=Response.fail(code=exc.status_code, message=str(exc.detail)).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常: 捕获所有未处理的异常 (类似 @ExceptionHandler(Exception.class))"""
    logger.exception("未处理异常 | path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content=Response.fail(code=500, message=f"服务器内部错误: {exc}").model_dump(),
    )


# ===================== 根路由 =====================
@app.get("/", tags=["根路径"], summary="健康检查")
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
    }


# ===================== 直接 python app/main.py 启动 =====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.DEBUG,
    )
