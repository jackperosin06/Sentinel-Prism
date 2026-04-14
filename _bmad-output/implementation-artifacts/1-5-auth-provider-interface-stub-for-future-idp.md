# Story 1.5: Auth provider interface stub for future IdP

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **credential verification behind a pluggable interface**,
so that **OIDC/SAML can be added without rewriting RBAC** (**NFR14**).

## Acceptance Criteria

1. **Given** an **`auth_provider` abstraction** (protocol or ABC) for **credential verification** on login   **When** the **local** provider is selected (default)  
   **Then** **`POST /auth/login`** still resolves the same **`User`** row and issues a JWT whose **`sub`** is that user’s UUID (**unchanged** behavior vs Story 1.3–1.4).

2. **Given** a **second provider implementation** (explicit **stub** — e.g. always fails verification or is a no-op placeholder)  
   **When** it is **registered** in the same factory/registry as the local provider  
   **Then** **route handlers** do **not** call `authenticate_user` (or raw password verify) **directly**; they depend on the **active** provider from a **single wiring point** (app state, settings-backed factory, or FastAPI dependency).

3. **Given** **`get_current_user`** / **`require_roles`**  
   **When** a user presents a valid JWT  
   **Then** behavior is **unchanged** (401/403 semantics as today). RBAC stays **orthogonal** to how the user first authenticated.

4. **Scope guard:** **No** OIDC/SAML/OAuth HTTP flows, **no** IdP redirect URLs, **no** new persistence for IdP subject mapping unless you add a clearly labeled **optional** follow-up (default: **out of scope** — epic only requires interface + local + stub).

5. **Configuration:** Document how operators choose the active provider (e.g. **`AUTH_PROVIDER=local`** in `.env` / `.env.example`); default **`local`**.

## Tasks / Subtasks

