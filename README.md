# FastAPI Demo — 学生 / 教师 管理系统

> 一个面向 **Java 开发者** 的 FastAPI 示例项目，对标 Spring Boot + MyBatis-Plus 的分层架构。

## 特性

- 分层架构：**Controller → Service → DAO → Model**（与 Spring Boot 一致）
- **全局异常处理** + 统一响应格式 `R<T>`（对应 Java 的 `R<T>` / `Result<T>`）
- **自动 OpenAPI 文档** —— Swagger UI & ReDoc（FastAPI 自带，无需集成 swagger-springdoc）
- SQLAlchemy 2.0 + Pydantic v2 + FastAPI 最新版
- 默认 SQLite（零配置启动），可一键切到 MySQL

## 快速开始

```bash
# 1. 进入项目
cd F:\projects\python\2026\framework\fastapi

# 2. 安装依赖（已有 .venv，跳过创建）
.venv\Scripts\pip.exe install -r requirements.txt

# 3. 启动服务
.venv\Scripts\python.exe -m uvicorn app.main:app --reload

# 浏览器打开
# Swagger UI:  http://127.0.0.1:8000/docs
# ReDoc:       http://127.0.0.1:8000/redoc
# 健康检查:     http://127.0.0.1:8000/
```

## 项目结构

```
fastapi/
├── app/
│   ├── main.py                  # 入口 + 异常处理 + 路由注册
│   ├── config.py                # 配置 (对应 application.yml)
│   ├── database.py              # 数据库连接 + Session 工厂
│   ├── common/
│   │   ├── response.py          # 统一响应 R<T> + 分页
│   │   └── exception.py         # BizException 业务异常
│   ├── model/                   # ORM 模型 (对应 Java Entity)
│   │   ├── student.py
│   │   └── teacher.py
│   ├── pojo/                    # 请求/响应模型 (对应 Java DTO/VO)
│   │   ├── student.py
│   │   └── teacher.py
│   ├── dao/                     # 数据访问 (对应 Java Mapper)
│   │   ├── student_dao.py
│   │   └── teacher_dao.py
│   ├── service/                 # 业务逻辑 (对应 Java ServiceImpl)
│   │   ├── student_service.py
│   │   └── teacher_service.py
│   └── controller/              # 路由 (对应 Java @RestController)
│       ├── student_controller.py
│       └── teacher_controller.py
├── docs/                        # 学习文档
│   ├── PROJECT_SUMMARY.md
│   ├── JAVA_TO_PYTHON.md
│   └── API_EXAMPLES.md
├── requirements.txt
├── .env.example
└── README.md
```

## 接口列表

| 模块 | Method | Path | 说明 |
| --- | --- | --- | --- |
| 学生 | GET | /api/students | 分页查询（支持 name/student_no/clazz 过滤） |
| 学生 | GET | /api/students/{id} | 按 ID 查询 |
| 学生 | POST | /api/students | 新增 |
| 学生 | PUT | /api/students/{id} | 修改 |
| 学生 | DELETE | /api/students/{id} | 删除 |
| 教师 | GET/POST/PUT/DELETE | /api/teachers/... | 同上 |

## 学习路线

1. **[docs/JAVA_TO_PYTHON.md](docs/JAVA_TO_PYTHON.md)** — 概念对照表，从 Spring Boot 一对一映射到 FastAPI
2. **[docs/PROJECT_SUMMARY.md](docs/PROJECT_SUMMARY.md)** — 项目各层详细解读
3. **[docs/API_EXAMPLES.md](docs/API_EXAMPLES.md)** — curl / Python / PowerShell 调试示例
