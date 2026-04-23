"""
Test fixtures for the Conversational Task Assistant.
"""

import asyncio
import sys

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from app.database.models import Task, TaskState, UserSettings

# ── Windows: switch to SelectorEventLoop so asyncpg works with pytest-asyncio.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ── Session-scoped engine override ────────────────────────────────────────────
#
# The module-level engine in app/database/session.py is created at import time,
# before pytest-asyncio sets up its session event loop. Asyncpg binds futures to
# the loop that was running at connection time, so reusing those connections in a
# different loop raises "Future attached to a different loop".
#
# Fix: for the test session, swap in a NullPool engine (no pooling, no pre-ping,
# no loop-affinity problems). Each acquire gets a fresh connection.

@pytest_asyncio.fixture(scope="session", autouse=True)
async def _test_db_engine():
    """Replace the shared engine with a NullPool engine for the test session."""
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    import app.database.session as db_module
    from app.config import settings

    test_engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        echo=False,
    )
    test_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    db_module.engine = test_engine
    db_module.AsyncSessionLocal = test_factory

    yield

    await test_engine.dispose()


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_now():
    """Returns a fixed point in time for testing."""
    return datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def base_task(mock_now):
    """Creates a basic task spanning from 10:00 to 14:00 relative to mock_now."""
    return Task(
        id="test-task-1",
        title="Test Task",
        start_time=mock_now - timedelta(hours=2),  # 10:00
        deadline=mock_now + timedelta(hours=2),    # 14:00
        state=TaskState.ACTIVE,
        extensions_count=0,
        ignored_prompts=0,
        no_more_action=False,
    )


@pytest.fixture
def default_settings():
    """Provides default user settings for rule evaluation."""
    return UserSettings(
        max_extensions=2,
        max_ignored_prompts=3,
        nudge_cooldown_minutes=30,
    )
