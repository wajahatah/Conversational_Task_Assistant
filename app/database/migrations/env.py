"""
Alembic migrations environment.
Uses a synchronous psycopg2 connection (Alembic requirement)
while keeping the async engine for the app itself.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Make sure the project root is on sys.path ──────────────────────────────
# env.py lives at: app/database/migrations/env.py
# We need root (3 levels up) in path for imports.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# ── Load .env so Settings can read env vars ────────────────────────────────
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

# ── Import app settings and all ORM models ─────────────────────────────────
from app.config import settings  # noqa: E402
from app.database.models import Base  # noqa: E402 — registers all models

# Alembic Config object
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point Alembic to our models' metadata for autogenerate support
target_metadata = Base.metadata

# Override sqlalchemy.url from our Settings (sync driver for Alembic)
config.set_main_option("sqlalchemy.url", settings.sync_database_url)


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (offline mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection (online mode)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
