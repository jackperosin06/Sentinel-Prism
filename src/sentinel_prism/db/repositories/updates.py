"""Normalized update explorer queries (Story 6.2 — FR9, FR31)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ExplorerSort = Literal[
    "created_at_desc",
    "created_at_asc",
    "published_at_desc",
    "published_at_asc",
]

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_MAX_OFFSET = 50_000
_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

_ORDER_SQL: dict[ExplorerSort, str] = {
    "created_at_desc": "wrapped.created_at DESC NULLS LAST, wrapped.id DESC",
    "created_at_asc": "wrapped.created_at ASC NULLS LAST, wrapped.id ASC",
    "published_at_desc": "wrapped.published_at DESC NULLS LAST, wrapped.created_at DESC, wrapped.id DESC",
    "published_at_asc": "wrapped.published_at ASC NULLS LAST, wrapped.created_at ASC, wrapped.id ASC",
}


@dataclass(frozen=True)
class ExplorerListRow:
    id: uuid.UUID
    raw_capture_id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    jurisdiction: str
    title: str | None
    published_at: datetime | None
    item_url: str
    document_type: str
    body_snippet: str | None
    run_id: uuid.UUID | None
    created_at: datetime
    explorer_status: str
    derived_severity: str | None


@dataclass(frozen=True)
class ExplorerListPage:
    items: list[ExplorerListRow]
    total: int
    limit: int
    offset: int
    sort: ExplorerSort
    default_sort: ExplorerSort


_BASE_SELECT = """
SELECT
  n.id AS id,
  n.raw_capture_id AS raw_capture_id,
  n.source_id AS source_id,
  n.source_name AS source_name,
  n.jurisdiction AS jurisdiction,
  n.title AS title,
  n.published_at AS published_at,
  n.item_url AS item_url,
  n.document_type AS document_type,
  n.body_snippet AS body_snippet,
  n.run_id AS run_id,
  n.created_at AS created_at,
  CASE
    WHEN EXISTS (
      SELECT 1 FROM review_queue_items r WHERE r.run_id = n.run_id
    ) THEN 'in_human_review'
    WHEN EXISTS (
      SELECT 1 FROM briefings b WHERE b.run_id = n.run_id
    ) THEN 'briefed'
    ELSE 'processed'
  END AS explorer_status,
  COALESCE(
    (
      SELECT m1.value->>'severity'
      FROM briefings b
      CROSS JOIN LATERAL jsonb_array_elements(
        CASE
          WHEN jsonb_typeof(b.groups) = 'array'
          THEN b.groups
          ELSE '[]'::jsonb
        END
      ) AS g1
      CROSS JOIN LATERAL jsonb_array_elements(
        CASE
          WHEN jsonb_typeof(g1.value->'members') = 'array'
          THEN g1.value->'members'
          ELSE '[]'::jsonb
        END
      ) AS m1
      WHERE b.run_id = n.run_id
        AND NULLIF(trim(m1.value->>'normalized_update_id'), '') IS NOT NULL
        AND CASE
          WHEN (m1.value->>'normalized_update_id') ~* :uuid_re
          THEN (m1.value->>'normalized_update_id')::uuid = n.id
          ELSE FALSE
        END
      LIMIT 1
    ),
    (
      SELECT i.severity::text
      FROM in_app_notifications i
      WHERE i.run_id = n.run_id AND i.item_url = n.item_url
      LIMIT 1
    ),
    (
      SELECT d.severity::text
      FROM notification_digest_queue d
      WHERE d.run_id = n.run_id AND d.item_url = n.item_url
      LIMIT 1
    )
  ) AS derived_severity
FROM normalized_updates n
WHERE
  (:created_from IS NULL OR n.created_at >= :created_from)
  AND (:created_to IS NULL OR n.created_at <= :created_to)
  AND (:published_from IS NULL OR n.published_at >= :published_from)
  AND (:published_to IS NULL OR n.published_at <= :published_to)
  AND (:jurisdiction IS NULL OR n.jurisdiction = :jurisdiction)
  AND (:source_id IS NULL OR n.source_id = :source_id)
  AND (:source_name IS NULL OR n.source_name ILIKE :source_name ESCAPE '\\')
  AND (:document_type IS NULL OR n.document_type = :document_type)
  AND (:explorer_status IS NULL OR
    CASE
      WHEN EXISTS (
        SELECT 1 FROM review_queue_items r WHERE r.run_id = n.run_id
      ) THEN 'in_human_review'
      WHEN EXISTS (
        SELECT 1 FROM briefings b2 WHERE b2.run_id = n.run_id
      ) THEN 'briefed'
      ELSE 'processed'
    END = :explorer_status
  )
