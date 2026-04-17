# Story 2.6: Per-source metrics exposure

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **operator**,
I want **success rate, error rate, latency, and item counts per source**,
so that **I can monitor ingestion health** (**NFR9**).

## Acceptance Criteria

1. **NFR9 coverage (observable per source)**  
   **Given** polls have run for a source (successes and/or terminal failures)  
   **When** I call the metrics API (see Dev Notes)  
   **Then** the response includes, per source: **success rate**, **error rate** (or equivalent explicit failure rate), **latency** (at least last successful fetch latency; document if you add a rolling average), and **items ingested** (count of new items accepted after dedup on successful polls).

2. **Last success time**  
   **Given** at least one successful poll completed after this feature ships  
   **When** I read metrics for that source  
   **Then** **`last_success_at`** (UTC timestamp) is present and matches the latest poll that reached the success tail (`clear_poll_failure` + dedup + `poll_completed` path in `execute_poll`).

3. **Consistency with existing health signals**  
   **Given** `last_poll_failure` in `Source.extra_metadata` (Story 2.4 / 2.5)  
   **When** metrics are returned  
   **Then** operators can reconcile **last failure** metadata with **rates and last success** (no contradictory semantics — e.g. after a success, `last_poll_failure` is cleared as today; metrics must reflect that same notion of “success”).

4. **Skipped polls do not corrupt rates**  
   **Given** `poll_skipped` (source disabled, missing source, etc.)  
   **When** the scheduler or manual trigger runs  
   **Then** those events **do not** increment success or failure attempt counters used for **NFR9** rates (define “attempt” = fetch actually started for an enabled source with supported `source_type`).

5. **REST contract**  
   **Given** an authenticated caller with permission (see RBAC note)  
   **When** they `GET` the metrics endpoint(s)  
   **Then** responses are JSON, documented in OpenAPI, stable field names suitable for a future UI (Epic 6 may consume this later).

6. **Tests**  
   **Given** CI without live network  
   **When** tests run  
   **Then** metrics update on **successful** `execute_poll` (mocked fetch + DB), on **connector failure** paths, and skipped/disabled behavior matches AC4.

## Tasks / Subtasks

