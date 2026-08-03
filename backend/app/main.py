"""
智慧城市神经末梢 — FastAPI 应用入口
====================================
市政排水智能监测与AI安防系统后端服务。

Copyright 2024 Smart City Neural Endpoints
Licensed under the Apache License, Version 2.0

启动命令:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

# 强制 stdout 使用 UTF-8 编码
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger

from app.api.routes import router as api_router
from app.core.config import settings
from app.core.database import check_database_connection, create_all_tables
from app.core.model_manager import get_model_manager
from app.core.redis_client import redis_client
from app.services.mock_data_generator import mock_generator
from app.services.stream_service import stream_service

# ============================================================
# 日志配置
# ============================================================


def setup_logging():
    log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    json_formatter = jsonlogger.JsonFormatter(
        "%(timestamp)s %(level)s %(name)s %(message)s",
        rename_fields={
            "timestamp": "timestamp",
            "level": "level",
            "name": "logger",
        },
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(json_formatter)

    file_handler = RotatingFileHandler(
        "logs/app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    for lib in ["httpx", "httpcore", "urllib3", "ultralytics", "uvicorn.access"]:
        logging.getLogger(lib).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def _handle_redis_alerts(message: dict):
    from app.services.sse_manager import sse_manager

    await sse_manager.broadcast("alerts", message)


async def _handle_redis_sensor(message: dict):
    from app.services.sse_manager import sse_manager

    await sse_manager.broadcast("sensors", message)


async def _handle_redis_model(message: dict):
    from app.services.sse_manager import sse_manager

    await sse_manager.broadcast("system", message)


# ============================================================
# 应用生命周期
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI 应用生命周期管理

    启动时:
      1. 初始化 ModelManager (加载默认模型)
      2. 启动流健康监控
      3. 启动模拟数据生成 (演示模式)
    关闭时:
      1. 停止模拟数据
      2. 停止所有视频转码
      3. 清理资源
    """
    logger.info("=" * 70)
    logger.info("🏙️  %s v%s 启动中...", settings.APP_NAME, settings.APP_VERSION)
    logger.info("=" * 70)

    setup_logging()

    # --- 启动阶段 ---

    # 1. 初始化模型管理器
    try:
        manager = get_model_manager()
        logger.info(
            "✅ ModelManager 就绪 | 活跃模型: %s", manager.active_version or "无"
        )
    except Exception as exc:
        logger.warning("⚠️  模型管理器初始化失败 (系统将以无模型模式运行): %s", exc)

    # 2. 连接数据库
    try:
        db_ok = await check_database_connection()
        if db_ok:
            await create_all_tables()
            logger.info("✅ 数据库已连接并初始化")
        else:
            logger.warning("⚠️  数据库不可用，系统将使用模拟数据")
    except Exception as exc:
        logger.warning("⚠️  数据库初始化失败: %s", exc)

    # 3. 连接 Redis
    try:
        await redis_client.connect()
        redis_client.on(settings.REDIS_CHANNEL_ALERTS, _handle_redis_alerts)
        redis_client.on(settings.REDIS_CHANNEL_SENSOR, _handle_redis_sensor)
        redis_client.on(settings.REDIS_CHANNEL_MODEL, _handle_redis_model)
        await redis_client.start_listener()
        logger.info("✅ Redis 已连接")
    except Exception as exc:
        logger.warning("⚠️  Redis 连接失败 (实时消息推送不可用): %s", exc)

    try:
        from app.services.influxdb_service import influxdb_service

        await influxdb_service.connect()
    except Exception as exc:
        logger.warning("⚠️  InfluxDB 连接失败: %s", exc)

    # 4. 启动流健康监控
    await stream_service.start_health_monitor()

    # 5. 启动模拟数据生成 (开发/演示模式)
    try:
        await mock_generator.start()
        logger.info("✅ 模拟数据生成器已启动")
    except Exception as exc:
        logger.warning("⚠️  模拟数据生成器启动失败: %s", exc)

    logger.info("🚀 服务已就绪! API 文档: http://localhost:8000/docs")
    logger.info("=" * 70)

    yield  # <-- 应用运行中

    # --- 关闭阶段 ---

    logger.info("🛑 正在优雅关闭...")

    await mock_generator.stop()
    await stream_service.stop_all()

    try:
        from app.services.influxdb_service import influxdb_service

        await influxdb_service.disconnect()
    except Exception:
        pass

    await redis_client.disconnect()

    # 关闭数据库连接池
    from app.core.database import dispose_engine

    await dispose_engine()

    logger.info("✅ 服务已安全关闭")


# ============================================================
# FastAPI 应用实例
# ============================================================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="市政排水智能监测与AI安防系统 — 数字孪生城市管理平台",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


# ============================================================
# CORS 中间件
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)


# ============================================================
# 请求计时中间件
# ============================================================


@app.middleware("http")
async def add_process_time_header(request, call_next):
    """为每个请求添加处理时间头和版权标记头"""
    import time

    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.3f}s"
    response.headers["X-Powered-By"] = "Smart City Neural Endpoints"
    return response


@app.middleware("http")
async def request_id_middleware(request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ============================================================
# 注册路由
# ============================================================

app.include_router(api_router)


# ============================================================
# 静态文件服务 (HLS 视频片)
# ============================================================

# 挂载 HLS 媒体目录
try:
    app.mount(
        "/media/hls",
        StaticFiles(directory=str(settings.hls_output_dir)),
        name="hls_media",
    )
except Exception:
    logger.warning("HLS 媒体目录不存在: %s", settings.hls_output_dir)


# ============================================================
# 根路径
# ============================================================


@app.get("/")
async def root():
    """根路径 - 系统信息"""
    manager = get_model_manager()
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "model_ready": manager.is_ready,
        "active_model": manager.active_version,
    }


@app.get("/health")
async def health():
    """Docker 健康检查端点"""
    return {"status": "healthy"}


# ============================================================
# 全局异常处理
# ============================================================


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """请求参数验证异常"""
    return JSONResponse(
        status_code=422,
        content={
            "detail": "请求参数验证失败",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理"""
    logger.error("未处理的异常: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误",
            "type": type(exc).__name__,
        },
    )


# ============================================================
# 开发服务器入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
