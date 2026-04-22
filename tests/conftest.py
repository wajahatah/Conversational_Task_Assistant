"""
Test fixtures for the Conversational Task Assistant.
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.database.models import Task, TaskState, UserSettings


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