- [x] **Define abstraction** (AC: #1–2)
  - [x] Add `AuthProvider` (prefer **`typing.Protocol`** or ABC) under `src/sentinel_prism/services/auth/` (e.g. `providers/protocol.py` + `providers/local.py` + `providers/stub.py`).
  - [x] Minimal method surface: async verification that takes **`AsyncSession` + normalized email + password** (or equivalent) and returns **`User | None`** — align with current `authenticate_user` signature to avoid churn.
- [x] **Local provider** (AC: #1)
  - [x] Implement by delegating to existing `authenticate_user` in `services/auth/service.py` (or move core logic into a shared private helper — **do not** duplicate Argon2/lookup rules).
- [x] **Stub provider** (AC: #2)
  - [x] Second class that satisfies the protocol and is safe to import (e.g. always returns `None`, or raises a controlled internal error type — pick one and test it).
- [x] **Wiring** (AC: #2, #5)
  - [x] Single factory/module: `get_auth_provider() -> AuthProvider` driven by settings (extend existing settings pattern if present; else minimal `os.environ` / pydantic settings consistent with Story 1.3).
  - [x] **`POST /auth/login`** in `api/routes/auth.py` uses the provider’s verify method only.
- [x] **Tests** (AC: #1–3)
  - [x] **Regression:** existing `tests/test_auth.py` flows stay green (register → login → `/auth/me`).
  - [x] **Unit:** stub provider never returns a user; local provider matches matrix for fake session / mocked `get_user_by_email` if needed (follow patterns from `tests/test_rbac.py`).
  - [x] Optional: dependency override or env switch proves login uses stub when configured (expects 401).
- [x] **Docs**
  - [x] **`README.md`**: one short subsection — pluggable auth provider, env var, future IdP.
  - [x] **`.env.example`**: `AUTH_PROVIDER=local` (and stub value if useful for dev).

## Dev Notes

### Epic 1 context

- **Order:** 1.1 → 1.2 → 1.3 → 1.4 → **1.5 (this story)**. After 1.5, Epic 1 is complete from an auth-foundation perspective before Epic 2 domain work.
- **FRs / NFRs:** **FR46** (stable internal identity for future federation), **NFR14** (credential verification behind a provider interface) [Source: `_bmad-output/planning-artifacts/prd.md`].
- **PRD intent:** Future **IdP / SSO** uses a **pluggable provider**, **stable internal user IDs**, and later a mapping from **IdP subject → user** — **mapping table is not required** for this story unless you explicitly split a follow-up [Source: `_bmad-output/planning-artifacts/prd.md` — Authentication architecture / SaaS].

### Developer context (guardrails)

- **Why:** Today `login` calls `authenticate_user` directly. NFR14 requires that **verification** be swappable so OIDC/SAML can plug in later **without** rewriting **`api/deps.py`** RBAC or JWT shape.
- **JWT contract:** Keep **`sub` = `user.id`** (UUID string) and existing **`create_access_token` / `decode_access_token`** — federated login in a future story should still mint the **same** token shape for **`get_current_user`**.
- **Registration:** **`POST /auth/register`** remains **local account creation** (password hash). Only **login verification** must go through **`AuthProvider`** unless you have a strong reason to generalize registration (out of epic scope).

### Technical requirements

- **Stack:** FastAPI async, SQLAlchemy 2 async session — unchanged [Source: `_bmad-output/planning-artifacts/architecture.md` §4, §6].
- **Boundaries:** Provider implementations live under **`services/auth/`**; routes stay thin in **`api/routes/auth.py`**; RBAC stays in **`api/deps.py`** [Source: `_bmad-output/planning-artifacts/architecture.md` — FR39–FR46 mapping table].
- **Errors:** Preserve **401** + **`WWW-Authenticate: Bearer`** on bad login; do not leak which provider rejected credentials beyond existing `"Invalid email or password"` message.

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Provider location | `services/auth/` (new `providers/` subpackage is appropriate) [Source: `architecture.md` §6 tree — `services/` for auth logic] |
| Routes | `api/routes/auth.py` — no new public endpoints required for the stub |
| RBAC | Unchanged; role checks remain independent of provider [Source: Story 1.4 file — Future IdP note] |

### Library / framework requirements

- Prefer **`typing.Protocol`** (stdlib) for the interface; **no** new PyPI dependency for DI.
- If introducing **Pydantic Settings** for `AUTH_PROVIDER`, reuse project conventions; otherwise keep env read minimal.

### File structure requirements

| Path | Purpose |
| --- | --- |
| `src/sentinel_prism/services/auth/providers/*.py` | Protocol + local + stub + optional registry/factory |
| `src/sentinel_prism/services/auth/service.py` | Keep `authenticate_user` as implementation detail the **local** provider calls |
| `src/sentinel_prism/api/routes/auth.py` | `login` uses injected/active `AuthProvider` |
| `src/sentinel_prism/main.py` | Only if lifespan/app state needed for provider singleton (prefer pure factory if possible) |
| `.env.example` / `README.md` | Document `AUTH_PROVIDER` |
| `tests/test_auth.py` (+ new `tests/test_auth_provider.py` if cleaner) | Regression + unit coverage |

### Testing requirements

- Full **`python -m pytest`** green before review.
- Integration tests follow Story 1.3 pattern: skip when **`DATABASE_URL`** unset; no weaker assertions on security-sensitive paths.

### UX / product notes

- No console/UI change; API-only plumbing.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 1, Story 1.5]
- [Source: `_bmad-output/planning-artifacts/prd.md` — FR46, NFR14, Authentication architecture]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §4 Auth, §6 structure & mapping]

## Previous story intelligence (Story 1.4)

- **`UserRole`**, **`require_roles`**, **`get_current_user`** with JWT `sub` → DB user — **do not** refactor RBAC for this story.
- **`authenticate_user`**, **`create_user`** (default **viewer**), Argon2 passwords — reuse; **avoid** duplicating password rules.
- Story1.4 review noted **`/rbac-demo`** and role promotion via SQL for tests — irrelevant except **keep** tests green.
- **`MeResponse`** exposes **`role`** — unchanged.

## Git intelligence summary

- Recent commits on tracked history are mostly skeleton/planning; treat **current workspace** `services/auth/`, `api/routes/auth.py`, `api/deps.py` as the **live** baseline (may include uncommitted Story 1.2–1.4 work).

## Latest technical information (implementation time)

- **Python 3.11+** `Protocol` with `@runtime_checkable` only if you need `isinstance` checks — otherwise omit for simplicity.
- Keep async DB access **async end-to-end** in the provider method to match FastAPI route style.

## Project context reference

- No `project-context.md` in repo; use Architecture + PRD + this file.

## Story completion status

- **Status:** review
- **Note:** Implementation complete; full test suite green. Run `code-review` next.

## Open questions (non-blocking; defer to implementation)

- Exact env var name (`AUTH_PROVIDER` vs `SENTINEL_AUTH_PROVIDER`) — pick one and match `.env.example`.
- Whether stub is selectable in production builds — document as **dev/test only** if concerned.

---

## Review Findings

### Decision Needed
_(none)_

### Patches
- [x] [Review][Patch] `ValueError` for unknown `AUTH_PROVIDER` surfaces as HTTP 500 at login time; add startup validation in `lifespan` to fail-fast on misconfiguration [`src/sentinel_prism/services/auth/providers/factory.py`, `src/sentinel_prism/main.py`]
- [x] [Review][Patch] Email normalization is an implicit caller contract, not documented on `AuthProvider` Protocol — add docstring note to `verify_email_password` that `email` should already be lowercased [`src/sentinel_prism/services/auth/providers/protocol.py`]

### Deferred
- [x] [Review][Defer] `verify_email_password` name embeds `email`/`password` concepts — won't fit future OIDC token flows; rename or extend interface at OIDC story — deferred, future IdP story concern
- [x] [Review][Defer] `LocalAuthProvider` and `StubAuthProvider` lack class-level docstrings — deferred, style/polish
- [x] [Review][Defer] Providers don't inherit `Protocol`; `isinstance` checks against `AuthProvider` silently fail without `@runtime_checkable` — deferred, acceptable for current no-`isinstance` codebase
- [x] [Review][Defer] `User(password_hash="x")` in test fixture — minor future fragility if ORM validators tighten — deferred, test infrastructure

## Change Log

- **2026-04-15:** Implemented `AuthProvider` protocol, local + stub providers, `get_auth_provider()` factory, `get_login_auth_provider` dependency, login wired through provider; tests and README / `.env.example` updates.
- **2026-04-15:** Code review patches applied — startup validation for `AUTH_PROVIDER` in `lifespan`; email normalization contract documented on Protocol.

---

## Dev Agent Record

### Agent Model Used

GPT-5.1 (Cursor agent)

### Debug Log References

### Completion Notes List

- Added `services/auth/providers/` with `AuthProvider` protocol (`verify_email_password`), `LocalAuthProvider` (delegates to `authenticate_user`), `StubAuthProvider` (always `None`), and `get_auth_provider()` reading **`AUTH_PROVIDER`** (`local` default, `stub` supported; unknown → `ValueError`).
- **`api/deps.py`:** `get_login_auth_provider()` — single injection point for **`POST /auth/login`**.
- **`api/routes/auth.py`:** login uses `Depends(get_login_auth_provider)`; no direct `authenticate_user` in the route.
- **Tests:** `tests/test_auth_provider.py` (unit); `tests/test_auth.py::test_login_with_stub_provider_always_401` (integration, optional AC).
- **Docs:** README subsection; `.env.example` **`AUTH_PROVIDER=local`**.
- **Regression:** `python -m pytest tests/` →22 passed, 4 skipped (integration skips without DB).

### File List

- `src/sentinel_prism/services/auth/providers/__init__.py`
- `src/sentinel_prism/services/auth/providers/protocol.py`
- `src/sentinel_prism/services/auth/providers/local.py`
- `src/sentinel_prism/services/auth/providers/stub.py`
- `src/sentinel_prism/services/auth/providers/factory.py`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/api/routes/auth.py`
- `tests/test_auth_provider.py`
- `tests/test_auth.py`
- `.env.example`
- `README.md`
- `_bmad-output/implementation-artifacts/1-5-auth-provider-interface-stub-for-future-idp.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
