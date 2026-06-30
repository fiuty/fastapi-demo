"""
应用配置
对应 Spring Boot 中的 application.yml / application.properties
通过 pydantic-settings 读取 .env 文件或环境变量
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    APP_NAME: str = "FastAPI Demo"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # 数据库连接 URL
    DATABASE_URL: str = "sqlite:///./app.db"

    # LLM 默认配置
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL_NAME: str = "deepseek-v4-flash"

    # Redis 配置 (AgentState 持久化)
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    # AgentState 会话过期时间 (秒), 1天 = 86400
    STATE_KEY_TTL: int = 86400

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """单例读取配置 (类似 Spring 的 @ConfigurationProperties)"""
    return Settings()


settings = get_settings()
