# Story 2.3: RSS/HTTP connector implementation (direct path)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **to fetch public RSS/HTTP content via the connector interface**,
so that **Scout uses direct connectors, not generic web search** (**FR42**).

## Acceptance Criteria

1. **Given** a **registered** source with `source_type == rss` and a valid **`primary_url`** pointing at an RSS or Atom feed  
   **When** **`execute_poll(source_id, trigger=...)`** runs (from **scheduled** or **manual** path)  
   **Then** the connector **loads** the `Source` row from the database, **fetches** the feed over HTTPS/HTTP using an **async** HTTP client, **parses** entries, and **returns** a **non-empty list** of structured **raw items** on success   **And** each item includes at least: **stable item URL** (or synthesized id if the feed omits links — document the fallback), **title** when present, **published** timestamp when present, and **`fetched_at`** (UTC, timezone-aware) common to the batch  
   **And** implementation **modules** live under **`src/sentinel_prism/services/connectors/`** per Architecture (**not** inside `graph/` or route handlers).

2. **Given** a **registered** source with `source_type == http` and a **`primary_url`**  
   **When** **`execute_poll`** runs  
   **Then** the connector performs a **GET** of that URL (no HTML intelligence required in this story) and returns **one** raw item representing the response (e.g. status code, `content-type`, and a **bounded** text body snippet — cap size to protect memory/logs)  
   **And** **`fetched_at`** is set.

3. **Given** a **missing** source id or **disabled** source at execution time  
   **When** **`execute_poll`** runs  
   **Then** behavior is **defined and tested**: e.g. **no HTTP traffic**, structured log, **empty list** returned (or a single internal error item — **pick one** and document); must be **consistent** for scheduler vs manual trigger (manual route already pre-checks enabled — avoid double-fetch races where possible).

4. **Given** **transient** HTTP failures (timeout, connection error, 5xx)  
   **When** the connector runs  
   **Then** errors are **structured-logged** with **`source_id`**, **`trigger`**, URL host/path (avoid logging secrets), and exception class   **And** the poll **does not** crash the APScheduler worker thread; **return** an **empty list** or **raise a narrow connector exception** caught at the `execute_poll` boundary — **Story 2.4** will add retry/backoff (**FR4**).

5. **Observability:** Extend structured logging so every successful poll logs at least: **`source_id`**, **`trigger`**, **`source_type`**, **item count**, **fetch latency ms** (optional but recommended). Do **not** log full raw HTML bodies at INFO.

6. **Contract / typing:** If **`execute_poll`** gains a **return type** (e.g. `list[RawFeedItem]`), update **`PollExecutor`** in `api/deps.py` and any tests using **`get_poll_executor`** overrides so types and protocols stay aligned.

7. **Tests:** Add automated tests (no live network in CI by default) using **`httpx.MockTransport`** or equivalent to serve **minimal RSS2.0** and **Atom** XML fixtures, plus an **`http`** source case. Skip or mark integration tests that need real URLs if you add any. Preserve the project convention: DB-less tests still run when **`DATABASE_URL`** is unset; DB-dependent tests skip when appropriate.

## Tasks / Subtasks

