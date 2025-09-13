# services/api/app/core/config.py

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    统一管理项目的所有配置。
    使用 pydantic-settings，这个类会自动从环境变量或 .env 文件中读取配置。
    """
    
    # -------------------------------------------------------------------------
    # App 基础配置 (Basic App Settings)
    # -------------------------------------------------------------------------
    APP_NAME: str = "YVideo Factory"
    APP_ENV: str = "dev"
    APP_TIMEZONE: str = "Asia/Shanghai"
    LOG_LEVEL: str = "INFO"
    
    # API服务的监听主机和端口 (主要用于Uvicorn命令行，但放在这里保持一致性)
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # -------------------------------------------------------------------------
    # 安全配置 (Security Settings)
    # -------------------------------------------------------------------------
    SERVICE_API_KEY: str
    CORS_ORIGINS: str = "http://localhost:3000"

    # -------------------------------------------------------------------------
    # 数据库 (PostgreSQL)
    # -------------------------------------------------------------------------
    DATABASE_URL: str

    # -------------------------------------------------------------------------
    # 缓存与队列 (Redis / Celery)
    # -------------------------------------------------------------------------
    REDIS_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    
    # -------------------------------------------------------------------------
    # 存储 (Google Cloud Storage)
    # -------------------------------------------------------------------------
    GCS_BUCKET_NAME: str
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    
    # -------------------------------------------------------------------------
    # GCP 项目配置 (Google Cloud Project)
    # -------------------------------------------------------------------------
    GCP_PROJECT_ID: Optional[str] = None
    GCP_LOCATION: Optional[str] = "us-central1"

    # -------------------------------------------------------------------------
    # 第三方 API Keys
    # -------------------------------------------------------------------------
    GOOGLE_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    
    # Pydantic-settings 的配置类
    # model_config 用于指定从哪个文件读取环境变量（默认为 .env）
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

# 创建一个全局唯一的settings实例
# 项目中其他任何地方需要配置时，都应该从这里导入
# from services.api.app.core.config import settings
settings = Settings()