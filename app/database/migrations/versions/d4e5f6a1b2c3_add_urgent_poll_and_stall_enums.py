"""add urgent_poll to interactiontype and stall to responsetype

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-04-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4e5f6a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE interactiontype ADD VALUE IF NOT EXISTS 'urgent_poll'")
    op.execute("ALTER TYPE responsetype ADD VALUE IF NOT EXISTS 'stall'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
