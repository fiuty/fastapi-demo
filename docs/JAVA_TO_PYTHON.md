# Java → Python 速查表 (Spring Boot → FastAPI)

> 假设你已经熟悉 Spring Boot + MyBatis-Plus，这张表帮你**几乎零成本**切换到 FastAPI。

## 1. 启动入口

### Java Spring Boot
```java
// DemoApplication.java
@SpringBootApplication
public class DemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

### Python FastAPI
```python
# app/main.py
from fastapi import FastAPI

app = FastAPI(title="FastAPI Demo", version="1.0.0")
# 启动: uvicorn app.main:app --reload
```

## 2. 分层架构对照

```
┌─────────────────────────┬────────────────────────────┐
│ Spring Boot             │ FastAPI                     │
├─────────────────────────┼────────────────────────────┤
│ @RestController         │ APIRouter                   │
│ @Service + Impl         │ 普通类                       │
│ Mapper / Repository     │ DAO (普通类)                  │
│ Entity / DO             │ SQLAlchemy Model             │
│ DTO / Form / VO         │ Pydantic BaseModel           │
│ application.yml         │ .env + pydantic-settings     │
│ DataSource + SqlSession │ engine + SessionLocal        │
│ @Transactional          │ get_db() 依赖 (commit/rollback) │
│ @ControllerAdvice       │ @app.exception_handler(...)  │
│ springdoc-openapi        │ 内置 OpenAPI (零配置)          │
└─────────────────────────┴────────────────────────────┘
```

## 3. Controller 对照

### 新增学生

**Java**
```java
@RestController
@RequestMapping("/api/students")
@RequiredArgsConstructor
public class StudentController {

    private final IStudentService studentService;

    @PostMapping
    public R<StudentVO> create(@Valid @RequestBody StudentCreateForm form) {
        Student student = studentService.createStudent(form);
        return R.ok(StudentVO.from(student));
    }

    @GetMapping("/{id}")
    public R<StudentVO> getById(@PathVariable Long id) {
        return R.ok(StudentVO.from(studentService.getById(id)));
    }

    @GetMapping
    public R<IPage<StudentVO>> page(StudentQuery query) {
        return R.ok(studentService.page(query));
    }
}
```

**Python**
```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.common.response import Response
from app.database import get_db
from app.pojo.student import StudentCreate, StudentVO
from app.service.student_service import StudentService

router = APIRouter(prefix="/api/students", tags=["学生管理"])

def get_service(db: Session = Depends(get_db)) -> StudentService:
    return StudentService(db)

@router.post("", response_model=Response[StudentVO], status_code=201)
def create(form: StudentCreate, service: StudentService = Depends(get_service)):
    return Response[StudentVO].ok(StudentVO.model_validate(service.create_student(form)))

@router.get("/{student_id}", response_model=Response[StudentVO])
def get_by_id(student_id: int, service: StudentService = Depends(get_service)):
    return Response[StudentVO].ok(StudentVO.model_validate(service.get_student(student_id)))

@router.get("", response_model=Response[PageData[StudentVO]])
def page(...): ...
```

**关键差异**：
- 路径变量用 `{student_id}` 不用 `{id}`（避免与 `id()` 内建函数冲突）
- `int` 直接做类型注解 → FastAPI 自动转 `Integer` 参数 + 校验
- 业务异常用 `Depends` 注入，**没有** `@Autowired`
- `response_model` 自动序列化 + 自动生成 Swagger

## 4. Service / DAO 对照

### Java Service
```java
@Service
@RequiredArgsConstructor
public class StudentServiceImpl extends ServiceImpl<StudentMapper, Student>
        implements IStudentService {

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Student createStudent(StudentCreateForm form) {
        if (studentMapper.selectByStudentNo(form.getStudentNo()) != null) {
            throw new BizException(400, "学号已存在");
        }
        Student student = new Student();
        BeanUtils.copyProperties(form, student);
        studentMapper.insert(student);
        return student;
    }
}
```

### Python Service
```python
class StudentService:
    def __init__(self, db: Session):
        self.db = db
        self.dao = StudentDAO(db)

    def create_student(self, form: StudentCreate) -> Student:
        if self.dao.get_by_student_no(form.student_no):
            raise BizException(code=400, message="学号已存在")
        student = Student(**form.model_dump())
        return self.dao.insert(student)
