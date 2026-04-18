"""Checkpointer factories for LangGraph (Story 3.2+).

Development and CI use an in-memory saver. Production-like runs use
:class:`langgraph.checkpoint.postgres.aio.AsyncPostgresSaver` when ``DATABASE_URL`` is set (Story 4.1 — Architecture §3.5, FR35).

``AsyncPostgresSaver`` expects a **psycopg** URI (``postgresql://…``), not
``postgresql+asyncpg://``. Call :func:`postgres_uri_for_langgraph` on ``DATABASE_URL`` before connecting.

One-time setup: the first process using Postgres must call ``await saver.setup()``
so LangGraph checkpoint tables exist (managed by the checkpointer package, not
Alembic). The FastAPI lifespan in :mod:`sentinel_prism.main` performs this
setup call against ``DATABASE_URL`` on startup whenever the Postgres checkpointer
is selected — operators pointing ``DATABASE_URL`` at a local database will see
the LangGraph tables created the first time the app boots.
"""

from __future__ import annotations

import os
import re

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


def dev_memory_checkpointer() -> BaseCheckpointSaver:
    """Return a process-local checkpointer suitable for tests and local dev."""

    return MemorySaver()


# Matches ``postgresql+<driver>://`` and ``postgres://`` — both of which
# ``AsyncPostgresSaver`` (psycopg) cannot consume directly.
_SQLALCHEMY_ASYNC_PREFIX = re.compile(r"^postgresql\+[a-zA-Z0-9_]+://")
_SHORTHAND_PREFIX = re.compile(r"^postgres://")


def postgres_uri_for_langgraph(database_url: str) -> str:
    """Normalize a PostgreSQL DSN to the ``postgresql://`` form LangGraph expects.

    Handles common input shapes:

    * ``postgresql+asyncpg://…`` → ``postgresql://…`` (SQLAlchemy async app DSN)
    * ``postgresql+psycopg://…`` / ``postgresql+psycopg2://…`` → ``postgresql://…``
    * ``postgres://…`` → ``postgresql://…`` (legacy shorthand)
    * ``postgresql://…`` → unchanged
    * any other scheme → returned unchanged so the caller's connect call
      surfaces a clear error (rather than silently substituting a wrong driver).
    """

    u = database_url.strip()
    if _SQLALCHEMY_ASYNC_PREFIX.match(u):
        return _SQLALCHEMY_ASYNC_PREFIX.sub("postgresql://", u, count=1)
    if _SHORTHAND_PREFIX.match(u):
        return _SHORTHAND_PREFIX.sub("postgresql://", u, count=1)
    return u


def use_postgres_pipeline_checkpointer() -> bool:
    """Return True when the API/worker should use :class:`AsyncPostgresSaver`.

    * ``PIPELINE_CHECKPOINTER=memory`` — always in-memory.
    * ``PIPELINE_CHECKPOINTER=postgres`` — Postgres (``DATABASE_URL`` required).
    * Otherwise — Postgres when ``DATABASE_URL`` is non-empty, else memory.

    **Startup side effect:** when this returns ``True`` the FastAPI lifespan
    calls ``await saver.setup()`` against ``DATABASE_URL`` to create LangGraph's
    checkpoint tables. Developers pointing ``DATABASE_URL`` at an Alembic-only
    database should set ``PIPELINE_CHECKPOINTER=memory`` to suppress the DDL.
    """

    flag = os.environ.get("PIPELINE_CHECKPOINTER", "").strip().lower()
    if flag == "memory":
        return False
    if flag == "postgres":
        return True
    return bool(os.environ.get("DATABASE_URL", "").strip())
