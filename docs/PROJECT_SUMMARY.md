# 项目总结

> 适合从 Spring Boot 转过来的同学，先总览全貌，再逐层深入。

## 1. 整体目录

```
app/
├── main.py           入口文件 — 装配 FastAPI app、注册路由、注册异常处理器
├── config.py         读取 .env 配置 (类似 application.yml)
├── database.py       SQLAlchemy 引擎 + SessionLocal + get_db() 依赖
│
├── common/           公共组件
│   ├── response.py       统一响应 Response[T] + PageData[T]  (类似 R<T>)
│   └── exception.py      BizException 自定义业务异常          (类似 BizException)
│
├── model/            ORM 模型 — 与数据库表结构一一对应 (类似 Entity / DO)
├── pojo/             Pydantic Schema — 入参 / 出参 / 查询条件 (类似 DTO / VO / Query)
├── dao/              数据访问 — 每个 DAO 持有一个 Session         (类似 Mapper)
├── service/          业务逻辑 — 调用 DAO 完成业务编排            (类似 ServiceImpl)
└── controller/       路由 — 通过 Depends 注入 Service             (类似 @RestController)
```

调用链完全对应 Spring Boot 的 Controller → Service → Mapper → DB：

```
HTTP Request
   │
   ▼
@Controller / @RestController  ──►  FastAPI router (APIRouter)
   │
   ▼
@Autowired Service  ────────────►  Depends(get_service)
   │
   ▼
@Autowired Mapper ──────────────►  DAO.__init__(db)
   │
   ▼
SqlSession / BaseMapper ────────►  SessionLocal + self.db.execute()
```

## 2. 各层职责一览

| 层 | 文件 | 关键装饰器 / 类 | 职责 |
| --- | --- | --- | --- |
| 入口 | `app/main.py` | `@app.exception_handler`、`@app.on_event`、`@app.get` | 应用装配、异常拦截、启动钩子 |
| 配置 | `app/config.py` | `BaseSettings` (pydantic-settings) | 读取 .env / 环境变量 |
| 数据库 | `app/database.py` | `DeclarativeBase`、`sessionmaker`、`get_db()` | 引擎、Session 工厂、依赖注入 |
| 公共 | `app/common/response.py` | `BaseModel`、`Generic[T]` | 统一响应、分页 |
| 公共 | `app/common/exception.py` | `Exception` 子类 | 业务异常 |
| Model | `app/model/*.py` | `Mapped[...]` + `mapped_column` | 数据库表结构 |
| POJO | `app/pojo/*.py` | `BaseModel` + `Field` | 请求/响应模型 + 字段约束 |
| DAO | `app/dao/*.py` | `select()`、`session.execute()` | 单表 CRUD + 条件构造 |
| Service | `app/service/*.py` | 普通类，业务校验 | 业务编排、事务边界 |
| Controller | `app/controller/*.py` | `APIRouter`、`Query`、`Path`、`Body` | 路由、参数解析、依赖注入 |

## 3. 关键设计决策

### 3.1 依赖注入 vs Java Spring

| Java Spring | FastAPI | 说明 |
| --- | --- | --- |
| `@Autowired` 字段注入 | `Depends(get_service)` | FastAPI 在函数签名里显式声明依赖，更直观 |
| IoC 容器管理 Bean 生命周期 | 函数调用栈管理 | 每次请求独立 Session，请求结束自动 commit/close |
| `@Service` / `@Component` | 普通类即可 | 无需注解，Python 无需 IoC 容器 |

> FastAPI 的 `Depends` 是**函数级**的 DI：每个请求进来时调用 `get_service`，函数返回什么就注入什么。**这比 Spring 更轻量**，但缺点是没有 IoC 容器管理单例（如 Redis 连接池）—— 这种场景需要自己封装 `lru_cache` 单例（参见 `app/config.py`）。

### 3.2 ORM 选择

