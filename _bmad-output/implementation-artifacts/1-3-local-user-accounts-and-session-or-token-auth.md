# Story 1.3: Local user accounts and session or token auth

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **to sign in with email/password or magic link (MVP choice)**,
so that **only authenticated users access the console** (**FR39**, **FR46**).

## Acceptance Criteria

1. **Given** a **registered** user (email + password) **when** they **POST** valid credentials to the documented **login** endpoint **then** the API returns **HTTP 200** with a **token** (or session artifact) usable on subsequent API calls **and** the response includes a stable **`user_id`** (same id as stored in DB) (**FR46**).
2. **Given** an unauthenticated request **when** calling a **documented protected** endpoint (e.g. current-user profile) **without** a valid token **then** the API returns **401**.
3. **Given** registration **when** a client **POST**s a new email + password meeting policy **then** a user row is persisted **and** the password is **never** returned or logged (**NFR3**).
4. **Password policy (**NFR4**):** passwords below minimum strength are **rejected** with **422** (or **400**) and a clear error body; document the rules in README or OpenAPI description.
5. **Scope guard:** **No** role-based route enforcement (**FR40**) — that is **Story 1.4**. **No** `auth_provider` / IdP plug-in surface — that is **Story 1.5**; use **local** verify only, structured so it can move behind an interface later.

## Tasks / Subtasks

