"""Replay-mode guardrails (Story 8.2).

Replay is an operator debugging tool: it must be *non-destructive* and must not
emit external side effects. We enforce this with a process-local context flag so
call sites don't need to thread "replay=True" through every service function.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


_REPLAY_MODE: ContextVar[bool] = ContextVar("sentinel_prism_replay_mode", default=False)


def in_replay_mode() -> bool:
    return bool(_REPLAY_MODE.get())


@contextmanager
def replay_mode() -> "object":
    token = _REPLAY_MODE.set(True)
    try:
        yield
    finally:
        _REPLAY_MODE.reset(token)

