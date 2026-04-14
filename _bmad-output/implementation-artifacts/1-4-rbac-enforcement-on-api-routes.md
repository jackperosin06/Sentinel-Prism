# Story 1.4: RBAC enforcement on API routes

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **administrator**,
I want **roles (Admin / Analyst / Viewer minimum)** enforced on APIs,
so that **configure vs view vs review** paths are protected (**FR40**).

## Acceptance Criteria

1. **Given** a **Viewer** user with a valid JWT **when** they call an endpoint reserved for **Admin** **then** the API returns **HTTP 403** with a clear error body (not 401).
2. **Given** an **Analyst** user **when** they call an endpoint that allows **Analyst or Admin** **then** the request succeeds (**200** or the route’s normal success code).
3. **Given** a **Viewer** user **when** they call an endpoint that allows **any authenticated** role **then** the request succeeds.
4. **Given** an **unauthenticated** request **when** calling a protected route **then** the API returns **401** (existing auth behavior preserved).
5. **Role resolution** is **centralized**: route handlers do not duplicate `if user.role == ...` logic; use **FastAPI dependencies** (or a single reusable dependency factory) in `api/deps.py` — aligns with epic wording “middleware/dependency” (prefer **dependency** for explicit OpenAPI/testing).
6. **Persistence:** Each `User` has a **single role** stored in PostgreSQL; new registrations default to **Viewer** unless you document a different product default (default **Viewer** is safest for least privilege).
7. **Scope guard:** **No** `auth_provider` / IdP plug-in — that is **Story 1.5**. **No** new domain features (sources, review queue, graph) — only RBAC plumbing + **small exemplar routes** proving403/200 behavior.

## Tasks / Subtasks

