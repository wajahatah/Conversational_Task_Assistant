"""
User Registration Service.

Handles multi-step onboarding for new users.
State is tracked via User.registration_state in the database.

Registration steps:
  awaiting_name → awaiting_email → awaiting_phone → awaiting_timezone
  → awaiting_summary_time → complete (registration_state = None)
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone

import pytz
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Platform, User, UserSettings
from app.platform.base import NormalizedMessage, OutboundMessage
from app.platform import get_platform_adapter

logger = structlog.get_logger(__name__)

# ── Registration step sequence ─────────────────────────────────────────────
STEPS = [
    "awaiting_name",
    "awaiting_email",
    "awaiting_phone",
    "awaiting_timezone",
    "awaiting_summary_time",
]

# Sent once on first contact — explains what will be collected
WELCOME_INTRO = (
    "👋 *Welcome to your Task Assistant\\!*\n\n"
    "I help you manage your daily tasks by:\n"
    "• ⏰ Reminding you when tasks start\n"
    "• 📊 Checking in as deadlines approach\n"
    "• 🆘 Assisting you when you're stuck\n"
    "• 📋 Sending a daily briefing every morning\n\n"
    "To get you set up, I'll need a few details:\n\n"
    "1\\. 📝 *Name*\n"
    "2\\. 📧 *Email address*\n"
    "3\\. 📱 *Phone number* \\(optional\\)\n"
    "4\\. 🌍 *Timezone* \\(e\\.g\\. Asia/Karachi\\)\n"
    "5\\. ⏰ *Daily summary time* \\(e\\.g\\. 08:00\\)\n\n"
    "This will only take a minute\\. Let's go\\! 👇"
)

STEP_PROMPTS = {
    "awaiting_name": "What's your *full name*?",
    "awaiting_email": "Thanks, {name}\\! What's your *email address*?",
    "awaiting_phone": (
        "What's your *phone number*?\n\n"
        "\\(Type `skip` if you'd prefer not to share it\\)"
    ),
    "awaiting_timezone": (
        "What's your *timezone*?\n\n"
        "Examples:\n"
        "`Asia/Karachi` \\| `America/New_York` \\| `Europe/London`\n\n"
        "Or a UTC offset like `UTC+5` or `UTC-4`"
    ),
    "awaiting_summary_time": (
        "Almost done\\! At what time should I send your *daily morning summary*?\n\n"
        "Format: `HH:MM` in 24\\-hour time \\(e\\.g\\. `08:00` or `07:30`\\)\n"
        "Type `default` to use 08:00"
    ),
}

WELCOME_COMPLETE = (
    "✅ *You're all set, {name}\\!*\n\n"
    "You can now add tasks in plain language, like:\n"
    "_\"Finish the project report from 2pm to 6pm\"_\n\n"
    "I'll remind you at the right times and help you stay on track\\.\n\n"
    "Commands you can use:\n"
    "• /tasks — View today's tasks\n"
    "• /summary — Get your daily summary now\n"
    "• /help — Show all commands\n\n"
    "Let's have a productive day\\! 🚀"
)


async def get_user_by_platform_id(
    db: AsyncSession, platform_id: str
) -> User | None:
    result = await db.execute(
        select(User).where(User.platform_id == platform_id)
    )
    return result.scalar_one_or_none()


async def is_registered(db: AsyncSession, platform_id: str) -> bool:
    user = await get_user_by_platform_id(db, platform_id)
    return user is not None and user.registration_state is None


async def start_registration(
    db: AsyncSession,
    msg: NormalizedMessage,
) -> None:
    """
    Create a new pending user record and send the first onboarding prompt.
    Called when an unknown platform_id sends its first message.
    """
    # Create user with first registration step
    user = User(
        platform=Platform(msg.platform),
        platform_id=msg.platform_id,
        registration_state=STEPS[0],
    )
    db.add(user)
    await db.flush()  # get user.id without committing

    # Create default settings
    settings_record = UserSettings(user_id=user.id)
    db.add(settings_record)
    await db.commit()

    adapter = get_platform_adapter()
    # First send the overview of what will be collected
    try:
        logger.info("Sending welcome intro", platform_id=msg.platform_id)
        await adapter.send_message(OutboundMessage(
            platform_id=msg.platform_id,
            text=WELCOME_INTRO,
        ))
        # Then immediately ask the first question
        logger.info("Sending first prompt", platform_id=msg.platform_id)
        await adapter.send_message(OutboundMessage(
            platform_id=msg.platform_id,
            text=STEP_PROMPTS["awaiting_name"],
        ))
        logger.info("Registration started", platform_id=msg.platform_id)
    except Exception as exc:
        logger.error("Failed to send registration messages", platform_id=msg.platform_id, error=str(exc))


async def restart_registration(
    db: AsyncSession,
    user: User,
    msg: NormalizedMessage,
) -> None:
    """
    Reset an in-progress (or stuck) registration back to step 1 and
    re-send the welcome intro + first question.

    Called when a mid-registration user sends /start again.
    """
    user.registration_state = STEPS[0]
    await db.commit()

    adapter = get_platform_adapter()
    try:
        await adapter.send_message(OutboundMessage(
            platform_id=msg.platform_id,
            text=WELCOME_INTRO,
        ))
        await adapter.send_message(OutboundMessage(
            platform_id=msg.platform_id,
            text=STEP_PROMPTS["awaiting_name"],
        ))
    except Exception as exc:
        logger.error(
            "Failed to send restart-registration messages",
            platform_id=msg.platform_id,
            error=str(exc),
        )


async def process_registration_step(
    db: AsyncSession,
    user: User,
    msg: NormalizedMessage,
) -> bool:
    """
    Process the user's reply for the current registration step.

    Returns:
        True if registration is now complete, False if more steps remain.
    """
    step = user.registration_state
    text = (msg.text or "").strip()
    adapter = get_platform_adapter()

    if step == "awaiting_name":
        user.name = text
        next_step = "awaiting_email"
        prompt = STEP_PROMPTS["awaiting_email"].format(name=_escape(text))

    elif step == "awaiting_email":
        if not _is_valid_email(text):
            await adapter.send_message(OutboundMessage(
                platform_id=msg.platform_id,
                text="⚠️ That doesn't look like a valid email\\. Please try again:",
            ))
            return False
        user.email = text
        next_step = "awaiting_phone"
        prompt = STEP_PROMPTS["awaiting_phone"]

    elif step == "awaiting_phone":
        user.phone = None if text.lower() == "skip" else text
        next_step = "awaiting_timezone"
        prompt = STEP_PROMPTS["awaiting_timezone"]

    elif step == "awaiting_timezone":
        tz = _parse_timezone(text)
        if not tz:
            await adapter.send_message(OutboundMessage(
                platform_id=msg.platform_id,
                text=(
                    "⚠️ Timezone not recognised\\. "
                    "Try something like `Asia/Karachi` or `UTC+5`:"
                ),
            ))
            return False
        user.timezone = tz
        next_step = "awaiting_summary_time"
        prompt = STEP_PROMPTS["awaiting_summary_time"]

    elif step == "awaiting_summary_time":
        summary_time = _parse_time(text)
        user.settings.summary_trigger_time = summary_time
        user.registered_at = datetime.now(tz=timezone.utc)
        user.registration_state = None  # ← Registration complete
        await db.commit()

        await adapter.send_message(OutboundMessage(
            platform_id=msg.platform_id,
            text=WELCOME_COMPLETE.format(name=_escape(user.name or "there")),
        ))
        logger.info("Registration complete", platform_id=msg.platform_id, name=user.name)
        return True

    else:
        logger.warning("Unknown registration state", state=step)
        return False

    # Advance to next step
    user.registration_state = next_step
    await db.commit()
    await adapter.send_message(OutboundMessage(
        platform_id=msg.platform_id,
        text=prompt,
    ))
    return False


# ── Helpers ────────────────────────────────────────────────────────────────

def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def _parse_timezone(raw: str) -> str | None:
    """Accept IANA strings and simple UTC offsets like UTC+5, UTC-4:30."""
    raw = raw.strip()
    if raw in pytz.all_timezones_set:
        return raw
    # Try UTC offset formats
    match = re.match(r"^UTC([+-]\d{1,2})(?::(\d{2}))?$", raw, re.IGNORECASE)
    if match:
        sign = match.group(1)
        tz_name = f"Etc/GMT{'-' if sign.startswith('+') else '+'}{abs(int(sign))}"
        if tz_name in pytz.all_timezones_set:
            return tz_name
    return None


def _parse_time(raw: str) -> time:
    """Parse HH:MM string, default to 08:00 if invalid or 'default'."""
    if raw.lower() == "default":
        return time(8, 0)
    try:
        parts = raw.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return time(8, 0)


def _escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
