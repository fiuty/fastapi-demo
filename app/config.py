"""
应用配置
对应 Spring Boot 中的 application.yml / application.properties
通过 pydantic-settings 读取 .env 文件或环境变量
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "FastAPI Demo"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # 数据库连接 URL
    DATABASE_URL: str = "sqlite:///./app.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """单例读取配置 (类似 Spring 的 @ConfigurationProperties)"""
    return Settings()


settings = get_settings()
