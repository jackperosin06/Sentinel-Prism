"""Bounded exponential backoff for transient HTTP failures (Story 2.4 — FR4)."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from sentinel_prism.services.connectors.errors import ConnectorFetchFailed

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Policy (MVP): documented constants — tune for production if needed.
MAX_ATTEMPTS = 4
BASE_DELAY_SEC = 0.75
BACKOFF_MULTIPLIER = 2.0
MAX_DELAY_SEC = 45.0
JITTER_MAX_SEC = 0.25

# ---------------------------------------------------------------------------
# Error classification table (AC6)
#
# HTTP status codes
#   Retryable    : 429 (rate-limit), 500 (server error), 502/503/504 (gateway)
#   Non-retryable: 400, 401, 403, 404, 405, 410, 422 — client or permanent errors
#   Other codes  : treated as non-retryable (fail fast on first attempt)
#
# httpx exception types
#   Retryable    : TimeoutException, ConnectError, ReadError, WriteError,
#                  RemoteProtocolError, NetworkError — transient I/O failures.
#                  NOTE: ConnectError is also raised for DNS failures.
#                  DNS failures are retried (up to MAX_ATTEMPTS) by design:
#                  separating them from transient connection failures is fragile
#                  and not worth the complexity at MVP scale (decision D2, 2026-04-16).
#   Non-retryable: All other Exception subclasses — unexpected; fail immediately.
# ---------------------------------------------------------------------------

# Retry when the server may recover or rate-limit clears.
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

# Fail fast — permanent or client-side errors; retrying is pointless (AC6).
NON_RETRYABLE_HTTP_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 422})


def _sleep_with_backoff(attempt_index: int) -> float:
    """Return delay before attempt ``attempt_index`` (1-based), after jitter."""

    delay = min(
        BASE_DELAY_SEC * (BACKOFF_MULTIPLIER ** (attempt_index - 1)),
        MAX_DELAY_SEC,
    )
    delay += random.uniform(0, JITTER_MAX_SEC)
    return delay


async def run_http_attempt_with_retry(
    *,
    source_id: uuid.UUID,
    trigger: str,
    url: str,
    operation: Callable[[], Awaitable[T]],
    failure_label: str,
) -> T:
    """Run ``operation`` with retries on transient ``httpx`` and HTTP status failures."""

    last_exc: BaseException | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await operation()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            code = exc.response.status_code
            u = httpx.URL(url)
            if code in NON_RETRYABLE_HTTP_STATUSES:
                msg = f"{failure_label}: HTTP {code}"
                logger.warning(
                    "fetch_attempt_non_retryable",
                    extra={
                        "source_id": str(source_id),
                        "trigger": trigger,
                        "url_host": u.host,
                        "url_path": u.path,
                        "attempt": attempt,
                        "http_status": code,
                        "error_class": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                raise ConnectorFetchFailed(msg, error_class=type(exc).__name__) from exc
            if code not in RETRYABLE_HTTP_STATUSES:
                msg = f"{failure_label}: HTTP {code}"
                logger.warning(
                    "fetch_attempt_failed",
                    extra={
                        "source_id": str(source_id),
                        "trigger": trigger,
                        "url_host": u.host,
                        "url_path": u.path,
                        "attempt": attempt,
                        "http_status": code,
                        "error_class": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                raise ConnectorFetchFailed(msg, error_class=type(exc).__name__) from exc
            logger.warning(
                "fetch_attempt_retryable_http",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "url_host": u.host,
                    "url_path": u.path,
                    "attempt": attempt,
                    "http_status": code,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
        ) as exc:
            last_exc = exc
            u = httpx.URL(url)
            logger.warning(
                "fetch_attempt_transient",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "url_host": u.host,
                    "url_path": u.path,
                    "attempt": attempt,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
        except ConnectorFetchFailed:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            last_exc = exc
            u = httpx.URL(url)
            logger.warning(
                "fetch_attempt_unexpected",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "url_host": u.host,
                    "url_path": u.path,
                    "attempt": attempt,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise ConnectorFetchFailed(
                f"{failure_label}: {exc}", error_class=type(exc).__name__
            ) from exc

        if attempt >= MAX_ATTEMPTS:
            break
        await asyncio.sleep(_sleep_with_backoff(attempt))

    if last_exc is None:  # pragma: no cover — only reachable if MAX_ATTEMPTS < 1
        raise RuntimeError(f"{failure_label}: retry loop exited without exception")
    u = httpx.URL(url)
    logger.warning(
        "fetch_exhausted_retries",
        extra={
            "source_id": str(source_id),
            "trigger": trigger,
            "url_host": u.host,
            "url_path": u.path,
            "attempt": MAX_ATTEMPTS,
            "error_class": type(last_exc).__name__,
            "error": str(last_exc),
        },
    )
    raise ConnectorFetchFailed(
        f"{failure_label} exhausted after {MAX_ATTEMPTS} attempts: {last_exc}",
        error_class=type(last_exc).__name__,
    ) from last_exc
