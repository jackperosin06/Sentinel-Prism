"""RSS/HTTP connectors (Story 2.3) — mocked HTTP only."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from sentinel_prism.db.models import SourceType
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.http_client import connector_async_client
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

    class _Ctx:
        async def __aenter__(self) -> MagicMock:
            return session

        async def __aexit__(self, *a: object) -> None:
            return None

    return MagicMock(return_value=_Ctx()), session


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
    row = MagicMock()
    row.enabled = False
    row.source_type = SourceType.RSS
    row.primary_url = "https://ex/f"

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
    row = MagicMock()
    row.enabled = True
    row.source_type = SourceType.RSS
    row.primary_url = "https://ex/feed.xml"
    row.extra_metadata = None

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
    row = MagicMock()
    row.enabled = True
    row.source_type = SourceType.RSS
    row.primary_url = "https://ex/feed.xml"

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
    row = MagicMock()
    row.enabled = True
    row.source_type = SourceType.HTTP
    row.primary_url = "https://ex/page"
    row.extra_metadata = None

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
