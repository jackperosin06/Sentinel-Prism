# Story 2.4: Deduplication and retry with backoff

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **to dedupe by fingerprint and retry transient failures**,
so that **we avoid duplicate processing and survive flaky sources** (**FR3**, **FR4**).

## Acceptance Criteria

1. **Fingerprint & contract**  
   **Given** a `ScoutRawItem` produced by the RSS or HTTP connector   **When** it is considered for ingestion  
   **Then** a **stable `content_fingerprint`** is computed per item such that the same logical document yields the same fingerprint across polls (align with **FR3**: URL + content hash or equivalent)  
   **And** the canonical rule is **documented in code** (e.g. hash of normalized URL + stable content bytes — title/summary/body fields that exist for that item type; avoid hashing volatile fields like `fetched_at`).

2. **Persistence for dedup**  
   **Given** no row exists for `(source_id, content_fingerprint)`  
   **When** an item passes the connector and is treated as **new**  
   **Then** a new record is written so future polls can recognize duplicates  
   **And** the schema enforces **uniqueness** on `(source_id, content_fingerprint)` (matches Architecture: idempotent keys **[Source: `architecture.md` §4 Data]**.

3. **Duplicate suppression**  
   **Given** two polls that yield items with the **same** URL + content (same fingerprint)  
   **When** `execute_poll` completes  
   **Then** only **one** logical new item is surfaced per fingerprint over time (downstream callers see duplicates filtered; **scheduler** and **manual** paths behave the same).

4. **Retry with backoff (transient fetch failures)**  
   **Given** a **transient** failure (e.g. timeout, connection error, HTTP **429**, **502**, **503**, **504** — align with Story 2.3’s “transient” notion)  
   **When** the connector performs a fetch  
   **Then** the implementation **retries** with **exponential backoff** and a **bounded** max attempts / total time (constants documented; no infinite loops)  
   **And** each failed attempt logs **structured** context: `source_id`, `trigger`, URL host/path, attempt number, `error_class`, and **failure reason** (`str(exc)` or response snippet where safe).

5. **Failure reason recorded**  
   **Given** a fetch ultimately **fails** after retries  
   **When** the poll ends without usable items  
   **Then** the **failure reason** is still observable beyond logs — e.g. persist on `Source` (recommended: `extra_metadata` JSON patch or dedicated columns if you prefer stronger typing) with **timestamp** and **last error summary** so operators can inspect without log grep (**FR4**).

6. **Non-transient errors**  
   **Given** failures that should **not** spam retries (e.g. DNS resolution failure after first attempt, **401**, **403**, **404** if you classify them as non-retryable)  
   **When** the connector runs  
   **Then** behavior is **defined and tested** (no unnecessary retry storm); document the classification table in `Dev Notes`.

7. **Regressions & boundaries**  
   **Given** Story 2.3 behavior (session usage, structured logs, `poll_completed` only on success, size caps, `feedparser` off thread)  
   **When** this story lands  
   **Then** existing connector tests remain green; **no imports** from `graph/` in `services/connectors/` **[Source: `architecture.md` §6]**.

8. **Tests**  
   **Given** CI without live network  
   **When** tests run  
   **Then** there is coverage for: fingerprint helper(s); **INSERT** dedup row + second poll skips duplicate; retry path (e.g. mock **503** then **200**); classification of at least one non-retryable path. DB-dependent tests follow existing **skip-if-no-`DATABASE_URL`** patterns.

## Tasks / Subtasks

- [x] **Design fingerprint** (AC: #1, #7)
  - [x] Add pure function(s) e.g. `fingerprint_for_item(item: ScoutRawItem) -> str` (or bytes) under `services/connectors/` with docstring specifying normalization (URL canonicalization, which fields hashed, encoding).
  - [x] Extend `ScoutRawItem` **only if needed** (e.g. optional `content_fingerprint` precomputed vs lazy) — prefer minimal DTO churn for Epic 3.

- [x] **Schema + migration** (AC: #2)
  - [x] New SQLAlchemy model + Alembic revision (follow existing migration style in `alembic/versions/`).
  - [x] `UNIQUE (source_id, fingerprint)`; index suitable for “exists?” checks; consider `item_url` column nullable for debugging only (not part of uniqueness if URL is already inside fingerprint).

- [x] **Repository / data access** (AC: #2, #3)
  - [x] Async repository helpers: `try_insert_fingerprint(...)`, or `filter_new_items(session, source_id, items) -> list[ScoutRawItem]` using **one round-trip** where practical (`INSERT ... ON CONFLICT DO NOTHING` returning inserted keys, or SELECT + INSERT in transaction).
  - [x] Integrate into `execute_poll` **after** successful fetch, **before** `poll_completed`: filter items, persist new fingerprints in the **same** DB session strategy as the rest of the app (avoid DetachedInstanceError patterns from Story 2.3).

- [x] **Retry helper** (AC: #4, #6)
  - [x] Implement small async retry wrapper (prefer **stdlib** `asyncio.sleep` loop — **no new dependency** unless you justify e.g. `tenacity` in `requirements.txt`).
  - [x] Apply at the **HTTP layer** (`http_client` / fetchers) or **once** around `fetch_rss_items` / `fetch_http_page_item` — avoid duplicating retry logic in two places.

- [x] **Failure persistence** (AC: #5)
  - [x] On final failure after retries, update `Source` state via `sources_repo` (patch `extra_metadata` or add columns) + structured log; ensure **manual** and **scheduled** paths both update consistently.

- [x] **Tests & docs** (AC: #8)
  - [x] New or extended test module(s); keep RSS/HTTP mocks consistent with `tests/test_connectors_rss_http.py`.
  - [x] README or dev note: fingerprint semantics + retry policy summary (one short paragraph).

### Review Findings

- [x] [Review][Decision] **`http_status` / `content_type` in fingerprint: volatile server metadata vs. document identity** — AC1 requires "stable content bytes — title/summary/body fields; avoid hashing volatile fields." `http_status` can shift (e.g. server temporarily returns 206 or 304), and `content_type` can vary by negotiation. Including them in the hash means the same document re-ingests when these change. Options: (a) remove both from hash; (b) keep `http_status` only for HTTP sources as a health proxy; (c) keep as-is with explicit justification. [`src/sentinel_prism/services/connectors/fingerprint.py`]
- [x] [Review][Decision] **DNS failures (`httpx.ConnectError`) classified as retryable — contradicts AC6** — AC6 explicitly lists "DNS resolution failure" as a case that must NOT trigger retry storm. `ConnectError` is the exception raised for DNS failures and is currently in the transient-retry bucket (up to 4 attempts). Options: (a) leave as-is, accepting DNS retries for simplicity; (b) inspect exception message/type to separate DNS from transient connect failures; (c) classify ALL `ConnectError` as non-retryable. [`src/sentinel_prism/services/connectors/fetch_retry.py`]
- [x] [Review][Patch] **HTTP 500 not in `RETRYABLE_HTTP_STATUSES` — fails immediately with zero retries** — `RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}`. Status 500 is in neither set; `http_fetch` calls `raise_for_status()` for ≥500 producing `HTTPStatusError`, which hits the "not in RETRYABLE" branch and raises `ConnectorFetchFailed` after attempt 1. 500 is a transient server overload code. Fix: add 500 to `RETRYABLE_HTTP_STATUSES`. [`src/sentinel_prism/services/connectors/fetch_retry.py:28-30`]
- [x] [Review][Patch] **`assert last_exc is not None` is stripped under Python `-O`/`-OO`** — Production containers commonly run optimised Python. Replace with `if last_exc is None: raise RuntimeError(...)`. [`src/sentinel_prism/services/connectors/fetch_retry.py:155`]
- [x] [Review][Patch] **Redundant `index=True` on `source_id` FK alongside unique constraint** — The `UNIQUE (source_id, fingerprint)` constraint already creates a covering index usable for prefix scans on `source_id`. The extra B-tree index adds write overhead on every insert. Remove `index=True`. [`src/sentinel_prism/db/models.py:127`]
- [x] [Review][Patch] **Inconsistent 4xx semantics between RSS and HTTP connectors** — `rss_fetch._one_fetch` calls `resp.raise_for_status()` unconditionally (401 → `ConnectorFetchFailed` + `record_poll_failure`). `http_fetch._one_fetch` only raises for ≥500 (401 → `ScoutRawItem`). Same operator mistake produces entirely different system behaviour. Decide one policy and apply it consistently. [`src/sentinel_prism/services/connectors/http_fetch.py:47`, `rss_fetch.py:91`]
- [x] [Review][Patch] **HTTP body truncation is silent — no log event** — `rss_fetch` logs `rss_body_truncated`; `http_fetch` silently breaks the loop. Add a `logger.warning("http_body_truncated", ...)` equivalent. [`src/sentinel_prism/services/connectors/http_fetch.py:57-60`]
- [x] [Review][Patch] **`clear_poll_failure` sets `extra_metadata = meta or None`, destroying unrelated keys** — After deleting `last_poll_failure`, `meta or None` nulls the entire column if no other key exists, but any *other* key present is preserved (safe). However if a future subsystem writes its own key while last_poll_failure is also present, clearing poll failure will not zero out those other keys but if meta ends up empty it becomes `None` — silently discarding `{}` which is fine. Actual bug: `meta or None` evaluates an empty dict as falsy and sets the column to `None` when it was `{}` before failure was added — correct. On second review this is safe but fragile. Fix: use explicit `row.extra_metadata = meta if meta else None` to signal intent. [`src/sentinel_prism/db/repositories/sources.py:128-130`]
- [x] [Review][Patch] **`client.aclose()` in `finally` can mask `ConnectorFetchFailed` with a secondary exception** — If `aclose()` raises, the original exception is lost. Wrap in `try/except Exception: pass` (or log and swallow). [`src/sentinel_prism/services/connectors/http_fetch.py:78`, `rss_fetch.py:118`]
- [x] [Review][Patch] **`feedparser.parse` exception in thread not caught — propagates as bare `Exception` to `execute_poll`** — `asyncio.to_thread(feedparser.parse, ...)` exceptions bubble out of `fetch_rss_items` and are caught by `execute_poll`'s bare `except Exception` — which calls `record_poll_failure`, which requires a live DB session. This is a new session open, so it is handled, but the error classification is wrong (misattributed as a network error). Wrap the `to_thread` call in a try/except and raise `ConnectorFetchFailed` explicitly. [`src/sentinel_prism/services/connectors/rss_fetch.py:123`]
- [x] [Review][Patch] **`content_fingerprint_for_item` exception in dedup loop aborts entire batch** — If fingerprinting raises for one item (e.g. malformed URL), `register_new_items` raises and no items are registered. Wrap per-item fingerprint computation in a try/except and skip the offending item with a warning. [`src/sentinel_prism/db/repositories/ingestion_dedup.py:33-38`]
- [x] [Review][Patch] **`error_class` not truncated — JSONB insertion risk on pathological exception names** — `reason` is capped at 4000 chars; `error_class` has no cap. Add `error_class[:255]`. [`src/sentinel_prism/db/repositories/sources.py:105`]
- [x] [Review][Patch] **Unsupported `source_type` returns `[]` without calling `record_poll_failure`** — An unknown type silently succeeds (empty result, no failure recorded, no `poll_completed`). Should call `record_poll_failure` or at minimum assert/raise so operators notice misconfigured sources. [`src/sentinel_prism/services/connectors/poll.py:83-92`]
- [x] [Review][Patch] **`clear_poll_failure` flush + `register_new_items` failure leaves poll failure un-cleared atomically** — If `register_new_items` raises after `clear_poll_failure` flushes (but before commit), the session context manager rolls both back — correct. But `clear_poll_failure` doing a flush creates a partial state within the transaction. Consider removing the intermediate `flush()` from `clear_poll_failure` and committing once at the end. [`src/sentinel_prism/services/connectors/poll.py:140-143`]
- [x] [Review][Patch] **`normalize_item_url` lacks empty-string guard** — An empty URL after `strip()` will produce an empty fingerprint prefix, causing all empty-URL items to hash identically. Add `if not u: return u` guard. [`src/sentinel_prism/services/connectors/fingerprint.py:29`]
- [x] [Review][Patch] **Fingerprint algorithm canonical rule not documented per AC1 spec requirement** — AC1 explicitly requires the canonical rule to be documented in code. The docstring names the fields but does not state *why* each is included or excluded. Add an explicit comment: "Volatile fields (`fetched_at`, `source_id`) are excluded; URL fragments are stripped to preserve identity across anchor changes." [`src/sentinel_prism/services/connectors/fingerprint.py:docstring`]
- [x] [Review][Patch] **No inline retry/non-retry classification table for full error taxonomy per AC6** — `RETRYABLE_HTTP_STATUSES` and `NON_RETRYABLE_HTTP_STATUSES` list status codes, but there is no comment documenting how `httpx` exception types (Timeout, ConnectError, etc.) are classified. AC6 requires documentation. Add a comment block at the top of `fetch_retry.py`. [`src/sentinel_prism/services/connectors/fetch_retry.py:27-31`]
- [x] [Review][Patch] **No test verifying `record_poll_failure` is called when retries are exhausted** — AC5 and AC8 require the failure reason to be persisted. The retry tests (`503×4→fail`) stop at the exception; no test asserts that `extra_metadata["last_poll_failure"]` is set after `execute_poll` handles the failure. [`tests/test_connectors_rss_http.py`]
- [x] [Review][Defer] **TOCTOU: `enabled` flag re-read is not atomic with dedup commit** [`src/sentinel_prism/services/connectors/poll.py`] — deferred, pre-existing from 2.2; acknowledged in earlier story reviews
- [x] [Review][Defer] **Poll failure stored in JSONB blob: no query index, no failure history** [`src/sentinel_prism/db/repositories/sources.py`] — deferred, Story 2.6 metrics work will add proper observability
- [x] [Review][Defer] **`Retry-After` header ignored on 429 responses** [`src/sentinel_prism/services/connectors/fetch_retry.py`] — deferred, enhancement; current backoff is safe for MVP
- [x] [Review][Defer] **Bozo RSS feed continues processing partial feedparser output** [`src/sentinel_prism/services/connectors/rss_fetch.py`] — deferred, acceptable MVP behaviour; hard-fail option can be added later
- [x] [Review][Defer] **`IntegrityError` on non-fingerprint unique constraint leaves session in broken state** [`src/sentinel_prism/db/repositories/ingestion_dedup.py`] — deferred, defensive concern; unlikely given current schema

## Dev Notes

### Epic 2 context

- **Goal:** Direct connectors with **dedup**, **retry**, **fallback** (2.5), **metrics** (2.6) [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2].
- **Predecessors:** **2.3** delivers `ScoutRawItem`, `execute_poll`, RSS/HTTP fetchers; **2.2** invokes `execute_poll` from APScheduler and manual route.
- **Follow-on:** **2.5** fallback URLs; **Epic 3** persists full **raw captures** — this story’s dedup ledger should remain a **lightweight idempotency index**, not a replacement for FR7 raw storage.

### Developer context (guardrails)

- **Single entrypoint:** Keep **`execute_poll(source_id, *, trigger)`** as the only public poll API; dedup and retry live **under** it or in shared fetch helpers [Source: `2-3-rss-http-connector-implementation-direct-path.md`].
- **Logging:** Preserve Story 2.3 semantics: **`poll_completed`** only after a **successful** fetch path; errors must not emit success-shaped metrics.
- **RSS index URNs:** `urn:sentinel-prism:feed-item:{source_id}:{index}` is **not** stable across polls — fingerprint must incorporate **content** (and/or stable link when present) so duplicates are detected when the feed order shifts [Source: deferred item in Story 2.3 review].
- **TOCTOU:** Scheduled job may fire around enable/disable toggles; re-check `enabled` inside `execute_poll` (already) — dedup writes must not resurrect disabled-source behavior incorrectly.

### Technical requirements

- **PostgreSQL** is system of record for registry; new dedup table is appropriate for **FR3** “previously ingested fingerprints” even before Epic 3 raw blob storage.
- **Hash:** Use **`hashlib.sha256`** (hex or base64) for fingerprints; do not rely on Python’s `hash()` (non-stable across processes).
- **Backoff:** Suggest defaults in code (e.g. base delay0.5–1s, multiplier 2, max delay 30–60s, max attempts 3–5) — tune for MVP, document clearly.

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Idempotent keys | `(source_id, content_fingerprint)` [Source: `architecture.md` §4 Data] |
| Location | `services/connectors/`, `db/models.py`, `db/repositories/`, `alembic/versions/` |
| Boundaries | Services **must not** import `graph/` [Source: `architecture.md` §6] |
| Scout | Dedup belongs with connector/scout path before full LangGraph persistence [Source: `architecture.md` §3.3, §6 table] |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **SQLAlchemy / Alembic / asyncpg** | Existing — new model + migration |
| **httpx** | Existing — classify exceptions vs status codes for retry |
| **pytest / pytest-asyncio** | Existing — async tests for poll + DB |

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/db/models.py` | New dedup model |
| `src/sentinel_prism/db/repositories/` | New or extended repository for fingerprints / source failure fields |
| `src/sentinel_prism/services/connectors/poll.py` | Orchestrate dedup after fetch |
| `src/sentinel_prism/services/connectors/` | `fingerprint.py` (or similar), retry helper if not colocated in `http_client.py` |
| `alembic/versions/` | New revision |
| `tests/` | Fingerprint, dedup, retry cases |

### Testing requirements

- **No live network** in default CI — reuse `httpx.MockTransport` patterns from Story 2.3.
- **DB tests:** Skip when `DATABASE_URL` unset, consistent with existing suite.
- **Concurrency (optional stretch):** Two parallel polls inserting same fingerprint should converge via DB uniqueness — document outcome (one wins, one conflict).

### UX / product notes

- **No** new UI; operators benefit from persisted **last failure** on `Source` / metadata for future admin surfaces (**2.6** metrics may consume later).

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2, Story 2.4]
- [Source: `_bmad-output/planning-artifacts/prd.md` — FR3, FR4]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §4 Data, §6 structure & boundaries]
- [Source: `_bmad-output/implementation-artifacts/2-3-rss-http-connector-implementation-direct-path.md` — DTOs, `execute_poll`, deferred dedup URN note]

## Previous story intelligence (Story 2.3)

- **`ScoutRawItem`** is frozen; `item_url`, `title`, `published_at`, `summary`, `body_snippet`, `http_status`, `content_type` — use these for fingerprint inputs; **`fetched_at`** must **not** define identity.
- **Session boundary:** All ORM attributes needed after `async with factory()` must be read **inside** the session block; `execute_poll` already snapshots `source_type` and `primary_url` before fetch.
- **Streaming caps:** RSS10 MB / HTTP 2 MB (post-review) — fingerprint should use **capped** content consistent with what the connector actually sees.
- **`poll_connector_error`** vs **`poll_completed`:** Do not log `poll_completed` when the fetch failed; retries should only emit **success** metrics when data is actually returned.
- **Feedparser:** Runs in `asyncio.to_thread` — keep it that way; retry wrapper should wrap **network** fetch, not CPU parse, unless you deliberately retry parse failures (justify if so).

## Git intelligence summary

- Recent committed baseline: **`131b586`** — sources API, APScheduler, connector stub patterns; **`services/connectors/`** + **`workers/`** split is established.
- Local workspace may contain **uncommitted** Story 2.3 connector files — rebase story implementation on **current** `main`/`execute_poll` signature (`list[ScoutRawItem]`).

## Latest technical information (implementation time)

- **`httpx` 0.28.x:** Map retryable cases explicitly (`httpx.TimeoutException`, `httpx.ConnectError`, `httpx.NetworkError`, selected status codes). Do not rely on generic `Exception` unless re-raising after classification.
- Prefer **jitter** on backoff (small random delta) to avoid thundering herd when many sources fail together (optional but good for FR4).

## Project context reference

- No **`project-context.md`** in repo; **`architecture.md`**, **`prd.md`**, **`epics.md`**, and **`2-3-...md`** are authoritative.

## Story completion status

- **review** — Implementation complete; full `pytest` green (42 passed, 7 skipped).

## Change Log

- 2026-04-16 — Story 2.4 implemented: `fingerprint.py`, `fetch_retry.py`, `ConnectorFetchFailed`, `SourceIngestedFingerprint` + migration `f8a3c1d2e4b5`, `ingestion_dedup.register_new_items`, `sources_repo` poll failure helpers, RSS/HTTP fetch retry integration, `execute_poll` dedup + `poll_completed` uses new-item count; tests (`test_fingerprint`, connector retry/dedup cases, alembic head), README paragraph.

## Dev Agent Record

### Agent Model Used

Composer

### Debug Log References

### Completion Notes List

- **Retry classification** (AC6): `RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}`; `NON_RETRYABLE_HTTP_STATUSES = {400, 401, 403, 404, 405, 410, 422}`; other HTTP statuses fail once via `ConnectorFetchFailed`; transient `httpx` errors (`TimeoutException`, `ConnectError`, `ReadError`, `WriteError`, `RemoteProtocolError`, `NetworkError`) retry up to `MAX_ATTEMPTS` with jittered exponential backoff (`fetch_retry.py`).
- **`execute_poll`**: successful fetch clears `last_poll_failure`, registers fingerprints, returns only **new** items; `item_count` in `poll_completed` reflects deduped count. `ConnectorFetchFailed` and other fetch exceptions record `extra_metadata.last_poll_failure` and return `[]` without `poll_completed`.
- **Integration**: `tests/test_ingestion_dedup.py` validates duplicate suppression when `DATABASE_URL` / `ALEMBIC_SYNC_URL` are set.

### File List

- `README.md`
- `alembic/versions/f8a3c1d2e4b5_add_source_ingested_fingerprints.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/ingestion_dedup.py`
- `src/sentinel_prism/db/repositories/sources.py`
- `src/sentinel_prism/services/connectors/errors.py`
- `src/sentinel_prism/services/connectors/fetch_retry.py`
- `src/sentinel_prism/services/connectors/fingerprint.py`
- `src/sentinel_prism/services/connectors/http_fetch.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `src/sentinel_prism/services/connectors/rss_fetch.py`
- `tests/test_alembic_cli.py`
- `tests/test_connectors_rss_http.py`
- `tests/test_fingerprint.py`
- `tests/test_ingestion_dedup.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-4-deduplication-and-retry-with-backoff.md`

