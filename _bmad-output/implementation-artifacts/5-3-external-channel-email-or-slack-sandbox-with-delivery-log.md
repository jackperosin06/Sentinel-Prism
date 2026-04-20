# Story 5.3: External channel (email or Slack sandbox) with delivery log

Status: review

<!-- Ultimate context engine analysis completed — comprehensive developer guide created -->

## Story

As the **system**,
I want **to send sandbox email or Slack and record outcomes**,
so that **delivery is traceable** (**FR23**, **FR25**, **NFR5**, **NFR10**).

## Acceptance Criteria

1. **External channel (FR25)**  
   **Given** sandbox credentials are configured via environment (no production secrets in repo — **NFR3**)  
   **When** a routed notification is eligible for external delivery under an explicit policy (see Dev Notes: align with routing `channel_slug` or a minimal “external enabled + channel type” config)  
   **Then** the system attempts delivery through **at least one** of: **SMTP email** (sandbox mailbox / provider) **or** **Slack-compatible incoming webhook** (HTTPS POST JSON).

2. **Delivery outcome log (FR23)**  
   **Given** an external send is attempted  
   **When** the attempt completes (success, transport failure, HTTP non-2xx, timeout)  
   **Then** a **durable PostgreSQL row** records: `run_id`, correlation to the routed item (`item_url` or equivalent), channel type, attempt timestamp, **outcome** (`success` | `failure` | `skipped`), **error class** / short detail (no raw secrets), and optional provider message id if available.

3. **Admin visibility (NFR10)**  
   **Given** an authenticated **admin** user  
   **When** they query the delivery log API  
   **Then** they can list recent external delivery attempts with filters (e.g. outcome, date range, run_id)  
   **And** failed attempts are clearly surfaced (sortable / filterable by `failure`).

4. **Architecture boundaries (FR21–FR25 mapping)**  
   **Given** **`graph/nodes/route.py`** already calls **`services/notifications/in_app.enqueue_critical_in_app_for_decisions`**  
   **When** external sends are added  
   **Then** vendor/protocol code lives under **`services/notifications/`** (adapters + orchestration) with **repositories** for persistence — **graph nodes do not embed SMTP/HTTP calls or raw SQL** for delivery logs.

5. **Non-blocking pipeline (NFR7 alignment)**  
   **Given** external providers can be slow or down  
   **When** `node_route` runs  
   **Then** routing decisions and in-app enqueue behavior from Story 5.2 are **not rolled back** because of an external failure — failures are **logged** to DB + `errors[]` / `delivery_events` as appropriate (match Story 5.2 patterns: surface, don’t swallow).

6. **PII minimization (NFR5)**  
   **Given** notification payloads  
   **When** stored or sent externally  
   **Then** content uses the same classes of data as briefings/classifications (title, URL, severity, team slug) — **no** end-user personal data beyond what is already in the regulatory URL/title context.

7. **Explicitly out of scope**  
   **Given** Epic 5 split  
   **Then** do **not** implement digest vs immediate scheduling (**5.4**), regulatory filing guardrail (**5.5**), or full preference UI — external sends follow a **single MVP policy** (document the chosen rule: e.g. mirror in-app severity gate `critical` only, or follow `channel_slug` from routing rules).

8. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** unit tests cover adapter edge cases with **fakes/mocks** (no real network in default CI)  
   **And** API tests cover admin list + auth boundaries (**401/403**)  
   **And** if Alembic head changes, update **`tests/test_alembic_cli.py`** per existing convention.

## Tasks / Subtasks

