"""RSS/HTTP connectors (Story 2.3) — mocked HTTP only."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.http_client import connector_async_client
from sentinel_prism.services.connectors.html_fallback import fetch_html_page_items
from sentinel_prism.services.connectors.http_fetch import fetch_http_page_item
from sentinel_prism.services.connectors.poll import execute_poll
from sentinel_prism.services.connectors.rss_fetch import fetch_rss_items
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem


@pytest.fixture
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_delay: float = 0) -> None:
        return None

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.fetch_retry.asyncio.sleep",
        _instant,
    )

RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>Item A</title>
      <link>https://regulator.example/item-a</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <description>Summary A</description>
    </item>
    <item>
      <title>Item B</title>
    </item>
  </channel>
</rss>
"""

ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test</title>
  <entry>
    <title>Atom Entry</title>
    <link href="https://regulator.example/atom-1"/>
    <updated>2024-06-15T10:00:00Z</updated>
    <summary>Atom summary</summary>
  </entry>
</feed>
"""


def _mock_transport_for_feed(url_suffix: str, body: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(url_suffix):
            return httpx.Response(200, content=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_rss_parses_rss2() -> None:
    sid = uuid.uuid4()
    fetched = datetime(2024, 3, 1, tzinfo=timezone.utc)
    transport = _mock_transport_for_feed("/feed.xml", RSS_FIXTURE)
    client = connector_async_client(transport=transport)
    items = await fetch_rss_items(
        source_id=sid,
        url="https://regulator.example/feed.xml",
        fetched_at=fetched,
        trigger="manual",
        client=client,
    )
    assert len(items) == 2
    assert items[0].item_url == "https://regulator.example/item-a"
    assert items[0].title == "Item A"
    assert items[0].summary == "Summary A"
    assert items[0].published_at is not None
    assert items[0].fetched_at == fetched
    assert items[1].item_url == f"urn:sentinel-prism:feed-item:{sid}:1"
    assert items[1].title == "Item B"


@pytest.mark.asyncio
async def test_fetch_rss_parses_atom() -> None:
    sid = uuid.uuid4()
    fetched = datetime(2024, 3, 2, tzinfo=timezone.utc)
    transport = _mock_transport_for_feed("/atom.xml", ATOM_FIXTURE)
    client = connector_async_client(transport=transport)
    items = await fetch_rss_items(
        source_id=sid,
        url="https://regulator.example/atom.xml",
        fetched_at=fetched,
        trigger="scheduled",
        client=client,
    )
    assert len(items) == 1
    assert items[0].item_url == "https://regulator.example/atom-1"
    assert items[0].title == "Atom Entry"
    assert items[0].summary == "Atom summary"


@pytest.mark.asyncio
async def test_fetch_rss_http_error_raises_after_retries(
    no_backoff_sleep: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    client = connector_async_client(transport=transport)
    with pytest.raises(ConnectorFetchFailed):
        await fetch_rss_items(
            source_id=uuid.uuid4(),
            url="https://regulator.example/feed.xml",
            fetched_at=datetime.now(timezone.utc),
            trigger="scheduled",
            client=client,
        )


@pytest.mark.asyncio
async def test_fetch_rss_retries_503_then_ok(
    no_backoff_sleep: None,
) -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=RSS_FIXTURE)

    transport = httpx.MockTransport(handler)
    client = connector_async_client(transport=transport)
    sid = uuid.uuid4()
    items = await fetch_rss_items(
        source_id=sid,
        url="https://regulator.example/feed.xml",
        fetched_at=datetime.now(timezone.utc),
        trigger="manual",
        client=client,
    )
    assert len(items) == 2
    assert n["i"] == 3


@pytest.mark.asyncio
async def test_fetch_rss_404_non_retryable(no_backoff_sleep: None) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    client = connector_async_client(transport=transport)
    with pytest.raises(ConnectorFetchFailed):
        await fetch_rss_items(
            source_id=uuid.uuid4(),
            url="https://regulator.example/missing.xml",
            fetched_at=datetime.now(timezone.utc),
            trigger="scheduled",
            client=client,
        )


@pytest.mark.asyncio
async def test_fetch_http_returns_one_item_with_snippet() -> None:
    body = b"<html><body>" + b"x" * 100 + b"</body></html>"
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    client = connector_async_client(transport=transport)
    fetched = datetime(2024, 4, 1, tzinfo=timezone.utc)
    items = await fetch_http_page_item(
        source_id=uuid.uuid4(),
        url="https://regulator.example/page",
        fetched_at=fetched,
        trigger="manual",
        client=client,
    )
    assert len(items) == 1
    assert items[0].http_status == 200
    assert items[0].content_type == "text/html"
    assert items[0].body_snippet is not None
    assert "xxx" in items[0].body_snippet


@pytest.mark.asyncio
async def test_fetch_http_5xx_raises_after_retries(no_backoff_sleep: None) -> None:
    """Persistent5xx exhausts retries and surfaces ``ConnectorFetchFailed``."""
    transport = httpx.MockTransport(
        lambda r: httpx.Response(500, content=b"Internal Server Error")
    )
    client = connector_async_client(transport=transport)
    with pytest.raises(ConnectorFetchFailed):
        await fetch_http_page_item(
            source_id=uuid.uuid4(),
            url="https://regulator.example/page",
            fetched_at=datetime.now(timezone.utc),
            trigger="scheduled",
            client=client,
        )


@pytest.mark.asyncio
async def test_fetch_http_retries_500_then_ok(no_backoff_sleep: None) -> None:
    n = {"i": 0}
    body = b"<html><body>ok</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] < 2:
            return httpx.Response(503, content=b"no")
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    client = connector_async_client(transport=transport)
    items = await fetch_http_page_item(
        source_id=uuid.uuid4(),
        url="https://regulator.example/page",
        fetched_at=datetime.now(timezone.utc),
        trigger="manual",
        client=client,
    )
    assert len(items) == 1
    assert items[0].http_status == 200
    assert n["i"] == 2


@pytest.mark.asyncio
async def test_fetch_http_4xx_returns_item_with_status() -> None:
    """4xx responses are captured as items for admin health visibility (P2 / D1 option 3)."""
    transport = httpx.MockTransport(
        lambda r: httpx.Response(404, content=b"Not Found")
    )
    client = connector_async_client(transport=transport)
    items = await fetch_http_page_item(
        source_id=uuid.uuid4(),
        url="https://regulator.example/page",
        fetched_at=datetime.now(timezone.utc),
        trigger="scheduled",
        client=client,
    )
    assert len(items) == 1
    assert items[0].http_status == 404


def _make_poll_ctx(row: object) -> tuple[MagicMock, MagicMock]:
    """Return (factory_mock, the session mock) wired up for execute_poll monkeypatching."""

    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    # Story 2.6 atomic counter updates use ``session.execute(update(Source)...)``;
    # ``session.get`` remains for legacy repo helpers (``get_source_by_id``, ``clear_poll_failure``).
    session.execute = AsyncMock()
    session.get = AsyncMock(return_value=row)

    class _Ctx:
        async def __aenter__(self) -> MagicMock:
            return session

        async def __aexit__(self, *a: object) -> None:
            return None

    return MagicMock(return_value=_Ctx()), session


def _poll_source_row(**overrides: object) -> MagicMock:
    """MagicMock ``Source`` row with Story 2.6 metric counters (ints) for ``execute_poll`` tests."""

    row = MagicMock()
    row.enabled = True
    row.source_type = SourceType.RSS
    row.primary_url = "https://ex/feed.xml"
    row.fallback_url = None
    row.fallback_mode = FallbackMode.NONE
    row.extra_metadata = None
    row.poll_attempts_success = 0
    row.poll_attempts_failed = 0
    row.items_ingested_total = 0
    row.last_success_at = None
    row.last_failure_at = None
    row.last_success_latency_ms = None
    row.last_success_fetch_path = None
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


@pytest.mark.asyncio
async def test_execute_poll_missing_source_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    factory, _session = _make_poll_ctx(None)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _no_source(session: object, sid: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _no_source,
    )
    out = await execute_poll(uuid.uuid4(), trigger="scheduled")
    assert out == []


@pytest.mark.asyncio
async def test_execute_poll_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _poll_source_row(enabled=False, primary_url="https://ex/f")

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, sid: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )
    out = await execute_poll(uuid.uuid4(), trigger="manual")
    assert out == []


@pytest.mark.asyncio
async def test_execute_poll_rss_calls_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = uuid.uuid4()
    row = _poll_source_row(primary_url="https://ex/feed.xml")

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        assert source_id == sid
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    expected = [
        ScoutRawItem(
            source_id=sid,
            item_url="https://ex/a",
            fetched_at=datetime.now(timezone.utc),
            title="T",
        )
    ]

    async def _fetch(**kwargs: object) -> list[ScoutRawItem]:
        assert kwargs.get("trigger") == "manual"
        assert kwargs.get("url") == "https://ex/feed.xml"
        return expected

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )
    out = await execute_poll(sid, trigger="manual")
    assert out == expected


@pytest.mark.asyncio
async def test_execute_poll_records_failure_after_connector_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """record_poll_failure is called when ConnectorFetchFailed propagates (AC5, AC8)."""
    sid = uuid.uuid4()
    row = _poll_source_row(primary_url="https://ex/feed.xml")

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    error = ConnectorFetchFailed(
        "rss_fetch exhausted after 4 attempts",
        error_class="HTTPStatusError",
    )

    async def _fail_fetch(**kwargs: object) -> list[ScoutRawItem]:
        raise error

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fail_fetch,
    )

    recorded: list[dict] = []

    async def _record_failure(
        session: object,
        source_id: uuid.UUID,
        *,
        reason: str,
        error_class: str,
    ) -> None:
        recorded.append({"source_id": source_id, "reason": reason, "error_class": error_class})

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        _record_failure,
    )

    out = await execute_poll(sid, trigger="scheduled")
    assert out == []
    assert len(recorded) == 1
    assert recorded[0]["source_id"] == sid
    assert "exhausted" in recorded[0]["reason"]
    assert recorded[0]["error_class"] == "HTTPStatusError"


@pytest.mark.asyncio
async def test_execute_poll_http_calls_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_poll dispatches to the HTTP fetcher for SourceType.HTTP (AC7)."""
    sid = uuid.uuid4()
    row = _poll_source_row(
        source_type=SourceType.HTTP,
        primary_url="https://ex/page",
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        assert source_id == sid
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    expected = [
        ScoutRawItem(
            source_id=sid,
            item_url="https://ex/page",
            fetched_at=datetime.now(timezone.utc),
            http_status=200,
        )
    ]

    async def _fetch(**kwargs: object) -> list[ScoutRawItem]:
        assert kwargs.get("trigger") == "scheduled"
        assert kwargs.get("url") == "https://ex/page"
        return expected

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_http_page_item",
        _fetch,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )
    out = await execute_poll(sid, trigger="scheduled")
    assert out == expected


