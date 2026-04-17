"""Normalization heuristic (Story 3.1) — unit tests only."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.ingestion.normalize import (
    DOCUMENT_TYPE_UNKNOWN,
    MVP_CONFIDENCE_MAX,
    normalize_scout_item,
)


def test_normalize_full_rss_shaped_item() -> None:
    sid = uuid.uuid4()
    pub = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    fetched = datetime(2024, 1, 16, 8, 0, tzinfo=timezone.utc)
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/doc",
        fetched_at=fetched,
        title="Label change",
        published_at=pub,
        summary="Short",
        http_status=200,
        content_type="application/rss+xml",
        body_snippet="Body",
    )
    n = normalize_scout_item(
        item,
        source_id=sid,
        source_name="FDA Feed",
        jurisdiction="US",
    )
    assert n.source_id == sid
    assert n.source_name == "FDA Feed"
    assert n.jurisdiction == "US"
    assert n.item_url == item.item_url
    assert n.title == "Label change"
    assert n.published_at == pub
    assert n.document_type == DOCUMENT_TYPE_UNKNOWN
    assert n.body_snippet == "Body"
    assert n.summary == "Short"
    assert n.extra_metadata == {"http_status": 200, "content_type": "application/rss+xml"}
    assert n.parser_confidence == pytest.approx(MVP_CONFIDENCE_MAX)
    assert n.extraction_quality == pytest.approx(MVP_CONFIDENCE_MAX)
    # MVP contract: both metrics mirror until a future pipeline diverges them.
    assert n.parser_confidence == n.extraction_quality


def test_normalize_missing_title_and_dates_lowers_confidence() -> None:
    sid = uuid.uuid4()
    fetched = datetime(2024, 2, 1, tzinfo=timezone.utc)
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/only-url",
        fetched_at=fetched,
        title=None,
        published_at=None,
        summary=None,
        body_snippet=None,
    )
    n = normalize_scout_item(
        item,
        source_id=sid,
        source_name="S",
        jurisdiction="EU",
    )
    assert n.title is None
    assert n.published_at is None
    assert n.extra_metadata is None
    assert n.parser_confidence == pytest.approx(0.35)
    assert n.extraction_quality == pytest.approx(0.35)


def test_normalize_http_snippet_without_title() -> None:
    sid = uuid.uuid4()
    fetched = datetime(2024, 3, 1, tzinfo=timezone.utc)
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/page",
        fetched_at=fetched,
        title=None,
        body_snippet="HTML excerpt",
        http_status=200,
    )
    n = normalize_scout_item(
        item,
        source_id=sid,
        source_name="HTTP",
        jurisdiction="UK",
    )
    assert n.body_snippet == "HTML excerpt"
    # 0.35 base + 0.14 body evidence = 0.49; mirrored across both metrics.
    assert n.parser_confidence == pytest.approx(0.49)
    assert n.extraction_quality == pytest.approx(0.49)


def test_normalize_whitespace_only_fields_treated_as_missing() -> None:
    """Whitespace-only title/summary/body score as missing AND persist as None."""

    sid = uuid.uuid4()
    fetched = datetime(2024, 4, 1, tzinfo=timezone.utc)
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/ws",
        fetched_at=fetched,
        title="   ",
        published_at=None,
        summary="\t\n",
        body_snippet="     ",
    )
    n = normalize_scout_item(
        item, source_id=sid, source_name="S", jurisdiction="EU"
    )
    assert n.title is None
    assert n.summary is None
    assert n.body_snippet is None
    # Scoring should see "nothing" → base-only 0.35.
    assert n.parser_confidence == pytest.approx(0.35)


def test_normalize_scrubs_nul_bytes_and_preserves_real_text() -> None:
    """NUL bytes in feeds must never reach JSONB/TEXT persistence boundaries."""

    sid = uuid.uuid4()
    fetched = datetime(2024, 5, 1, tzinfo=timezone.utc)
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/nul",
        fetched_at=fetched,
        title="Label\x00 change",
        published_at=None,
        summary="Short\x00",
        body_snippet="Body\x00text",
    )
    n = normalize_scout_item(
        item, source_id=sid, source_name="S", jurisdiction="EU"
    )
    assert "\x00" not in (n.title or "")
    assert "\x00" not in (n.summary or "")
    assert "\x00" not in (n.body_snippet or "")
    assert n.title == "Label change"
    assert n.summary == "Short"
    assert n.body_snippet == "Bodytext"


def test_normalize_coerces_naive_published_at_to_utc() -> None:
    """Naive ``published_at`` from lax RSS producers is pinned to UTC, not rejected."""

    sid = uuid.uuid4()
    fetched = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    naive_pub = datetime(2024, 6, 1, 9, 0)  # no tzinfo
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/naive",
        fetched_at=fetched,
        title="T",
        published_at=naive_pub,
    )
    n = normalize_scout_item(
        item, source_id=sid, source_name="S", jurisdiction="EU"
    )
    assert n.published_at is not None
    assert n.published_at.tzinfo is not None
    assert n.published_at == naive_pub.replace(tzinfo=timezone.utc)


def test_normalize_keeps_falsy_but_present_http_status() -> None:
    """``http_status=0`` and ``content_type=""`` are distinguishable from absence."""

    sid = uuid.uuid4()
    fetched = datetime(2024, 7, 1, tzinfo=timezone.utc)
    item = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/falsy",
        fetched_at=fetched,
        http_status=0,
        content_type="",
    )
    n = normalize_scout_item(
        item, source_id=sid, source_name="S", jurisdiction="EU"
    )
    assert n.extra_metadata == {"http_status": 0, "content_type": ""}
