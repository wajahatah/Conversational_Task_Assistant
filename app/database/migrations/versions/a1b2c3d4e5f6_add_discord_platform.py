"""add discord to platform enum

Revision ID: a1b2c3d4e5f6
Revises: f92992a820d7
Create Date: 2026-04-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f92992a820d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'DISCORD'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # To roll back, recreate the enum without DISCORD (requires no DISCORD rows).
    pass
