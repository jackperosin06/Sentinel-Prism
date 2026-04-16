# Story 2.1: Source registry CRUD API and persistence

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **admin**,
I want **to create, read, update sources** with metadata (jurisdiction, endpoints, schedule),
so that **the system knows what to poll** (**FR1**).

## Acceptance Criteria

1. **Given** an authenticated user with the **admin** role  
   **When** they **POST** `/sources` with a **valid** payload  
   **Then** a new source row is persisted with a stable **`id`** (UUID)  
   **And** the response returns the created source (including **`id`**) with **201**.

2. **Given** an authenticated **admin**  
   **When** they **GET** `/sources`  
   **Then** they receive a JSON list of all sources (order: stable, e.g. by **`created_at`** ascending or **`name`** — document the chosen rule in OpenAPI description).

3. **Given** an authenticated **admin**  
   **When** they **GET** `/sources/{source_id}` for an existing id  
   **Then** they receive that source’s JSON representation  
   **When** the id does not exist  
   **Then** the API returns **404**.

4. **Given** an authenticated **admin**  
   **When** they **PATCH** `/sources/{source_id}` with allowed fields  
   **Then** persisted fields update and the response reflects the new state (**200**).

5. **Validation:** Requests with **missing required fields** on create (see Dev Notes for the required set) return **422** with a clear validation error body (FastAPI/Pydantic default shape is acceptable).

6. **Authorization:** Unauthenticated callers receive **401**. Authenticated **non-admin** users (analyst/viewer) receive **403** on all routes in this story (match Story 1.4 `require_roles` patterns).

7. **Scope guard — out of scope for 2.1:** **No** scheduled polling, **no** connector HTTP fetch, **no** deduplication, **no** metrics emission, **no** LangGraph runs — those belong to **Stories 2.2–2.6 / Epic 3**. Persist **`schedule`** and **endpoint(s)** as data only.

## Tasks / Subtasks