@pytest.mark.asyncio
async def test_fetch_html_page_items_extracts_title_and_body(
    no_backoff_sleep: None,
) -> None:
    sid = uuid.uuid4()
    html = (
        b"<html><head><title>Notice Board</title></head>"
        b"<body><p>Important update text</p></body></html>"
    )
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, content=html, headers={"content-type": "text/html"}
        )
    )
    client = connector_async_client(transport=transport)
    items = await fetch_html_page_items(
        source_id=sid,
        url="https://regulator.example/notices",
        fetched_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        trigger="manual",
        client=client,
    )
    assert len(items) == 1
    assert items[0].title == "Notice Board"
    assert items[0].body_snippet and "Important update" in items[0].body_snippet
    assert items[0].http_status == 200


@pytest.mark.asyncio
async def test_execute_poll_primary_failure_triggers_fallback_same_as_primary(
    monkeypatch: pytest.MonkeyPatch,
    no_backoff_sleep: None,
) -> None:
    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/bad.xml",
        fallback_url="https://ex/good.xml",
        fallback_mode=FallbackMode.SAME_AS_PRIMARY,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    urls: list[str] = []

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        urls.append(str(kwargs.get("url")))
        if kwargs.get("url") == "https://ex/bad.xml":
            raise ConnectorFetchFailed("primary down", error_class="HTTPStatusError")
        return [
            ScoutRawItem(
                source_id=sid,
                item_url="https://ex/from-fallback",
                fetched_at=datetime.now(timezone.utc),
                title="FB",
            )
        ]

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )

    out = await execute_poll(sid, trigger="manual")
    assert urls == ["https://ex/bad.xml", "https://ex/good.xml"]
    assert len(out) == 1
    assert out[0].item_url == "https://ex/from-fallback"


