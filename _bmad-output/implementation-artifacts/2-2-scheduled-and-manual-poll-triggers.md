# Story 2.2: Scheduled and manual poll triggers

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **to poll or trigger fetches per source schedule**,
so that **ingestion runs reliably** (**FR2**).

## Acceptance Criteria

1. **Given** a **enabled** source with a **valid** `schedule`  
   **When** the **in-process scheduler** fires for that source  
   **Then** the **connector poll entrypoint** is invoked **asynchronously** with that source’s **`source_id`** (UUID)  
   **And** **disabled** sources are **not** registered for scheduled fires (**FR6** alignment with Story 2.5 — scheduled path only).

2. **Given** an authenticated **admin**  
   **When** they **POST** `/sources/{source_id}/poll` for an **existing**, **enabled** source  
   **Then** the **same** poll entrypoint runs (shared code path with the scheduler)  
   **And** the response is **202 Accepted** (or **204 No Content** if you prefer empty body — document the chosen contract in OpenAPI).

3. **Given** an authenticated **admin**  
   **When** they **POST** `/sources/{source_id}/poll` for a **missing** `source_id`  
   **Then** the API returns **404**.

4. **Given** an authenticated **admin**  
   **When** they **POST** `/sources/{source_id}/poll` for a **disabled** source  
   **Then** the API returns **409 Conflict** (or **403** — pick one and document; **409** recommended: “source disabled”).

5. **Authorization:** Unauthenticated callers **401**. Authenticated **non-admin** **403** on the poll route (same pattern as Story 2.1 / `require_roles`).

