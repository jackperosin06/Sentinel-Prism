"""Slack webhook adapter edge cases (Story 5.3 — AC #8).

Covers retry/backoff, 2xx / 4xx / 5xx / 429 branches, transport
exception sanitization, and the webhook URL scrubbing on error detail.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from sentinel_prism.services.notifications.adapters import slack as slack_adapter


_WEBHOOK = "https://hooks.slack.test/services/TSECRET/BSECRET/zverysecret"


def _mock_transport_for_responses(
    responses: list[httpx.Response],
) -> httpx.MockTransport:
    it = iter(responses)

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(it)

    return httpx.MockTransport(handler)


def _mock_transport_for_exceptions(
    exceptions: list[Exception],
) -> httpx.MockTransport:
    it = iter(exceptions)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise next(it)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(slack_adapter.asyncio, "sleep", _sleep)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(slack_adapter.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_slack_success_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        _mock_transport_for_responses([httpx.Response(200, text="ok")]),
    )
    ok, err_class, detail, hint = await slack_adapter.send_slack_webhook_text(
        webhook_url=_WEBHOOK, text="hello"
    )
    assert ok is True
    assert err_class is None
    assert detail is None
    # Adapter no longer echoes Slack's "ok" into provider_hint.
    assert hint is None


@pytest.mark.asyncio
async def test_slack_404_is_permanent_failure_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="no_service")

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    ok, err_class, detail, _hint = await slack_adapter.send_slack_webhook_text(
        webhook_url=_WEBHOOK, text="hello", max_attempts=3
    )
    assert ok is False
    assert err_class == "HTTPStatusError"
    assert detail is not None
    assert "status=404" in detail
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_slack_retries_on_5xx_and_eventually_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    ok, err_class, _detail, _hint = await slack_adapter.send_slack_webhook_text(
        webhook_url=_WEBHOOK, text="hello", max_attempts=3
    )
    assert ok is False
    assert err_class == "HTTPStatusError"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_slack_retries_on_429_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        httpx.Response(429, text="rate_limited", headers={"Retry-After": "1"}),
        httpx.Response(200, text="ok"),
    ]
    _patch_client(monkeypatch, _mock_transport_for_responses(responses))

    ok, err_class, detail, _hint = await slack_adapter.send_slack_webhook_text(
        webhook_url=_WEBHOOK, text="hello", max_attempts=3
    )
    assert ok is True
    assert err_class is None
    assert detail is None


@pytest.mark.asyncio
async def test_slack_transport_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A request error whose message embeds the URL (including secret token).
    boom = httpx.ConnectError(
        f"failed to connect to {_WEBHOOK}",
    )
    _patch_client(monkeypatch, _mock_transport_for_exceptions([boom, boom, boom]))

    ok, err_class, detail, _hint = await slack_adapter.send_slack_webhook_text(
        webhook_url=_WEBHOOK, text="hello", max_attempts=3
    )
    assert ok is False
    assert err_class == "ConnectError"
    assert detail is not None
    # Secret token must not appear in the persisted detail.
    assert "zverysecret" not in detail
    assert "TSECRET" not in detail
    assert "<slack-webhook-redacted>" in detail


@pytest.mark.asyncio
async def test_slack_body_in_4xx_is_scrubbed_of_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some endpoints echo the request URL / headers into the response body.
    body = f"bad URL: {_WEBHOOK}"
    _patch_client(
        monkeypatch,
        _mock_transport_for_responses([httpx.Response(400, text=body)]),
    )

    ok, err_class, detail, _hint = await slack_adapter.send_slack_webhook_text(
        webhook_url=_WEBHOOK, text="hello", max_attempts=1
    )
    assert ok is False
    assert err_class == "HTTPStatusError"
    assert detail is not None
    assert "zverysecret" not in detail