- [x] **Dependencies & config** (AC: #1, #3–4)
  - [x] Add pinned packages to `requirements.txt`: **async**-compatible stack (e.g. **`bcrypt`** or **`argon2-cffi`**, **PyJWT** or **python-jose** with explicit crypto deps, **`pwdlib`** or **`passlib`** if used — pick one hashing approach and pin); **`python-multipart`** for form/json auth routes; **`email-validator`** if using Pydantic `EmailStr`.
  - [x] Extend **`.env.example`** with **non-secret** placeholders: e.g. **`JWT_SECRET`** (or reuse documented **`SECRET_KEY`** — pick **one** name, document in README), **`JWT_ALGORITHM`** (default `HS256`), **`JWT_EXPIRE_MINUTES`** (reasonable demo default, e.g. 60). **NFR3:** never commit real secrets.
- [x] **Persistence: `User` model + migration** (AC: #1, #3, #5)
  - [x] Define **`User`** on `Base` in `src/sentinel_prism/db/models.py`: stable primary key (**UUID** recommended), **unique `email`**, **`password_hash`**, **`is_active`** (default true), **`created_at`** / **`updated_at`** (timezone-aware or UTC).
  - [x] Add **Alembic revision** after current head (`e7d4f1a08c2b`) creating `users` table + indexes (unique on email). No `role` column yet unless you need a harmless default for 1.4 — **prefer** omitting roles until **Story 1.4** to avoid duplicating RBAC work.
- [x] **Async DB session** (AC: #1–3)
  - [x] Add `src/sentinel_prism/db/session.py`: **`create_async_engine`** from **`DATABASE_URL`**, **`async_sessionmaker`**, FastAPI **`Depends`**-able session scope (request-scoped async context pattern per SQLAlchemy 2.x async docs).
- [x] **Auth service layer (local only)** (AC: #1, #3–5)
  - [x] Under `src/sentinel_prism/services/auth/` (or equivalent): **hash password**, **verify password**, **create user**, **authenticate user** (by email); keep **no imports** from `graph/`.
  - [x] **MVP auth mode:** implement **email + password** only; **defer magic link** to a follow-up story unless PM explicitly expands scope (magic link needs outbound email + token store).
- [x] **API routes** (AC: #1–2)
  - [x] Add `src/sentinel_prism/api/routes/auth.py` (wire in `main.py`): e.g. **`POST /auth/register`**, **`POST /auth/login`**, **`GET /auth/me`** (Bearer JWT). Use Pydantic schemas; return OpenAPI-friendly models.
  - [x] Implement **JWT** access token containing **`sub`** = **`user_id`** (stringified UUID) and **`exp`** aligned with **NFR4** session/expiry intent; document clock skew tolerance (none required for MVP).
- [x] **Dependencies** (AC: #2)
  - [x] Extend `api/deps.py`: **`get_current_user`** (optional → raises 401), **`oauth2_scheme` or HTTPBearer** — one consistent pattern for the API.
- [x] **Tests** (AC: #1–4)
  - [x] Unit tests: password policy rejection, hash verify round-trip (no plaintext storage).
  - [x] Integration tests: register → login → `/auth/me` with token; missing token → 401. Use **real Postgres** via `DATABASE_URL` or **`pytest-asyncio`** + DB fixture — **avoid** mocking away the ORM for the main path; if CI has no Postgres, **`pytest.mark.integration`** skip pattern (same as Story 1.2 Alembic test).

## Dev Notes

### Epic 1 context

- **Order:** 1.1 → 1.2 → **1.3 auth** → **1.4 RBAC** → **1.5 auth provider**. Do **not** implement Admin/Analyst/Viewer checks in this story.
- **FRs:** **FR39** (authenticate to console), **FR46** (stable internal identity for future IdP mapping).

### Technical requirements (must follow)

- **Stack:** FastAPI **async**, PostgreSQL, SQLAlchemy 2 **async** session — align with **`DATABASE_URL`** (`postgresql+asyncpg://`) [Source: `architecture.md` §2, §4].
- **Auth transport:** Architecture allows **JWT or session cookies** — this story standardizes on **Bearer JWT** in `Authorization` for the SPA + OpenAPI clarity [Source: `architecture.md` §4 Auth].
- **Boundaries:** **`api/routes/auth.py`** + **`services/auth/`**; **no** `graph/` imports from services [Source: `architecture.md` §6].
- **Future:** **`auth_provider`** interface lands in **1.5** — keep local verification in dedicated functions/classes today [Source: `architecture.md` §4; `epics.md` Story 1.5].

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Auth location | `api/routes/auth.py`, identity in `db/models.py` [Source: `architecture.md` §6 tree] |
| API style | REST JSON, OpenAPI from FastAPI [Source: `architecture.md` §4 API] |
| Audit-ready id | Same `user_id` in token subject and DB (**FR46**) |

### Library / framework requirements

- Pin versions at implementation time on **PyPI**; prefer **maintained** JWT + password libraries compatible with Python **3.11+**.
- Do **not** log request bodies for login/register.

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/db/models.py` | `User` model |
| `src/sentinel_prism/db/session.py` | Async engine + session factory |
| `src/sentinel_prism/services/auth/` | Hashing, user lookup, JWT create/verify helpers |
| `src/sentinel_prism/api/routes/auth.py` | Register / login / me |
| `src/sentinel_prism/api/deps.py` | `get_db`, `get_current_user` |
| `src/sentinel_prism/main.py` | Include `auth` router |
| `alembic/versions/*.py` | New revision for `users` |
| `requirements.txt`, `.env.example`, `README.md` | Deps + env + how to obtain token |

### Testing requirements

- All new tests pass with full suite green before story → review.
- Document how to run integration tests (env vars) in README or story Dev Agent Record.

### UX / product notes

- Console UI login form is **Epic 6**; this story is **API-only** — contract must be suitable for future SPA consumption.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 1.3]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §4 Auth, §6 structure]
- [Source: `_bmad-output/planning-artifacts/prd.md` — FR39, FR46, NFR3, NFR4, NFR14 (future)]

## Previous story intelligence (Story 1.2)

- **DB:** `Base` + `metadata` in `db/models.py`; Alembic env uses **`ALEMBIC_SYNC_URL`**; app uses **`DATABASE_URL`** with `+asyncpg` [Source: Story 1.2 file list].
- **Migrations:** Add new revision **after** `e7d4f1a08c2b`; run `alembic upgrade head` after adding models.
- **Tests:** `pytest` + optional integration marker pattern exists (`tests/test_alembic_cli.py`).

## Git intelligence summary

- Monorepo skeleton and health route established; **`api/routes/auth.py`** is net-new for this story.

## Latest technical information (implementation time)

- Use current **SQLAlchemy 2.0 async** session patterns (`AsyncSession`, `async with session.begin()`).
- Prefer **Argon2id** or **bcrypt** with sensible cost parameters; confirm library defaults against OWASP **Password Storage Cheat Sheet** at implementation time.

## Project context reference

- No `project-context.md` in repo; use Architecture + PRD + this file.

## Story completion status

- **Status:** done
- **Note:** Code review complete; all patches applied.

## Review Findings

### Decision Needed
- [x] [Review][Defer] Account enumeration via 409 on register — keeping 409 intentionally; enumeration concern deferred to rate-limiting story alongside broader security hardening.

### Patches
- [x] [Review][Patch] IntegrityError too broad on register — narrowed to pgcode `23505` (unique_violation); unexpected integrity errors are re-raised [`src/sentinel_prism/api/routes/auth.py`]
- [x] [Review][Patch] verify_password catches only VerifyMismatchError — broadened to `Argon2Error` base class; covers invalid/corrupt hash formats [`src/sentinel_prism/services/auth/passwords.py`]
- [x] [Review][Patch] JWT_ALGORITHM unconstrained — added allowlist `{"HS256", "HS384", "HS512"}`; raises `RuntimeError` on invalid algorithm [`src/sentinel_prism/services/auth/tokens.py`]
- [x] [Review][Patch] No upper bound on password length — added `max_length=128` to both `RegisterRequest.password` and `LoginRequest.password` [`src/sentinel_prism/api/routes/auth.py`]
- [x] [Review][Patch] users.id lacks server_default in migration — added `server_default=sa.text("gen_random_uuid()")` [`alembic/versions/c4f9e2b18d0a_add_users_table.py`]
- [x] [Review][Patch] users.is_active lacks server_default in migration — added `server_default=sa.true()` [`alembic/versions/c4f9e2b18d0a_add_users_table.py`]
- [x] [Review][Patch] JWT_EXPIRE_MINUTES silently degrades — added `logging.warning` on bad value and cap at 10080 (1 week) [`src/sentinel_prism/services/auth/tokens.py`]
- [x] [Review][Patch] .env.example credential mismatch — DATABASE_URL now uses `sentinel:change-me-local-only` to match ALEMBIC_SYNC_URL [`.env.example`]
- [x] [Review][Patch] JWT_SECRET missing at runtime → unhandled RuntimeError — added FastAPI `lifespan` startup hook that calls `_secret()` and `_algorithm()` to fail fast at boot [`src/sentinel_prism/main.py`]

### Deferred
- [x] [Review][Defer] updated_at stale without PostgreSQL trigger — SQLAlchemy `onupdate=func.now()` is ORM-level only; direct SQL UPDATEs will not refresh `updated_at` [`src/sentinel_prism/db/models.py:36`] — deferred, pre-existing architectural pattern
- [x] [Review][Defer] Broad database errors unhandled at service/route level — connection failures and timeouts in `get_user_by_email`, `create_user`, `get_user_by_id` propagate as generic 500 [`src/sentinel_prism/services/auth/service.py`] — deferred, cross-cutting concern for later error-handling epic
- [x] [Review][Defer] Security hardening gaps — no rate limiting, no token revocation, no `iss`/`aud` claims — out of MVP scope for Story 1.3; revisit for production hardening [`src/sentinel_prism/services/auth/tokens.py`] — deferred, out of scope
- [x] [Review][Defer] Engine/session singleton reset via private attribute in integration test — `session_mod._engine = None` is fragile; a public `reset_engine()` helper would be cleaner [`tests/test_auth.py:76`] — deferred, test infrastructure improvement

## Open questions (non-blocking)

- **Magic link:** Deferred unless product insists — requires email delivery and token persistence.
- **Refresh tokens:** Out of scope for MVP; single access token + re-login acceptable for demos.

## Change Log

- **2026-04-15:** Story 1.3 authored — local email/password, JWT, `User` table, async session, explicit 1.4 / 1.5 scope guards.
- **2026-04-14:** Implemented Story 1.3 — Argon2 password hashing, JWT Bearer auth, Alembic `users` migration `c4f9e2b18d0a`, unit + integration tests.

---

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Chose **argon2-cffi** (Argon2id via `PasswordHasher`) and **PyJWT** (HS256) with env **`JWT_SECRET`**, **`JWT_ALGORITHM`**, **`JWT_EXPIRE_MINUTES`**.
- **`POST /auth/login`** returns `access_token`, `token_type`, and **`user_id`** matching DB primary key; **`GET /auth/me`** requires `Authorization: Bearer`.
- Register enforces password policy (Pydantic **422**); duplicate email → **409**. No RBAC or `auth_provider` surface (Stories 1.4 / 1.5).
- Integration test `tests/test_auth.py::test_register_login_me_and_unauthorized` runs when **`DATABASE_URL`** and **`ALEMBIC_SYNC_URL`** are set; applies `alembic upgrade head` then exercises register → login → me and401 without token.

### File List

- `requirements.txt`
- `.env.example`
- `pyproject.toml`
- `README.md`
- `alembic/versions/c4f9e2b18d0a_add_users_table.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/session.py`
- `src/sentinel_prism/services/auth/__init__.py`
- `src/sentinel_prism/services/auth/passwords.py`
- `src/sentinel_prism/services/auth/tokens.py`
- `src/sentinel_prism/services/auth/service.py`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/api/routes/auth.py`
- `src/sentinel_prism/main.py`
- `tests/test_auth.py`
- `tests/test_db_models.py`
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/1-3-local-user-accounts-and-session-or-token-auth.md`
