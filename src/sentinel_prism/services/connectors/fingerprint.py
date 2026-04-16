"""Stable content fingerprints for ingestion dedup (Story 2.4 — FR3)."""

from __future__ import annotations

import hashlib
from urllib.parse import urldefrag, urlsplit, urlunsplit

from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem


def normalize_item_url(url: str) -> str:
    """Normalize URL for fingerprinting.

    - Strips surrounding whitespace and URL fragments (``#...``).
    - For ``http``/``https``: lowercases scheme and host; preserves path and query.
    - For other schemes (e.g. synthetic ``urn:`` feed placeholders): strips fragment only.
    """

    u = url.strip()
    if not u:
        return u
    u, _frag = urldefrag(u)
    parts = urlsplit(u)
    if parts.scheme in ("http", "https"):
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        return urlunsplit((scheme, netloc, parts.path, parts.query, ""))
    return u


def content_fingerprint_for_item(item: ScoutRawItem) -> str:
    """Return a hex SHA-256 fingerprint stable across polls for the same document.

    Canonical rule (AC1): hash is computed over document-identity fields only.

    Included fields (UTF-8, joined with ``\\0``):
      1. Normalized ``item_url`` — fragment stripped, host lowercased
      2. ``title`` or empty — document heading
      3. ``summary`` or empty — body digest / description
      4. ``body_snippet`` or empty — bounded page text for HTTP sources
      5. ``published_at`` ISO-8601 if set, else empty — publication timestamp

    Deliberately excluded:
      - ``fetched_at`` — poll timestamp; volatile per fetch
      - ``source_id`` — same document may come from multiple sources in future
      - ``http_status`` — server-response metadata; a temporary 206/304 must not
        produce a new fingerprint for the same document (decision D1, 2026-04-16)
      - ``content_type`` — negotiation artifact; can shift without content change
    """

    nu = normalize_item_url(item.item_url)
    pub = item.published_at.isoformat() if item.published_at else ""
    parts = [
        nu,
        item.title or "",
        item.summary or "",
        item.body_snippet or "",
        pub,
    ]
    blob = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
