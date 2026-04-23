"""
Integration tests for the full Task Lifecycle.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.database.models import User, Task, TaskState
from app.services.task_service import create_task_from_nl, update_task_state
from app.llm import ParsedTask


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_llm_provider():
    with patch("app.services.task_service.get_llm_provider") as mock_get:
        llm = AsyncMock()
        mock_get.return_value = llm
        yield llm


@pytest.mark.asyncio
async def test_create_task_from_nl_success(mock_db_session, mock_llm_provider):
    """Test task creation through the LLM parser to the DB object."""
    # Setup mock LLM response
    now = datetime.now(tz=timezone.utc)
    mock_llm_provider.parse_task.return_value = ParsedTask(
        title="Test NL Task",
        start_time=now.isoformat(),
        deadline=(now + timedelta(hours=2)).isoformat(),
        description="A test description"
    )

    user = User(id="user-123", timezone="UTC")
    
    task = await create_task_from_nl(
        mock_db_session,
        user,
        "Finish this task in 2 hours"
    )
    
    assert task.title == "Test NL Task"
    assert task.description == "A test description"
    assert task.state == TaskState.NOT_STARTED
    assert task.user_id == "user-123"
    
    # DB was committed and refreshed
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_update_task_state(mock_db_session):
    """Test manual state update utility."""
    task = Task(id="task-123", state=TaskState.ACTIVE)
    
    updated_task = await update_task_state(mock_db_session, task, TaskState.COMPLETED)
    
    assert updated_task.state == TaskState.COMPLETED
    assert updated_task.updated_at is not None
    
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once()
