# Story 5.2: In-app notifications

Status: review

<!-- Ultimate context engine analysis completed — comprehensive developer guide created -->

## Story

As a **user**,
I want **in-app notifications for routed items**,
so that **I see work inside the console** (**FR24**).

## Acceptance Criteria

1. **Persisted in-app notifications (FR24)**  
   **Given** the pipeline has produced **`routing_decisions`** with resolved **`team_slug`** (and severity) per Story 5.1  
   **When** a decision is eligible for in-app delivery (see Dev Notes: severity + team targeting rules)  
   **Then** durable rows exist in PostgreSQL that represent “this user should see this notification,” keyed by user + run/item context  
   **And** Alembic migration(s) create the table(s) with indexes for listing by user and time (newest first).

2. **User ↔ team targeting**  
   **Given** mock routing uses string **`team_slug`** (see `RoutingRule`, `resolve_routing_decision` output)  
   **When** notifications are materialized  
   **Then** each notification row is associated with users whose **team membership** matches that slug (implement **one** clear rule: e.g. nullable **`users.team_slug`** column matching routing slug, or a small **`user_teams`** table—pick one, document it, and test it)  
   **And** users without a team assignment receive **no** team-targeted notifications (avoid silent mis-delivery).

3. **“Routed critical item” path (epic AC)**  
   **Given** classification severities are **`critical` | `high` | `medium` | `low`** (see `services/llm/classification.py`)  
   **When** the epic says “routed **critical** item”  
   **Then** in-app notifications are created at minimum for **`severity == "critical"`** (document if **`high`** is included under a named policy flag or config constant).

4. **Service boundary (Architecture §6, FR21–FR25 mapping)**  
   **Given** **`graph/nodes/route.py`** already evaluates rules and populates **`routing_decisions`**  
   **When** this story is complete  
   **Then** notification persistence runs via **`services/notifications/`** (expand the stub package) with **repository + async session** patterns—**nodes must not embed raw SQL** for notification inserts (match **`node_brief`** / **`node_route`** patterns).

5. **REST API for the console**  
   **Given** authenticated users (JWT Bearer — existing **`api/deps.py`**)  
   **When** a user calls the notifications API  
   **Then** they receive **only their** notifications (scoped by `user_id`), with pagination or a sane default limit  
   **And** there is a way to mark a notification **read** (PATCH or POST) so repeat visits do not show stale “unread” noise.

6. **Minimal web visibility (FR24 UX)**  
   **Given** `web/src/App.tsx` is still a shell (“console UI comes in later epics”)  
   **When** a user opens the web app with a valid token  
   **Then** they can **see** at least a **minimal list** of in-app notifications (newest first, unread state visible)—does not need full design polish; must prove end-to-end FR24.

7. **Explicitly out of scope**  
   **Given** Epic 5 split  
   **Then** do **not** implement external email/Slack (**5.3**), digest vs immediate scheduling (**5.4**), filing guardrail (**5.5**), or full admin routing table UI (**6.3**).  
   **And** do **not** block the graph on third-party vendors (**NFR7** / **NFR10** alignment): in-app path stays DB-local.

8. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** add unit tests for **targeting** (which users get rows for which `team_slug` / severity)  
   **And** API tests (httpx + ASGI) for list + mark-read + 403/401 boundaries  
   **And** if Alembic head changes, update **`tests/test_alembic_cli.py`** per existing convention.

## Tasks / Subtasks

