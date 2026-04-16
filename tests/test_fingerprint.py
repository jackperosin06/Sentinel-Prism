"""Content fingerprint helpers (Story 2.4)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sentinel_prism.services.connectors.fingerprint import (
    content_fingerprint_for_item,
    normalize_item_url,
)
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem


def test_normalize_item_url_strips_fragment_and_lowercases_host() -> None:
    assert (
        normalize_item_url("HTTPS://Example.COM/path?x=1#frag")
        == "https://example.com/path?x=1"
    )


def test_content_fingerprint_stable_ignores_fetched_at() -> None:
    sid = uuid.uuid4()
    t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2025, 6, 1, tzinfo=timezone.utc)
    a = ScoutRawItem(
        source_id=sid,
        item_url="https://Reg.EXAMPLE/a",
        fetched_at=t1,
        title="T",
        summary="S",
        published_at=None,
    )
    b = ScoutRawItem(
        source_id=sid,
        item_url="https://reg.example/a",
        fetched_at=t2,
        title="T",
        summary="S",
        published_at=None,
    )
    assert content_fingerprint_for_item(a) == content_fingerprint_for_item(b)


def test_content_fingerprint_differs_when_body_changes() -> None:
    sid = uuid.uuid4()
    ft = datetime.now(timezone.utc)
    a = ScoutRawItem(
        source_id=sid,
        item_url="https://ex/x",
        fetched_at=ft,
        body_snippet="one",
        http_status=200,
        content_type="text/plain",
    )
    b = ScoutRawItem(
        source_id=sid,
        item_url="https://ex/x",
        fetched_at=ft,
        body_snippet="two",
        http_status=200,
        content_type="text/plain",
    )
    assert content_fingerprint_for_item(a) != content_fingerprint_for_item(b)


def test_content_fingerprint_ignores_http_status_and_content_type() -> None:
    """D1 decision: volatile server metadata must not affect document identity."""
    sid = uuid.uuid4()
    ft = datetime.now(timezone.utc)
    base = ScoutRawItem(
        source_id=sid,
        item_url="https://ex/y",
        fetched_at=ft,
        title="T",
        body_snippet="body",
        http_status=200,
        content_type="text/html",
    )
    changed_meta = ScoutRawItem(
        source_id=sid,
        item_url="https://ex/y",
        fetched_at=ft,
        title="T",
        body_snippet="body",
        http_status=206,
        content_type="application/xhtml+xml",
    )
    assert content_fingerprint_for_item(base) == content_fingerprint_for_item(changed_meta)


def test_normalize_item_url_empty_string_is_safe() -> None:
    assert normalize_item_url("") == ""
    assert normalize_item_url("   ") == ""
