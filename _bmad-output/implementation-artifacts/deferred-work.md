# Deferred work tracker

## Deferred from: code review of 2-1 and 2-2 (2026-04-16)

- **TOCTOU race in `_run_scheduled_poll`:** Checks `row.enabled`, closes session, then calls `execute_poll` outside it — gap widens when Story 2.3 adds real network fetch; re-check atomically or pass enabled state into `execute_poll`. [`poll_scheduler.py:92-100`]
- **`execute_poll` exceptions not caught from scheduled/manual paths:** Stub is silent but real fetches can raise; add structured error handling and logging in Story 2.3. [`services/connectors/poll.py`]
- **`shutdown(wait=True)` may block on long-running jobs:** Safe with stub; revisit when 2.3 adds real I/O-bound fetches — consider `wait=False` + job-level timeout. [`workers/poll_scheduler.py:70`]
- **Multi-process scheduler divergence:** Each Uvicorn worker runs its own in-process APScheduler instance — jobs multiply and refresh calls are not propagated across workers. Acceptable for single-worker MVP; requires a distributed job backend (Celery/ARQ) before horizontal scale. [`workers/poll_scheduler.py`]
- **URL regex too permissive:** `^https?://\S+` accepts `http://x`; replace with Pydantic `AnyHttpUrl` during a security/hardening pass. [`api/routes/sources.py:33`]

## Deferred from: code review of 2-1-source-registry-crud-api-and-persistence.md (2026-04-16)

- **`schedule` string has no format validation:** Accepted as any non-empty string; Story 2.2 scheduler will need to parse it. Establish a cron or interval string contract at Story 2.2.
- **Detached `User` from `get_current_user`:** `get_current_user` closes its session and returns a detached ORM object. Safe with scalar columns only; add a note when ORM relationships are added to `User` (Epic 7+).
- **Two DB sessions per admin request (accepted):** `get_current_user` opens an internal session for JWT→user lookup; `get_db_for_admin` opens a second for the route body. Intentional design — ensures 401 paths never open Postgres. Revisit if pool pressure becomes observable at scale.


## Deferred from: code review of 1-5-auth-provider-interface-stub-for-future-idp.md (2026-04-15)

- **`verify_email_password` name won't fit OIDC:** Method name embeds email/password concepts; a future OIDC provider will need a different method signature or a second interface method. Rename or extend interface at OIDC story.
- **Providers lack class-level docstrings:** `LocalAuthProvider` and `StubAuthProvider` have no class docstring. Style/polish; address during a documentation pass.
- **`AuthProvider` Protocol not `@runtime_checkable`:** Providers don't inherit Protocol; `isinstance` checks silently fail. Acceptable now (no `isinstance` in codebase), but add `@runtime_checkable` if runtime type checks ever become needed.
- **`User(password_hash="x")` in test fixture:** Minor fragility if ORM field-level validators are added to `password_hash` later. Revisit when hardening test fixtures.

## Deferred from: code review of 1-4-rbac-enforcement-on-api-routes.md (2026-04-14)

- **asyncio.run in sync unit tests:** Using `asyncio.run` inside `def` tests rather than `async def` + pytest-asyncio is a growing friction point; refactor when test suite expands.
- **Per-request DB read for user role:** `get_current_user` always hits the DB; acceptable for MVP but consider JWT role claims or a short-TTL cache when traffic grows.
- **No admin endpoint for role promotion:** Integration test uses raw SQL `UPDATE` to set roles; a proper admin API endpoint for role management is planned for Epic 2+.

## Deferred from: code review of 1-3-local-user-accounts-and-session-or-token-auth.md (2026-04-14)

- **updated_at stale without PostgreSQL trigger:** SQLAlchemy `onupdate=func.now()` is ORM-level only; direct SQL UPDATEs will not refresh `updated_at`. Add a DB trigger or document the limitation when moving to production.
- **Broad database errors unhandled at service/route level:** Connection failures and timeouts in `get_user_by_email`, `create_user`, `get_user_by_id` propagate as generic 500. Implement a cross-cutting error-handling middleware or per-route guards in a future error-handling epic.
- **Security hardening gaps:** No rate limiting on auth endpoints, no token revocation/rotation, no `iss`/`aud` JWT claims. Revisit for production hardening before public launch. Also: `POST /auth/register` returns 409 for duplicate email (intentional UX choice); mitigate enumeration risk when rate limiting is added.
- **Engine/session singleton reset via private attribute in integration test:** `session_mod._engine = None` is fragile. Add a public `reset_engine()` helper in `db/session.py` to avoid future cross-test pollution.

## Deferred from: code review (.env / .env.example) (2026-04-14)

- **Missing keys in `.env.example`:** `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `SLACK_WEBHOOK_URL`, `SECRET_KEY` exist in `.env` but are undocumented in `.env.example`. Add placeholder entries when those services are first wired up (Epic 3/5).

## Deferred from: code review of 1-1-initialize-application-skeleton-per-architecture.md (2026-04-13)

- **npm audit (high, transitive):** Revisit `web/` dependency tree (`npm audit`, upgrades, or pin overrides) when doing a security pass — not blocking Story 1.1 acceptance criteria.
- **docker-compose scope:** File was optional local tooling; ensure Epic 1 / Story 1.2 docs stay aligned if compose becomes canonical for dev DB.