- [x] **Schema + migration (AC: #2)**  
  - [x] Design `notification_delivery_attempts` (name may vary) — columns listed in Dev Notes.  
  - [x] Alembic revision; indexes for admin queries (`created_at DESC`, `(outcome, created_at)`).

- [x] **Adapters (AC: #1, #4)**  
  - [x] `services/notifications/adapters/` — `EmailSender` protocol + SMTP implementation (stdlib `smtplib` via `asyncio.to_thread` **or** add `aiosmtplib` if team prefers native async — justify in PR).  
  - [x] `SlackWebhookSender` using **`httpx.AsyncClient`** (already in `requirements.txt`) with timeouts.

- [x] **Orchestration + hook (AC: #4, #5)**  
  - [x] Service function: given `routing_decisions` + `run_id`, select targets for external channel (reuse team resolution patterns from `in_app.py` where applicable — **do not fork** user lookup logic; extract shared helper if needed).  
  - [x] Invoke from **`node_route`** after in-app enqueue (same run context), merging **`delivery_events`** entries for external attempts consistently with existing reducer semantics.

- [x] **Admin API (AC: #3)**  
  - [x] New router (e.g. `api/routes/delivery_attempts.py` or under `notifications`) with **`require_roles(UserRole.ADMIN)`** and `get_db_for_admin` pattern from **`api/deps.py`**.  
  - [x] Pydantic models `extra="forbid"` consistent with **`api/routes/briefings.py`**.

- [x] **Config (AC: #1, NFR3)**  
  - [x] Document env vars in **`.env.example`** — e.g. `NOTIFICATIONS_EXTERNAL_CHANNEL=none|smtp|slack`, SMTP host/port/user/pass/from, Slack webhook URL — all placeholders.

- [x] **Tests (AC: #8)**  
  - [x] Mock httpx / SMTP at boundaries; repository tests for persistence.

### Review Findings

Generated by code-review workflow on 2026-04-21. Source layers: Blind Hunter, Edge Case Hunter, Acceptance Auditor.

**Decision-needed (0 — all resolved)**

- [x] [Review][Decision] PII envelope for `recipient_descriptor` — **resolved**: explicit carve-out. `recipient_descriptor` is permitted to contain SMTP recipient addresses for delivery traceability; this is an operational/audit exception to AC #6. Converted to patch `[P0]` below (add docstring + Dev Notes carve-out).

**Patch — critical/high (8, all applied 2026-04-21)**

- [x] [Review][Patch][P0] Document the `recipient_descriptor` PII carve-out — applied. Docstring on `NotificationDeliveryAttempt.recipient_descriptor` now records the SMTP-email + Slack team-scoped descriptor contract as an operational carve-out from AC #6 / NFR5; matching paragraph added to Dev Notes ("PII envelope — AC #6 carve-out") below. [`src/sentinel_prism/db/models.py`]
- [x] [Review][Patch] TOCTOU race + send-before-persist — applied. Repository replaced with `claim_attempt_pending` (`INSERT ... ON CONFLICT DO NOTHING` on the idempotent unique constraint, outcome=`pending`) and `finalize_attempt_outcome` (UPDATE from `pending` only). Orchestrator claims the row in a short session → commits → releases the session → sends → opens a fresh session to UPDATE to `success`/`failure`. Applies to both SMTP and Slack paths. [`src/sentinel_prism/services/notifications/external.py`; `src/sentinel_prism/db/repositories/notification_delivery_attempts.py`; `src/sentinel_prism/db/models.py`; `alembic/versions/f7e8d9c0b1a2_*.py`]
- [x] [Review][Patch] `node_route` not protected from external failures — applied. `enqueue_external_for_decisions` is now wrapped in a broad `try/except` in `node_route` that converts unhandled failures to a structured `errors[]` entry while preserving `routing_decisions`. [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] Adapter edge-case coverage gap — applied. New `tests/test_adapters_smtp.py` and `tests/test_adapters_slack.py` cover success/4xx/5xx/429-retry/timeout/transport-error/URL-scrubbing for Slack and success/STARTTLS/SMTPS/auth-failure/address-validation/connection-refused for SMTP. `test_external_notifications.py` expanded to cover `matched=False`, non-critical severity skip log, missing-config, invalid `run_id`, and adapter-failure → failure-row paths. [`tests/test_adapters_smtp.py`; `tests/test_adapters_slack.py`; `tests/test_external_notifications.py`]
- [x] [Review][Patch] Slack team-membership gate — applied. Slack path now calls `list_active_users_for_team_slug` before posting and emits `external_slack_no_recipients` (parity with SMTP) when the team is empty. [`src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] Slack `<!channel>` / `@here` / `@everyone` injection — applied. `_slack_escape` strips `<!...>` / `<@...>` control sequences and defuses bare `@channel`/`@here`/`@everyone` before interpolation. [`src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] SMTP exception text can leak secrets — applied. `_sanitize_detail` redacts the configured SMTP user, strips CR/LF, replaces any `AUTH` continuation argument with `<redacted>`, and redacts base64-ish blobs before the string is returned to the orchestrator for persistence. [`src/sentinel_prism/services/notifications/adapters/smtp.py`]
- [x] [Review][Patch] Slack webhook URL leaked into `detail` — applied. `_scrub` in the Slack adapter replaces the full URL and its path (where the token lives) with `<slack-webhook-redacted>` on both transport-error and non-2xx paths; unit test asserts the token never appears in persisted detail. [`src/sentinel_prism/services/notifications/adapters/slack.py`; `tests/test_adapters_slack.py`]

**Patch — medium (9, all applied 2026-04-21)**

- [x] [Review][Patch] Unknown `NOTIFICATIONS_EXTERNAL_CHANNEL` value — applied. `load_external_notification_settings` now logs `external_channel_unknown_mode` WARNING with the raw value and the list of valid modes when coercing to `none`. [`src/sentinel_prism/services/notifications/external_settings.py`]
- [x] [Review][Patch] Invalid `NOTIFICATIONS_SMTP_PORT` — applied. `_parse_smtp_port` logs `external_smtp_port_invalid` / `external_smtp_port_out_of_range` WARNING and range-checks `[1, 65535]` before falling back to `587`. [`src/sentinel_prism/services/notifications/external_settings.py`]
- [x] [Review][Patch] No retry / backoff on transient Slack responses — applied. Slack adapter retries on 429 (honouring `Retry-After`, capped at 10s) and 5xx up to `max_attempts` (default 3) with bounded linear backoff. Transport exceptions also retry. Unit tests cover both retryable branches. [`src/sentinel_prism/services/notifications/adapters/slack.py`; `tests/test_adapters_slack.py`]
- [x] [Review][Patch] SMTP adapter — implicit TLS support — applied. New `use_ssl` parameter auto-selects `SMTP_SSL` for port 465 (override-able). Unit test asserts port 465 chooses `SMTP_SSL` and does NOT fall back to `smtplib.SMTP`. [`src/sentinel_prism/services/notifications/adapters/smtp.py`; `tests/test_adapters_smtp.py`]
- [x] [Review][Patch] Slack webhook URL scheme validation — applied. Settings load logs `external_slack_webhook_scheme_unsafe` WARNING when the URL is not `https://`. URL is still returned so a misconfigured operator sees a transport-level failure rather than a silent disable; the adapter scrubs the URL from persisted error details regardless. (Full SSRF allowlisting deferred to a future story — out of MVP scope.) [`src/sentinel_prism/services/notifications/external_settings.py`]
- [x] [Review][Patch] `provider_message_id` for Slack — applied. Slack incoming webhooks return literal `"ok"` and do not carry a message id; adapter now returns `provider_hint=None` unconditionally, and the orchestrator persists `provider_message_id=None`. [`src/sentinel_prism/services/notifications/adapters/slack.py`; `src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] Slack idempotency key team-scoping — applied. `_slack_descriptor(team_slug_key)` produces `slack_webhook:<team>` so a run with decisions for multiple teams sends one message per team, not one per run. [`src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] Admin list endpoint naive datetime — applied. `_to_utc_aware` rejects tz-naive query params with HTTP 422 and normalizes tz-aware inputs to UTC before passing to the repository. [`src/sentinel_prism/api/routes/delivery_attempts.py`; `tests/test_delivery_attempts_api.py`]
- [x] [Review][Patch] `delivery_events` status semantics for Slack — applied. Success emits `status="recorded"` with `outcome="success"`; failure emits `status="recorded_failure"` with `outcome="failure"` so operators can distinguish without reading the row. [`src/sentinel_prism/services/notifications/external.py`]

**Patch — low (9, all applied 2026-04-21)**

- [x] [Review][Patch] Mixed batches `skipped` counter — applied. Both `recorded` and `no_new_rows` SMTP events now include `attempts` + `skipped` + optional `failed_to_persist`, so a mixed batch no longer loses the skip counter. [`src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] Recipient casing consistency — applied. Orchestrator now lower-cases the recipient once (`desc = email.strip().lower()`) and uses it for both the envelope `to_addr` and the idempotency `recipient_descriptor`. [`src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] Admin pagination half-open boundary — applied. Repository now uses `>= created_after` and `<` `created_before`; ORDER BY retains the `created_at DESC, id DESC` tiebreak for same-microsecond ties. [`src/sentinel_prism/db/repositories/notification_delivery_attempts.py`]
- [x] [Review][Patch] `to_addr` validation — applied. `_validate_address` (via `email.utils.parseaddr`) rejects whitespace, commas/semicolons, missing `@`, and empty parses before the SMTP envelope is built; unit test covers comma-injection and missing-`@` cases. [`src/sentinel_prism/services/notifications/adapters/smtp.py`; `tests/test_adapters_smtp.py`]
- [x] [Review][Patch] `verify_imports.py` adapters — applied. `verify_imports.py` now imports `services.notifications.adapters.{slack,smtp}` and `services.notifications.external_settings` so the canary catches adapter/settings breakage. [`verify_imports.py`]
- [x] [Review][Patch] Admin endpoint inverted range — applied. Route rejects `created_after > created_before` with HTTP 422 after the tz-normalization step; test covers the boundary. [`src/sentinel_prism/api/routes/delivery_attempts.py`; `tests/test_delivery_attempts_api.py`]
- [x] [Review][Patch] Severity-skip log parity — applied. Non-critical severities now emit `external_severity_skipped` INFO log with `{run_id, severity, mode}` mirroring `in_app_severity_skipped`. [`src/sentinel_prism/services/notifications/external.py`]
- [x] [Review][Patch] Repeated Slack error envelope — applied. A per-call set of `(message, error_class)` keys deduplicates identical envelopes, so N decisions hitting a stale-token 404 produce one `slack_webhook_failed` envelope. [`src/sentinel_prism/services/notifications/external.py`; `tests/test_external_notifications.py::test_error_envelope_dedup_across_decisions`]
- [x] [Review][Patch] MVP policy docstring — applied. Module docstring on `external.py` now explicitly records the MVP policy (severity=critical, same team membership as in-app, `channel_slug` reserved for Story 5.4) alongside the two-phase idempotency and PII-envelope carve-out. [`src/sentinel_prism/services/notifications/external.py`]

**Deferred — pre-existing or out-of-scope for MVP (4, checked off)**

- [x] [Review][Defer] Admin API offset ceiling of 10,000 prevents paging deep history — no cursor pagination. Acceptable for MVP volume; revisit in Epic 6/8. [`src/sentinel_prism/api/routes/delivery_attempts.py:382, 410`]
- [x] [Review][Defer] `outcome` / `channel` stored as `String(32)` + `CheckConstraint` rather than native PG enum; drift between model and migration is possible. Existing project pattern; revisit when any enum needs a new value. [`alembic/versions/f7e8d9c0b1a2_*.py`; `src/sentinel_prism/db/models.py`]
- [x] [Review][Defer] `asyncio.to_thread` SMTP sends are uncancellable — cancelling `node_route` can orphan a thread that completes the send without writing a row. Low likelihood under current graph semantics; revisit if cancellation becomes part of the pipeline contract. [`src/sentinel_prism/services/notifications/adapters/smtp.py:835–849`]
- [x] [Review][Defer] No per-run wall-clock budget on the SMTP loop — N decisions × M recipients × 30s timeout can block `node_route` for an extended period under provider slowness. Add a batch budget / concurrency policy in Story 5.4 (immediate vs digest). [`src/sentinel_prism/services/notifications/external.py:974–1013`]

**Dismissed (8, not recorded):** bare `except Exception` in adapter boundaries (acceptable DB-logged pattern); `str(run_id)` defensive coercion; invalid `run_id` produces no row (correct behavior); `outcome` StrEnum case-sensitivity (standard FastAPI); duplicated txn-pattern critique (covered by critical TOCTOU patch); `team_slug` double-lower-casing (cosmetic); `_MAX_DETAIL_CHARS` vs adapter `[:500]` envelope (cosmetic); `run_id: str` annotation vs UUID parsing (stylistic).

## Dev Notes

### Architecture compliance

- **Canonical locations:** `services/notifications/` (adapters + orchestration), `db/models.py` + `db/repositories/`, `api/routes/`, `alembic/versions/`. [Source: `_bmad-output/planning-artifacts/architecture.md` §6 table — FR21–FR25 Route]
- **Boundary:** Graph **nodes** call **services/repositories**; **services** do not import `graph.graph` or node modules.
- **State:** Append to **`delivery_events`** using the same list-of-dicts contract as Story 5.2 so LangGraph **`operator.add`** reducers stay predictable. [Source: `src/sentinel_prism/graph/state.py`]

### Dependency on Story 5.2 (in-app)

- **`enqueue_critical_in_app_for_decisions`** in **`services/notifications/in_app.py`** establishes: severity gating (`IN_APP_ALLOWED_SEVERITIES`), team targeting via **`users.team_slug`**, idempotency, `delivery_events` + `errors[]` return shape, and **`node_route`** integration — **extend, don’t duplicate** user resolution.
- Story 5.2 file: `_bmad-output/implementation-artifacts/5-2-in-app-notifications.md` — review **Review Findings** for transaction/savepoint and replay semantics before changing `node_route`.

### Dependency on Story 5.1 (routing)

- **`routing_decisions`** entries carry **`team_slug`**, **`channel_slug`**, **`item_url`**, **`severity`** — use these as inputs to external targeting. Resolver: **`services/routing/resolve.py`**, **`graph/nodes/route.py`**.

### PII envelope — AC #6 carve-out (decision-needed resolution, 2026-04-21)

AC #6 / NFR5 scope operational payload content to the same classes of data as briefings/classifications ("title, URL, severity, team slug — no end-user personal data"). The `notification_delivery_attempts.recipient_descriptor` column is an **explicit operational/audit carve-out** from that envelope: for the `smtp` channel it holds the lower-cased SMTP recipient email so an operator can audit which mailbox actually received a sandbox send; for the `slack_webhook` channel it holds a non-PII team-scoped descriptor (`slack_webhook:<team_slug>`).

Reviewers extending this table or exposing new surfaces (UI, exports, webhooks, briefing bodies) that inherit the AC #6 envelope MUST NOT project `recipient_descriptor` into those surfaces without a matching documented carve-out. The carve-out exists solely to make delivery-log traceability — a transport-level audit concern — work for email-class channels where the recipient address is the only durable identity available.

Scoped by: idempotency unique constraint `(run_id, item_url, channel, recipient_descriptor)` so the column is a key, not a free-text payload; admin API is gated on `require_roles(UserRole.ADMIN)`. See `NotificationDeliveryAttempt` docstring in `src/sentinel_prism/db/models.py` for the lifecycle (incl. the two-phase `pending`→terminal outcome flow introduced in the 2026-04-21 code-review fixes).

### Suggested persistence shape (implementer may refine)

| Column | Purpose |
|--------|---------|
| `id` | UUID PK |
| `run_id` | UUID string / UUID — align with existing notification rows |
| `item_url` | Text (cap length consistent with in-app — 2048) |
| `channel` | Enum or string: `smtp` \| `slack_webhook` |
| `outcome` | `success` \| `failure` \| `skipped` |
| `error_class` | Short string nullable |
| `detail` | Truncated safe text nullable (no secrets) |
| `created_at` | Timestamptz server default |

### References

- Epics: `_bmad-output/planning-artifacts/epics.md` — Epic 5, Story 5.3.
- PRD: `_bmad-output/planning-artifacts/prd.md` — **FR23**, **FR25**, **NFR5**, **NFR7**, **NFR10**.
- Architecture: `_bmad-output/planning-artifacts/architecture.md` — §3.2 `delivery_events`, §6 layout.
- Code: `src/sentinel_prism/graph/nodes/route.py`, `src/sentinel_prism/services/notifications/in_app.py`, `src/sentinel_prism/api/deps.py`.

### Project structure notes

- Extend **`verify_imports.py`** if new public modules are added.
- Register router in **`main.py`** alongside existing routes.

## Dev Agent Record

### Agent Model Used

Composer (dev-story workflow)

### Debug Log References

### Completion Notes List

- Implemented `notification_delivery_attempts` table + `NotificationDeliveryAttempt` ORM with idempotent unique key `(run_id, item_url, channel, recipient_descriptor)`.
- **MVP policy:** `NOTIFICATIONS_EXTERNAL_CHANNEL` selects `none` (default), `smtp`, or `slack`; external sends mirror in-app **critical**-only + same team membership as `list_active_users_for_team_slug` (new helper alongside existing user-id lookup).
- SMTP via `smtplib` in `asyncio.to_thread`; Slack via `httpx` POST with JSON `{"text": ...}`.
- `node_route` calls `enqueue_external_for_decisions` after in-app enqueue; failures surface in `errors[]` without blocking routing decisions.
- Admin-only `GET /admin/delivery-attempts` with filters (`outcome`, `run_id`, `created_after`, `created_before`).
- Full suite post code-review: 255 passed, 10 skipped.

### File List

- `alembic/versions/f7e8d9c0b1a2_add_notification_delivery_attempts.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/in_app_notifications.py`
- `src/sentinel_prism/db/repositories/notification_delivery_attempts.py`
- `src/sentinel_prism/services/notifications/adapters/__init__.py`
- `src/sentinel_prism/services/notifications/adapters/smtp.py`
- `src/sentinel_prism/services/notifications/adapters/slack.py`
- `src/sentinel_prism/services/notifications/external_settings.py`
- `src/sentinel_prism/services/notifications/external.py`
- `src/sentinel_prism/graph/nodes/route.py`
- `src/sentinel_prism/api/routes/delivery_attempts.py`
- `src/sentinel_prism/main.py`
- `.env.example`
- `verify_imports.py`
- `tests/test_alembic_cli.py`
- `tests/test_adapters_slack.py`
- `tests/test_adapters_smtp.py`
- `tests/test_delivery_attempts_api.py`
- `tests/test_external_notifications.py`

### Change Log

- 2026-04-21: Story 5.3 — external SMTP/Slack adapters, delivery attempt persistence, `node_route` hook, admin delivery log API, tests.
- 2026-04-21: Code-review fixes applied — 26 patch findings (8 critical/high, 9 medium, 9 low). Two-phase idempotency (`pending`→terminal) replaces check-then-insert TOCTOU; Slack team-membership gate + mention escape + team-scoped descriptor; SMTP address validation + STARTTLS/SMTPS selector + secret sanitization; Slack URL scrubbing + bounded retry; tz-aware admin datetime params + inverted-range rejection + half-open pagination boundary; settings warnings for unknown mode / port / scheme; PII envelope carve-out documented. Test suite: 255 passed, 10 skipped.

---

## Technical requirements (guardrails)

| Requirement | Detail |
|-------------|--------|
| Stack | FastAPI, SQLAlchemy 2.x async, Alembic, LangGraph 1.1.x (`requirements.txt`) |
| HTTP client | **`httpx`** for Slack webhook POSTs — already pinned |
| Email | Stdlib **`smtplib`** + **`asyncio.to_thread`** *or* add **`aiosmtplib`** — avoid blocking the event loop |
| Auth | Admin routes: **`require_roles(UserRole.ADMIN)`** + **`get_db_for_admin`** |
| Secrets | Load from env only; document placeholders in **`.env.example`** |

## Architecture extraction (story-specific)

- **Connector swap:** PRD Technical Direction — outbound email/Slack behind **adapter interfaces**; this story is the first external implementation. [Source: `prd.md` — Outbound adapters]
- **UI:** Console UI for operators is limited in MVP; **admin REST** satisfies **NFR10** until Epic 6 dashboards.

## Library / framework notes

- **Slack:** Incoming Webhooks expect `POST` JSON `{"text": "..."}` (rich formatting optional later). Use short timeouts (e.g. 10–30s).
- **SMTP:** TLS/starttls per provider docs; sandbox often uses submission port 587.

## File structure requirements

- New: `src/sentinel_prism/services/notifications/adapters/*.py`, `db/repositories/*delivery*.py`, `api/routes/*delivery*.py` (names flexible)
- Modify: `src/sentinel_prism/db/models.py`, `src/sentinel_prism/graph/nodes/route.py`, `src/sentinel_prism/main.py`, `alembic/versions/*.py`, `.env.example`, `verify_imports.py`, `tests/…`

## Testing requirements

- Pytest + pytest-asyncio; mock external I/O.
- Follow patterns in **`tests/test_notifications_api.py`** for ASGI + auth boundaries.

## Previous story intelligence (Story 5.2)

- **`node_route`** merges **`delivery_events`** from in-app enqueue — add external events in a **consistent dict shape** (include `channel` / `kind` discriminator).
- **Idempotency:** Graph retries can re-invoke route; avoid duplicate **external** spam — consider idempotency key `(run_id, item_url, channel, recipient_descriptor)` or “send at most once per run+URL+channel” policy documented in code.
- **Transient failures:** Story 5.2 documents replay limitations for in-app; external channel should similarly **not** block routing — persist failure rows for operator visibility.

## Git intelligence (recent commits)

- `feat(epic-5): in-app notifications…` — patterns for **`node_route`** hook, **`services/notifications/`**, repositories, tests.
- `feat(epic-5): routing rules engine…` — **`routing_decisions`** contract and audit.

## Latest technical information

- **httpx 0.28.x** — async client; reuse connection limits thoughtfully in long-running workers.
- **LangGraph 1.1.6** — do not upgrade in-story unless required for a security fix.

## Project context reference

- No `project-context.md` in repo; rely on Architecture + this story + codebase patterns above.

## Story completion status

- **review** — Implementation complete; ready for code-review workflow.
