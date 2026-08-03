"""
异步数据库引擎与会话管理
============================
基于 SQLAlchemy 2.0 async 风格，提供连接池、会话工厂和 FastAPI 依赖注入。
支持 PostgreSQL + TimescaleDB。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterator
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.db_models import Base

logger = logging.getLogger(__name__)

# ============================================================
# 引擎创建
# ============================================================

_engine: AsyncEngine | None = None


def _build_safe_db_url() -> str:
    from urllib.parse import urlparse, urlunparse

    raw = settings.DATABASE_URL
    parsed = urlparse(raw)
    if (
        parsed.hostname
        and parsed.hostname != "localhost"
        and parsed.hostname != "127.0.0.1"
    ):
        return raw
    safe_url = urlunparse(
        (
            parsed.scheme,
            f"{quote_plus(parsed.username or '')}:{quote_plus(parsed.password or '')}@{parsed.hostname or 'localhost'}:{parsed.port or 5432}",
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    return safe_url


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine

    safe_url = _build_safe_db_url()
    logger.info("初始化异步数据库引擎: %s", _mask_password(safe_url))
    _engine = create_async_engine(
        safe_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.DEBUG,
    )
    return _engine


# ============================================================
# 会话工厂
# ============================================================

_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """异步会话工厂（单例）"""
    global _async_session_factory
    if _async_session_factory is not None:
        return _async_session_factory

    _async_session_factory = async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _async_session_factory


# ============================================================
# FastAPI 依赖注入
# ============================================================


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖：为每个请求提供一个数据库会话。
    请求结束时自动提交或回滚。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """用于非 FastAPI Depends 上下文（如手动查询）的正确会话上下文管理器。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ============================================================
# 表创建（开发用）
# ============================================================


async def create_all_tables() -> None:
    """创建所有声明的数据库表（开发环境用，生产环境应使用 Alembic 迁移）"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("所有数据库表已创建/确认存在")


# ============================================================
# 连接测试
# ============================================================


async def check_database_connection() -> bool:
    """测试数据库连接"""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            from sqlalchemy import text

            result = await conn.execute(text("SELECT 1"))
            if result.fetchone():
                logger.info("数据库连接正常")
                return True
    except Exception as exc:
        logger.error("数据库连接失败: %s", exc)
    return False


# ============================================================
# 工具
# ============================================================


def _mask_password(url: str) -> str:
    """隐藏 URL 密码"""
    import re

    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)


async def dispose_engine() -> None:
    """关闭引擎，释放所有连接"""
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
        logger.info("数据库连接池已关闭")
