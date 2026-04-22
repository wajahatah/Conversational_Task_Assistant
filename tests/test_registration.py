"""
Integration tests for the Registration flow.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.platform.base import NormalizedMessage, MessageType
from app.services.registration import process_registration_step, start_registration
from app.database.models import User, Platform, UserSettings


@pytest.fixture
def mock_db_session():
    """Mock an async SQLAlchemy session."""
    session = AsyncMock()
    # For scalar_one_or_none
    session.execute.return_value.scalar_one_or_none.return_value = None
    return session


@pytest.fixture
def mock_platform_adapter():
    """Mock the PlatformAdapter so we don't send real Telegram messages."""
    with patch("app.services.registration.get_platform_adapter") as mock_get:
        adapter = AsyncMock()
        mock_get.return_value = adapter
        yield adapter


@pytest.mark.asyncio
async def test_registration_flow_success(mock_db_session, mock_platform_adapter):
    """Test the complete 5-step registration flow successfully."""
    
    # 1. Start registration
    msg = NormalizedMessage(
        platform="telegram",
        platform_id="12345",
        message_type=MessageType.TEXT,
        text="Hello",
    )
    
    await start_registration(mock_db_session, msg)
    
    # Verify user was added with step 1
    mock_db_session.add.assert_called()
    user_arg = mock_db_session.add.call_args_list[0][0][0]
    assert isinstance(user_arg, User)
    assert user_arg.registration_state == "awaiting_name"
    
    # Verify platform adapter sent the welcome intro and first prompt
    assert mock_platform_adapter.send_message.call_count == 2
    
    # 2. Simulate User state in DB for the remaining steps
    test_user = User(
        id="test-uuid",
        platform=Platform.telegram,
        platform_id="12345",
        registration_state="awaiting_name",
        settings=UserSettings()
    )
    
    # Step 1: Name
    msg.text = "Alice Smith"
    is_complete = await process_registration_step(mock_db_session, test_user, msg)
    assert is_complete is False
    assert test_user.name == "Alice Smith"
    assert test_user.registration_state == "awaiting_email"
    
    # Step 2: Email
    msg.text = "alice@example.com"
    is_complete = await process_registration_step(mock_db_session, test_user, msg)
    assert is_complete is False
    assert test_user.email == "alice@example.com"
    assert test_user.registration_state == "awaiting_phone"
    
    # Step 3: Phone
    msg.text = "skip"
    is_complete = await process_registration_step(mock_db_session, test_user, msg)
    assert is_complete is False
    assert test_user.phone is None
    assert test_user.registration_state == "awaiting_timezone"
    
    # Step 4: Timezone
    msg.text = "UTC+5"
    is_complete = await process_registration_step(mock_db_session, test_user, msg)
    assert is_complete is False
    assert test_user.timezone == "Etc/GMT-5"
    assert test_user.registration_state == "awaiting_summary_time"
    
    # Step 5: Summary Time
    msg.text = "08:30"
    is_complete = await process_registration_step(mock_db_session, test_user, msg)
    assert is_complete is True
    assert test_user.settings.summary_trigger_time.hour == 8
    assert test_user.settings.summary_trigger_time.minute == 30
    assert test_user.registration_state is None  # Complete!
    assert test_user.registered_at is not None