- [x] **Schema + migration (AC: #1–#2)**  
  - [x] Design `in_app_notifications` (names may vary) + user/team linkage approach; document JSON payload shape (title, body, `run_id`, `item_url`, `severity`, `team_slug` mirror, etc.).  
  - [x] Alembic revision; indexes for `(user_id, created_at DESC)` and unread queries.

- [x] **Repository + notification service (AC: #2–#4)**  
  - [x] `db/repositories/` module for inserting and listing notifications.  
  - [x] `services/notifications/` orchestration: given `routing_decisions` + run context, resolve target user IDs, bulk-insert idempotently where needed (define idempotency key: e.g. `(run_id, item_url, user_id)` unique partial index).

- [x] **Pipeline hook (AC: #3–#4)**  
  - [x] From **`node_route`** (after decisions are computed, same transaction boundaries as Story 5.1 review notes) **or** a dedicated helper invoked at end of `node_route`: call notification service for eligible decisions.  
  - [x] Optional: append summary entries to **`delivery_events`** in `AgentState` for in-app enqueue (keep JSON-serializable; align with Architecture §3.2)—if omitted, justify in Dev Notes.

- [x] **API routes (AC: #5)**  
  - [x] New router under `api/routes/` (e.g. `notifications.py`), registered in **`main.py`**.  
  - [x] Pydantic models with `extra="forbid"` / `ignore` patterns consistent with **`briefings.py`**.

- [x] **Web shell (AC: #6)**  
  - [x] Minimal React fetch to API (Bearer token storage pattern—document dev workflow: login via `/auth/login`, store token, call notifications endpoint).  
  - [x] CORS: ensure FastAPI CORS middleware if dev serves Vite on another origin (only if needed).

- [x] **Tests (AC: #8)**  
  - [x] Resolver-style unit tests + API tests; follow **`tests/test_briefings_api.py`** / graph test patterns.

### Review Findings

<!-- Added by bmad-code-review workflow on 2026-04-20 -->

_Triaged from three parallel adversarial review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) over the Story 5.2 diff (~1,037 lines). All 10 decision-needed items resolved by product/architecture call on 2026-04-20 and converted below into patches, defers, or accepted-as-is dismissals._

**Decisions resolved (2026-04-20):**

- **Transient enqueue failure replay** → accept best-effort-with-error-envelope; document the limitation inline. (→ patch P29)
- **JWT in `localStorage` (web shell)** → keep `localStorage` for the minimal shell per AC #6; httpOnly-cookie migration deferred to Epic 6 full-console UI work. (→ deferred)
- **Unbounded enqueue transaction size** → keep atomic single-transaction per enqueue call; accepted as-is. (→ dismissed)
- **Missing index/FK on `in_app_notifications.run_id`** → defer until a `runs` parent table lands; index + FK will be added together. (→ deferred)
- **Audit metadata does not reflect enqueue errors** → keep `ROUTING_APPLIED` scoped strictly to routing; enqueue visibility lives in `errors[]` / `delivery_events`. (→ dismissed)
- **No `?unread=` filter / unread-count endpoint** → add `GET /notifications?unread=true` query param. (→ patch P31)
- **`team_slug` snapshot semantics on team change** → accept snapshot-at-delivery; document semantics on the column and in service docstring. (→ patch P32)
- **Pagination `has_more`/`total`/keyset** → add `has_more: bool`; keyset can wait for Epic 6. (→ patch P33)
- **Hardcoded title + narrow frontend type** → accepted for shell UI; Epic 6 will redesign. (→ dismissed)
- **`delivery_events` silent on replay** → emit `status="no_new_rows"` with `rows_inserted: 0` on replay. (→ patch P34)

**Patches (unambiguous fixes) — all 34 applied 2026-04-20 via batch-apply:**

_Suite: 222 passed, 10 skipped (no regressions). New migration head `e6f7a8b9c0d1`._

- [x] [Review][Patch] `delivery_events` falsely reports success when `session.commit()` raises after partial inserts [`src/sentinel_prism/services/notifications/in_app.py:67-140`]
- [x] [Review][Patch] CORS config with `CORS_ORIGINS=*` + `allow_credentials=True` is broken/unsafe — validate or auto-drop credentials when wildcard [`src/sentinel_prism/main.py:80-91`]
- [x] [Review][Patch] FK race on user deletion aborts the whole enqueue batch — wrap per-user inserts in `session.begin_nested()` savepoints [`src/sentinel_prism/services/notifications/in_app.py:67-104`]
- [x] [Review][Patch] No functional index for `lower(team_slug)` query — replace `ix_users_team_slug` with a functional index, or store slug lowercased and drop `func.lower` [`alembic/versions/d4e5f6a7b8c9_*.py:22`, `src/sentinel_prism/db/repositories/in_app_notifications.py:26`]
- [x] [Review][Patch] `test_enqueue_skips_non_critical_severity` is tautological (no users returned regardless of severity) [`tests/test_notifications_enqueue.py:17-46`]
- [x] [Review][Patch] `markRead` silently swallows non-204 responses in the React shell — add `else` branch that surfaces the error [`web/src/App.tsx`]
- [x] [Review][Patch] Pagination order unstable on identical `created_at` — add `id DESC` tiebreaker [`src/sentinel_prism/db/repositories/in_app_notifications.py:73`]
- [x] [Review][Patch] Notification `team_slug` stored lowercased loses canonical casing — store original `ts` casing, keep lowercased only as query key [`src/sentinel_prism/services/notifications/in_app.py:81-97`]
- [x] [Review][Patch] 401 from expired token leaves UI "stuck" — on 401 clear `token` and reset auth state [`web/src/App.tsx`]
- [x] [Review][Patch] `fetch` network failure raises unhandled promise rejection — wrap in try/catch, show error [`web/src/App.tsx`]
- [x] [Review][Patch] Critical decision with zero matching active users drops silently (no log, no error envelope) [`src/sentinel_prism/services/notifications/in_app.py:86-104`]
- [x] [Review][Patch] No integration test exercises `list_for_user`'s per-user scoping (AC #5) — mocked-only coverage [`tests/test_notifications_api.py`]
- [x] [Review][Patch] Targeting tests stub the very `list_user_ids_for_team_slug` function they are meant to validate — add real-session targeting test [`tests/test_notifications_enqueue.py`]
- [x] [Review][Patch] Missing boundary test for cross-user mark-read (user A cannot mark user B's notification, expect 404) [`tests/test_notifications_api.py`]
- [x] [Review][Patch] `IN_APP_MIN_SEVERITY` name implies threshold but code does exact equality — rename to `IN_APP_SEVERITY` or `IN_APP_ALLOWED_SEVERITIES = {"critical"}` [`src/sentinel_prism/services/notifications/in_app.py:18,76`]
- [x] [Review][Patch] `_to_out` hand-copy causes silent schema drift as `InAppNotification` fields grow — use `NotificationOut.model_validate(row, from_attributes=True)` [`src/sentinel_prism/api/routes/notifications.py`]
- [x] [Review][Patch] `User.team_slug` ORM model lacks `index=True` despite migration creating `ix_users_team_slug` — `alembic autogenerate` will propose dropping it [`src/sentinel_prism/db/models.py`]
- [x] [Review][Patch] `mark_read` read-then-write is non-atomic (concurrent PATCH race + CASCADE race) — replace with atomic `UPDATE ... WHERE id=? AND user_id=? AND read_at IS NULL RETURNING id` [`src/sentinel_prism/db/repositories/in_app_notifications.py:91-107`]
- [x] [Review][Patch] Login/list error surfaces raw server response body (`setErr(await r.text())`) — parse JSON error and render short message [`web/src/App.tsx`]
- [x] [Review][Patch] Severity variants beyond "critical" silently drop without a log — emit INFO-level skip event to detect upstream taxonomy drift [`src/sentinel_prism/services/notifications/in_app.py:75-77`]
- [x] [Review][Patch] Token literal string `"null"` / `"undefined"` from bad prior writes sticks the UI in `Authorization: Bearer null` — filter those values when rehydrating [`web/src/App.tsx:12`]
- [x] [Review][Patch] No `AbortController` on the notifications `useEffect` — rapid token change or StrictMode double-mount leaves stale `setItems` [`web/src/App.tsx`]
- [x] [Review][Patch] `item_url` and `body` are unconstrained Text — a pathological multi-MB URL bloats every row and every list response; cap length (e.g., 2048) in the service [`src/sentinel_prism/services/notifications/in_app.py`, `src/sentinel_prism/db/models.py`]
- [x] [Review][Patch] Dev Notes do not document the chosen user↔team rule (nullable `users.team_slug`) or the JSON payload shape (AC #2 "pick one, document it"; Tasks subtask explicit on payload shape)
- [x] [Review][Patch] No 403/404 boundary test on `/notifications` routes (AC #8 explicitly calls for 403/401 boundaries; only 401 covered today) [`tests/test_notifications_api.py`]
- [x] [Review][Patch] No test covers the `matched=False` skip branch in the enqueue service [`tests/test_notifications_enqueue.py`]
- [x] [Review][Patch] No integration test verifies `node_route` → `enqueue_critical_in_app_for_decisions` wiring or `delivery_events` merge [`tests/` graph tests]
- [x] [Review][Patch] `offset` in `GET /notifications` has no upper bound (`ge=0` but no `le=`) — cheap DoS via `offset=2_000_000_000` [`src/sentinel_prism/api/routes/notifications.py`]
- [x] [Review][Patch] P29 — Document best-effort enqueue replay contract: transient enqueue failures on first pass are not re-attempted by `node_route` because state-level `routing_decisions` dedup skips their URLs; surface via `errors[]` and log, document in service docstring [`src/sentinel_prism/services/notifications/in_app.py`, `src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] P31 — Add `?unread=true` filter on `GET /notifications` (leverage the partial index `ix_in_app_notifications_user_unread`) [`src/sentinel_prism/api/routes/notifications.py`, `src/sentinel_prism/db/repositories/in_app_notifications.py`]
- [x] [Review][Patch] P32 — Document "snapshot-at-delivery" semantics for `InAppNotification.team_slug` (value captured at enqueue time; not re-evaluated on user team change) in model docstring and service docstring [`src/sentinel_prism/db/models.py`, `src/sentinel_prism/services/notifications/in_app.py`]
- [x] [Review][Patch] P33 — Add `has_more: bool` to `NotificationListOut`; compute via `limit+1` probe in repository [`src/sentinel_prism/api/routes/notifications.py`, `src/sentinel_prism/db/repositories/in_app_notifications.py`]
- [x] [Review][Patch] P34 — Emit `delivery_events` entry with `status="no_new_rows", rows_inserted: 0` on replay / idempotent re-enqueue so the audit trail distinguishes "not considered" from "considered, all duplicates" [`src/sentinel_prism/services/notifications/in_app.py`]

**Deferred (pre-existing / cross-cutting / scoped to later epics):**

- [x] [Review][Defer] `_norm_item_url` only strips whitespace — no scheme/host case-folding, no trailing-slash collapse, no query-order canonicalization [`src/sentinel_prism/services/notifications/in_app.py:23`, `src/sentinel_prism/graph/nodes/route.py:29`] — deferred, pre-existing (mirrors `brief.py` / `route.py` across Epic 3–5; URL canonicalization belongs in a cross-cutting normalization pass)
- [x] [Review][Defer] JWT access token stored in `localStorage` is XSS-exploitable in the web shell [`web/src/App.tsx`] — deferred to Epic 6 full-console UI work (AC #6 of Story 5.2 only mandates a minimal shell; httpOnly-cookie flow will ship alongside the real console UI to avoid reworking the auth plumbing twice)
- [x] [Review][Defer] Missing index / FK on `in_app_notifications.run_id` [`alembic/versions/d4e5f6a7b8c9_*.py`, `src/sentinel_prism/db/models.py`] — deferred until a `runs` parent table lands; index and FK will be added together at that point (no current query path needs the index today)

## Dev Notes

### Architecture compliance

- **Canonical locations:** `services/notifications/`, `db/models.py` + `db/repositories/`, `api/routes/`, migrations under `alembic/versions/`. [Source: `_bmad-output/planning-artifacts/architecture.md` §6 table — FR21–FR25 Route]
- **Boundary:** Graph **nodes** call **services/repositories**; **services** do not import `graph.graph` or node modules (Architecture §6 Boundaries).
- **Orchestration:** Prefer **no new graph nodes** unless required; **`node_route`** extension keeps topology **`brief` → `route` → END** unchanged (Architecture §3.4).

### Dependency on Story 5.1

- **`routing_decisions`** shape and **`node_route`** behavior are defined in **`5-1-routing-rules-engine.md`** and code under `graph/nodes/route.py`, `services/routing/resolve.py`. Do not redefine routing precedence here—consume outputs.
- If Story 5.1 is still in **review** at implementation time, branch from the merged 5.1 baseline so resolver + audit semantics stay stable.

### User model gap

- **`User`** currently has **`role`** (`UserRole`) but **no team field**. This story **must** introduce a supported way to match users to **`team_slug`** from routing (AC #2). Keep migration backward-compatible (nullable column or additive table).

### AC #2 chosen rule and JSON payload shape (documented per code-review P24)

- **Rule chosen:** nullable **`users.team_slug: String(128)`** column. Users are targeted for an in-app notification when `lower(users.team_slug) == lower(routing_decisions[i].team_slug)` and `users.is_active IS TRUE`. Users with `team_slug IS NULL` receive **no** team-targeted notifications (explicit in AC #2: "avoid silent mis-delivery"). The alternative — a `user_teams` join table — was rejected for MVP because (a) the routing model is a single slug per decision, (b) membership changes in MVP are rare enough that a column + functional index (`ix_users_team_slug_lower`) is cheaper to maintain, and (c) a future migration to a join table is straightforward (ORM stays, repo adds a JOIN, data migration moves the column into the new table).
- **JSON payload shape persisted per notification row** (mirrors `InAppNotification` columns; see `src/sentinel_prism/db/models.py:InAppNotification` and the repository / API layer):
  - `id` (UUID) — primary key
  - `user_id` (UUID) — FK to `users.id`, CASCADE on delete
  - `run_id` (UUID) — pipeline run that produced the routing decision
  - `item_url` (Text ≤ 2048 chars) — canonical regulatory item URL from the decision
  - `team_slug` (String(128)) — **original casing** of the rule's team slug at enqueue time (snapshot-at-delivery; see `InAppNotification` docstring)
  - `severity` (String(32)) — lowercase severity ("critical" in MVP)
  - `title` (String(512)) — presently the constant "Critical routed update" (Epic 6 will enrich)
  - `body` (Text ≤ 2048 chars, nullable) — currently mirrors `item_url`
  - `read_at` (Timestamptz, nullable) — set on mark-read; never cleared
  - `created_at` (Timestamptz) — `server_default now()`
- **Idempotency key:** UNIQUE `(run_id, item_url, user_id)` — enforced at the DB layer so graph retries do not duplicate rows even if the application-side dedup check races.

### NFR reminders

- **NFR5:** Minimize PII in notification payloads; prefer URLs/titles already used in briefings/classifications, not end-user personal data.
- **NFR7:** In-app path must not depend on external notification providers.

### References

- Epics: `_bmad-output/planning-artifacts/epics.md` — Epic 5, Story 5.2.
- PRD: `_bmad-output/planning-artifacts/prd.md` — **FR24**.
- Prior story: `_bmad-output/implementation-artifacts/5-1-routing-rules-engine.md`.
- Architecture: `_bmad-output/planning-artifacts/architecture.md` — §3.2 state (`delivery_events`), §3.3 `route` node, §6 layout.
- Code: `src/sentinel_prism/graph/nodes/route.py`, `src/sentinel_prism/graph/state.py`, `src/sentinel_prism/db/models.py`, `src/sentinel_prism/api/deps.py`.

### Project structure notes

- **`services/notifications/__init__.py`** is currently a stub—replace/extend with real modules without breaking import hygiene (`verify_imports.py`).
- Follow existing **FastAPI** router registration and **`require_roles`** patterns where appropriate (likely **`analyst`** + **`admin`** + **`viewer`** can read their own notifications; restrict writes to self).

## Dev Agent Record

### Agent Model Used

Composer (dev-story workflow)

### Debug Log References

### Completion Notes List

- Implemented nullable `users.team_slug`, `in_app_notifications` table with unique `(run_id, item_url, user_id)`, repository + `enqueue_critical_in_app_for_decisions` (**critical** severity only), `node_route` hook with `delivery_events` summary, `GET /notifications` + `PATCH /notifications/{id}/read`, CORS for Vite defaults, minimal `web` login + list UI. Full suite: `211 passed, 10 skipped`.

### File List

- `alembic/versions/d4e5f6a7b8c9_add_in_app_notifications_and_user_team_slug.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/in_app_notifications.py`
- `src/sentinel_prism/services/notifications/__init__.py`
- `src/sentinel_prism/services/notifications/in_app.py`
- `src/sentinel_prism/api/routes/notifications.py`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/graph/nodes/route.py`
- `web/src/App.tsx`
- `verify_imports.py`
- `tests/test_alembic_cli.py`
- `tests/test_notifications_api.py`
- `tests/test_notifications_enqueue.py`

### Change Log

- 2026-04-20: Story 5.2 — in-app notifications (migration, enqueue from `node_route`, REST API, React shell, tests).

---

## Technical requirements (guardrails)

| Requirement | Detail |
|---------------|--------|
| Stack | FastAPI, SQLAlchemy 2.x async, Alembic, LangGraph 1.1.x (`requirements.txt`) |
| Auth | JWT Bearer — `get_current_user`, `require_roles` (`api/deps.py`) |
| State | Optional `delivery_events` entries; do not break `AgentState` reducers (`operator.add` lists) |
| DB | PostgreSQL; prefer explicit columns + JSONB only for flexible payload extras |

## Architecture extraction (story-specific)

- **Route node** applies mock tables and should **enqueue** notifications per Architecture §3.3 (“enqueue notifications”). [Source: `architecture.md` §3.3]
- **UI talks only to REST**, not graph internals. [Source: `architecture.md` §6 Boundaries]

## Library / framework notes

- No new major dependencies expected; use **httpx** / **pytest-asyncio** already in `requirements.txt` for API tests.

## File structure requirements

- New: `src/sentinel_prism/api/routes/notifications.py` (or similar), `src/sentinel_prism/services/notifications/*.py`, `src/sentinel_prism/db/repositories/*notifications*.py`
- Modify: `src/sentinel_prism/db/models.py`, `src/sentinel_prism/graph/nodes/route.py` (hook only), `src/sentinel_prism/main.py`, `alembic/versions/*.py`, `web/src/App.tsx`, `tests/…`

## Testing requirements

- Pytest + pytest-asyncio; ASGI lifespan tests if other routes use them.
- Extend Alembic CLI test if migration count/version expectations are asserted.

## Previous story intelligence (Story 5.1)

- **`node_route`** uses a **private session factory** pattern and structured **`errors[]`** envelopes; notification failures should **not** silently drop routing decisions—follow the same “surface error, preserve decisions” philosophy.
- **Once-per-run audit** for `ROUTING_APPLIED` is special-cased; notification inserts should be **idempotent** to avoid duplicate rows on graph retries (mirror dedupe thinking from `node_route`).
- **Naming:** `graph/routing.py` is **conditional route after classify** — do not confuse with **`graph/nodes/route.py`**.

## Git intelligence (recent commits)

- `feat(epic-5): routing rules engine…` — routing resolver, `node_route`, graph wiring; extend this path for notification enqueue.
- Prior: `feat(epic-4): briefings pipeline, APIs…` — patterns for **list/detail APIs**, Pydantic strictness, and repository usage.

## Latest technical information

- LangGraph **1.1.6** — avoid upgrading in-story unless a security fix is required; pin changes belong in a separate chore.

## Project context reference

- No `project-context.md` in repo; rely on Architecture + this story + codebase patterns above.

## Story completion status

- **review** — Implementation complete; ready for code-review workflow.
