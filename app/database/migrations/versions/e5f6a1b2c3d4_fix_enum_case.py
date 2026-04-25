"""add URGENT_POLL and STALL (uppercase) to enums

The previous migration (d4e5f6a1b2c3) added 'urgent_poll' and 'stall' (lowercase
values), but SQLAlchemy's default `Enum(EnumClass)` mapping uses enum NAMES,
which are uppercase. The lowercase entries are therefore unreachable and inserts
fail silently. This migration adds the correct uppercase entries.

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-04-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e5f6a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE interactiontype ADD VALUE IF NOT EXISTS 'URGENT_POLL'")
    op.execute("ALTER TYPE responsetype ADD VALUE IF NOT EXISTS 'STALL'")


def downgrade() -> None:
    pass
