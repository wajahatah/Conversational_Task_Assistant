from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# ─── Engine ───────────────────────────────────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,          # logs all SQL when DEBUG=true
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,           # verify connections before use
)

# ─── Session Factory ──────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,       # keeps objects usable after commit
    autoflush=False,
    autocommit=False,
)


# ─── Dependency (FastAPI) ─────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ─── Standalone Session (Celery / background tasks) ──────────────────────────

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside FastAPI (Celery jobs, scripts)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