- [x] **Data contracts** (AC: #1, #2, #6)
  - [x] Define immutable **DTOs** (e.g. `dataclass` or `TypedDict`) for connector results in `services/connectors/` — names should align conceptually with Architecture **`raw_items` / Scout** output [Source: `architecture.md` §3.2, §6].
  - [x] Document field meanings in docstrings for the dev agent and Epic 3 handoff.

- [x] **RSS/Atom fetch + parse** (AC: #1, #4, #5)
  - [x] Add a **pinned** parsing dependency (recommended: **`feedparser`**) to **`requirements.txt`**; use **`httpx.AsyncClient`** (already pinned) with explicit **timeouts**, **redirect** limits, and a sensible **User-Agent** identifying Sentinel Prism.
  - [x] Implement parsing in a dedicated module e.g. `services/connectors/rss_fetch.py` (name flexible — keep **`poll.py`** as orchestration entrypoint).

- [x] **HTTP direct GET** (AC: #2, #4, #5)
  - [x] Implement minimal GET path for `SourceType.HTTP` e.g. `services/connectors/http_fetch.py` — no fallback URL logic (**Story 2.5**).

- [x] **Orchestration** (AC: #1–#6)
  - [x] Replace stub body of **`execute_poll`** in `services/connectors/poll.py`: open **`AsyncSession`** via **`get_session_factory()`** (same pattern as `poll_scheduler._run_scheduled_poll`), load **`Source`**, branch on **`source_type`**, call fetchers, return **`list`**.  
  - [x] **Do not** import **`graph/`** from **`services/`** [Source: `architecture.md` §6 Boundaries].

- [x] **Tests & docs** (AC: #7)
  - [x] New test module e.g. `tests/test_connectors_rss_http.py` with mocked HTTP.
  - [x] README: one bullet — connectors perform **real** RSS/HTTP fetch in dev when DB + network configured.

### Review Findings

- [x] [Review][Patch] ORM row accessed after session close — DetachedInstanceError at runtime [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] HTTP 5xx → log + return [], 4xx → return item (D1 resolved: option 3) [`src/sentinel_prism/services/connectors/http_fetch.py`]
- [x] [Review][Patch] HTTP and RSS response bodies fully buffered in memory before any size cap [`src/sentinel_prism/services/connectors/http_fetch.py`, `rss_fetch.py`]
- [x] [Review][Patch] feedparser.parse() is synchronous — blocks event loop in async context [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- [x] [Review][Patch] User-Agent header silently overwritten when caller passes `headers=` kwarg [`src/sentinel_prism/services/connectors/http_client.py`]
- [x] [Review][Patch] RSS feed entry count unbounded — no MAX_ENTRIES cap [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- [x] [Review][Patch] bozo parse warning suppressed when feed returns any entries [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- [x] [Review][Patch] feedparser encoding misdetection — raw bytes passed without Content-Type header [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- [x] [Review][Patch] One bad feed entry drops all remaining entries in the poll (no per-entry error isolation) [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- [x] [Review][Patch] Fetcher error logs omit `trigger` field — AC4 requires source_id, trigger, url host/path, error_class [`src/sentinel_prism/services/connectors/rss_fetch.py`, `http_fetch.py`]
- [x] [Review][Patch] `poll_connector_error` omits url_host/url_path — AC4 requires full URL context [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] `poll_completed` emitted after `poll_connector_error` — conflates error with success for AC5 metrics [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] No `execute_poll` → HTTP branch dispatch test — AC7 requires http source case through orchestration [`tests/test_connectors_rss_http.py`]
- [x] [Review][Patch] Exception message discarded — only class name in log, no `str(exc)` [`src/sentinel_prism/services/connectors/rss_fetch.py`, `http_fetch.py`, `poll.py`]
- [x] [Review][Patch] No explicit `max_redirects` — spec requires explicit redirect limit [`src/sentinel_prism/services/connectors/http_client.py`]
- [x] [Review][Patch] `fetched_at` timezone not enforced on DTO — naive datetime accepted silently [`src/sentinel_prism/services/connectors/scout_raw_item.py`]
- [x] [Review][Patch] `**kwargs: object` on `connector_async_client` breaks static analysis — should be `**kwargs: Any` [`src/sentinel_prism/services/connectors/http_client.py`]
- [x] [Review][Defer] Index-based dedup URN unstable across polls — Story 2.4 owns dedup [`src/sentinel_prism/services/connectors/rss_fetch.py`] — deferred, Story 2.4 concern
- [x] [Review][Defer] TOCTOU: scheduled poll can fire after source disabled — pre-existing from 2.2 [`src/sentinel_prism/workers/poll_scheduler.py`] — deferred, pre-existing

## Dev Notes

### Epic 2 context

- **Goal:** Admins operate **public** sources; Scout path uses **direct connectors** with dedup, retry, fallback, metrics [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2].
- **This story:** **FR42** — direct HTTP/RSS for **registered** sources; **not** Tavily/web-search as primary ingestion [Source: `_bmad-output/planning-artifacts/prd.md` — FR42, Technical Direction].
- **Predecessors:** **2.1** registry (`Source`, `primary_url`, `source_type`); **2.2** invokes **`execute_poll`** from scheduler and **`POST /sources/{id}/poll`**.
- **Follow-on:** **2.4** dedup + backoff; **2.5** fallback endpoints; **2.6** metrics; **Epic 3** persists raw captures and runs LangGraph — connector output should be **easy to pass** into `normalize` / state without rework.

### Developer context (guardrails)

- **Source model:** `SourceType` is **`rss` | `http`**; **`primary_url`** is validated HTTP(S) on create/update [Source: `src/sentinel_prism/db/models.py`, `api/routes/sources.py`].
- **Poll entrypoint:** **`execute_poll(source_id, *, trigger)`** is the **single** entry used by **`PollScheduler._run_scheduled_poll`** and **`trigger_manual_poll`** — extend it; do not fork a second public API for fetches.
- **Scheduler caveat:** Scheduled path **closes** the DB session before **`execute_poll`**; **`execute_poll`** must **open its own** session to load **`Source`** (TOCTOU between enable-check and fetch is acknowledged; **2.4** may tighten with transactional patterns).
- **Normalization boundary:** Connectors return **DTOs**; **no** full normalization here — Architecture places normalization in the **normalize** node / helpers [Source: `architecture.md` §6 Boundaries].

### Technical requirements

- **HTTP client:** **`httpx==0.28.1`** already in **`requirements.txt`** — use async APIs consistently with FastAPI.
- **RSS parsing:** Prefer **`feedparser`** over ad-hoc XML unless there is a strong reason — feeds are messy; pin version when added.
- **Security:** Only **public** URLs in scope; enforce **size** limits on response bodies; do not disable TLS verification; avoid logging PII from feed content at default log level.

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Location | `src/sentinel_prism/services/connectors/` for fetchers + DTOs + `poll.py` orchestration [Source: `architecture.md` §5–§6] |
| Boundaries | Services **must not** import **`graph/`**; graph nodes will call services later [Source: `architecture.md` §6] |
| Scout mapping | FR1–FR6 Scout area maps to **`services/connectors/`** and later **`graph/nodes/scout.py`** [Source: `architecture.md` §6 table] |
| Logging | Structured fields toward **NFR8**; full **`run_id`** correlation arrives with Epic 3 — use **`source_id`** + **`trigger`** now |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **httpx** | Already pinned; use `AsyncClient`, timeouts, `follow_redirects` intentionally set |
| **feedparser** | Add with pin when implementing; verify license compatibility |
| **pytest / pytest-asyncio** | Existing — async tests for `execute_poll` |

### File structure requirements

| Path | Purpose |
| --- | --- |
| `requirements.txt` | Add **`feedparser`** (or chosen parser) with pin |
| `src/sentinel_prism/services/connectors/poll.py` | Orchestrating **`execute_poll`** |
| `src/sentinel_prism/services/connectors/*.py` | DTOs + RSS + HTTP fetch helpers |
| `src/sentinel_prism/api/deps.py` | Update **`PollExecutor`** return type if **`execute_poll`** returns data |
| `tests/test_connectors_rss_http.py` | Mocked HTTP fixtures |

### Testing requirements

- Mock **`httpx`** responses with representative **RSS 2.0** and **Atom** XML.
- Cover **`rss`** and **`http`** `source_type` branches and at least one **HTTP error** path (timeout or 503).
- If tests need DB: reuse patterns from **`tests/test_poll_triggers.py`** / **`tests/test_sources.py`** for session factory and skips.

### UX / product notes

- **No** new UI; admin still uses registry + manual poll from **2.2** [Source: `ux-design-specification.md` — Jordan persona operates sources].

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2, Story 2.3]
- [Source: `_bmad-output/planning-artifacts/prd.md` — FR42, ingestion / connectors]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §2 stack, §3.3 Scout, §6 structure & boundaries]
- [Source: `_bmad-output/implementation-artifacts/2-2-scheduled-and-manual-poll-triggers.md` — poll entrypoint, out-of-scope boundaries, file list]

## Previous story intelligence (Story 2.2)

- **`execute_poll`** is the **shared** hook for scheduler + manual **`POST /sources/{id}/poll`**; **extend** it rather than adding parallel fetch APIs [Source: `2-2-...md` — Tasks].
- **Review / defer items relevant to 2.3:** uncaught exceptions from **`execute_poll`** were deferred — **must** harden when real I/O lands; **`shutdown(wait=True)`** may block longer with real jobs — acceptable for MVP single-worker; multi-process scheduler duplication deferred.
- **Structured logging:** 2.2 story text mentioned **`run_id`** placeholders — Epic 3 owns **`run_id`**; 2.3 should still log **`source_id`** + **`trigger`** consistently (current stub logs these) [Source: `src/sentinel_prism/services/connectors/poll.py`].
- **Poll scheduler** loads **`Source`** before calling **`execute_poll`** for **enabled** check only — **`execute_poll`** must re-load for authoritative **`primary_url`** / **`source_type`**.

## Git intelligence summary

- Recent work: **`131b586`** — Epic 2 Stories **2.1** & **2.2** (sources API, APScheduler, **`services/connectors/poll.py`** stub). Patterns: **`workers/`** for scheduling, **`services/connectors/`** for fetch logic, admin routes thin.

## Latest technical information (implementation time)

- Confirm **`feedparser`** (if used) current stable on PyPI and pin; verify compatibility with **Python 3.12+** if that is the project runtime.
- **`httpx`**: use explicit **`timeout=`** (`connect`, `read`, `write`, `pool`) — defaults are infinite in some versions.
- Revisit **`httpx`** release notes for redirect and encoding behavior when implementing.

## Project context reference

- No **`project-context.md`** in repo; **`architecture.md`**, **`prd.md`**, **`epics.md`**, and **`2-2-...md`** are authoritative.

## Story completion status

- **review** — Implementation complete; `pytest` green (33 passed, 6 skipped).

## Change Log

- 2026-04-16 — Story 2.3: `ScoutRawItem` DTO, `rss_fetch` / `http_fetch`, `execute_poll` orchestration with structured logs, `feedparser` pin, `tests/test_connectors_rss_http.py`, README connector note; `PollExecutor` returns `list[ScoutRawItem]`.
- 2026-04-16 — Code review: applied 17 patches — streaming body caps (RSS 10 MB / HTTP 2 MB), `asyncio.to_thread` for feedparser, ORM session-close fix, `max_redirects=10`, header merge, per-entry error isolation, trigger field in all logs, 5xx→`[]` / 4xx→item for HTTP, `poll_completed` only on success, `__post_init__` UTC guard on DTO, `**kwargs: Any`, 3 new tests (36 passed total). 2 deferred.

## Dev Agent Record

### Agent Model Used

Composer

### Debug Log References

### Completion Notes List

- **AC3:** Missing or disabled source → **no HTTP**; structured **`poll_skipped`** log; **`[]`** returned.
- **AC4:** RSS fetch uses **`rss_fetch_http_error`** / **`poll_connector_error`** warnings; returns **`[]`**; outer **`execute_poll`** try/except prevents scheduler crashes.
- **`urn:sentinel-prism:feed-item:{source_id}:{index}`** when a feed entry has no link.

### File List

- `requirements.txt`
- `README.md`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/services/connectors/__init__.py`
- `src/sentinel_prism/services/connectors/http_client.py`
- `src/sentinel_prism/services/connectors/http_fetch.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `src/sentinel_prism/services/connectors/rss_fetch.py`
- `src/sentinel_prism/services/connectors/scout_raw_item.py`
- `tests/test_connectors_rss_http.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-3-rss-http-connector-implementation-direct-path.md`