6. **Schedule contract (closes 2.1 review deferral):** Define and enforce a **single** schedule format for MVP — recommend **standard 5-field cron**: `minute hour day month day_of_week` (APScheduler / `croniter`-compatible). Sources with **invalid** cron on **create**/**PATCH** return **422** with a clear message. **Document** the format in OpenAPI field descriptions for `schedule` on create/update.

7. **Lifecycle:** Scheduler **starts** with the FastAPI app and **shuts down cleanly** on app shutdown (no orphaned tasks). After **admin** creates/updates/deletes a source, **scheduled jobs** reflect the change **without** requiring process restart (reload or upsert jobs for that `source_id`).

8. **Scope guard — out of scope for 2.2:** **No** real HTTP/RSS fetch (**Story 2.3**), **no** deduplication/retry (**2.4**), **no** fallback routing (**2.5**), **no** LangGraph run (**Epic 3**). The connector entrypoint may be a **stub** that **structured-logs** `source_id` + trigger kind (`scheduled` vs `manual`) so **NFR8** correlation can attach `run_id` later.

## Tasks / Subtasks

- [x] **Dependency** (AC: #1, #6, #7)
  - [x] Add **`APScheduler`** (async-capable) to `requirements.txt` with a **pinned** version; prefer **`AsyncIOScheduler`** + async job callable so jobs align with **asyncpg** / FastAPI stack [Source: `architecture.md` §2 Job scheduling].
- [x] **Schedule validation** (AC: #6)
  - [x] Central helper e.g. `validate_cron_expression(schedule: str) -> None` used by **`SourceCreate`** / **`SourceUpdate`** in `api/routes/sources.py` (or a small `services/sources/schedule.py` module if routes get crowded).
- [x] **Connector entrypoint (stub)** (AC: #1, #2, #8)
  - [x] Define a narrow protocol/async function in `services/connectors/` e.g. `async def execute_poll(source_id: uuid.UUID, *, trigger: Literal["scheduled", "manual"]) -> None` — **Story 2.3** replaces body with real fetch; **do not** import graph modules.
- [x] **Worker / scheduler service** (AC: #1, #6, #7)
  - [x] Implement under `src/sentinel_prism/workers/` (e.g. `poll_scheduler.py`): load **enabled** sources from DB, `add_job` per source with **stable job id** (`poll:{source_id}`), `replace_existing=True`, remove job when source deleted or disabled.
  - [x] Wire **`lifespan`** in `main.py`: start scheduler after auth provider init; `shutdown(wait=True)` on exit.
  - [x] Expose `refresh_jobs_for_source(source_id)` / `sync_all_sources` callable from a small module the routes can call after mutating writes (avoid duplicating DB access in routes — reuse `sources` repository).
- [x] **Manual trigger route** (AC: #2–#5)
  - [x] Add **`POST /sources/{source_id}/poll`** on the existing **`sources`** router, **`require_roles(ADMIN)`**, delegate to the same `execute_poll` used by scheduler (inject via `Depends` or app-scoped singleton for test overrides).
- [x] **Tests** (AC: #1–#5)
  - [x] Extend or add tests (e.g. `tests/test_poll_triggers.py`): admin manual poll **202/204**; **404**; **409** when disabled; **403** non-admin; **422** invalid cron on PATCH; scheduler smoke (optional: use `pytest-asyncio` + short-lived scheduler or mock `add_job` — **must** prove wiring doesn’t regress startup).
  - [x] Preserve convention: skip DB-dependent tests when **`DATABASE_URL`** unset (match `tests/test_sources.py`).
- [x] **Docs**
  - [x] README: one bullet that API process runs an **in-process** poll scheduler and lists **`POST /sources/{id}/poll`**.

### Review Findings

- [ ] [Review][Patch] `sync_all_sources` is a no-op at startup — `self._started` guard fires before the flag is set, so zero jobs are registered on restart (violates AC #1 and #7) [`src/sentinel_prism/workers/poll_scheduler.py:51-65,127-129`]
- [ ] [Review][Patch] `_remove_job_if_exists` silences all exceptions — should catch only APScheduler `JobLookupError` [`src/sentinel_prism/workers/poll_scheduler.py:84-90`]
- [ ] [Review][Patch] `updated_at` has no DB-level `BEFORE UPDATE` trigger in sources migration — staleness after every PATCH (ORM `onupdate` is bypassed by raw SQL) [`alembic/versions/a3c5e7d9f1b2_add_sources_table.py`]
- [ ] [Review][Patch] `"run_id": None` hardcoded in every `execute_poll` structured log line — poisons log indexers before Epic 3 wires a real run id [`src/sentinel_prism/services/connectors/poll.py:22-28`]
- [ ] [Review][Patch] `refresh_jobs_for_source` guards only on `self._started`; missing `_scheduler is None` guard → `AttributeError` if scheduler is in a partially-torn-down state [`src/sentinel_prism/workers/poll_scheduler.py:146-153`]
- [x] [Review][Defer] TOCTOU race: `_run_scheduled_poll` checks `row.enabled`, closes the session, then calls `execute_poll` outside it — Story 2.3 concern when real fetch is added [`src/sentinel_prism/workers/poll_scheduler.py:92-100`] — deferred, pre-existing
- [x] [Review][Defer] `execute_poll` exceptions propagate uncaught from both scheduled and manual paths — stub in 2.2; relevant when 2.3 adds real network calls [`src/sentinel_prism/services/connectors/poll.py`] — deferred, pre-existing
- [x] [Review][Defer] `shutdown(wait=True)` may block the event loop indefinitely once real fetch jobs exist — acceptable for stub-only 2.2; revisit in 2.3 [`src/sentinel_prism/workers/poll_scheduler.py:70`] — deferred, pre-existing
- [x] [Review][Defer] Multi-process scheduler divergence — each Uvicorn worker has an independent in-process scheduler; single-worker MVP; revisit before horizontal scale [`src/sentinel_prism/workers/poll_scheduler.py`] — deferred, pre-existing

## Dev Notes

### Epic 2 context

- **Goal:** Reliable **polling** and **triggers** before **connectors**, **dedup**, **retry**, **metrics** [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2].
- **This story:** **FR2** + **NFR6** groundwork (idempotent **windows** fully realized in **2.4**; scheduler should pass a logical **poll instant** into the stub if useful for later idempotency keys).
- **Follow-on:** **2.3** implements fetch; **2.4** adds fingerprint dedup + backoff; keep **poll** API stable.

### Developer context (guardrails)

- **RBAC:** Reuse **`require_roles(UserRole.ADMIN)`** and existing **`get_db_for_admin`** patterns [Source: `2-1-source-registry-crud-api-and-persistence.md` — Dev Notes].
- **Thin routes:** Scheduler sync and poll execution live in **`workers/`** + **`services/connectors/`**, not inside route bodies beyond delegation.
- **Boundaries:** **`services/`** must **not** import **`graph/`** [Source: `architecture.md` §6 Boundaries].
- **Logging:** Use structured key-value logging (or `extra={}`) including **`source_id`**, **`trigger`**, and placeholder for **`run_id`** when Epic 3 wires runs.

### Technical requirements

- **Stack:** FastAPI lifespan, SQLAlchemy 2 async, APScheduler with asyncio — consistent with **§2** stack [Source: `architecture.md` §2].
- **DB:** Reuse **`Source`** model fields: `schedule`, `enabled`, `id` [Source: `src/sentinel_prism/db/models.py`].
- **Errors:** **404** missing source; **403** RBAC; **401** unauthenticated; **422** validation; **409** disabled manual poll (if chosen).

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Workers | `src/sentinel_prism/workers/` — scheduled jobs [Source: `architecture.md` §6 tree] |
| Connectors | Stub lives under `services/connectors/`; real HTTP/RSS in **2.3** [Source: `architecture.md` FR mapping FR1–FR6] |
| Graph | **Do not** start LangGraph runs in this story |

### Library / framework requirements

- **APScheduler:** Pin in `requirements.txt`; use **`AsyncIOScheduler`** for FastAPI process integration. Verify **cron** field order matches your validation helper and documented API contract.
- **Optional:** `croniter` only if you need validation without starting the scheduler — otherwise APScheduler’s **`CronTrigger`** parse errors may suffice for **422** messages.

### File structure requirements

| Path | Purpose |
| --- | --- |
| `requirements.txt` | `APScheduler` pin |
| `src/sentinel_prism/main.py` | Lifespan start/stop scheduler |
| `src/sentinel_prism/workers/poll_scheduler.py` (or equivalent) | Job registration + DB sync |
| `src/sentinel_prism/services/connectors/` | `execute_poll` stub + protocol |
| `src/sentinel_prism/api/routes/sources.py` | Cron validation + `POST .../poll` |
| `tests/test_poll_triggers.py` (or merged with `test_sources.py`) | Auth + behavior matrix |

### Testing requirements

- **`python -m pytest`** green locally/CI when DB available.
- Cover **admin manual path**, **RBAC denial**, **disabled behavior**, **invalid cron**, and **scheduler lifecycle** at least at smoke level.

### UX / product notes

- **Admin-only** manual trigger aligns with **Jordan (Admin)** operating sources [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — persona context]. No frontend required.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2, Story 2.2]
- [Source: `_bmad-output/planning-artifacts/prd.md` — **FR2**, **NFR6**, **NFR8**]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §2 Job scheduling, §6 `workers/` tree, Boundaries]

## Previous story intelligence (Story 2.1)

- **`get_db_for_admin` / `get_current_user`:** Admin routes avoid opening DB before RBAC; **reuse** — do not reintroduce pre-RBAC `get_db` on new poll route [Source: `2-1-source-registry-crud-api-and-persistence.md` — Completion Notes].
- **`schedule` was opaque** in 2.1; **this story** defines **cron validation** and OpenAPI docs [Source: `2-1-source-registry-crud-api-and-persistence.md` — Review deferral].
- **Repositories:** Prefer `db/repositories/sources.py` for loading sources for the scheduler [Source: `2-1-source-registry-crud-api-and-persistence.md` — File List].
- **Unique `name`:** Already on `Source`; unrelated to polling but don’t break CRUD when adding validation.

## Git intelligence summary

- Recent baseline: Epic 1 completion commit **`debe39c`**; Story **2.1** artifacts describe **`sources`** module patterns (local working tree may be ahead of last pushed commit).

## Latest technical information (implementation time)

- Pin **APScheduler** (and any cron helper) at implementation time; confirm **async** job API for your chosen major version — follow project convention: **no** version numbers in this story as authoritative; verify against PyPI and existing pins [Source: `architecture.md` §2 Version pinning].

## Project context reference

- No **`project-context.md`** in repo; treat **`architecture.md`**, **`prd.md`**, this file, and **`2-1-...`** as ground truth.

## Story completion status

- **review** — Implementation complete; all tasks done; full test suite green (`26 passed`, integration tests skip without DB).

## Change Log

- 2026-04-16 — Implemented APScheduler `AsyncIOScheduler`, five-field UTC cron validation, `execute_poll` stub with structured logging, `PollScheduler` sync/refresh, `POST /sources/{id}/poll` (202), `DELETE /sources/{id}` for job cleanup, README and `tests/test_poll_triggers.py`.

## Dev Agent Record

### Agent Model Used

Composer

### Debug Log References

### Completion Notes List

- Pinned **APScheduler 3.10.4**; scheduler starts only when **`DATABASE_URL`** is set so unit tests without Postgres still load the app.
- Manual and scheduled paths both call **`services/connectors/poll.execute_poll`**; admin routes use **`Depends(get_poll_executor)`** for test overrides.
- **`DELETE /sources/{id}`** added to satisfy AC7 (job removed via `refresh_jobs_for_source` when row is gone).
- FastAPI **204** delete uses **`response_class=Response`** and returns an empty **`Response`**.

### File List

- `requirements.txt`
- `README.md`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/api/routes/sources.py`
- `src/sentinel_prism/db/repositories/sources.py`
- `src/sentinel_prism/services/sources/__init__.py`
- `src/sentinel_prism/services/sources/schedule.py`
- `src/sentinel_prism/services/connectors/__init__.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `src/sentinel_prism/workers/__init__.py`
- `src/sentinel_prism/workers/poll_scheduler.py`
- `tests/test_poll_triggers.py`
- `tests/test_sources.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-2-scheduled-and-manual-poll-triggers.md`