- **SQLAlchemy 2.0**（用 `Mapped[]` 类型注解）—— 对应 Java 的 JPA/Hibernate 思路
- **不引入 `sqlmodel` 等第三方封装** —— 保持"明面就是 SQLAlchemy"，便于日后排查
- 默认 SQLite，启动零成本；要切 MySQL 改 `.env` 即可

### 3.3 请求/响应模型分离

| 层 | 用途 | 对应 Java |
| --- | --- | --- |
| `StudentCreate` | 入参校验 | `@RequestBody` 入参 Form/DTO |
| `StudentUpdate` | 局部更新，所有字段可选 | `@RequestBody` UpdateForm |
| `StudentVO` | 返回给前端的视图 | VO / Resp |
| `StudentQuery` | 分页查询条件 | `@RequestParam` 或 Query 类 |

> **Java 经验提示**：和 Spring Boot 一样，**入参 DTO 和出参 VO 不要混用**。混用会导致"入参要求 id、name、create_time 一堆字段"。

### 3.4 统一响应

```json
{
  "code": 200,
  "message": "success",
  "data": { ... }
}
```

对应 Java 的 `R<T>` / `Result<T>`：
- `code`: 业务码（200 成功，其他失败）
- `message`: 提示信息
- `data`: 业务数据

### 3.5 异常处理

**核心思想：抛异常 → 全局捕获 → 统一格式返回**。这与 Spring Boot 的 `@ControllerAdvice` + `@ExceptionHandler` 思路完全一致。

```python
# 业务层抛异常
raise BizException(code=400, message="学号已存在")

# 入口处统一拦截
@app.exception_handler(BizException)
async def biz_exception_handler(request, exc): ...
```

详细对照见 `JAVA_TO_PYTHON.md`。

### 3.6 事务边界

FastAPI 没有 `@Transactional` 注解。我们让 `get_db()` 依赖**自己控制事务**：

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()   # 函数体正常执行完 → 提交
    except Exception:
        db.rollback()  # 抛异常 → 回滚
        raise
    finally:
        db.close()
```

> 这就是 Spring 的 `@Transactional` 行为：**Controller 正常返回 → commit；抛异常 → rollback**。

### 3.7 Swagger 文档

**FastAPI 的最大杀手锏**：内置 OpenAPI 3.0 文档生成，零配置。

| Spring Boot | FastAPI |
| --- | --- |
| 集成 `springdoc-openapi` + `knife4j` | 内置，无需任何依赖 |
| 写一堆注解：`@Operation`、`@ApiModel`、`@Schema` | 函数签名 + 类型注解自动生成 |
| 单独启动 Swagger 服务 | 同一个应用端口直接访问 |

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## 4. 已实现的学生 / 教师接口

详见 `API_EXAMPLES.md`，均已通过 curl 测试：

- ✅ 分页查询（带 name/编号/班级 模糊+精确过滤）
- ✅ 按 ID 查询
- ✅ 新增（含学号/工号唯一性校验）
- ✅ 局部更新（基于 Pydantic 的 `exclude_unset=True`）
- ✅ 删除
- ✅ 业务异常（`BizException`）
- ✅ 参数校验（`RequestValidationError`）
- ✅ HTTP 异常（404 / 405 等）

## 5. 后续可扩展方向

| 需求 | 推荐方案 |
| --- | --- |
| 鉴权 / 登录 | `fastapi-users` 或自己写 `OAuth2PasswordBearer` |
| 跨域 | `from fastapi.middleware.cors import CORSMiddleware` |
| 日志持久化 | `loguru` 替代标准 `logging` |
| 异步 SQL | `SQLAlchemy[asyncio]` + `asyncmy` |
| 缓存 | `redis-py` + `aioredis` |
| 接口限流 | `slowapi` |
| 定时任务 | `apscheduler` |
| 单元测试 | `pytest` + `httpx.AsyncClient` |
| 容器化 | `Dockerfile` + `docker-compose`（搭配 MySQL） |
