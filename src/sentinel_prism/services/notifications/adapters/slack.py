"""Slack incoming webhook via :mod:`httpx` (Story 5.3).

Incoming webhooks reply with literal ``"ok"`` on success and do not
return a message id, so this adapter does **not** surface the response
body as ``provider_message_id``. The returned ``provider_hint`` is
always ``None`` for Slack webhooks (field kept for symmetry with
future adapters that do return an id).

Transient responses — HTTP 429 (with optional ``Retry-After``) and 5xx —
trigger a small bounded retry before being recorded as a permanent
failure. Exception text and 4xx/5xx bodies are scrubbed of the webhook
URL (which carries a secret token) before being returned for
persistence.
"""

from __future__ import annotations

import asyncio

import httpx


_MAX_DETAIL = 500
_DEFAULT_ATTEMPTS = 3
_DEFAULT_BACKOFF = 0.5


def _scrub(text: str, *, webhook_url: str | None) -> str:
    out = (text or "").replace("\r", " ").replace("\n", " ")
    if webhook_url:
        # Replace the whole URL first, then also its path (Slack tokens live
        # in the path so a partial echo would still expose the secret).
        out = out.replace(webhook_url, "<slack-webhook-redacted>")
        try:
            path = webhook_url.split("://", 1)[-1]
            if path and path in out:
                out = out.replace(path, "<slack-webhook-redacted>")
        except Exception:  # noqa: BLE001 — pure string cleanup, never fatal
            pass
    return out.strip()[:_MAX_DETAIL]


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        secs = float(value.strip())
    except ValueError:
        return None
    if secs < 0:
        return None
    # Cap to something sane so a pathological header does not block the
    # graph for a long time.
    return min(secs, 10.0)


async def send_slack_webhook_text(
    *,
    webhook_url: str,
    text: str,
    timeout_seconds: float = 20.0,
    max_attempts: int = _DEFAULT_ATTEMPTS,
) -> tuple[bool, str | None, str | None, str | None]:
    """POST ``{"text": ...}`` with bounded retry; return ``(ok, error_class, detail, provider_hint)``.

    ``provider_hint`` is reserved for adapters that return a real id; for
    Slack incoming webhooks it is always ``None`` (the body is literal
    ``"ok"`` and is not useful as an identifier).
    """

    attempts = max(1, int(max_attempts))
    last_error_class: str | None = None
    last_detail: str | None = None
    last_status: int | None = None

    for i in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                r = await client.post(
                    webhook_url,
                    json={"text": text},
                    headers={"Content-Type": "application/json"},
                )
        except Exception as exc:  # noqa: BLE001
            last_error_class = type(exc).__name__
            last_detail = _scrub(str(exc), webhook_url=webhook_url)
            # Connection-level failures are typically transient; sleep
            # and retry unless we are out of attempts.
            if i + 1 < attempts:
                await asyncio.sleep(_DEFAULT_BACKOFF * (i + 1))
                continue
            return False, last_error_class, last_detail, None

        if 200 <= r.status_code < 300:
            return True, None, None, None

        last_status = r.status_code
        last_error_class = "HTTPStatusError"
        last_detail = _scrub(
            f"status={r.status_code} body={(r.text or '')[:200]}",
            webhook_url=webhook_url,
        )

        is_retryable = r.status_code == 429 or 500 <= r.status_code < 600
        if not is_retryable or i + 1 >= attempts:
            return False, last_error_class, last_detail, None

        retry_after = _parse_retry_after(r.headers.get("Retry-After"))
        await asyncio.sleep(retry_after or _DEFAULT_BACKOFF * (i + 1))

    # Fallthrough for the degenerate max_attempts=0 path (normalized to 1
    # above) — keep explicit for type checkers.
    return (
        False,
        last_error_class or "HTTPStatusError",
        last_detail or (f"status={last_status}" if last_status else None),
        None,
    )
