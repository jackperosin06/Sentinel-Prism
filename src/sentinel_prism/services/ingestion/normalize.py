"""Map ``ScoutRawItem`` → normalized update fields (Story 3.1 — FR8, FR10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sentinel_prism.db.models import NormalizedUpdateRow
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

DOCUMENT_TYPE_UNKNOWN = "unknown"

# Explicit weights for the MVP heuristic. Sum is 0.95 by design: the 1.0 headroom
# is reserved for future non-MVP pipelines (LLM-graded extraction, cross-field
# corroboration, etc.) so an MVP score can never masquerade as "perfect".
_W_BASE = 0.35
_W_TITLE = 0.28
_W_PUBLISHED = 0.18
_W_BODY = 0.14
MVP_CONFIDENCE_MAX = round(_W_BASE + _W_TITLE + _W_PUBLISHED + _W_BODY, 4)  # 0.95


def _clean_text(value: str | None) -> str | None:
    """Scrub content so it is safe for Postgres TEXT / JSONB persistence.

    Postgres TEXT and JSONB string literals both reject ``\\x00``. RSS/HTML feeds
    occasionally carry NUL bytes or lone surrogates (decoded from malformed
    byte streams), which would otherwise raise ``DataError`` at ``session.flush``
    and poison the *entire* poll transaction (dedup fingerprints + metrics +
    every other item in the batch). We strip NULs and re-encode any orphaned
    UTF-16 surrogates to their replacement character. Whitespace-only strings
    collapse to ``None`` so the scoring heuristic and persisted value agree.
    """

    if value is None:
        return None
    scrubbed = value.replace("\x00", "")
    scrubbed = scrubbed.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    if not scrubbed.strip():
        return None
    return scrubbed


def _mvp_confidence_scores(item: ScoutRawItem) -> tuple[float, float]:
    """Heuristic parser confidence and extraction quality in ``[0, 0.95]`` (MVP).

    Weights are explicit (not magic): base credit for a durable URL capture, then
    additive evidence for title, publication time, and text snippets. For MVP both
    metrics mirror the same score; future pipeline steps may diverge them. The
    upper bound is intentionally 0.95 — see ``MVP_CONFIDENCE_MAX``.
    """

    score = _W_BASE
    if item.title and item.title.strip():
        score += _W_TITLE
    if item.published_at is not None:
        score += _W_PUBLISHED
    if (item.summary and item.summary.strip()) or (
        item.body_snippet and item.body_snippet.strip()
    ):
        score += _W_BODY
    capped = round(score, 4)
    return capped, capped


def _tz_aware_or_none(value: datetime | None) -> datetime | None:
    """Coerce a naive datetime to UTC; leave aware datetimes untouched.

    ``NormalizedUpdateRow.published_at`` is ``DateTime(timezone=True)``; asyncpg
    refuses to bind naive datetimes to a tz-aware column (``DataError``). Feeds
    with naive ``published_at`` (rare but present in lax RSS producers) would
    otherwise crash the whole poll. We pin them to UTC with an audit-log note;
    the raw capture payload still preserves the exact original isoformat.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class NormalizedUpdate:
    """Canonical normalized update before ORM persistence (internal domain shape)."""

    source_id: UUID
    source_name: str
    jurisdiction: str
    item_url: str
    title: str | None
    published_at: datetime | None
    document_type: str
    body_snippet: str | None
    summary: str | None
    extra_metadata: dict[str, Any] | None
    parser_confidence: float | None
    extraction_quality: float | None


def normalized_update_to_state_dict(n: NormalizedUpdate) -> dict[str, Any]:
    """Checkpoint-safe dict (ISO datetimes, string UUID) for ``AgentState.normalized_updates``."""

    return {
        "source_id": str(n.source_id),
        "source_name": n.source_name,
        "jurisdiction": n.jurisdiction,
        "item_url": n.item_url,
        "title": n.title,
        "published_at": n.published_at.isoformat() if n.published_at is not None else None,
        "document_type": n.document_type,
        "body_snippet": n.body_snippet,
        "summary": n.summary,
        "extra_metadata": n.extra_metadata,
        "parser_confidence": n.parser_confidence,
        "extraction_quality": n.extraction_quality,
    }


def normalized_update_orm_to_pipeline_state_dict(row: NormalizedUpdateRow) -> dict[str, Any]:
    """``AgentState.normalized_updates`` shape for a persisted row (post-poll pipeline)."""

    r = row
    d = normalized_update_to_state_dict(
        NormalizedUpdate(
            source_id=r.source_id,
            source_name=r.source_name,
            jurisdiction=r.jurisdiction,
            item_url=r.item_url,
            title=r.title,
            published_at=r.published_at,
            document_type=r.document_type,
            body_snippet=r.body_snippet,
            summary=r.summary,
            extra_metadata=r.extra_metadata,
            parser_confidence=r.parser_confidence,
            extraction_quality=r.extraction_quality,
        )
    )
    d["normalized_update_id"] = str(r.id)
    return d


def normalize_scout_item(
    item: ScoutRawItem,
    *,
    source_id: UUID,
    source_name: str,
    jurisdiction: str,
) -> NormalizedUpdate:
    """Fill PRD FR8 fields from RSS/HTTP-shaped scout data; FR10 via :func:`_mvp_confidence_scores`."""

    # Gate every optional field uniformly on ``is not None`` so falsy-but-present
    # values (e.g. ``http_status=0``, ``content_type=""``) are recorded as-is
    # rather than silently dropped. An all-absent metadata block collapses to
    # ``None`` so JSONB never stores ``{}``.
    meta: dict[str, Any] = {}
    if item.http_status is not None:
        meta["http_status"] = item.http_status
    if item.content_type is not None:
        meta["content_type"] = item.content_type

    pc, eq = _mvp_confidence_scores(item)

    return NormalizedUpdate(
        source_id=source_id,
        source_name=source_name,
        jurisdiction=jurisdiction,
        item_url=item.item_url,
        title=_clean_text(item.title),
        published_at=_tz_aware_or_none(item.published_at),
        document_type=DOCUMENT_TYPE_UNKNOWN,
        body_snippet=_clean_text(item.body_snippet),
        summary=_clean_text(item.summary),
        extra_metadata=meta or None,
        parser_confidence=pc,
        extraction_quality=eq,
    )
