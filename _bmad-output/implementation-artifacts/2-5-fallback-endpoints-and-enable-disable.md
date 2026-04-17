# Story 2.5: Fallback endpoints and enable/disable

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **admin**,
I want **optional fallback URL and HTML-parse alternate retrieval when the primary path fails, plus enable/disable without losing history**,
so that **sources stay operable and operators can pause ingestion safely** (**FR5**, **FR6**).

## Acceptance Criteria

1. **Fallback configuration (API + persistence)**  
   **Given** an admin creates or updates a source  
   **When** they supply optional fallback settings (see Dev Notes for recommended shape)  
   **Then** values persist on `Source` and round-trip in `GET`/`PATCH`/`POST` responses  
   **And** validation rejects invalid URLs the same way as `primary_url` (HTTP/HTTPS only).

2. **Primary fails → fallback attempted**  
   **Given** a source with a configured fallback and primary fetch that **ultimately** fails after Story 2.4 retry/backoff (i.e. `ConnectorFetchFailed` or equivalent terminal failure from the primary URL path)  
   **When** `execute_poll` runs  
   **Then** the connector attempts the **fallback path** before giving up  
   **And** retry/backoff policy applies to **each** path independently (fresh attempt budget for fallback — do not share attempt counters across primary vs fallback).

3. **Fallback success is a real success**  
   **Given** fallback fetch returns usable items (RSS/HTTP/HTML path per configuration)  
   **When** dedup runs   **Then** behavior matches Story 2.4: `clear_poll_failure`, fingerprint registration, `poll_completed` with **non-error** semantics, returned list is deduped new items only.

4. **Outcome logging**  
   **Given** primary fails and fallback is configured  
   **When** poll completes (success or both paths fail)  
   **Then** structured logs include which path succeeded or that both failed (`primary` / `fallback` / `both_failed`), `source_id`, `trigger`, and for failures the existing error context pattern from Story 2.4 (`error_class`, URL host/path where applicable)  
   **And** `poll_completed` is emitted **only** when a path returns usable items (same rule as 2.3/2.4 — no success-shaped log on total failure).

5. **HTML-parse alternate (FR5)**  
   **Given** fallback is configured to use **HTML extraction** (not “same connector as `source_type`”)  
   **When** that path runs  
   **Then** the implementation performs an HTTP GET of the fallback URL, parses HTML to produce **one or more** `ScoutRawItem` values with stable `item_url`/`title`/body fields consistent with existing DTO usage  
   **And** parsing runs off the event loop if CPU-heavy (mirror `feedparser` `asyncio.to_thread` pattern).

6. **Enable / disable without deleting history (FR6)**  
   **Given** a source is **disabled**  
   **When** the scheduler runs or `execute_poll` is invoked  
   **Then** no fetch occurs — early exit with `poll_skipped` / `reason=source_disabled` (already implemented — **verify** and extend tests if gaps)  
   **And** scheduled jobs are **removed** while disabled (`PollScheduler` — already implemented — **verify**)  
   **And** manual `POST /sources/{id}/poll` returns **409** with clear detail (already implemented — **verify**)  
   **And** `Source` row, `source_ingested_fingerprints`, and `extra_metadata` history (e.g. `last_poll_failure`) **remain** — disabling never deletes registry or dedup rows.

7. **Regressions**  
   **Given** Story 2.4 connector and dedup behavior  
   **When** this story lands  
   **Then** existing connector tests remain green; **no imports** from `graph/` in `services/connectors/` **[Source: `architecture.md` §6]**.

8. **Tests**  
   **Given** CI without live network  
   **When** tests run  
   **Then** there is coverage for: primary fails → fallback succeeds (mock transport); primary succeeds → fallback **not** called; both fail → `record_poll_failure` / metadata behavior consistent with 2.4; disabled source still skips fetch; API accepts/persists fallback fields.

## Tasks / Subtasks