@pytest.mark.asyncio
async def test_execute_poll_primary_ok_does_not_call_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/primary.xml",
        fallback_url="https://ex/fallback.xml",
        fallback_mode=FallbackMode.SAME_AS_PRIMARY,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    calls = 0

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        nonlocal calls
        calls += 1
        assert kwargs.get("url") == "https://ex/primary.xml"
        return [
            ScoutRawItem(
                source_id=sid,
                item_url="https://ex/a",
                fetched_at=datetime.now(timezone.utc),
                title="ok",
            )
        ]

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )

    await execute_poll(sid, trigger="manual")
    assert calls == 1


@pytest.mark.asyncio
async def test_execute_poll_both_primary_and_fallback_fail(
    monkeypatch: pytest.MonkeyPatch,
    no_backoff_sleep: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/a.xml",
        fallback_url="https://ex/b.xml",
        fallback_mode=FallbackMode.SAME_AS_PRIMARY,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        if kwargs.get("url") == "https://ex/a.xml":
            raise ConnectorFetchFailed("primary fail", error_class="ConnectError")
        raise ConnectorFetchFailed("fallback fail", error_class="HTTPStatusError")

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    recorded: list[dict[str, object]] = []

    async def _record_failure(
        session: object,
        source_id: uuid.UUID,
        *,
        reason: str,
        error_class: str,
    ) -> None:
        recorded.append(
            {"source_id": source_id, "reason": reason, "error_class": error_class}
        )

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        _record_failure,
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="sentinel_prism.services.connectors.poll"):
        out = await execute_poll(sid, trigger="scheduled")

    assert out == []
    assert len(recorded) == 1
    reason = str(recorded[0]["reason"])
    assert "primary:" in reason and "fallback:" in reason
    # error_class is the merged "primary|fallback" so both classes are filterable.
    assert recorded[0]["error_class"] == "ConnectError|HTTPStatusError"

    # caplog must contain poll_fetch_both_failed with outcome=both_failed and both URL hosts.
    both = [r for r in caplog.records if r.getMessage() == "poll_fetch_both_failed"]
    assert len(both) == 1, [r.getMessage() for r in caplog.records]
    assert getattr(both[0], "outcome") == "both_failed"
    assert getattr(both[0], "primary_error_class") == "ConnectError"
    assert getattr(both[0], "fallback_error_class") == "HTTPStatusError"
    assert not any(r.getMessage() == "poll_completed" for r in caplog.records)


