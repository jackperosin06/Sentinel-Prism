"""Lightweight observability helpers (Story 8.3 — NFR8).

We intentionally keep this module dependency-free: no structlog / logging
configuration framework. Call sites standardize on:

  logger.<level>(..., extra={"event": <name>, "ctx": <dict>})
"""

from __future__ import annotations

from typing import Any


def obs_ctx(
    *,
    run_id: str | None = None,
    source_id: str | None = None,
    node_id: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    if run_id:
        ctx["run_id"] = run_id
    if source_id:
        ctx["source_id"] = source_id
    if node_id:
        ctx["node_id"] = node_id
    if request_id:
        ctx["request_id"] = request_id
    if user_id:
        ctx["user_id"] = user_id
    for k, v in extra.items():
        if v is None:
            continue
        ctx[k] = v
    return ctx

