# Story 3.1: Persist raw captures and normalized records

Status: in-progress

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **to store raw captures and normalized update records**,
so that **analysts have auditable structured data** (**FR7**, **FR8**, **FR10**).

## Acceptance Criteria

1. **FR7 — Raw capture persistence**  
   **Given** new items accepted after dedup (post-`register_new_items`) from a poll  
   **When** persistence runs for those items  
   **Then** each item has a durable **raw capture** row with **source reference**, **capture timestamp** (use `ScoutRawItem.fetched_at` semantics; timezone-aware UTC), and enough payload to reconstruct what was fetched (at minimum: fields on `ScoutRawItem` + any stable ids you introduce).

2. **FR8 — Normalized record shape**  
   **Given** a raw capture for an ingested item  
   **When** normalization runs for that capture (MVP mapper from scout output — full LangGraph `normalize` node wiring is Story 3.3)  
   **Then** the stored **normalized update** includes PRD fields **where extractable** from current connectors: **title**, **dates** (e.g. `published_at` when present), **URL** (`item_url`), **source** (at least `source_id`; denormalize `Source.name` or join-friendly key if useful), **jurisdiction** (from `Source.jurisdiction` at capture time), **document type** (explicit column or structured metadata — may be `"unknown"` / MVP enum when not inferable), **body text** / **summary** when present (`body_snippet`, `summary` from `ScoutRawItem`), and **metadata** (JSONB for connector-specific extras, http status, content type, etc.).

3. **FR10 — Extraction quality / parser confidence**  
   **Given** normalization produces a record  
   **When** it is persisted  
   **Then** **extraction quality** and/or **parser confidence** is stored in a typed, query-friendly way (e.g. nullable `float` 0–1, optional enum, or small structured JSON with a documented schema — pick one approach and use it consistently; null allowed when not yet computed).

4. **Referential integrity & idempotency**  
   **Given** the existing dedup ledger `(source_id, fingerprint)` (Story 2.4)  
   **When** the same logical item is not re-inserted by dedup  
   **Then** raw/normalized rows are **not** duplicated for that ingest cycle (new rows only for `new_items` returned from `register_new_items`). Link **normalized → raw** (FK) so FR9-side-by-side can be implemented later without schema churn.

5. **Architecture boundaries**  
   **Given** Architecture §6 mapping for FR7–FR10  
   **When** code is added  
   **Then** persistence and normalization helpers live under **`db/`** + **`services/`** (no imports from `graph/` in services), and poll orchestration may call a small ingestion service from **`execute_poll`** success path after dedup — do not stuff large ORM blocks inline in `poll.py` if a repository/service keeps boundaries clear.

6. **Migrations & tests**  
   **Given** CI without live network  
   **When** tests run  
   **Then** a new **Alembic** revision applies cleanly; tests cover insert paths (mocked session or test DB pattern consistent with `tests/test_connectors_rss_http.py` / `tests/test_source_metrics.py`) and assert required columns are populated for a synthetic `ScoutRawItem`.

## Tasks / Subtasks