@pytest.mark.asyncio
async def test_execute_poll_html_fallback_success(
    monkeypatch: pytest.MonkeyPatch,
    no_backoff_sleep: None,
) -> None:
    """Primary RSS fails → HTML_PAGE fallback is routed through fetch_html_page_items."""

    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/primary.xml",
        fallback_url="https://ex/page.html",
        fallback_mode=FallbackMode.HTML_PAGE,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        raise ConnectorFetchFailed("primary down", error_class="HTTPStatusError")

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    html_calls: list[dict[str, object]] = []

    async def _fetch_html(**kwargs: object) -> list[ScoutRawItem]:
        html_calls.append(kwargs)
        return [
            ScoutRawItem(
                source_id=sid,
                item_url="https://ex/page.html",
                fetched_at=datetime.now(timezone.utc),
                title="page",
            )
        ]

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_html_page_items",
        _fetch_html,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )

    out = await execute_poll(sid, trigger="manual")
    assert len(out) == 1
    assert out[0].item_url == "https://ex/page.html"
    assert len(html_calls) == 1
    assert html_calls[0]["url"] == "https://ex/page.html"


@pytest.mark.asyncio
async def test_execute_poll_html_fallback_4xx_logs_both_failed(
    monkeypatch: pytest.MonkeyPatch,
    no_backoff_sleep: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Primary RSS fails, HTML fallback hits 404 → poll_fetch_both_failed."""

    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/primary.xml",
        fallback_url="https://ex/missing.html",
        fallback_mode=FallbackMode.HTML_PAGE,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        raise ConnectorFetchFailed("primary 500", error_class="HTTPStatusError")

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    # Route HTML fallback through the real fetch_html_page_items against a 404 transport
    # so fetch_retry's non-retryable-HTTP path runs and raises ConnectorFetchFailed.
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    client = connector_async_client(transport=transport)

    async def _fetch_html(**kwargs: object) -> list[ScoutRawItem]:
        return await fetch_html_page_items(
            source_id=kwargs["source_id"],  # type: ignore[arg-type]
            url=kwargs["url"],  # type: ignore[arg-type]
            fetched_at=kwargs["fetched_at"],  # type: ignore[arg-type]
            trigger=kwargs["trigger"],  # type: ignore[arg-type]
            client=client,
        )

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_html_page_items",
        _fetch_html,
    )

    recorded: list[dict[str, object]] = []

    async def _record_failure(
        session: object,
        source_id: uuid.UUID,
        *,
        reason: str,
        error_class: str,
    ) -> None:
        recorded.append({"reason": reason, "error_class": error_class})

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        _record_failure,
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="sentinel_prism.services.connectors.poll"):
        out = await execute_poll(sid, trigger="scheduled")

    assert out == []
    assert len(recorded) == 1
    assert "primary:" in recorded[0]["reason"] and "fallback:" in recorded[0]["reason"]
    assert any(r.getMessage() == "poll_fetch_both_failed" for r in caplog.records)


@pytest.mark.asyncio
async def test_execute_poll_primary_non_connector_exception_does_not_trigger_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Policy (Story 2.5 AC2): only ConnectorFetchFailed flips to the fallback path."""

    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/a.xml",
        fallback_url="https://ex/b.xml",
        fallback_mode=FallbackMode.SAME_AS_PRIMARY,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(session: object, source_id: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    primary_calls = 0

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        nonlocal primary_calls
        primary_calls += 1
        raise ValueError("unexpected primary bug")

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    # Fallback must NOT be called — use the same spec's same_as_primary path but set a
    # sentinel that blows up if touched.
    def _must_not_be_called(*_a: object, **_k: object) -> None:
        raise AssertionError("fallback must not be attempted on non-ConnectorFetchFailed")

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll._fetch_fallback",
        _must_not_be_called,
    )

    recorded: list[dict[str, object]] = []

    async def _record_failure(
        session: object,
        source_id: uuid.UUID,
        *,
        reason: str,
        error_class: str,
    ) -> None:
        recorded.append({"reason": reason, "error_class": error_class})

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        _record_failure,
    )

    out = await execute_poll(sid, trigger="scheduled")
    assert out == []
    assert primary_calls == 1
    assert len(recorded) == 1
    assert recorded[0]["error_class"] == "ValueError"


