"""Source fallback field validation and PATCH merge logic (Story 2.5)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sentinel_prism.api.routes import sources as sources_routes
from sentinel_prism.api.routes.sources import SourceCreate, SourceUpdate, patch_source
from sentinel_prism.db.models import FallbackMode, SourceType


def test_source_create_rejects_fallback_url_when_mode_none() -> None:
    with pytest.raises(ValidationError):
        SourceCreate(
            name="n",
            jurisdiction="j",
            source_type=SourceType.RSS,
            primary_url="https://a.com/f.xml",
            schedule="0 * * * *",
            fallback_mode=FallbackMode.NONE,
            fallback_url="https://b.com/x",
        )


def test_source_create_accepts_html_fallback() -> None:
    b = SourceCreate(
        name="n",
        jurisdiction="j",
        source_type=SourceType.RSS,
        primary_url="https://a.com/f.xml",
        schedule="0 * * * *",
        fallback_mode=FallbackMode.HTML_PAGE,
        fallback_url="https://b.com/page",
    )
    assert b.fallback_url == "https://b.com/page"
    assert b.fallback_mode == FallbackMode.HTML_PAGE


def test_source_create_rejects_primary_url_with_whitespace_or_control_chars() -> None:
    for bad in (
        "https://a.com/feed\n",
        "https://a.com/feed\r\nX-Injected: 1",
        " https://a.com/feed",
        "https://a.com/feed\tfoo",
    ):
        with pytest.raises(ValidationError):
            SourceCreate(
                name="n",
                jurisdiction="j",
                source_type=SourceType.RSS,
                primary_url=bad,
                schedule="0 * * * *",
            )


def test_source_create_url_error_message_names_the_field() -> None:
    """Message must identify which field is invalid (primary_url vs fallback_url)."""

    with pytest.raises(ValidationError) as ei:
        SourceCreate(
            name="n",
            jurisdiction="j",
            source_type=SourceType.RSS,
            primary_url="not-a-url",
            schedule="0 * * * *",
        )
    assert "primary_url" in str(ei.value)

    with pytest.raises(ValidationError) as ei:
        SourceCreate(
            name="n",
            jurisdiction="j",
            source_type=SourceType.RSS,
            primary_url="https://a.com/f",
            schedule="0 * * * *",
            fallback_mode=FallbackMode.HTML_PAGE,
            fallback_url="not-a-url",
        )
    assert "fallback_url" in str(ei.value)


# ---------------------------------------------------------------------------
# PATCH route merge-logic tests (unit-level — no HTTP, no real DB)
# ---------------------------------------------------------------------------


def _existing_row(
    *,
    fallback_mode: FallbackMode = FallbackMode.NONE,
    fallback_url: str | None = None,
) -> SimpleNamespace:
    """Minimal stand-in for a ``Source`` ORM row."""

    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="n",
        jurisdiction="j",
        source_type=SourceType.RSS,
        primary_url="https://a.com/f.xml",
        schedule="0 * * * *",
        fallback_url=fallback_url,
        fallback_mode=fallback_mode,
        enabled=True,
        extra_metadata=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def patched_repo(monkeypatch: pytest.MonkeyPatch):
    """Stub repository + scheduler so patch_source can be called directly."""

    state: dict[str, object] = {"existing": None, "update_calls": []}

    async def _get(_db: object, _sid: uuid.UUID):
        return state["existing"]

    async def _update(_db: object, sid: uuid.UUID, data: dict):
        state["update_calls"].append({"sid": sid, "data": dict(data)})
        existing = state["existing"]
        for k, v in data.items():
            setattr(existing, k, v)
        return existing

    monkeypatch.setattr(sources_routes.sources_repo, "get_source_by_id", _get)
    monkeypatch.setattr(sources_routes.sources_repo, "update_source_fields", _update)

    class _Sched:
        async def refresh_jobs_for_source(self, _db: object, _sid: uuid.UUID) -> None:
            return None

    monkeypatch.setattr(sources_routes, "get_poll_scheduler", lambda: _Sched())

    return state


async def _call_patch(db: object, sid: uuid.UUID, body: SourceUpdate):
    return await patch_source(db=db, source_id=sid, body=body)


@pytest.mark.asyncio
async def test_patch_set_mode_none_clears_stored_fallback_url(patched_repo: dict):
    existing = _existing_row(
        fallback_mode=FallbackMode.HTML_PAGE,
        fallback_url="https://b.com/page",
    )
    patched_repo["existing"] = existing
    db = MagicMock()
    db.commit = AsyncMock()

    resp = await _call_patch(
        db, existing.id, SourceUpdate(fallback_mode=FallbackMode.NONE)
    )
    assert resp.fallback_mode == FallbackMode.NONE
    assert resp.fallback_url is None
    assert patched_repo["update_calls"][-1]["data"]["fallback_url"] is None


@pytest.mark.asyncio
async def test_patch_rejects_mode_none_with_non_null_url(patched_repo: dict):
    existing = _existing_row(
        fallback_mode=FallbackMode.HTML_PAGE, fallback_url="https://b.com/page"
    )
    patched_repo["existing"] = existing
    db = MagicMock()
    db.commit = AsyncMock()

    with pytest.raises(HTTPException) as ei:
        await _call_patch(
            db,
            existing.id,
            SourceUpdate(
                fallback_mode=FallbackMode.NONE, fallback_url="https://still/present"
            ),
        )
    assert ei.value.status_code == 422
    assert "fallback_url must be omitted" in ei.value.detail


@pytest.mark.asyncio
async def test_patch_rejects_null_fallback_mode(patched_repo: dict):
    existing = _existing_row(
        fallback_mode=FallbackMode.HTML_PAGE, fallback_url="https://b.com/page"
    )
    patched_repo["existing"] = existing
    db = MagicMock()
    db.commit = AsyncMock()

    # Construct the body directly from a dict so ``exclude_unset`` keeps the null.
    body = SourceUpdate.model_validate({"fallback_mode": None})
    with pytest.raises(HTTPException) as ei:
        await _call_patch(db, existing.id, body)
    assert ei.value.status_code == 422
    assert "fallback_mode cannot be null" in ei.value.detail


@pytest.mark.asyncio
async def test_patch_rejects_null_fallback_url_without_clearing_mode(
    patched_repo: dict,
):
    existing = _existing_row(
        fallback_mode=FallbackMode.HTML_PAGE, fallback_url="https://b.com/page"
    )
    patched_repo["existing"] = existing
    db = MagicMock()
    db.commit = AsyncMock()

    body = SourceUpdate.model_validate({"fallback_url": None})
    with pytest.raises(HTTPException) as ei:
        await _call_patch(db, existing.id, body)
    assert ei.value.status_code == 422
    assert "also set fallback_mode to none" in ei.value.detail


@pytest.mark.asyncio
async def test_patch_switches_mode_to_html_with_url_in_same_call(
    patched_repo: dict,
):
    existing = _existing_row(fallback_mode=FallbackMode.NONE, fallback_url=None)
    patched_repo["existing"] = existing
    db = MagicMock()
    db.commit = AsyncMock()

    resp = await _call_patch(
        db,
        existing.id,
        SourceUpdate(
            fallback_mode=FallbackMode.HTML_PAGE, fallback_url="https://b.com/page"
        ),
    )
    assert resp.fallback_mode == FallbackMode.HTML_PAGE
    assert resp.fallback_url == "https://b.com/page"


@pytest.mark.asyncio
async def test_patch_rejects_mode_html_without_url_when_existing_has_none(
    patched_repo: dict,
):
    existing = _existing_row(fallback_mode=FallbackMode.NONE, fallback_url=None)
    patched_repo["existing"] = existing
    db = MagicMock()
    db.commit = AsyncMock()

    with pytest.raises(HTTPException) as ei:
        await _call_patch(
            db, existing.id, SourceUpdate(fallback_mode=FallbackMode.HTML_PAGE)
        )
    assert ei.value.status_code == 422
    assert "fallback_url is required" in ei.value.detail
