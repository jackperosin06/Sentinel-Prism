"""DTOs for Scout connector output (Story 2.3 — aligns with Architecture ``raw_items``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