@pytest.mark.asyncio
async def test_fetch_html_page_items_rejects_non_html_content_type(
    no_backoff_sleep: None,
) -> None:
    """Non-HTML 2xx response must surface as ConnectorFetchFailed, not parse as garbage."""

    sid = uuid.uuid4()
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            content=b'{"items": []}',
            headers={"content-type": "application/json"},
        )
    )
    client = connector_async_client(transport=transport)
    with pytest.raises(ConnectorFetchFailed):
        await fetch_html_page_items(
            source_id=sid,
            url="https://ex/notices",
            fetched_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
            trigger="manual",
            client=client,
        )


@pytest.mark.asyncio
async def test_fetch_html_page_items_honors_declared_encoding(
    no_backoff_sleep: None,
) -> None:
    """windows-1252 bytes must decode with the server-declared encoding."""

    sid = uuid.uuid4()
    # 0x80 is '€' in windows-1252 and invalid in UTF-8.
    body = (
        b"<html><head><title>Price 50\x80</title></head>"
        b"<body><p>Fee is 10\x80 per item</p></body></html>"
    )
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/html; charset=windows-1252"},
        )
    )
    client = connector_async_client(transport=transport)
    items = await fetch_html_page_items(
        source_id=sid,
        url="https://ex/notices",
        fetched_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        trigger="manual",
        client=client,
    )
    assert len(items) == 1
    assert items[0].title is not None and "€" in items[0].title
    assert items[0].body_snippet is not None and "€" in items[0].body_snippet
    assert items[0].http_status == 200


# ---------------------------------------------------------------------------
# Story 2.6 — NFR9 metrics hooks on execute_poll (Review P4)
# ---------------------------------------------------------------------------