- [x] **Persistence design** (AC: #1–#4)  
  - [x] Choose **typed columns** on `sources` vs a documented **JSONB** sub-key under `extra_metadata` (prefer typed columns if you expect filtering/sorting by metrics in SQL soon; justify JSONB if you need flexible schema).  
  - [x] Add Alembic revision if schema changes; defaults must not break existing rows.

- [x] **Instrumentation in `execute_poll`** (AC: #1–#4)  
  - [x] On the **success tail** (after dedup commits), persist: increment items ingested by `len(new_items)`, bump success counter, set `last_success_at`, record latency (reuse `elapsed_ms` already computed — see `poll_completed` today).  
  - [x] On **terminal failure** paths that call `record_poll_failure`, bump failure counter and record `last_poll_at` / `last_failure_at` as appropriate — align with existing `last_poll_failure.at`.  
  - [x] Ensure **dedup failure** after a successful fetch updates metrics consistently with `poll_dedup_failed` (health should look “failed” for that attempt).

- [x] **API** (AC: #2, #5)  
  - [x] Expose `GET` endpoint(s): e.g. per-source metrics and optionally list-all for dashboard prep — keep scope minimal but useful.  
  - [x] Add Pydantic response models; wire repository reads.

- [x] **RBAC** (AC: #5)  
  - [x] **Default:** match existing `/sources` routes (**Admin-only** via `get_db_for_admin`) unless you add a short product note + dependency for Analyst/Viewer read-only (do not silently widen without an explicit decision in this story file).

- [x] **Tests** (AC: #6)  
  - [x] Extend or add tests alongside `tests/test_connectors_rss_http.py` / `tests/test_sources.py` patterns; use `httpx.MockTransport` and DB session fixtures consistent with existing skips when `DATABASE_URL` unset.

### Review Findings

- [x] [Review][Patch] AC4 resolution — on unsupported `source_type`: record a single failure (as today) AND set `enabled=False` so the scheduler stops polling; prevents `error_rate` drift to 1.0 on a stuck row [src/sentinel_prism/services/connectors/poll.py — unsupported-source-type guard] (resolved from Decision 1: auto-disable)
- [x] [Review][Decision] Dedup-after-success attribution — dedup failures continue to count as source failures; `error_rate` reports combined upstream + persistence health (resolved from Decision 2: keep current behavior; current code already implements this)
- [x] [Review][Patch] Lost-update race on metric counters [src/sentinel_prism/db/repositories/sources.py — `record_poll_failure`, `record_poll_success_metrics`] — fixed via `UPDATE sources SET col = col + :n`, JSONB `||` merge is atomic in Postgres
- [x] [Review][Patch] `GET /sources/metrics` has no pagination / limit [src/sentinel_prism/api/routes/sources.py — `list_source_metrics`] — added `limit` (1–500, default 100) and `offset` (≥0) query params; threaded through `sources_repo.list_sources`
- [x] [Review][Patch] `tests/test_source_metrics.py` is untracked in git [tests/test_source_metrics.py] — staged via `git add`
- [x] [Review][Patch] Default-CI test gaps (counter monotonicity, AC4 skip, auto-disable, fallback-path attribution, dedup-failure attribution, fetch_path validation) [tests/test_connectors_rss_http.py] — added 7 mocked-session tests that run in default CI
- [x] [Review][Patch] `assert fetch_outcome is not None` [src/sentinel_prism/services/connectors/poll.py] — replaced with `if fetch_outcome is None: raise RuntimeError(...)` so the invariant survives `python -O`
- [x] [Review][Patch] `items_ingested_total` (and `poll_attempts_*`) should be `BigInteger` [src/sentinel_prism/db/models.py, alembic/versions/a7f6e5d4c3b2_add_source_ingestion_metrics.py] — migration amended in place (not yet applied)
- [x] [Review][Patch] `last_success_fetch_path` silently truncates [src/sentinel_prism/db/repositories/sources.py] — repo helper now raises `ValueError` on anything other than `"primary"`/`"fallback"`; no silent slice
- [x] [Review][Patch] `SourceMetricsResponse.last_success_fetch_path` [src/sentinel_prism/api/routes/sources.py] — narrowed to `Literal["primary","fallback"] | None`; unknown DB values coerced to `None` in the serializer
- [x] [Review][Patch] `LastPollFailurePayload.at` [src/sentinel_prism/api/routes/sources.py] — typed as `datetime`; `_parse_last_poll_failure` best-effort-parses ISO-8601 and drops malformed legacy rows
- [x] [Review][Patch] `last_success_at` uses `fetched_at` from `execute_poll` [src/sentinel_prism/db/repositories/sources.py, src/sentinel_prism/services/connectors/poll.py] — moment-of-fetch, not moment-of-commit
- [x] [Review][Defer] Primary exception is dropped when fallback raises a non-`ConnectorFetchFailed` [src/sentinel_prism/services/connectors/poll.py] — deferred, Story 2.5 code (uncommitted residue); address before committing 2.5
- [x] [Review][Defer] `_fallback_configured` silently short-circuits when a future `FallbackMode` variant has no URL [src/sentinel_prism/services/connectors/poll.py] — deferred, Story 2.5 code
- [x] [Review][Defer] `error_class` uses `"primary|fallback"` pipe separator; breaks downstream filter/group [src/sentinel_prism/services/connectors/poll.py] — deferred, Story 2.5 code
- [x] [Review][Defer] PATCH `/sources/{id}` silently clears `fallback_url` when `fallback_mode=none` is sent without `fallback_url`; inconsistent with the explicit-null rejection nearby [src/sentinel_prism/api/routes/sources.py — `patch_source`] — deferred, Story 2.5 code
- [x] [Review][Defer] PATCH `/sources/{id}` has TOCTOU on fallback-pair invariant under concurrent admins; no row lock / `CHECK` constraint [src/sentinel_prism/api/routes/sources.py — `patch_source`] — deferred, Story 2.5 code
- [x] [Review][Defer] Alembic downgrade drops accumulated counter state unconditionally; ops runbook concern [alembic/versions/a7f6e5d4c3b2_add_source_ingestion_metrics.py] — deferred, standard additive-migration rollback trade-off; document in ops runbook rather than block 2.6

## Dev Notes

### Epic 2 context

- **Goal:** Direct connectors with dedup, retry, fallback (**2.5**), and **metrics** (**this story**) [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2].
- **Predecessor:** **2.5** — `execute_poll` primary→fallback orchestration, structured logs `poll_fetch_outcome`, `poll_completed`, `poll_fetch_both_failed`, `last_poll_failure` merge in `sources_repo.record_poll_failure` / `clear_poll_failure`.

### Developer context (guardrails)

- **Single entrypoint:** Poll execution remains **`execute_poll(source_id, *, trigger)`** — metrics must be updated from this path (and any future wrapper must not bypass it) [Source: `2-3-rss-http-connector-implementation-direct-path.md`].
- **Session / ORM:** Continue the **snapshot pattern** at the start of `execute_poll` to avoid `DetachedInstanceError`; new DB writes belong in the existing session blocks or small repository helpers.
- **Logging:** Keep **`poll_completed`** `extra` keys stable (`source_id`, `trigger`, `item_count`, `fetch_latency_ms`, …) — Epic 8 / operators may correlate logs with DB metrics [Source: Story 2.5 Dev Notes].
- **Boundaries:** Services **must not** import `graph/` [Source: `_bmad-output/planning-artifacts/architecture.md` §6].

### Technical requirements

| Topic | Requirement |
| --- | --- |
| NFR9 | Per source: success rate, error rate, latency, items ingested [Source: `_bmad-output/planning-artifacts/prd.md` — NFR9] |
| Observability | Architecture calls for per-source scrape success metrics [Source: `_bmad-output/planning-artifacts/architecture.md` — §3 Observability] |
| PRD | Per-source health (success rate, latency) visible and actionable [Source: `prd.md` — Success measures / ingestion] |
| Rates | Define denominator: recommend **completed fetch attempts** (success + terminal connector/dedup failure), excluding skips |

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Stack | FastAPI, SQLAlchemy 2 async, Alembic [Source: `architecture.md` — Data / API] |
| API | REST JSON + OpenAPI [Source: `architecture.md` — API] |
| Layout | `api/routes/sources.py` (or sibling route module), `db/repositories/sources.py`, `services/connectors/poll.py`, `db/models.py`, `alembic/versions/` |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **prometheus_client** | **Not** in `requirements.txt` today — add **only** if you implement Prometheus scrape; NFR9 can be satisfied with **JSON API** first. |
| **SQLAlchemy** | Use existing async session patterns from `sources_repo`. |

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/services/connectors/poll.py` | Update counters / timestamps on success and failure tails |
| `src/sentinel_prism/db/repositories/sources.py` | New helpers: e.g. `merge_poll_metrics_*` or field updates |
| `src/sentinel_prism/db/models.py` | New columns or document JSON shape |
| `src/sentinel_prism/api/routes/sources.py` | New GET route(s) + response models |
| `alembic/versions/` | Migration when schema changes |
| `tests/` | Metrics behavior coverage |

### Testing requirements

- **No live network** in default CI — reuse connector mocking from Stories 2.3–2.5.
- Assert **counter monotonicity** and correct handling when a source has **never** succeeded (null `last_success_at`).

### UX / product notes

- **Full console UI** is out of scope; a **documented JSON endpoint** satisfies “UI placeholder” in the epic until Epic 6 wires widgets [Source: `epics.md` — Story 2.6 AC].

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2, Story 2.6]
- [Source: `_bmad-output/planning-artifacts/prd.md` — NFR9, ingestion success measures]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — Observability, §4 Data, §6 boundaries]
- [Source: `_bmad-output/implementation-artifacts/2-5-fallback-endpoints-and-enable-disable.md` — `execute_poll` outcomes and metadata patterns]

## Previous story intelligence (Story 2.5)

- **`poll_completed`** emits `item_count` and `fetch_latency_ms` **after** dedup registers new items — metrics should align with **`len(new_items)`**, not raw fetched item count before dedup.
- **Fallback:** `fetch_outcome` is `primary` or `fallback`; consider exposing **optional** `last_success_fetch_path` in metrics for operators debugging fallback reliance.
- **Review hardening:** Avoid bare exception handlers that bypass structured failure recording; keep **fetch_path** attribution patterns when logging.
- **Files to extend:** `poll.py`, `sources_repo`, `models.py`, `sources.py` routes, new tests.

## Git intelligence summary

- Recent commits on `main` may predate local **2.5** work; treat **`poll.py`** and **`sources_repo.record_poll_failure` / `clear_poll_failure`** as the current baseline [verify with `git log` on your branch].

## Latest technical information (implementation time)

- **FastAPI 0.115.x** and **Pydantic v2** — use `model_validate` / typed response models consistent with existing `SourceCreate` patterns.
- If you later add Prometheus, pin **`prometheus_client`** explicitly in `requirements.txt` and expose a dedicated route (avoid blocking the event loop).

## Project context reference

- No **`project-context.md`** in repo; **`architecture.md`**, **`prd.md`**, **`epics.md`**, and prior story files under `_bmad-output/implementation-artifacts/` are authoritative.

## Story completion status

- **done** — Implementation complete, code review applied. Full `pytest` green (75 passed, 8 skipped).

## Change Log

- 2026-04-17 — Story 2.6 implemented: typed `sources` columns for poll attempt counters, items ingested, last success/failure timestamps, last success latency and fetch path; `record_poll_failure` increments failed attempts with `last_failure_at` aligned to `last_poll_failure.at`; `record_poll_success_metrics` on successful dedup tail; `GET /sources/metrics` and `GET /sources/{id}/metrics` (Admin RBAC); migration `a7f6e5d4c3b2`; tests in `test_source_metrics.py`, RBAC/unauth coverage, connector test mock updates.
- 2026-04-17 — Code review patches applied (D1 + 10 P-level findings): atomic counter UPDATEs via `UPDATE … SET col = col + :n` + JSONB `||` merge to eliminate lost-update race; `GET /sources/metrics` pagination (`limit`/`offset`); unsupported `source_type` auto-disable (D1 option 2) via new `sources_repo.disable_source`; `BigInteger` counters (migration amended in place, not yet applied); `fetch_path` strictly validated to `"primary"|"fallback"` in repo; `SourceMetricsResponse.last_success_fetch_path` narrowed to `Literal`; `LastPollFailurePayload.at` typed as `datetime` with best-effort ISO parse + legacy drop; `last_success_at` sourced from `fetched_at` (moment-of-fetch, not commit); `assert` replaced by explicit `RuntimeError` (survives `python -O`); added 7 default-CI unit tests covering AC4 skip invariants, auto-disable, fallback-path attribution, dedup-after-success failure recording, and repo fetch_path validation.

## Dev Agent Record

### Agent Model Used

Composer

### Debug Log References

### Completion Notes List

- **Persistence:** Typed columns on `sources` (not JSONB) for straightforward SQL filtering and stable API mapping.
- **Rates:** `success_rate` / `error_rate` are `null` when there are zero completed attempts; otherwise `success / (success + failed)` and `failed / (success + failed)` (dedup failures count as failed attempts via `record_poll_failure`).
- **AC3:** `SourceMetricsResponse.last_poll_failure` mirrors `extra_metadata` when present; cleared after success path like today.
- **AC4:** Skips (missing source, disabled, unsupported type before fetch) do not call `record_poll_failure` except unsupported type still records failure — counts as a completed failure attempt per product path.

### File List

- `alembic/versions/a7f6e5d4c3b2_add_source_ingestion_metrics.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/sources.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `src/sentinel_prism/api/routes/sources.py`
- `tests/test_connectors_rss_http.py`
- `tests/test_source_metrics.py`
- `tests/test_sources.py`
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-6-per-source-metrics-exposure.md`
- `_bmad-output/implementation-artifacts/deferred-work.md` (appended code-review deferrals)

