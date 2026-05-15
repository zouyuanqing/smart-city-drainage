"""
核心配置模块
--------------
使用 pydantic-settings 管理所有环境变量和配置项。
支持 .env 文件加载，提供类型安全和验证。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置单例"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ============ 应用基础 ============
    APP_NAME: str = "Smart City Neural Endpoints"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = Field(default=False, description="调试模式")
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")

    # ============ 数据库 ============
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://drainage_admin:Dr@inage_S3cur3_2024!@localhost:5432/smart_drainage",
        description="异步数据库连接串 (asyncpg)",
    )
    DATABASE_URL_SYNC: str = Field(
        default="postgresql://drainage_admin:Dr@inage_S3cur3_2024!@localhost:5432/smart_drainage",
        description="同步数据库连接串 (psycopg2)",
    )
    DB_POOL_SIZE: int = Field(default=20, description="数据库连接池大小")
    DB_MAX_OVERFLOW: int = Field(default=10, description="最大溢出连接数")

    # ============ InfluxDB ============
    INFLUXDB_URL: str = Field(default="http://localhost:8086")
    INFLUXDB_TOKEN: str = Field(default="scn-influx-token-2024-secure")
    INFLUXDB_ORG: str = Field(default="smart-city")
    INFLUXDB_BUCKET: str = Field(default="sensor_data")

    # ============ Redis ============
    REDIS_URL: str = Field(default="redis://:R3d1s_S3cur3_2024!@localhost:6379/0")
    REDIS_CHANNEL_ALERTS: str = "scn:alerts"
    REDIS_CHANNEL_SENSOR: str = "scn:sensor_data"
    REDIS_CHANNEL_MODEL: str = "scn:model_status"

    # ============ JWT 认证 ============
    JWT_SECRET_KEY: str = Field(default="scn-jwt-secret-key-change-in-production-2024")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440, description="Token 过期时间 (分钟), 默认24小时"
    )

    # ============ YOLO 模型 ============
    MODEL_STORAGE_PATH: str = Field(default="./models/weights")
    DEFAULT_MODEL_VERSION: str = Field(default="v1", description="默认加载的模型版本")
    MODEL_INFERENCE_DEVICE: str = Field(
        default="cuda:0", description="推理设备: 'cuda:0' 或 'cpu'"
    )
    MODEL_CONFIDENCE_THRESHOLD: float = Field(
        default=0.45, description="检测置信度阈值"
    )
    MODEL_IOU_THRESHOLD: float = Field(default=0.45, description="NMS IoU 阈值")

    # ============ 视频流 ============
    FFMPEG_PATH: str = Field(default="ffmpeg", description="FFmpeg 可执行文件路径")
    FFPROBE_PATH: str = Field(default="ffprobe", description="FFprobe 可执行文件路径")
    HLS_OUTPUT_DIR: str = Field(default="./backend/media/hls")
    SCREENSHOT_DIR: str = Field(default="./backend/media/screenshots")
    HLS_SEGMENT_TIME: int = Field(default=4, description="HLS 分片时长 (秒)")
    HLS_LIST_SIZE: int = Field(default=6, description="HLS 播放列表保留分片数")
    STREAM_HEARTBEAT_INTERVAL: int = Field(
        default=30, description="流健康检查间隔 (秒)"
    )

    # ============ CORS ============
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="允许的跨域来源 (JSON 数组)",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list) -> list[str]:
        """解析 CORS 来源列表"""
        if isinstance(v, list):
            return v
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return [origin.strip() for origin in v.split(",") if origin.strip()]

    # ============ 安全 ============
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, description="每分钟请求限制")
    MAX_UPLOAD_SIZE_MB: int = Field(default=100, description="最大上传大小 (MB)")

    @property
    def model_storage_dir(self) -> Path:
        """模型存储目录 Path 对象"""
        p = Path(self.MODEL_STORAGE_PATH)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def hls_output_dir(self) -> Path:
        """HLS 输出目录 Path 对象"""
        p = Path(self.HLS_OUTPUT_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def screenshot_dir(self) -> Path:
        """截图输出目录 Path 对象"""
        p = Path(self.SCREENSHOT_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


# 全局单例
settings = Settings()