def _record_success_spy() -> tuple[list[dict[str, object]], object]:
    """Return (calls-list, replacement coroutine) for ``record_poll_success_metrics``."""

    calls: list[dict[str, object]] = []

    async def _spy(
        _session: object,
        source_id: uuid.UUID,
        *,
        items_new_count: int,
        latency_ms: int,
        fetch_path: str,
        fetched_at: datetime,
    ) -> None:
        calls.append(
            {
                "source_id": source_id,
                "items_new_count": items_new_count,
                "latency_ms": latency_ms,
                "fetch_path": fetch_path,
                "fetched_at": fetched_at,
            }
        )

    return calls, _spy


def _record_failure_spy() -> tuple[list[dict[str, object]], object]:
    calls: list[dict[str, object]] = []

    async def _spy(
        _session: object,
        source_id: uuid.UUID,
        *,
        reason: str,
        error_class: str,
    ) -> None:
        calls.append(
            {"source_id": source_id, "reason": reason, "error_class": error_class}
        )

    return calls, _spy


@pytest.mark.asyncio
async def test_execute_poll_disabled_does_not_touch_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 — disabled source skip must not call any metric helper (Review P4)."""
    row = _poll_source_row(enabled=False)
    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(_s: object, _sid: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    fail_calls, fail_spy = _record_failure_spy()
    succ_calls, succ_spy = _record_success_spy()
    disable_calls: list[uuid.UUID] = []

    async def _disable(_s: object, sid: uuid.UUID) -> None:
        disable_calls.append(sid)

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        fail_spy,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_success_metrics",
        succ_spy,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.disable_source",
        _disable,
    )

    out = await execute_poll(uuid.uuid4(), trigger="scheduled")
    assert out == []
    assert fail_calls == []
    assert succ_calls == []
    assert disable_calls == []


@pytest.mark.asyncio
async def test_execute_poll_missing_source_does_not_touch_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 — missing source skip must not call any metric helper (Review P4)."""
    factory, _session = _make_poll_ctx(None)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _no_source(_s: object, _sid: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _no_source,
    )

    fail_calls, fail_spy = _record_failure_spy()
    succ_calls, succ_spy = _record_success_spy()

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        fail_spy,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_success_metrics",
        succ_spy,
    )

    out = await execute_poll(uuid.uuid4(), trigger="scheduled")
    assert out == []
    assert fail_calls == []
    assert succ_calls == []


@pytest.mark.asyncio
async def test_execute_poll_unsupported_source_type_records_failure_and_auto_disables(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Story 2.6 Decision 1 — unsupported ``source_type`` records one failure + auto-disables.

    Uses a sentinel object that is *not* in ``(SourceType.RSS, SourceType.HTTP)``
    so the guard branch actually fires — a real enum value is always supported today.
    """
    sid = uuid.uuid4()
    bogus_type = "bogus"  # deliberately not a SourceType member
    row = _poll_source_row(source_type=bogus_type)

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(_s: object, _sid: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    fail_calls, fail_spy = _record_failure_spy()
    disable_calls: list[uuid.UUID] = []

    async def _disable(_s: object, source_id: uuid.UUID) -> None:
        disable_calls.append(source_id)

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        fail_spy,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.disable_source",
        _disable,
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="sentinel_prism.services.connectors.poll"):
        out = await execute_poll(sid, trigger="scheduled")

    assert out == []
    assert len(fail_calls) == 1
    assert fail_calls[0]["source_id"] == sid
    assert fail_calls[0]["error_class"] == "UnsupportedSourceType"
    assert disable_calls == [sid]
    assert any(r.getMessage() == "source_auto_disabled" for r in caplog.records)


@pytest.mark.asyncio
async def test_execute_poll_success_records_success_metrics_with_primary_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 / AC2 — success tail calls ``record_poll_success_metrics`` with fetched_at + primary (Review P4)."""
    sid = uuid.uuid4()
    row = _poll_source_row(primary_url="https://ex/ok.xml")

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(_s: object, _sid: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    expected = [
        ScoutRawItem(
            source_id=sid,
            item_url="https://ex/a",
            fetched_at=datetime.now(timezone.utc),
            title="T",
        ),
        ScoutRawItem(
            source_id=sid,
            item_url="https://ex/b",
            fetched_at=datetime.now(timezone.utc),
            title="U",
        ),
    ]

    async def _fetch(**_kwargs: object) -> list[ScoutRawItem]:
        return expected

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )

    async def _clear(_s: object, _sid: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.clear_poll_failure",
        _clear,
    )

    succ_calls, succ_spy = _record_success_spy()
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_success_metrics",
        succ_spy,
    )

    out = await execute_poll(sid, trigger="manual")
    assert out == expected
    assert len(succ_calls) == 1
    call = succ_calls[0]
    assert call["source_id"] == sid
    assert call["items_new_count"] == 2
    assert call["fetch_path"] == "primary"
    assert isinstance(call["latency_ms"], int) and call["latency_ms"] >= 0
    assert isinstance(call["fetched_at"], datetime) and call["fetched_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_execute_poll_fallback_success_records_fetch_path_fallback(
    monkeypatch: pytest.MonkeyPatch,
    no_backoff_sleep: None,
) -> None:
    """Fallback success must stamp ``fetch_path='fallback'`` so operators see primary drift (Review P4)."""
    sid = uuid.uuid4()
    row = _poll_source_row(
        primary_url="https://ex/bad.xml",
        fallback_url="https://ex/good.xml",
        fallback_mode=FallbackMode.SAME_AS_PRIMARY,
    )

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(_s: object, _sid: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    async def _fetch_rss(**kwargs: object) -> list[ScoutRawItem]:
        if kwargs.get("url") == "https://ex/bad.xml":
            raise ConnectorFetchFailed("primary down", error_class="HTTPStatusError")
        return [
            ScoutRawItem(
                source_id=sid,
                item_url="https://ex/from-fb",
                fetched_at=datetime.now(timezone.utc),
                title="FB",
            )
        ]

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch_rss,
    )

    async def _dedupe(
        _session: object, _source_id: uuid.UUID, items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        return items

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _dedupe,
    )

    async def _clear(_s: object, _sid: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.clear_poll_failure",
        _clear,
    )

    succ_calls, succ_spy = _record_success_spy()
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_success_metrics",
        succ_spy,
    )

    await execute_poll(sid, trigger="manual")
    assert len(succ_calls) == 1
    assert succ_calls[0]["fetch_path"] == "fallback"


