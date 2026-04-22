"""
Unit tests for the State Inference Engine.
"""

from unittest.mock import patch
from datetime import timedelta

from app.database.models import TaskState
from app.services.state_engine import infer_state, should_transition


@patch("app.services.state_engine._now")
def test_terminal_states(mock_now_func, base_task, default_settings, mock_now):
    """Test that COMPLETED and DROPPED tasks are never re-evaluated."""
    mock_now_func.return_value = mock_now

    base_task.state = TaskState.COMPLETED
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.COMPLETED

    base_task.state = TaskState.DROPPED
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.DROPPED


@patch("app.services.state_engine._now")
def test_stalled_max_ignored(mock_now_func, base_task, default_settings, mock_now):
    """Test STALLED state is reached when max_ignored_prompts is met."""
    mock_now_func.return_value = mock_now

    # The task has plenty of time left...
    base_task.ignored_prompts = 3
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.STALLED
    assert "Ignored 3/3" in res.reason


@patch("app.services.state_engine._now")
def test_stalled_max_extensions(mock_now_func, base_task, default_settings, mock_now):
    """Test STALLED state is reached when max_extensions is met."""
    mock_now_func.return_value = mock_now

    base_task.extensions_count = 2
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.STALLED
    assert "extensions" in res.reason


@patch("app.services.state_engine._now")
def test_at_risk_threshold_1(mock_now_func, base_task, default_settings, mock_now):
    """Test AT_RISK_1 triggers at <= 35% time remaining."""
    # Total task time = 4 hours. 35% = 1.4 hours remaining (84 mins).
    # Deadline is 14:00. Time needs to be >= 12:36. Let's set now = 12:40.
    mock_now_func.return_value = mock_now + timedelta(minutes=40)
    
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.AT_RISK
    assert res.trigger == "AT_RISK_1"
    assert res.pct_remaining <= 35.0


@patch("app.services.state_engine._now")
def test_at_risk_threshold_2(mock_now_func, base_task, default_settings, mock_now):
    """Test AT_RISK_2 triggers at <= 10% time remaining."""
    # Total task time = 4 hours. 10% = 0.4 hours remaining (24 mins).
    # Deadline is 14:00. Time needs to be >= 13:36. Let's set now = 13:40.
    mock_now_func.return_value = mock_now + timedelta(hours=1, minutes=40)
    
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.AT_RISK
    assert res.trigger == "AT_RISK_2"
    assert res.pct_remaining <= 10.0


@patch("app.services.state_engine._now")
def test_not_started(mock_now_func, base_task, default_settings, mock_now):
    """Test NOT_STARTED state when current time is before start_time."""
    # Start time is 10:00. Set now to 09:00.
    mock_now_func.return_value = mock_now - timedelta(hours=3)
    
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.NOT_STARTED


@patch("app.services.state_engine._now")
def test_active_state(mock_now_func, base_task, default_settings, mock_now):
    """Test ACTIVE state when plenty of time remains."""
    # Start time 10:00, deadline 14:00. Current time 12:00 (50% remaining).
    mock_now_func.return_value = mock_now
    
    res = infer_state(base_task, default_settings)
    assert res.state == TaskState.ACTIVE


def test_should_transition():
    """Test state transition guards."""
    assert should_transition(TaskState.NOT_STARTED, TaskState.ACTIVE) is True
    assert should_transition(TaskState.ACTIVE, TaskState.AT_RISK) is True
    assert should_transition(TaskState.ACTIVE, TaskState.ACTIVE) is False
    
    # Terminal states cannot be left
    assert should_transition(TaskState.COMPLETED, TaskState.ACTIVE) is False
    assert should_transition(TaskState.DROPPED, TaskState.ACTIVE) is False
