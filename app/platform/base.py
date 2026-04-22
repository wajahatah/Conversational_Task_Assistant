"""
Platform Adapter — Abstract base.

ALL platform-specific code lives behind this interface.
The rest of the system only works with NormalizedMessage and OutboundMessage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class MessageType(str, Enum):
    TEXT = "text"
    POLL_RESPONSE = "poll_response"
    COMMAND = "command"


@dataclass
class NormalizedMessage:
    """
    Unified message format — fed to the API gateway regardless of platform.
    Platform-specific details are stripped here.
    """
    platform: str                       # "telegram" | "slack"
    platform_id: str                    # Unique user ID on the platform
    message_type: MessageType
    text: str | None = None             # For TEXT and COMMAND types
    command: str | None = None          # e.g. "/start", "/tasks"
    poll_response: str | None = None    # e.g. "DONE", "IN_PROGRESS"
    poll_message_id: str | None = None  # The message ID of the original poll
    raw: dict = field(default_factory=dict)  # Original payload (for debugging)


@dataclass
class PollOption:
    """A single option in an outbound poll."""
    text: str
    callback_data: str   # Machine-readable value (e.g. "DONE")


@dataclass
class OutboundMessage:
    """
    A message the system wants to send to a user.
    The platform adapter converts this to platform-native format.
    """
    platform_id: str
    text: str
    poll_options: list[PollOption] | None = None  # If set, send as a poll/buttons
    reply_to_message_id: str | None = None


class BasePlatformAdapter(ABC):
    """
    Abstract interface for all platform adapters.

    Inbound:  parse raw webhook payload → NormalizedMessage
    Outbound: send OutboundMessage → platform-native API call
    """

    @property
    @abstractmethod
    def platform_name(self) -> str: ...

    @abstractmethod
    async def parse_incoming(self, payload: dict) -> NormalizedMessage | None:
        """
        Parse a raw incoming webhook payload into a NormalizedMessage.
        Returns None if the payload should be ignored (e.g. bot's own message).
        """
        ...

    @abstractmethod
    async def send_message(self, message: OutboundMessage) -> str | None:
        """
        Send a text message (plain or with inline buttons/poll).
        Returns the platform message_id of the sent message, if available.
        """
        ...

    @abstractmethod
    async def send_poll(
        self,
        platform_id: str,
        question: str,
        options: list[PollOption],
    ) -> str | None:
        """
        Send a structured poll/button menu to the user.
        Returns the platform message_id.
        """
        ...