@pytest.mark.asyncio
async def test_execute_poll_dedup_failure_after_success_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 2 — dedup failure after successful fetch counts as a failure (Review P4)."""
    sid = uuid.uuid4()
    row = _poll_source_row(primary_url="https://ex/ok.xml")

    factory, _session = _make_poll_ctx(row)
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.get_session_factory",
        lambda: factory,
    )

    async def _get(_s: object, _sid: uuid.UUID) -> object:
        return row

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.get_source_by_id",
        _get,
    )

    async def _fetch(**_kwargs: object) -> list[ScoutRawItem]:
        return [
            ScoutRawItem(
                source_id=sid,
                item_url="https://ex/a",
                fetched_at=datetime.now(timezone.utc),
            )
        ]

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.fetch_rss_items",
        _fetch,
    )

    async def _boom(
        _session: object, _source_id: uuid.UUID, _items: list[ScoutRawItem]
    ) -> list[ScoutRawItem]:
        raise RuntimeError("unique violation")

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.ingestion_dedup.register_new_items",
        _boom,
    )

    async def _clear(_s: object, _sid: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.clear_poll_failure",
        _clear,
    )

    succ_calls, succ_spy = _record_success_spy()
    fail_calls, fail_spy = _record_failure_spy()
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_success_metrics",
        succ_spy,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.connectors.poll.sources_repo.record_poll_failure",
        fail_spy,
    )

    out = await execute_poll(sid, trigger="manual")
    assert out == []
    assert succ_calls == []
    assert len(fail_calls) == 1
    assert fail_calls[0]["error_class"] == "RuntimeError"
    assert "dedup after primary success" in str(fail_calls[0]["reason"])


@pytest.mark.asyncio
async def test_record_poll_success_metrics_rejects_unknown_fetch_path() -> None:
    """Repo helper raises ``ValueError`` on unknown ``fetch_path`` (Review P9)."""
    from sentinel_prism.db.repositories.sources import record_poll_success_metrics

    with pytest.raises(ValueError, match="fetch_path"):
        await record_poll_success_metrics(
            MagicMock(),  # session unused before the guard
            uuid.uuid4(),
            items_new_count=0,
            latency_ms=0,
            fetch_path="weirdo",  # type: ignore[arg-type]
            fetched_at=datetime.now(timezone.utc),
        )