```

**关键差异**：
- Python **没有 `@Transactional`** 注解；事务由 `get_db()` 依赖统一管理
- `form.model_dump()` 取代 `BeanUtils.copyProperties(form, student)`
- `Student(**form.model_dump())` 把字典展开为构造参数
- 不需要写 interface / impl 分离（Python 是鸭子类型）

### Java DAO (MyBatis-Plus)
```java
public interface StudentMapper extends BaseMapper<Student> {
    @Select("SELECT * FROM t_student WHERE student_no = #{studentNo}")
    Student selectByStudentNo(@Param("studentNo") String studentNo);
}
```

### Python DAO
```python
class StudentDAO:
    def __init__(self, db: Session):
        self.db = db

    def get_by_student_no(self, student_no: str) -> Optional[Student]:
        stmt = select(Student).where(Student.student_no == student_no)
        return self.db.execute(stmt).scalar_one_or_none()
```

**关键差异**：
- `BaseMapper` 提供的 17 个方法需要自己写，但语义更清晰
- 不需要 XML 映射，函数本身就是 SQL 的封装
- 复杂条件用 `select(Student).where(条件)` 链式构造
- 分页用 `.offset((page-1)*size).limit(size)`

## 5. Entity / Model 对照

### Java Entity (MyBatis-Plus)
```java
@Data
@TableName("t_student")
public class Student {
    @TableId(type = IdType.AUTO)
    private Long id;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createTime;

    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updateTime;

    private String name;
    private String studentNo;  // 驼峰转下划线
}
```

### Python Model (SQLAlchemy 2.0)
```python
class Student(Base):
    __tablename__ = "t_student"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    student_no: Mapped[str] = mapped_column(String(20), unique=True)  # 直接蛇形命名
    create_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

**关键差异**：
- `Mapped[类型]` = "这个属性的类型是 X"
- `mapped_column(...)` = "数据库里这一列的元数据"
- 没有 `@Data` 注解，直接读写属性即可
- 没有 MetaObjectHandler，`server_default` / `onupdate` 由 SQLAlchemy / 数据库自动处理

## 6. DTO / Pydantic Schema 对照

### Java Form (入参)
```java
@Data
public class StudentCreateForm {
    @NotBlank
    @Size(max = 50)
    private String name;

    @NotBlank
    @Size(max = 20)
    private String studentNo;

    @NotNull
    @Min(1) @Max(200)
    private Integer age;

    @NotBlank
    private String clazz;
}
```

### Python Schema (入参)
```python
class StudentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="姓名")
    student_no: str = Field(..., min_length=1, max_length=20, description="学号")
    age: int = Field(..., ge=1, le=200, description="年龄")
    clazz: str = Field(..., min_length=1, max_length=50, description="班级")
```

**关键差异**：
- `Field(...)` 中的 `...` 表示必填
- `ge`/`le` 取代 `@Min`/`@Max`；`min_length`/`max_length` 取代 `@Size`
- `description` 自动进入 Swagger 文档
- 没有 Lombok，`@Data` 用 `BaseModel` 替代

### Java VO (出参)
```java
@Data
public class StudentVO {
    private Long id;
    private String name;
    private LocalDateTime createTime;
    public static StudentVO from(Student s) {
        StudentVO vo = new StudentVO();
        BeanUtils.copyProperties(s, vo);
        return vo;
    }
}
```

### Python VO (出参)
```python
class StudentVO(StudentBase):
    id: int
    create_time: datetime
    update_time: datetime

    model_config = ConfigDict(from_attributes=True)  # 允许从 ORM 对象创建

# 用法: StudentVO.model_validate(student)
```

**关键差异**：
- `model_config = ConfigDict(from_attributes=True)` 取代 `BeanUtils.copyProperties`
- `StudentVO.model_validate(student)` 直接从 ORM 对象生成 VO

## 7. 全局异常处理对照

### Java @ControllerAdvice
```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BizException.class)
    public R<?> handleBiz(BizException e) {
        return R.fail(e.getCode(), e.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public R<?> handleValid(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + ": " + f.getDefaultMessage())
                .findFirst().orElse("参数校验失败");
        return R.fail(400, msg);
    }

    @ExceptionHandler(Exception.class)
    public R<?> handleAll(Exception e) {
        log.error("服务器异常", e);
        return R.fail(500, "服务器内部错误");
    }
}
```

### Python 全局异常处理
```python
@app.exception_handler(BizException)
async def biz_exception_handler(request: Request, exc: BizException) -> JSONResponse:
    return JSONResponse(
        status_code=200,  # 业务异常仍返回 200
        content=Response.fail(code=exc.code, message=exc.message).model_dump()
    )

@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", []))
    return JSONResponse(
        status_code=422,
        content=Response.fail(code=400, message=f"{loc}: {first.get('msg', '')}",
                              data=exc.errors()).model_dump()
    )

@app.exception_handler(Exception)
async def unhandled_handler(request, exc):
    logger.exception("未处理异常")
    return JSONResponse(
        status_code=500,
        content=Response.fail(code=500, message=f"服务器内部错误: {exc}").model_dump()
    )
```

