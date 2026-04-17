"""DTOs for Scout connector output (Story 2.3 — aligns with Architecture ``raw_items``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ScoutRawItem:
    """One fetched entry or HTTP capture before normalization (Epic 3).

    ``item_url`` is a stable identifier for dedup: prefer the feed/link URL; when a feed
    entry has no link, use ``urn:sentinel-prism:feed-item:{source_id}:{index}`` (see
    ``rss_fetch``).

    ``fetched_at`` **must** be timezone-aware (``datetime.now(timezone.utc)``).
    """

    source_id: UUID
    item_url: str
    fetched_at: datetime
    title: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    body_snippet: str | None = None

    def __post_init__(self) -> None:
        if self.fetched_at.tzinfo is None:
            raise ValueError(
                "ScoutRawItem.fetched_at must be timezone-aware; "
                "use datetime.now(timezone.utc)"
            )


def scout_raw_item_from_payload(data: dict[str, Any]) -> ScoutRawItem:
    """Rebuild :class:`ScoutRawItem` from :func:`scout_raw_item_payload` / JSONB output."""

    sid = data["source_id"]
    # Narrow the input to the two shapes produced by scout_raw_item_payload (str)
    # and by in-process round-trips (UUID). Anything else (int, dict, bytes, …)
    # would otherwise fall through to ScoutRawItem's frozen dataclass and fail
    # with a cryptic downstream error far from the decode site.
    if isinstance(sid, str):
        source_id = UUID(sid)
    elif isinstance(sid, UUID):
        source_id = sid
    else:
        raise TypeError(
            f"source_id must be str or UUID, got {type(sid).__name__}"
        )
    fetched_raw = data["fetched_at"]
    if isinstance(fetched_raw, datetime):
        fetched_at = fetched_raw
    else:
        fetched_at = datetime.fromisoformat(str(fetched_raw))
    pub = data.get("published_at")
    published_at: datetime | None = None
    if pub is not None:
        published_at = (
            datetime.fromisoformat(str(pub)) if isinstance(pub, str) else pub
        )
    return ScoutRawItem(
        source_id=source_id,
        item_url=data["item_url"],
        fetched_at=fetched_at,
        title=data.get("title"),
        published_at=published_at,
        summary=data.get("summary"),
        http_status=data.get("http_status"),
        content_type=data.get("content_type"),
        body_snippet=data.get("body_snippet"),
    )