- [x] **Model & migration** (AC: #1, #5)
  - [x] Add a **`Source`** (or agreed table name) ORM model in `src/sentinel_prism/db/models.py` with at least:
    - `id` (UUID PK), `name`, `jurisdiction`, `source_type` (string or small enum — e.g. `rss`, `http` — align naming with PRD “type”),
    - **Endpoints:** store **primary URL** as a column **or** a structured field — prefer **`primary_url`** (text, required) plus optional **`metadata` JSONB** for extra endpoints/fallback placeholders **without** implementing fallback **behavior** (that is Story 2.5),
    - **`schedule`:** string acceptable for MVP (e.g. cron expression or documented interval string) — must be non-empty on create; validation in Pydantic,
    - **`enabled`:** boolean, default **true** (supports FR6 / Story 2.5 flows later without a breaking migration),
    - `created_at` / `updated_at` (timezone-aware), consistent with `User`.
  - [x] Add **Alembic** revision under `alembic/versions/` that creates the table; **`alembic upgrade head`** succeeds on empty DB after prior revisions.
- [x] **Persistence layer** (AC: #1–#4)
  - [x] Implement async CRUD helpers — either `src/sentinel_prism/db/repositories/sources.py` (preferred for Epic 2 growth) **or** a thin `services/sources/` module — **do not** put SQL in route handlers beyond one-liner delegation.
- [x] **API** (AC: #1–#6)
  - [x] Add `src/sentinel_prism/api/routes/sources.py` with router prefix **`/sources`**, tags **`sources`** [Source: `architecture.md` §6 tree — `sources.py`].
  - [x] Pydantic v2 schemas: create body, update body (partial), list/response model — mirror patterns in `api/routes/auth.py` (field validators where needed).
  - [x] Protect all endpoints with `Depends(require_roles(UserRole.ADMIN))` from `api/deps.py`.
  - [x] Register router in `main.py` (`include_router`).
- [x] **Tests** (AC: #1–#6)
  - [x] New tests (e.g. `tests/test_sources.py`): admin can CRUD; viewer/analyst get **403**; missing fields **422**; unknown id **404**. Reuse auth fixtures / patterns from `tests/test_auth.py` / `tests/test_rbac.py`.
  - [x] Skip integration tests when **`DATABASE_URL`** unset (same convention as existing auth tests).
- [x] **Docs**
  - [x] OpenAPI will auto-generate; optional README bullet under API section listing **`/sources`** admin endpoints.

### Review Findings

- [x] [Review][Defer] Two DB sessions per admin request — accepted as-is (intentional 401-before-DB optimization; low-traffic admin API). Revisit when pool pressure becomes observable. [`src/sentinel_prism/api/deps.py:48–97`] — deferred by user decision
- [x] [Review][Patch] Add UNIQUE constraint on `sources.name` — migration `d4f6b8e0a2c1` added; `unique=True` on model column. [`src/sentinel_prism/db/models.py`, `alembic/versions/d4f6b8e0a2c1_add_unique_sources_name.py`]
- [x] [Review][Patch] `SourceResponse.created_at` and `updated_at` typed as `object` — fixed to `datetime` with proper import. [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Patch] Integration test `assert len(lst.json()) == 1` — replaced with `assert sid in [s["id"] for s in lst.json()]`. [`tests/test_sources.py`]
- [x] [Review][Patch] `primary_url` URL format validation — added `field_validator` with HTTP/HTTPS regex to `SourceCreate` and `SourceUpdate`. [`src/sentinel_prism/api/routes/sources.py`]
- [x] [Review][Defer] `schedule` has no format validation — opaque string accepted — Story 2.2 scheduler will need a contract. [`src/sentinel_prism/api/routes/sources.py:29–34`] — deferred, Story 2.2 concern
- [x] [Review][Defer] Detached `User` returned from `get_current_user` after its session closes — safe with scalar attributes now, fragile if relationships added later. [`src/sentinel_prism/api/deps.py:72–88`] — deferred, pre-existing architectural tradeoff
- [ ] [Review][Patch] Migration `alembic/versions/d4f6b8e0a2c1_add_unique_sources_name.py` is untracked (git `??`) — UNIQUE constraint on `sources.name` is not committed; DB and ORM model disagree until it is [`alembic/versions/d4f6b8e0a2c1_add_unique_sources_name.py`]
- [ ] [Review][Patch] Integration test hard-codes `"EMA RSS"` source name — unique-constraint violation on second run without full DB wipe; add UUID suffix like other tests [`tests/test_sources.py:113`]
- [ ] [Review][Patch] `DELETE /{source_id}` untested for 403 (non-admin) — 2.1 AC6 requires 403 on *all* routes [`tests/test_sources.py`]
- [ ] [Review][Patch] Admin `DELETE` 204 (success) and 404 (missing id) paths absent from `test_sources.py` — only covered in 2.2 test file [`tests/test_sources.py`]
- [ ] [Review][Patch] `list_sources` sort order non-deterministic for same-millisecond inserts — add secondary `id` tiebreaker for truly stable order per AC2 [`src/sentinel_prism/db/repositories/sources.py:17-19`]
- [ ] [Review][Patch] `get_db_for_admin` return type annotation is `AsyncGenerator[AsyncSession, None]` — FastAPI DI resolves the yielded `AsyncSession` correctly but the annotation misleads mypy/pyright [`src/sentinel_prism/api/deps.py:109`]
- [x] [Review][Defer] URL regex `^https?://\S+` accepts `http://x` and any non-whitespace path — pre-existing; MVP acceptable; replace with `AnyHttpUrl` during hardening [`src/sentinel_prism/api/routes/sources.py:33`] — deferred, pre-existing

## Dev Notes

### Epic 2 context

- **Goal:** Admins manage **public** sources; later stories add **scheduler**, **connectors**, **dedup**, **fallback**, **metrics** [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2].
- **This story:** **FR1** only — **registry + API + persistence**. Establishes the **`source_id`** foreign key target for ingestion and graph work later.
- **Follow-on:** Story **2.2** will **invoke** connector entrypoints by `source_id`; keep **`id`** stable and **schema** extensible (JSONB is fine for forward-compatible fields).

### Developer context (guardrails)

- **RBAC:** Reuse **`require_roles(UserRole.ADMIN)`** — do **not** fork a parallel permission system [Source: Story 1.4 / `api/deps.py`].
- **Auth transport:** Same **Bearer JWT** as existing routes; no new auth mechanism.
- **REST/OpenAPI:** FastAPI generates OpenAPI — keep models explicit for the React console later [Source: `architecture.md` §4 API].
- **Postgres:** System of record for **source registry** per architecture [Source: `architecture.md` §4 Data].

### Technical requirements

- **Stack:** FastAPI (async), SQLAlchemy 2 async session, Pydantic v2 — consistent with Epic 1 [Source: `architecture.md` §2, §6].
- **DB session:** `Depends(get_db)` from `db/session.py` — same as auth routes.
- **Errors:** Use **404** for missing source; **403** for wrong role; **401** unauthenticated — align with existing HTTP semantics.

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Routes | `api/routes/sources.py` [Source: `architecture.md` §6 directory tree] |
| Connectors | **Do not** implement connector logic in this story — `services/connectors/` stays unused or stub-only [Source: `architecture.md` FR1–FR6 mapping — Scout path is `services/connectors/`, `graph/nodes/scout.py`] |
| Graph / workers | **No** new graph nodes or `workers/` scheduling — Story 2.2 |

### Library / framework requirements

- No new major dependencies expected; use existing **SQLAlchemy**, **FastAPI**, **Alembic**, **Pydantic** pins from `requirements.txt`.
- If you introduce a **cron** validation library, justify minimal footprint; otherwise validate with **simple rules** (non-empty string + optional length bounds) and document format in field description.

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/db/models.py` | `Source` model |
| `alembic/versions/<rev>_add_sources_table.py` (example name) | DDL for `sources` |
| `src/sentinel_prism/db/repositories/sources.py` (recommended) | Async CRUD |
| `src/sentinel_prism/api/routes/sources.py` | REST handlers |
| `src/sentinel_prism/main.py` | Register `sources` router |
| `tests/test_sources.py` | API + auth matrix |

### Testing requirements

- **`python -m pytest`** green locally and in CI when DB available.
- Cover **happy path** + **403** for non-admin + **422** validation + **404** — minimum parity with RBAC test thoroughness.

### UX / product notes

- **Admin-only** configuration aligns with UX **Jordan (Admin)** configuring sources [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — personas / admin surfaces]. No frontend required in this story.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 2, Story 2.1]
- [Source: `_bmad-output/planning-artifacts/prd.md` — **FR1**]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §4 Data, §6 project structure & FR mapping]

## Previous story intelligence (Epic 1 completion)

- **Story 1.5:** `AuthProvider` wiring, **`require_roles`**, JWT **`sub`** → user — unchanged; sources routes only add **admin-gated** domain API [Source: `1-5-auth-provider-interface-stub-for-future-idp.md`].
- **Migrations:** Follow Alembic + `ALEMBIC_SYNC_URL` / `DATABASE_URL` conventions from Story **1.2** README [Source: `1-2-postgresql-schema-core-and-alembic-migrations.md`].
- **Patterns:** Prefer **thin routes**, **services/repositories** for DB — avoid duplicating user/password patterns.

## Git intelligence summary

- Latest relevant commit: **`debe39c`** — “Complete Epic 1: secure platform foundation (Stories 1.2–1.5)”. Baseline **`api/deps.py`**, **`services/auth/`**, **`db/models.py`** (`User` only) — Epic 2 adds first **non-user** domain table.

## Latest technical information (implementation time)

- Pin versions in **`requirements.txt`** at implementation time; do not treat any version numbers inside this story as authoritative — verify against PyPI/repo constraints [Source: `architecture.md` §2].

## Project context reference

- No **`project-context.md`** found in repo; use this file + `architecture.md` + `prd.md` as ground truth.

## Story completion status

- Implementation complete. Code review applied 4 patches: datetime typing, URL validation, unique name constraint, test robustness fix. Status: **done**.

## Change Log

- 2026-04-16 — Story 2.1: `sources` table + Alembic `a3c5e7d9f1b2`, admin `/sources` CRUD API, `get_db_for_admin`, `get_current_user` opens DB only after Bearer token parses; tests and README updated.
- 2026-04-16 — Code review patches: `datetime` typing on `SourceResponse`, HTTP/HTTPS `field_validator` on `primary_url`, unique name index migration `d4f6b8e0a2c1`, robust test list assertion.

## Dev Agent Record

### Agent Model Used

Composer (dev-story workflow)

### Debug Log References

### Completion Notes List

- Implemented `Source` ORM + migration, `db/repositories/sources.py`, `api/routes/sources.py`, registered in `main.py`.
- Added `get_db_for_admin` so admin routes do not resolve a standalone `get_db` before RBAC; refactored `get_current_user` to avoid opening Postgres when no/invalid Bearer token (401 paths work without `DATABASE_URL` in tests).
- `tests/test_sources.py`: unit 401 check + integration matrix (skipped without DB).
- Updated `tests/test_alembic_cli.py` head revision assertion; documented `/sources` in README.

### File List

- `alembic/versions/a3c5e7d9f1b2_add_sources_table.py`
- `alembic/versions/d4f6b8e0a2c1_add_unique_sources_name.py`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/api/routes/sources.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/sources.py`
- `src/sentinel_prism/main.py`
- `tests/test_sources.py`
- `tests/test_alembic_cli.py`
- `README.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-1-source-registry-crud-api-and-persistence.md`