**关键差异**：
- `@app.exception_handler` 装饰器 = `@RestControllerAdvice` + `@ExceptionHandler` 合并
- 异常处理函数签名固定为 `(request, exc)`，不能像 Java 那样精确到参数
- 业务异常用 HTTP 200 + body 里的 `code` 区分；HTTP 状态码留给真正的 HTTP 错误
- 没有 AOP，**所有拦截逻辑都靠异常抛出**（无侵入的横切关注点）

## 8. 配置文件对照

### Java application.yml
```yaml
spring:
  datasource:
    url: jdbc:mysql://127.0.0.1:3306/demo
    username: root
    password: 123456
  application:
    name: demo

mybatis-plus:
  configuration:
    log-impl: org.apache.ibatis.logging.stdout.StdOutImpl
```

### Python .env
```bash
APP_NAME=FastAPI Demo
DEBUG=True
DATABASE_URL=sqlite:///./app.db
# 切 MySQL:
# DATABASE_URL=mysql+pymysql://root:123456@127.0.0.1:3306/demo?charset=utf8mb4
```

```python
# app/config.py
class Settings(BaseSettings):
    APP_NAME: str = "FastAPI Demo"
    DEBUG: bool = True
    DATABASE_URL: str = "sqlite:///./app.db"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

settings = Settings()
```

**关键差异**：
- 用 `pydantic-settings` 读 .env，比 Java 的 `application.yml` 简洁
- 没有 `@Value` 注解，直接 `settings.DATABASE_URL`
- 没有 `@ConfigurationProperties`（Python 一切皆对象）

## 9. 启动命令对照

| 场景 | Java Spring Boot | Python FastAPI |
| --- | --- | --- |
| 启动 | `mvn spring-boot:run` | `uvicorn app.main:app --reload` |
| 指定端口 | `server.port=8080` (yml) | `uvicorn ... --port 8080` |
| 指定环境 | `--spring.profiles.active=dev` | `ENV_FILE=.env.dev` |
| 打 jar/war | `mvn package` | 写 Dockerfile |
| 看依赖 | `mvn dependency:tree` | `pip list` |
| 改完热重启 | DevTools | `--reload` (uvicorn 内置) |

## 10. 思维转变关键点

| 主题 | Java 思维 | Python 思维 |
| --- | --- | --- |
| 依赖管理 | Maven/Gradle 中央仓库 | pip + PyPI + 虚拟环境 (venv) |
| 类型 | 静态类型，必须显式声明 | 动态类型 + 可选类型注解 (Pydantic) |
| 线程模型 | Tomcat 线程池，阻塞 IO | uvicorn 异步事件循环 (asyncio) |
| 注解 | `@Xxx` 改变行为 | 装饰器 / 函数式 / 上下文管理器 |
| 反射 | 运行时改字节码 / 代理 | 鸭子类型，monkey-patch |
| 模块 | import + 严格包路径 | `__init__.py` 包 + 显式 `__all__` |
| 接口 | `interface` + `implements` | 鸭子类型，无显式接口 |
| 测试 | JUnit + Mockito | pytest + httpx |
| 文档 | 写 javadoc | 写 docstring（自动进 Swagger） |

## 11. 推荐学习顺序

1. **先会跑** —— `uvicorn app.main:app --reload`，打开 `/docs` 点几下
2. **读一遍 pojo** —— 理解 Pydantic 的 `Field`、`BaseModel`、`model_config`
3. **读一遍 model** —— 理解 SQLAlchemy 2.0 的 `Mapped[]`、`mapped_column`
4. **读 controller → service → dao** —— 体会"层层依赖注入"
5. **读 main.py 的异常处理** —— 体会"装饰器拦截"
6. **改一行试试** —— 把 `clazz` 字段加个 `unique=True`，重启看看效果
7. **学异步** —— 把 DAO 方法改成 `async def` + `async with AsyncSession`
8. **学部署** —— 写 `Dockerfile`，`docker run` 起来

## 12. 常见坑 (Java 程序员易踩)

| 坑 | 解释 |
| --- | --- |
| 忘记 `db.commit()` | SQLAlchemy 默认 `autocommit=False`，本项目用 `get_db()` 自动 commit |
| 跨函数共享 Session | 容易出现"对象已 detached"错误；建议一个请求一个 Session（用 `Depends`） |
| `model_dump()` vs `dict()` | Pydantic v2 用 `model_dump()`，v1 是 `.dict()`；别用错 |
| 异步函数不能调同步阻塞 | `async def` 里不能 `time.sleep()`，要 `await asyncio.sleep()` |
| 路径参数 `{id}` | `id` 是 Python 内建名，建议用 `{student_id}` |
| 中文乱码 | 数据库连接 URL 加 `?charset=utf8mb4`；终端 `chcp 65001` |
| 浮点精度 | 金额用 `Numeric(10,2)` 或 Python 的 `Decimal` |