"""


def _normalize_severity_filter(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().lower()


def _escape_like(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def fetch_updates_page(
    session: AsyncSession,
    *,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    sort: ExplorerSort = "created_at_desc",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    jurisdiction: str | None = None,
    source_id: uuid.UUID | None = None,
    source_name_contains: str | None = None,
    document_type: str | None = None,
    severity: str | None = None,
    include_unknown_severity: bool = False,
    explorer_status: str | None = None,
) -> ExplorerListPage:
    """Server-side filtered list with derived severity and status.

    **Severity derivation (precedence):** briefing member → in-app notification
    → digest queue row, matched on ``run_id`` + ``normalized_update_id`` /
    ``item_url`` as applicable.

    **Severity filter:** When ``severity`` is set, rows with no derivable
    severity are **excluded** unless ``include_unknown_severity`` is ``True``,
    in which case those rows are **included** in addition to severity matches.
    """

    lim = max(1, min(int(limit), _MAX_LIMIT))
    off = max(0, min(int(offset), _MAX_OFFSET))
    sort_key: ExplorerSort = sort if sort in _ORDER_SQL else "created_at_desc"
    order_clause = _ORDER_SQL[sort_key]

    sev_norm = _normalize_severity_filter(severity)
    source_name = source_name_contains.strip() if source_name_contains else ""
    patt = f"%{_escape_like(source_name)}%" if source_name else None

    inner_params: dict[str, Any] = {
        "created_from": created_from,
        "created_to": created_to,
        "published_from": published_from,
        "published_to": published_to,
        "jurisdiction": jurisdiction,
        "source_id": source_id,
        "source_name": patt,
        "document_type": document_type,
        "explorer_status": explorer_status,
        "uuid_re": _UUID_RE,
    }

    severity_where = "TRUE"
    if sev_norm is not None:
        if include_unknown_severity:
            severity_where = (
                "(wrapped.derived_severity IS NULL OR "
                "lower(wrapped.derived_severity) = :severity_norm)"
            )
        else:
            severity_where = (
                "wrapped.derived_severity IS NOT NULL AND "
                "lower(wrapped.derived_severity) = :severity_norm"
            )

    list_sql = f"""
SELECT wrapped.*
FROM (
  {_BASE_SELECT}
) AS wrapped
WHERE {severity_where}
ORDER BY {order_clause}
LIMIT :lim OFFSET :off
"""

    count_sql = f"""
SELECT count(*)::bigint AS c
FROM (
  {_BASE_SELECT}
) AS wrapped
WHERE {severity_where}
"""

    params: dict[str, Any] = {**inner_params, "lim": lim, "off": off}
    if sev_norm is not None:
        params["severity_norm"] = sev_norm

    count_row = (await session.execute(text(count_sql), params)).mappings().first()
    total = int(count_row["c"]) if count_row else 0

    rows = (await session.execute(text(list_sql), params)).mappings().all()

    items = [
        ExplorerListRow(
            id=r["id"],
            raw_capture_id=r["raw_capture_id"],
            source_id=r["source_id"],
            source_name=r["source_name"],
            jurisdiction=r["jurisdiction"],
            title=r["title"],
            published_at=r["published_at"],
            item_url=r["item_url"],
            document_type=r["document_type"],
            body_snippet=r["body_snippet"],
            run_id=r["run_id"],
            created_at=r["created_at"],
            explorer_status=r["explorer_status"],
            derived_severity=r["derived_severity"],
        )
        for r in rows
    ]

    return ExplorerListPage(
        items=items,
        total=total,
        limit=lim,
        offset=off,
        sort=sort_key,
        default_sort="created_at_desc",
    )


_OVERLAY_SQL = text(
    """
SELECT
  m1.value->>'severity' AS severity,
  m1.value->'impact_categories' AS impact_categories_json,
  CASE
    WHEN (m1.value->>'confidence') ~ '^-?[0-9]+(\\.[0-9]+)?$'
    THEN (m1.value->>'confidence')::float
    ELSE NULL
  END AS confidence
FROM briefings b
CROSS JOIN LATERAL jsonb_array_elements(
  CASE
    WHEN jsonb_typeof(b.groups) = 'array'
    THEN b.groups
    ELSE '[]'::jsonb
  END
) AS g1
CROSS JOIN LATERAL jsonb_array_elements(
  CASE
    WHEN jsonb_typeof(g1.value->'members') = 'array'
    THEN g1.value->'members'
    ELSE '[]'::jsonb
  END
) AS m1
WHERE b.run_id = :run_id
  AND NULLIF(trim(m1.value->>'normalized_update_id'), '') IS NOT NULL
  AND CASE
    WHEN (m1.value->>'normalized_update_id') ~* :uuid_re
    THEN (m1.value->>'normalized_update_id')::uuid = :nid
    ELSE FALSE
  END
LIMIT 1
"""
)


async def fetch_classification_overlay(
    session: AsyncSession,
    *,
    run_id: uuid.UUID | None,
    normalized_update_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Best-effort briefing member overlay; returns ``None`` if not found."""

    if run_id is None:
        return None
    row = (
        await session.execute(
            _OVERLAY_SQL,
            {"run_id": run_id, "nid": normalized_update_id, "uuid_re": _UUID_RE},
        )
    ).mappings().first()
    if row is None:
        return None
    sev = row["severity"]
    raw_cats = row["impact_categories_json"]
    conf = row["confidence"]
    cats: list[str] = []
    if isinstance(raw_cats, list):
        cats = [str(x) for x in raw_cats]
    elif raw_cats is not None:
        if isinstance(raw_cats, str):
            try:
                parsed = json.loads(raw_cats)
                if isinstance(parsed, list):
                    cats = [str(x) for x in parsed]
            except (TypeError, ValueError):
                pass
    if sev is None and not cats and conf is None:
        return None
    return {
        "severity": sev,
        "impact_categories": cats,
        "confidence": conf,
    }
