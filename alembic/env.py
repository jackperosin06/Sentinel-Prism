"""Alembic migration environment.

Migrations use a **sync** URL (``postgresql+psycopg://``) via ``ALEMBIC_SYNC_URL``.
The FastAPI app uses ``DATABASE_URL`` with ``postgresql+asyncpg://`` (Story 1.3+).
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import create_engine, pool

# Project root = parent of alembic/
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sentinel_prism.db.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_sync_url() -> str:
    url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not url:
        msg = (
            "ALEMBIC_SYNC_URL is not set. Use a sync DSN, e.g. "
            "postgresql+psycopg://USER:PASS@localhost:5432/sentinel_prism "
            "(see .env.example)."
        )
        raise RuntimeError(msg)
    return url


def run_migrations_offline() -> None:
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_sync_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
