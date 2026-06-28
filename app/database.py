"""
数据库连接与会话管理
对应 Spring Boot 中的 DataSource + SqlSession
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from app.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类 (类似 MyBatis-Plus 的 BaseModel)"""
    pass


# 创建数据库引擎
# check_same_thread=False 仅 SQLite 需要, 允许在多线程下使用
# pool_pre_ping / pool_recycle 防止 MySQL 空闲连接被服务端断开
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    pool_pre_ping=True,
    pool_recycle=3600,
)

# 会话工厂 (类似 SqlSessionFactory)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Session:
    """
    FastAPI 依赖注入用的数据库会话生成器
    每个请求自动开启/提交/回滚事务, 类似 Spring 的 @Transactional

    用法:
        @router.get("/")
        def list_students(db: Session = Depends(get_db)):
            ...

    行为:
        - Controller 正常返回 → 自动 commit
        - Controller 抛异常    → 自动 rollback
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """初始化数据库表 (项目启动时调用)"""
    # 导入所有模型, 让 Base.metadata 能感知到它们
    from app.model import student, teacher, conversation, message  # noqa: F401
    Base.metadata.create_all(bind=engine)