- [x] **Schema (AC: #1, #2, #3, #4)**  
  - [x] Add ORM models for **raw captures** and **normalized updates** in `src/sentinel_prism/db/models.py` (prefer JSONB for flexible raw payload / metadata per Architecture §2).  
  - [x] Add Alembic revision under `alembic/versions/` with indexes sensible for later APIs: `(source_id, captured_at)`, `(source_id, created_at)` on normalized, FK `normalized_updates.raw_capture_id` → `raw_captures.id` (ON DELETE strategy documented — usually RESTRICT or CASCADE per product preference).  
  - [x] Optional nullable `run_id` (UUID, no FK yet) on both tables for Story 3.2+ graph correlation — if added, document as unused until graph exists.

- [x] **Repositories (AC: #1–#5)**  
  - [x] New module e.g. `src/sentinel_prism/db/repositories/captures.py` (name to taste) with async SQLAlchemy 2 patterns matching `sources.py` / `ingestion_dedup.py`.  
  - [x] Transactional insert: raw row(s) then normalized row(s) in same session as `register_new_items` success block when possible to avoid orphan raw rows.

- [x] **Normalization MVP (AC: #2, #3)**  
  - [x] Define a canonical **`NormalizedUpdate`** (Pydantic model or dataclass) colocated with ingestion/normalization service — align field names with Architecture `normalized_updates` / PRD FR8.  
  - [x] Implement `normalize_scout_item(item: ScoutRawItem, *, source: Source | snapshot) -> NormalizedUpdate` (pure function or service method) that fills PRD fields from RSS/HTTP-shaped data; document_type may default to unknown.  
  - [x] Set parser confidence / extraction quality heuristically for MVP (e.g. high when title+url present, lower when only snippet; document the heuristic in code comment — avoid magic numbers without explanation).

- [x] **Wire into poll success path (AC: #1, #4, #5)**  
  - [x] After `new_items = await ingestion_dedup.register_new_items(...)` in `execute_poll`, persist raw+normalized for each item in `new_items` before `record_poll_success_metrics`.  
  - [x] Reuse existing `Source` snapshot pattern: avoid `DetachedInstanceError` — load `jurisdiction` / `name` inside session or pass primitives.

- [x] **Tests (AC: #6)**  
  - [x] Unit tests for mapper edge cases (missing title, missing published_at, HTML vs RSS).  
  - [x] Integration-style test with DB: one poll or direct repository call asserting row counts and FK link.

### Review Findings

_Code review (2026-04-17) — 3 adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Source diff: `_bmad-output/implementation-artifacts/3-1-review.diff` (770 lines)._

**Decision needed (0 — all resolved below)**

- [x] [Review][Decision→Patch] **D1 · NUL / invalid-UTF-8 content scrubbing** — resolved: scrub at normalize boundary (see patch list).
- [x] [Review][Decision→Defer] **D2 · Per-item savepoint in `persist_new_items_after_dedup`** — deferred: MVP tolerates atomic failure; revisit when production ingestion actually hits a malformed item causing a livelock. (See `deferred-work.md`.)
- [x] [Review][Decision→Patch] **D3 · `body_text` column semantics** — resolved: rename column to `body_snippet` in this migration (see patch list).

**Patches (19 / 19 applied)**

- [x] [Review][Patch] **[D1]** Scrub `\x00` bytes and replace invalid UTF-8 surrogates in `title` / `summary` / `body_snippet` via `_clean_text` inside `normalize_scout_item` AND at the raw JSONB payload boundary (`_jsonable` in `captures.py`). Whitespace-only collapses to `None` at the same choke point.
- [x] [Review][Patch] **[D3]** Renamed `normalized_updates.body_text` → `body_snippet` across model, alembic migration, `NormalizedUpdate` dataclass, mapper, and both test files.
- [x] [Review][Patch] Added `_persist_noop` monkeypatch for `persist_new_items_after_dedup` in every `execute_poll` unit test alongside the existing `register_new_items` stub — tests no longer rely on `MagicMock` session semantics.
- [x] [Review][Patch] Added `_tz_aware_or_none` coercion for naive `published_at` in `normalize_scout_item`; pinned by `test_normalize_coerces_naive_published_at_to_utc`.
- [x] [Review][Patch] Added `UNIQUE (raw_capture_id)` constraint (`uq_normalized_updates_raw_capture_id`) to model + migration; dropped the now-redundant plain `ix_normalized_updates_raw_capture_id` index. Integration test asserts the `IntegrityError` on duplicate insert.
- [x] [Review][Patch] `execute_poll` success tail refactored: (a) `persist_new_items_after_dedup` short-circuits empty batches; (b) `source_name` / `source_jurisdiction` extracted in the **first** session alongside other primitives — second `get_source_by_id` call removed (no TOCTOU, no extra round-trip); (c) `failed_stage` (`dedup | persist | metrics`) threads through the error log (`poll_{stage}_failed`) and `record_poll_failure` reason.
- [x] [Review][Patch] `_mvp_confidence_scores` docstring reconciled: explicit weight constants (`_W_BASE`, `_W_TITLE`, `_W_PUBLISHED`, `_W_BODY`), exported `MVP_CONFIDENCE_MAX = 0.95`, dead `min(1.0, ...)` cap removed; `NormalizedUpdateRow` docstring updated to state `[0, 0.95]`.
- [x] [Review][Patch] `jurisdiction` tightened to `String(64)` in model + migration; `NormalizedUpdateRow` docstring documents `source_name` / `jurisdiction` / `body_snippet` semantics as point-in-time audit snapshots.
- [x] [Review][Patch] `test_captures_persist.py` expanded to a two-item batch with per-row alignment, tz round-trip of `captured_at` and `payload["fetched_at"]`, `parser_confidence` / `extraction_quality` ∈ [0,1] range check, UNIQUE(raw_capture_id) `IntegrityError` assertion, ON DELETE RESTRICT `IntegrityError` assertion, payload rehydration round-trip (`ScoutRawItem(**payload) == item`), and source-id-mismatch `ValueError` assertion.
- [x] [Review][Patch] Both `persist_new_items_after_dedup` and `captures_repo.insert_raw_capture` now raise `ValueError` on `item.source_id != source_id`.
- [x] [Review][Patch] Logging: replaced two-events request with a single `capture_persisted` event carrying namespaced `extra={"event": ..., "ctx": {...}}` so keys cannot collide with `LogRecord` built-ins.
- [x] [Review][Patch] `scout_raw_item_payload` now iterates `dataclasses.fields(item)` via `asdict` + `_jsonable` — every future `ScoutRawItem` field is captured automatically; integration test's `ScoutRawItem(**payload)` round-trip guards against regressions.
- [x] [Review][Patch] All confidence assertions switched to `pytest.approx`; magic `0.95` replaced with the exported `MVP_CONFIDENCE_MAX` constant.
- [x] [Review][Patch] HTTP-snippet test now pins both `parser_confidence` and `extraction_quality` (`≈ 0.49`).
- [x] [Review][Patch] Whitespace-only `title` / `summary` / `body_snippet` coerce to `None` via `_clean_text`; `test_normalize_whitespace_only_fields_treated_as_missing` locks it in.
- [x] [Review][Patch] Unified `is not None` gates for `content_type` and `http_status`; `test_normalize_keeps_falsy_but_present_http_status` pins `http_status=0` and `content_type=""` round-trip into `extra_metadata`.
- [x] [Review][Patch] Added `test_normalize_whitespace_only_fields_treated_as_missing`, `test_normalize_scrubs_nul_bytes_and_preserves_real_text`, `test_normalize_coerces_naive_published_at_to_utc`, `test_normalize_keeps_falsy_but_present_http_status`.
- [x] [Review][Patch] `test_captures_persist.py` teardown in a `finally:` block deletes normalized → raw → source (survives failing asserts).
- [x] [Review][Patch] `tests/test_ingestion_normalize.py` now ends with a trailing newline.

**Deferred (6)** — see `_bmad-output/implementation-artifacts/deferred-work.md`

- [x] [Review][Defer] No `CheckConstraint("0 <= parser_confidence <= 1")` — domain invariant is documentation-only — deferred, future hardening
- [x] [Review][Defer] `alembic/env.py` has no `naming_convention`; `op.f(...)` is a no-op wrapper — latent autogenerate churn — deferred, pre-existing project-wide
- [x] [Review][Defer] `server_default=sa.text("now()")` is session-TZ-dependent — deferred, pre-existing project-wide convention
- [x] [Review][Defer] Composite index `(source_id, created_at)` may be wrong for `published_at`-ordered queries — deferred, revisit when query patterns emerge
- [x] [Review][Defer] `document_type` NOT NULL always stored as `"unknown"` poisons future equality filters — deferred, spec explicitly permits MVP default
- [x] [Review][Defer] `raw_captures` has no defense-in-depth `UNIQUE (source_id, item_url)` — deferred, spec AC4 delegates to upstream dedup

**Dismissed (7)**: missing `@pytest.mark.asyncio` (false positive — `asyncio_mode=auto` in `pyproject.toml`); redundant explicit `drop_index` in downgrade (matches project style); no runtime type-validation of `new_items` (type hints suffice); `parser_confidence`/`extraction_quality` schema-nullable but always-written (intentional relaxation); `downgrade` without `CASCADE` (speculative); intra-batch shared `fetched_at` (by design); post-persist `fetch_outcome is None` invariant check (protected by txn rollback).

### Review Findings (follow-up, 2026-04-17)

- [x] [Review][Patch] Stage-specific poll failure event names may break existing dashboards/alerts keyed on `poll_dedup_failed` (event contract regression risk). [src/sentinel_prism/services/connectors/poll.py:348]
- [x] [Review][Patch] `sprint-status.yaml` `last_updated` moved backward (`23:59:30Z` -> `23:45:00Z`), which can misorder automation/audit timelines. [_bmad-output/implementation-artifacts/sprint-status.yaml:34]
- [x] [Review][Defer] Dedup + persist run in a single transaction, so one persist error can roll back dedup fingerprints and cause reprocessing/livelock on repeated bad items. [src/sentinel_prism/services/connectors/poll.py:309] — deferred, pre-existing

## Dev Notes

### Epic 3 context

- **Goal:** LangGraph **StateGraph** with **AgentState**, nodes (**scout → normalize → classify**), checkpointer, retries, Tavily — Epic 3 introduction in `_bmad-output/planning-artifacts/epics.md`.  
- **This story** establishes **durable domain storage** for pipeline I/O so Story **3.2** (state shell) and **3.3** (graph nodes calling services) attach to real tables instead of inventing persistence later.

### Continuity from Epic 2 (no same-epic “previous story” file)

- **Entry:** `execute_poll` → `ingestion_dedup.register_new_items` returns only **new** `ScoutRawItem` rows (`_bmad-output/implementation-artifacts/2-6-per-source-metrics-exposure.md` reinforces metrics on success tail — extend that tail, do not break metrics invariants).  
- **DTO:** `ScoutRawItem` in `src/sentinel_prism/services/connectors/scout_raw_item.py` is the **authoritative** pre-normalization shape.  
- **Dedup:** `SourceIngestedFingerprint` + `content_fingerprint_for_item` — persistence must align with “new items only” semantics.

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Do not** | Import `graph/` from `services/` (Architecture §6 boundaries). |
| **Do** | Keep `poll.py` readable — delegate bulk persistence to a service/repository. |
| **FR9** | Side-by-side analyst view is **not** required in this story; persist data so a future `GET /updates`-style API can serve FR9. |
| **Logging** | Optional structured log when rows are written (`raw_capture_persisted`, `normalized_update_persisted`) with `source_id` for NFR8 correlation — `run_id` may be absent until 3.2. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR7 | Raw capture + timestamp + source reference — `prd.md` §Functional Requirements |
| FR8 | Normalized fields: title, dates, URL, source, jurisdiction, document type, body, summary, metadata — `prd.md` |
| FR10 | Extraction quality / parser confidence on normalized record — `prd.md` |
| NFR3 | No secrets in repo; use `.env.example` only if new env vars — `prd.md` |
| Architecture | PostgreSQL + JSONB, Alembic; FR7–FR10 mapping to `db/models.py` + `graph/nodes/normalize.py` (node comes later) — `architecture.md` §5–6 |

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Stack | FastAPI, SQLAlchemy 2.x async, Alembic, PostgreSQL — `architecture.md` §2 |
| Layout | `db/models.py`, `db/repositories/`, `services/` (normalization/ingestion helper), touch `services/connectors/poll.py` minimally — `architecture.md` §6 tree |
| AgentState (future) | Architecture §3.2 lists `raw_items` / `normalized_updates` in state — tables created here should be serializable from/to those concepts when 3.2 lands |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| SQLAlchemy | Match existing 2.0 `Mapped`, `AsyncSession`, `insert().on_conflict` patterns in `ingestion_dedup.py` / `sources_repo`. |
| Alembic | Autogenerate or hand-write revision consistent with existing `versions/` style. |
| Pydantic | If used for API responses later, keep **internal** domain model separate or clearly named (`NormalizedUpdateRecord` vs response DTO). |

### File structure requirements

- New persistence code under `src/sentinel_prism/db/` and `src/sentinel_prism/services/` per Architecture §6.  
- Do **not** add `graph/` package in this story unless needed for a trivial placeholder (prefer **no** graph code until 3.2).

### Testing requirements

- Follow pytest + async session fixtures used in connector tests; skip DB-dependent tests when `DATABASE_URL` unset if that is existing convention.  
- Assert FK `normalized_updates.raw_capture_id` points to inserted raw row.  
- Regression: `items_ingested_total` and metrics behavior from Story 2.6 still correct after success path change.

### Project structure notes

- Package name: `sentinel_prism` under `src/sentinel_prism/`.  
- No `project-context.md` in repo — rely on Architecture + this story.

### References

- `_bmad-output/planning-artifacts/epics.md` — Epic 3, Story 3.1  
- `_bmad-output/planning-artifacts/prd.md` — FR7, FR8, FR10, Normalization & Storage  
- `_bmad-output/planning-artifacts/architecture.md` — §2 Data, §3.2 AgentState, §3.3 Normalizer node, §5–6 layout & FR mapping  
- `src/sentinel_prism/services/connectors/scout_raw_item.py` — `ScoutRawItem`  
- `src/sentinel_prism/services/connectors/poll.py` — `execute_poll` success tail  
- `src/sentinel_prism/db/repositories/ingestion_dedup.py` — dedup contract  
- `_bmad-output/implementation-artifacts/2-6-per-source-metrics-exposure.md` — metrics / poll success-path constraints

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented `RawCapture` and `NormalizedUpdateRow` ORM models with RESTRICT FKs, composite indexes, nullable `run_id`; Alembic revision `d8e9f0a1b2c3`.
- Added `db/repositories/captures.py`, `services/ingestion/normalize.py` (dataclass `NormalizedUpdate` + heuristic confidence), `services/ingestion/persist.py`; wired `execute_poll` success path after dedup.
- Unit tests: `tests/test_ingestion_normalize.py`; integration: `tests/test_captures_persist.py` (skips without DB); `_poll_source_row` gains `name`/`jurisdiction`; alembic CLI test updated for new head.
- Full suite: 78 passed, 9 skipped (2026-04-17).

**Code review (2026-04-17)** — 3 adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) produced 33 raw findings. Triaged: 3 decision-needed (all resolved), 19 patches (all applied), 6 deferred, 7 dismissed. Key hardening in this pass:

- **Data-safety boundary** — `_clean_text` scrubs `\x00` / invalid UTF-8 and coerces whitespace-only to `None` at the normalize boundary; `_jsonable` applies the same scrub to the raw JSONB payload.
- **Schema correctness** — `UNIQUE (raw_capture_id)` enforces spec AC4 one-to-one; `jurisdiction` tightened to `String(64)`; `body_text` column renamed to `body_snippet` to reflect that it stores `ScoutRawItem.body_snippet` (decision D3).
- **Success-tail simplification** — removed the second-session `get_source_by_id`; `source_name` / `source_jurisdiction` snapshot in the first session; `failed_stage` (`dedup | persist | metrics`) threaded through error log + metrics reason.
- **Drift-proofing** — `scout_raw_item_payload` now iterates `dataclasses.fields(item)` via `asdict`, so future `ScoutRawItem` additions are captured automatically; the integration test round-trips `ScoutRawItem(**payload) == item`.
- **Defensive checks** — `item.source_id == source_id` enforced in both `persist_new_items_after_dedup` and `insert_raw_capture`; naive `published_at` pinned to UTC.
- **Test hardening** — integration test covers multi-item batches, tz round-trip, UNIQUE enforcement, ON DELETE RESTRICT enforcement, payload rehydration, mismatch defensive check, and a `finally:` teardown. Poll unit tests stub `persist_new_items_after_dedup` so MagicMock session semantics no longer mask regressions.
- **Final run:** 82 passed, 9 skipped (2026-04-17, post-review).

_Deferred items recorded in `_bmad-output/implementation-artifacts/deferred-work.md` (6 entries under the 3.1 heading)._

### File List

- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/captures.py`
- `src/sentinel_prism/services/ingestion/__init__.py`
- `src/sentinel_prism/services/ingestion/normalize.py`
- `src/sentinel_prism/services/ingestion/persist.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `alembic/versions/d8e9f0a1b2c3_add_raw_captures_and_normalized_updates.py`
- `tests/test_ingestion_normalize.py`
- `tests/test_captures_persist.py`
- `tests/test_connectors_rss_http.py`
- `tests/test_alembic_cli.py`

### Change Log

- 2026-04-17 — Story 3.1: raw + normalized persistence, poll integration, tests, migration `d8e9f0a1b2c3`.
- 2026-04-17 — Code review applied: 19 patches (data scrubbing, UNIQUE(raw_capture_id), `body_text`→`body_snippet`, success-tail simplification, payload drift-proofing, defensive source-id checks, naive-`published_at` coercion, expanded tests). Migration `d8e9f0a1b2c3` updated in place (not yet applied to any environment).

---

## Git intelligence summary

Recent work on `main` is **Epic 2–focused**: RSS/HTTP connectors, dedup fingerprints, retry/backoff, fallback, per-source metrics (`bdbd636`, `85cd667`, `131b586`). Patterns to mirror: **async** `execute_poll`, **repository** helpers for DB writes, **structured logging** with stable `extra` keys, **Alembic** migrations alongside `models.py` changes.

## Latest technical information

- Prefer **SQLAlchemy 2.0** async APIs already in use; avoid sync session patterns.  
- **JSONB** for raw payload and extensible metadata matches Architecture; add GIN indexes only if query patterns justify (defer until API stories).  
- **LangGraph / AgentState** land in Stories **3.2–3.3** — do not block this story on checkpointer choice.

## Project context reference

No `project-context.md` found in workspace; Architecture + PRD + epics are the authority.

## Story completion status

**done** — Implementation complete; code review passed with all patches applied and all decision items resolved (1 deferred to `deferred-work.md`, rest fixed).

### Saved questions / clarifications (non-blocking)

- **ON DELETE** policy for `raw_captures` if a `Source` is ever deleted — product may prefer soft-delete sources (current model uses real rows); document FK behavior in migration comment.  
- Whether to store **full HTTP response body** for HTTP connector vs snippet only — MVP can store `ScoutRawItem` fields + metadata JSON; expand if PRD demands full raw bytes later.