- [x] **Schema & API** (AC: #1, #6)
  - [x] Add Alembic revision + SQLAlchemy columns (or justified JSONB sub-schema in `extra_metadata` **only if** you document why columns are unsuitable — prefer typed columns for `fallback_url` / mode).
  - [x] Extend `SourceCreate`, `SourceUpdate`, `SourceResponse`, and `sources_repo` field mapping.
- [x] **Fallback orchestration in `execute_poll`** (AC: #2–#4, #7)
  - [x] Refactor minimally: primary attempt wrapped so terminal failure can invoke fallback without duplicating dedup/`poll_completed` logic.
  - [x] Ensure `ConnectorFetchFailed` from primary triggers fallback; unexpected exceptions policy should be **defined** (log + same as today vs attempt fallback — pick one and test).
- [x] **HTML fallback implementation** (AC: #5)
  - [x] Add vetted HTML dependency to `requirements.txt` if needed (e.g. `beautifulsoup4` + parser backend) **or** justify stdlib-only approach with explicit limits.
  - [x] New helper module under `services/connectors/` (e.g. `html_fallback.py`) building `list[ScoutRawItem]`.
- [x] **Logging** (AC: #4)
  - [x] New structured log events or `extra` keys: `fetch_path`, `primary_error_class`, `fallback_error_class` (when relevant).
- [x] **Tests** (AC: #8)
  - [x] Extend `tests/test_connectors_rss_http.py` or add `tests/test_poll_fallback.py`; reuse `httpx.MockTransport` patterns from 2.3/2.4.

### Review Findings

All fixes applied in this review cycle — full test suite green (67 passed / 7 DB-gated skipped).

- [x] [Review][Patch] **PATCH must reject `fallback_mode=none` + non-null `fallback_url` with 422 (match `SourceCreate`); only auto-null when `fallback_url` was NOT in payload** [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Patch] **`SourceUpdate` accepts explicit JSON `null` for `fallback_mode` → DB `NOT NULL` violation (500 vs 422)** [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Patch] **`fetch_html_page_items` discards `_http_status` and hardcodes `http_status=200` in the DTO** [`src/sentinel_prism/services/connectors/html_fallback.py`]
- [x] [Review][Patch] **`html_fallback` parses any 2xx response without content-type guard — binary/JSON/PDF becomes a false success** [`src/sentinel_prism/services/connectors/html_fallback.py`]
- [x] [Review][Patch] **`html_fallback` does not pass response encoding to BeautifulSoup — non-UTF-8 pages are garbled** [`src/sentinel_prism/services/connectors/html_fallback.py`]
- [x] [Review][Patch] **Outer `except Exception` in `execute_poll` mis-attributes fallback failures to `fetch_path="primary"` and logs the primary URL host/path** [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] **`both_failed` branch records only `fb_exc.error_class` in `record_poll_failure` — now persists `primary|fallback` merged class** [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] **Outcome vocabulary drifts from spec: `primary_ok`/`fallback_ok` vs spec's `primary`/`fallback`** [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] **`_validate_url` error message rewording is a wire-level regression — restored field-specific messages via `field=` kwarg** [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Patch] **`_validate_url` accepts embedded `\n`, `\r`, `\t`, trailing whitespace — now uses `re.fullmatch`-style anchor plus strip check** [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Patch] **Primary-success dedup path propagated raw exceptions — now wrapped with `record_poll_failure` + `poll_dedup_failed` log** [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] **Unsupported-source-type guard moved outside outer `try` so unrelated exceptions are not mis-logged as primary connector errors** [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] **`_fallback_configured` simplified — removed redundant enum allow-list so future `FallbackMode` variants surface via `_fetch_fallback.ValueError`** [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] **PATCH error message for `fallback_url: null` without mode — now returns "to clear fallback_url, also set fallback_mode to none"** [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Patch] **`execute_poll` test for `FallbackMode.HTML_PAGE`** — `test_execute_poll_html_fallback_success`
- [x] [Review][Patch] **HTML fallback 4xx test** — `test_execute_poll_html_fallback_4xx_logs_both_failed`
- [x] [Review][Patch] **Policy test: non-`ConnectorFetchFailed` primary exception does NOT trigger fallback** — `test_execute_poll_primary_non_connector_exception_does_not_trigger_fallback`
- [x] [Review][Patch] **API round-trip tests for populated fallback pair (unit-level PATCH route tests)** — six new tests in `test_sources_fallback_validation.py`
- [x] [Review][Patch] **`caplog` assertion on `poll_fetch_both_failed` event shape** — extended `test_execute_poll_both_primary_and_fallback_fail` with `outcome`/`primary_error_class`/`fallback_error_class` and absence of `poll_completed`
- [x] [Review][Patch] **AC6 scheduler disable/re-enable coverage** — new `tests/test_poll_scheduler_enable_disable.py`
- [x] [Review][Patch] **Non-HTML content-type guard test** — `test_fetch_html_page_items_rejects_non_html_content_type`
- [x] [Review][Patch] **Declared-encoding decode test** — `test_fetch_html_page_items_honors_declared_encoding`

## Dev Notes

### Epic 2 context

- **Goal:** Direct connectors with **dedup**, **retry**, **fallback** (this story), **metrics** (2.6) [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2].
- **Predecessors:** **2.4** — fingerprint ledger, `fetch_retry`, `ConnectorFetchFailed`, `record_poll_failure` / `clear_poll_failure`, `register_new_items` inside `execute_poll`.
- **Follow-on:** **2.6** metrics may consume log fields and `last_poll_failure`; keep log keys **stable** and documented.

### Developer context (guardrails)

- **Single entrypoint:** All poll paths must still flow through **`execute_poll(source_id, *, trigger)`** — no parallel public poll APIs [Source: `2-3-rss-http-connector-implementation-direct-path.md`].
- **Session snapshot:** Continue to read `source_type`, `primary_url`, `enabled`, and **new** fallback fields **inside** the first session block before any I/O (same `DetachedInstanceError` avoidance as 2.3/2.4).
- **Success logging:** **`poll_completed` only after successful fetch + dedup path** — if both primary and fallback fail, **no** `poll_completed` [Source: Story 2.4 Dev Notes].
- **TOCTOU:** Re-check `enabled` at start of `execute_poll` (already); fallback must not run if source became disabled mid-flight **after** session snapshot — acceptable to complete in-flight poll; document if you add a mid-poll re-read.

### Technical requirements

| Topic | Requirement |
| --- | --- |
| FR5 | Alternate retrieval when primary fails — **URL** and/or **HTML parse** [Source: `_bmad-output/planning-artifacts/prd.md` — FR5] |
| FR6 | Enable/disable without deleting history [Source: `prd.md` — FR6] |
| Dedup | Unchanged: `(source_id, content_fingerprint)` [Source: `architecture.md` §4 Data] |
| Retry | Reuse `fetch_retry` / connector-level retry for each URL fetch; do not nest conflicting retry layers |

**Recommended configuration shape (implementer may refine naming in OpenAPI):**

- `fallback_url: str | None` — optional; validated like `primary_url`.
- `fallback_mode: str | enum` — e.g. `none` (default), `same_as_primary` (use existing RSS or HTTP fetcher against `fallback_url`), `html_page` (HTML extraction path). Keeps RSS-primary + HTML-fallback expressible without ambiguous type coercion.

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Location | `services/connectors/`, `api/routes/sources.py`, `db/models.py`, `db/repositories/sources.py`, `alembic/versions/` |
| Boundaries | Services **must not** import `graph/` [Source: `architecture.md` §6] |
| FR mapping | FR1–FR6 → `services/connectors/` [Source: `architecture.md` requirements table] |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **httpx** | Already pinned `0.28.1` — use for HTML fallback GET; align exception handling with `fetch_retry` classification |
| **feedparser** | Existing — for `same_as_primary` when `source_type == RSS` |
| **HTML** | If adding **beautifulsoup4**, pin a current stable 4.12.x in `requirements.txt` and document parser choice (`lxml` vs `html.parser`) |

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/services/connectors/poll.py` | Orchestrate primary → fallback; shared success tail (dedup + `poll_completed`) |
| `src/sentinel_prism/services/connectors/` | New HTML fallback helper(s); avoid bloating `http_fetch.py` |
| `src/sentinel_prism/db/models.py` | New `Source` columns |
| `src/sentinel_prism/api/routes/sources.py` | Pydantic models for fallback fields |
| `alembic/versions/` | New revision |
| `tests/` | Fallback + disable regression coverage |

### Testing requirements

- **No live network** in default CI — `httpx.MockTransport` only.
- **DB tests:** Skip when `DATABASE_URL` unset, same as 2.4.
- Assert **fallback not invoked** when primary returns items (avoid wasted traffic in real deployments).

### UX / product notes

- **Admin UI** is out of scope; REST/OpenAPI is the contract. Optional fields should be **omittable** on create (defaults = no fallback).

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2, Story 2.5]
- [Source: `_bmad-output/planning-artifacts/prd.md` — FR5, FR6]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §4 Data, §6 boundaries]
- [Source: `_bmad-output/implementation-artifacts/2-4-deduplication-and-retry-with-backoff.md` — retry, dedup, logging semantics]

## Previous story intelligence (Story 2.4)

- **`execute_poll`** already: loads source, checks `enabled`, dispatches `fetch_rss_items` / `fetch_http_page_item`, catches `ConnectorFetchFailed` → `record_poll_failure`, success path → `clear_poll_failure` + `register_new_items` + `poll_completed`.
- **Terminal failure** for retryable cases surfaces as **`ConnectorFetchFailed`** after attempts exhausted — that is the natural hook to chain fallback.
- **Review-hardening** from 2.4: consistent RSS vs HTTP error semantics, `aclose()` masking, fingerprint edge cases — when adding fallback, **do not reintroduce** bare `Exception` paths that bypass structured `ConnectorFetchFailed` without operator context.
- **Files touched in 2.4** (expect to extend): `poll.py`, `sources.py` (routes + repo), `models.py`, connector fetch modules, `tests/test_connectors_rss_http.py`, `tests/test_ingestion_dedup.py`.

## Git intelligence summary

- Latest ingestion work: **`85cd667`** — `feat(connectors): RSS/HTTP fetch, dedup fingerprints, retry with backoff` — establishes `services/connectors/` layout, `execute_poll`, tests. Implement fallback as a **thin** layer on this baseline.

## Latest technical information (implementation time)

- **`httpx` 0.28.x:** Continue mapping errors through the same classification as `fetch_retry.py`; fallback GET should reuse the shared async client patterns where possible to avoid divergent timeout/size limits.
- **`beautifulsoup4` 4.12.x** (if adopted): well-supported for defensive HTML parsing; prefer explicit encoding handling and size cap consistent with HTTP connector body limits.

## Project context reference

- No **`project-context.md`** in repo; **`architecture.md`**, **`prd.md`**, **`epics.md`**, and prior story files under `_bmad-output/implementation-artifacts/` are authoritative.

## Story completion status

- **review** — Implementation complete; `pytest` green (51 passed, 7 skipped).

### Open questions (non-blocking)

- Should **cross-type** fallback be required in MVP (e.g. `source_type=RSS` but fallback URL treated as raw HTML) or is **`fallback_mode`** sufficient to make intent explicit? Story assumes explicit mode enum.
- If primary raises **non-connector** `Exception`, should fallback still run? Default recommendation: **no** — log and `record_poll_failure` like today unless product wants maximum resilience.

## Change Log

- 2026-04-17 — Story 2.5 implemented: `FallbackMode`, `sources.fallback_url` / `fallback_mode`, Alembic `b9c8d7e6f5a4`, `html_fallback.fetch_html_page_items` (BeautifulSoup + `asyncio.to_thread`), `execute_poll` primary→fallback orchestration with `poll_primary_failed_try_fallback`, `poll_fetch_both_failed`, `poll_fetch_outcome`; API validation and PATCH merge rules; tests for fallback paths and pydantic validation.
- 2026-04-17 — Code review: applied 20 patches across `poll.py`, `html_fallback.py`, `api/routes/sources.py` and tests. Highlights: strict PATCH parity with `SourceCreate` (reject `mode=none`+url and `mode=null`); fallback failures now attributed to `fetch_path=fallback` with merged `error_class`; HTML fallback honors declared encoding, rejects non-HTML content-type, and threads the real `http_status`; outcome vocabulary aligned to spec (`primary`/`fallback`/`both_failed`); dedup failure wrapped in `record_poll_failure` + `poll_dedup_failed` log; unsupported-source-type guard moved outside primary/fallback `try`; `_validate_url` now rejects whitespace/control chars and preserves field-specific error messages. Tests: new `test_poll_scheduler_enable_disable.py`, HTML_PAGE execute_poll paths, non-`ConnectorFetchFailed` policy, `caplog` shape checks, and six PATCH-route unit tests. Suite: 67 passed / 7 DB-gated skipped.

## Dev Agent Record

### Agent Model Used

Composer

### Debug Log References

### Completion Notes List

- **Policy:** Fallback runs only after **`ConnectorFetchFailed`** on the primary path; other exceptions follow the existing single-path failure handler (no fallback).
- **`FallbackMode`:** `none` (default), `same_as_primary` (RSS/HTTP fetcher against `fallback_url`), `html_page` (`fetch_html_page_items`).
- **Logging:** `poll_fetch_outcome` emits `primary` or `fallback` before `poll_completed`; `poll_fetch_both_failed` carries `outcome="both_failed"`, `primary_error_class`, `fallback_error_class`, and both URL host/path pairs; `record_poll_failure` persists a merged `primary|fallback` error class so both classes are filterable downstream.
- **PATCH:** `fallback_mode=none` + non-null `fallback_url` in the same body is rejected 422 (strict parity with `SourceCreate`); `fallback_mode=none` alone clears the stored URL; `fallback_mode=null` is rejected 422; nulling `fallback_url` without clearing mode returns a targeted 422 telling the caller how to resolve it.

### File List

- `requirements.txt`
- `alembic/versions/b9c8d7e6f5a4_add_sources_fallback_columns.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/sources.py`
- `src/sentinel_prism/api/routes/sources.py`
- `src/sentinel_prism/services/connectors/html_fallback.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `tests/test_connectors_rss_http.py`
- `tests/test_sources_fallback_validation.py`
- `tests/test_sources.py`
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-5-fallback-endpoints-and-enable-disable.md`