- [x] **Model & migration** (AC: #6)
  - [x] Add a **`role`** column on `User` in `src/sentinel_prism/db/models.py` using a **Python `StrEnum`** or `enum.Enum` mapped to PostgreSQL (either `sa.Enum` / native PG enum, or `String(32)` + app-level validation — pick one approach and document it in Dev Notes).
  - [x] Add **Alembic revision** after current head (`c4f9e2b18d0a`) that adds the column with **`server_default`** for existing rows (e.g. `'viewer'`) so upgrades on non-empty DBs do not fail.
  - [x] Ensure **`CREATE TYPE`** / enum migrations are **downgrade-safe** if you use a native enum (follow Alembic patterns for PG enums).
- [x] **Auth integration** (AC: #4–5)
  - [x] Extend **`create_user`** (or registration path) in `services/auth/service.py` so new users persist **`role=viewer`** (or constant).
  - [x] Keep **`get_current_user`** as the base dependency; add **`require_roles(*roles)`** (or equivalent) that depends on `get_current_user` and raises **`HTTPException(403)`** when `current_user.role` is not allowed. Use a **403** detail string that does not leak internal policy diagrams (e.g. “Insufficient permissions”).
- [x] **Exemplar protected routes** (AC: #1–3, #7)
  - [x] Add **`api/routes/rbac_demo.py`** (or `admin_health.py` — name clearly as **temporary exemplar**, not product API) mounted under a prefix like **`/rbac-demo`** with **three** `GET` routes:
    - **Admin-only** — Viewer and Analyst → **403**; Admin → **200**.
    - **Analyst-or-above** — Viewer → **403**; Analyst and Admin → **200**.
    - **Any authenticated** — all roles → **200** (validates dependency composes with `get_current_user`).
  - [x] Wire router in **`main.py`**; tag for OpenAPI so `/docs` shows the examples.
- [x] **Tests** (AC: #1–4)
  - [x] **Regression:** existing **`tests/test_auth.py`** integration test (`register → login → /auth/me`) must remain **green** after `users.role` migration (update fixtures if response bodies or DB constraints change).
  - [x] **Unit tests:** dependency behavior with **fake `User`** objects (no DB) for role allow/deny matrix.
  - [x] **Integration tests** (`@pytest.mark.integration`): reuse Story 1.3 pattern (`DATABASE_URL`, `ALEMBIC_SYNC_URL`, `alembic upgrade head`). Create **three users** (or mutate roles via SQL/`UPDATE`) with **Admin / Analyst / Viewer**; obtain JWTs via **`/auth/login`**; assert **403 vs 200** on exemplar routes. Skip when DB env not set (same as `tests/test_auth.py`).
- [x] **Documentation**
  - [x] Update **`README.md`**: role names, default role on register, and **how to promote a user to Admin** for local dev (e.g. `UPDATE users SET role = 'admin' WHERE email = ...` — adjust to your enum/storage choice).
  - [x] Do **not** log passwords or tokens.

## Dev Notes

### Epic 1 context

- **Order:** 1.1 → 1.2 → 1.3 → **1.4 RBAC** → **1.5 auth provider**. Story 1.3 deliberately omitted roles; this story adds them.
- **FRs:** **FR40** (role-based permissions for view, review, configure, administer). PRD permission model: **Admin** (sources, routing, users), **Analyst** (review, override, feedback), **Viewer** (read-only) [Source: `_bmad-output/planning-artifacts/prd.md` — Permission Model (RBAC)].

### PRD / product mapping (for future routes)

Use this **intent** when naming roles and ordering privilege (highest → lowest):

| Role | Typical capabilities (product intent) |
|------|----------------------------------------|
| **admin** | Configure sources, routing, users; all analyst/viewer capabilities |
| **analyst** | Review, override, feedback; viewer read paths |
| **viewer** | Read-only console/API paths |

Exemplar routes in this story **stand in** for future “configure” vs “review” vs “view” endpoints; Epic 2+ routes should attach the same dependencies.

### Technical requirements (must follow)

- **Stack:** FastAPI **async**, PostgreSQL, SQLAlchemy 2 **async** session, Alembic — same as Story 1.3 [Source: `architecture.md` §2, §4].
- **Identity:** Stable **`user_id`** in JWT **`sub`** unchanged; role may be read from **DB** on each request via `get_current_user` (simplest MVP). Optional: add **`role`** to JWT claims in a **later** story if perf requires it — **not required** for 1.4 acceptance.
- **Boundaries:** RBAC dependencies live in **`api/deps.py`**; keep **`services/auth/`** focused on credential verification and user lookup — **avoid** importing **`graph/`** from services [Source: `architecture.md` §6].
- **Errors:** **401** = not authenticated or invalid token; **403** = authenticated but not allowed for this route.

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Auth / RBAC location | `api/deps.py`, identity + role on `User` in `db/models.py` [Source: `architecture.md` §6 tree, FR39–FR41 mapping] |
| API style | REST JSON, OpenAPI from FastAPI [Source: `architecture.md` §4 API] |
| Future IdP | Story 1.5 introduces **`auth_provider`**; keep role checks **orthogonal** to how the user authenticated [Source: `architecture.md` §4 Auth, `epics.md` Story 1.5] |

### Library / framework requirements

- Prefer **stdlib `enum`** + SQLAlchemy mapping; no new dependency unless justified (e.g. `fastapi-rbac` third-party — **avoid** unless team standard).
- Pin any new package in **`requirements.txt`** if added.

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/db/models.py` | `User.role` |
| `alembic/versions/*.py` | Revision: add `role` column (+ enum type if used) |
| `src/sentinel_prism/services/auth/service.py` | Default role on `create_user` |
| `src/sentinel_prism/api/deps.py` | `get_current_user`, `require_roles` (or factory) |
| `src/sentinel_prism/api/routes/rbac_demo.py` (or agreed name) | Exemplar protected routes |
| `src/sentinel_prism/main.py` | Include exemplar router |
| `tests/test_rbac.py` (or extend `test_auth.py`) | Unit + integration tests |
| `README.md` | Roles, defaults, local admin promotion |

### Testing requirements

- Full **`python -m pytest`** green before story → review.
- Integration tests follow Story 1.3 env pattern: **`DATABASE_URL`**, **`ALEMBIC_SYNC_URL`**, **`JWT_SECRET`**, `pytest -m integration` when DB available.

### UX / product notes

- Console RBAC UX is **Epic 6**; this story is **API-only**. Exemplar routes exist for **contract testing** and **developer clarity** only.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 1, Story 1.4]
- [Source: `_bmad-output/planning-artifacts/prd.md` — FR40, Permission Model (RBAC)]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §4 Auth, §6 structure]
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — RBAC personas (console)]

## Previous story intelligence (Story 1.3)

- **`User`** table exists: UUID `id`, `email`, `password_hash`, `is_active`, timestamps; **no `role` yet** — add in this story with migration after **`c4f9e2b18d0a`**.
- **Auth:** `HTTPBearer`, **`get_current_user`** in `api/deps.py`, JWT via **`services/auth/tokens.py`** (`sub` = user id); **`lifespan`** in `main.py` validates `JWT_SECRET` / allowed algorithms at startup.
- **Registration:** `POST /auth/register` defaults new users to a role in **this** story (recommend **`viewer`**); duplicate email → **409** (pgcode `23505`); passwords max **128** chars, Argon2 hashing.
- **Tests:** `pytest-asyncio` `auto` mode; **`@pytest.mark.integration`** skips without DB; integration tests may reset **`sentinel_prism.db.session`** engine singleton between runs.
- **Do not** regress Story 1.3 behavior: `/auth/login`, `/auth/me`, password policy, and JWT validation must remain intact.

## Git intelligence summary

- Recent commits on `main` are mostly planning/skeleton; **Story 1.3 implementation** may be local/uncommitted in your workspace — treat **`src/sentinel_prism/api/deps.py`**, **`services/auth/`**, **`db/models.py`**, and **`alembic/versions/c4f9e2b18d0a_*.py`** as the baseline to extend.

## Latest technical information (implementation time)

- Use **SQLAlchemy 2.0** `Mapped[...]` + `mapped_column` for the new field; keep timezone-aware timestamps unchanged.
- For PostgreSQL **native enum**, prefer creating the type in Alembic in the **same** revision as `ALTER TABLE users ADD COLUMN role ...` to avoid drift.

## Project context reference

- No `project-context.md` in repo; use Architecture + PRD + this file.

## Story completion status

- **Status:** review
- **Note:** Implementation complete; all tasks done. Run `code-review` next.

## Review Findings

### Decision Needed
- [x] [Review][Decision] `/rbac-demo` routes permanently expand production route table — resolved: **remove from `main.py`**, test-only via fixture.
- [x] [Review][Decision] `create_user` exposes `role` keyword parameter — resolved: **remove kwarg**, force VIEWER; role changes via future dedicated endpoint.

### Patches
- [x] [Review][Patch] Alembic `server_default="viewer"` bare string — changed to `sa.text("'viewer'")` for explicit PostgreSQL DDL [`alembic/versions/b2a8c3d19e4f_add_user_role_column.py`]
- [x] [Review][Patch] `require_roles()` with zero arguments silently denies all — added `if not allowed: raise ValueError` guard [`src/sentinel_prism/api/deps.py`]
- [x] [Review][Patch] DB role column has no CHECK constraint — added `CHECK (role IN ('admin','analyst','viewer'))` via `op.create_check_constraint`; downgrade drops it first [`alembic/versions/b2a8c3d19e4f_add_user_role_column.py`]
- [x] [Review][Patch] Invalid persisted role value causes ORM 500 — added `UserRole(user.role)` validation in `get_current_user`; returns 403 with admin contact message [`src/sentinel_prism/api/deps.py`]
- [x] [Review][Patch] Integration test overrides not cleaned up on failure — wrapped `asyncio.run(_run())` in `try/finally` for `.clear()` [`tests/test_rbac.py`]
- [x] [Review][Patch] 403 response body not asserted in tests — integration test now asserts `r.json()["detail"] == "Insufficient permissions"` on viewer 403 cases [`tests/test_rbac.py`]
- [x] [Review][Patch] `/auth/me` does not expose user role — added `role` field to `MeResponse`; `me()` now returns `role=current.role.value` [`src/sentinel_prism/api/routes/auth.py`]

### Deferred
- [x] [Review][Defer] `asyncio.run` in sync unit test — using `asyncio.run` inside a `def` test rather than `async def` + pytest-asyncio is a friction point as the suite grows; refactor to async test when convenient [`tests/test_rbac.py:96`] — deferred, test infrastructure improvement
- [x] [Review][Defer] Per-request DB read for role with no caching — `get_current_user` always queries by user_id; acceptable for MVP but noted for future JWT claims or caching layer — deferred, performance concern for later
- [x] [Review][Defer] Integration test promotes roles via raw SQL UPDATE — validates the happy path but no product admin endpoint for role promotion yet; endpoint planned for Epic 2+ — deferred, out of Story 1.4 scope

## Open questions (non-blocking)

- **Exact exemplar path prefix** (`/rbac-demo` vs `/internal/rbac`) — pick one and delete before v1 if undesired; prefer **clearly non-product** naming.
- **JWT `role` claim:** optional future optimization; out of scope unless you need to reduce DB reads.

## Change Log

- **2026-04-14:** Story 1.4 authored — Admin/Analyst/Viewer, centralized deps, migration, exemplar routes, integration tests, Story 1.5 scope guard.
- **2026-04-14:** Story 1.4 implemented — `UserRole` + `users.role` (VARCHAR + SQLAlchemy `Enum` non-native), Alembic `b2a8c3d19e4f`, `require_roles` in `deps.py`, `/rbac-demo` routes, `tests/test_rbac.py`, README RBAC section, Alembic head test updated.

---

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- **`UserRole`** (`StrEnum`): `admin`, `analyst`, `viewer`. Persisted as **32-char string** (no PostgreSQL `CREATE TYPE`); `server_default='viewer'` on column + migration.
- **`require_roles(*allowed)`** returns a FastAPI dependency that composes **`get_current_user`** and returns **403** with detail `Insufficient permissions` when role not in allowed set.
- **`/rbac-demo/*`** exemplar routes documented in README; OpenAPI tag **`rbac-demo`**.
- **Tests:** nine parametrized unit cases (dependency overrides on mini app); **`test_rbac_integration_role_matrix`** marked `@pytest.mark.integration`. **`test_alembic_cli`** head revision updated to **`b2a8c3d19e4f`**.

### File List

- `src/sentinel_prism/db/models.py`
- `alembic/versions/b2a8c3d19e4f_add_user_role_column.py`
- `src/sentinel_prism/services/auth/service.py`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/api/routes/rbac_demo.py`
- `src/sentinel_prism/main.py`
- `tests/test_rbac.py`
- `tests/test_db_models.py`
- `tests/test_alembic_cli.py`
- `README.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/1-4-rbac-enforcement-on-api-routes.md`

