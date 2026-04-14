# Deferred work tracker

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
