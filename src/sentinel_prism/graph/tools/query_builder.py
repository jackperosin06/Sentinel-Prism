"""Build **public-only** web search queries from normalized updates (NFR12).

Callers must **not** pass raw :class:`~sentinel_prism.graph.state.AgentState` or
unfiltered dicts into outbound search APIs. This module only reads an
**allow-listed** subset of keys so tenant metadata, secrets, or analyst notes
that might appear on a normalized row are **never** concatenated into the query.

**Allow-listed keys (public ingest fields):** ``title``, ``summary``,
``body_snippet``, ``item_url``, ``jurisdiction``, ``document_type``.
"""

from __future__ import annotations

from typing import Any, Mapping

# Order matters for readable queries (broad → specific).
_PUBLIC_SEARCH_FIELD_ORDER: tuple[str, ...] = (
    "title",
    "summary",
    "body_snippet",
    "item_url",
    "jurisdiction",
    "document_type",
)

PUBLIC_SEARCH_FIELDS: frozenset[str] = frozenset(_PUBLIC_SEARCH_FIELD_ORDER)

_MAX_QUERY_CHARS = 500


def build_public_web_search_query(normalized: Mapping[str, Any]) -> str:
    """Concatenate allow-listed fields into a single query string.

    Unknown keys on ``normalized`` are **ignored**. Values that are ``None`` or
    blank after ``str(...).strip()`` are skipped. Non-scalar values (``list``,
    ``dict``, ``bytes``, etc.) are also skipped — :py:func:`repr` of a container
    would leak Python syntax / internal structure into the outbound query
    (NFR12), and there is no meaningful text derivation for the search API.
    """

    parts: list[str] = []
    for key in _PUBLIC_SEARCH_FIELD_ORDER:
        val = normalized.get(key)
        if val is None:
            continue
        if not isinstance(val, (str, int, float)) or isinstance(val, bool):
            continue
        s = str(val).strip()
        if s:
            parts.append(s)
    q = " ".join(parts).strip()
    if len(q) > _MAX_QUERY_CHARS:
        return q[:_MAX_QUERY_CHARS]
    return q


def normalized_keys_outside_allowlist(normalized: Mapping[str, Any]) -> frozenset[str]:
    """Keys present on the row that are **not** allow-listed for query text (NFR12).

    Extra keys are **ignored** by :func:`build_public_web_search_query`; this helper
    is for tests and diagnostics.
    """

    return frozenset(k for k in normalized if k not in PUBLIC_SEARCH_FIELDS)
