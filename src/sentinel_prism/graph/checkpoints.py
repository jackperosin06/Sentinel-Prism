"""Checkpointer factories for LangGraph (Story 3.2).

Development and CI use an in-memory saver. Production should adopt a SQL-backed
checkpointer (e.g. Postgres) for durable resume/replay (Architecture §3.5, FR35).
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


def dev_memory_checkpointer() -> BaseCheckpointSaver:
    """Return a process-local checkpointer suitable for tests and local dev."""

    return MemorySaver()
