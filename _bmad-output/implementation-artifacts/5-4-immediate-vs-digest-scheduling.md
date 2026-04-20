# Story 5.4: Immediate vs digest scheduling

Status: in-progress

<!-- Ultimate context engine analysis completed — comprehensive developer guide created -->

## Story

As the **system**,
I want **immediate alerts for critical/high and batched delivery for lower priority per policy**,
so that **alert fatigue is reduced** (**FR22**) while **critical information still arrives fast** (**PRD success metric**).

## Acceptance Criteria

1. **Policy-driven split (FR22)**  
   **Given** configurable **notification policy thresholds** (see Dev Notes — env-backed MVP is acceptable; DB-backed policy can follow Epic 6 patterns)  
   **When** `node_route` completes routing decisions for matched items  
   **Then** severities in the **immediate set** (minimum: **critical**; PRD also names **high** — default policy should include **critical** and **high** unless explicitly narrowed) follow the **immediate** path: **in-app** and **external** delivery behave per that policy **on the same run** (no artificial delay).  
   **And** severities **outside** the immediate set **do not** use the immediate single-item path; they are **recorded for digest** per config (enqueue-only during `node_route` — see AC #2).

2. **Digest enqueue (batched path)**  
   **Given** a non-immediate routed decision (matched, with `team_slug` / `item_url` / `severity` as today)  
   **When** digest policy is enabled  
   **Then** the system **persists** digest-bound work durably (PostgreSQL) with enough fields to render a later batched notification (title/URL/severity/team, `run_id`, timestamps) **without** sending SMTP/Slack per item inline in `node_route`.  
   **And** duplicate graph replays remain safe (idempotency key aligned with existing notification patterns — e.g. `(run_id, item_url, …)` scoped to digest membership).

3. **Digest flush job**  
   **Given** pending digest rows  
   **When** a scheduled job runs (reuse **APScheduler** — already used for ingestion in `workers/poll_scheduler.py`)  
   **Then** pending items are **grouped** (at minimum by **team** and **digest window**) and **delivered** via existing notification surfaces (in-app and/or external adapters) according to a documented MVP rule (e.g. one combined message per team per flush).  
   **And** failures are **observable** (structured logs + optional `delivery_events` / delivery-attempt rows consistent with Stories 5.2–5.3 — do not swallow).

4. **Routing integration point**  
   **Given** `graph/nodes/route.py` already calls `enqueue_critical_in_app_for_decisions` then `enqueue_external_for_decisions`  
   **When** this story lands  
   **Then** orchestration **splits** decisions by severity/policy **before** calling enqueue functions — **avoid duplicating** user resolution, idempotency, and `delivery_events` shape; **extend** `services/notifications/in_app.py` / `external.py` (or add a thin `scheduling.py` coordinator **called from** those modules or from `node_route`) rather than embedding SMTP/HTTP in the graph node.  
   **And** `channel_slug` on routing decisions — **reserved in Story 5.3** — may begin influencing **which channel** receives digest vs immediate **if** the change is bounded and tested; if scope explodes, document “channel_slug hooks stub only” and ship severity split + digest queue first.

5. **Non-blocking pipeline (NFR7)**  
   **Given** digest flush may perform slow I/O  
   **When** the **route** node runs  
   **Then** **only** enqueue + cheap DB work happens inline; **heavy** fan-out for digest batches runs in the **worker / scheduled job**, not blocking graph completion.  
   **And** address the **deferred Story 5.3** item: optional **wall-clock / concurrency budget** for external immediate sends so a large decision list cannot block `node_route` unbounded (document chosen cap).

6. **Config & docs**  
   **Given** operators need to tune behavior without code changes  
   **When** they read `.env.example` and Dev Notes  
   **Then** variables exist for: immediate severity set (or ordered thresholds), digest enable/disable, digest cadence (cron or interval), and flush batch limits — **placeholders only**, no secrets.

7. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** unit tests cover policy classification (immediate vs digest), idempotency on replay, and digest repository grouping; integration-style tests follow existing `tests/test_external_notifications.py` / `tests/test_notifications_*.py` patterns; if Alembic head changes, update **`tests/test_alembic_cli.py`**.

## Tasks / Subtasks

- [x] **Policy module (AC: #1, #6)**  
  - [x] Define canonical severity strings consistent with classifications (`critical`, `high`, `medium`, `low` — match existing routing/classify outputs).  
  - [x] Load immediate vs digest policy from settings (pydantic settings or small loader parallel to `external_settings.py`).

- [x] **Refactor enqueue entry points (AC: #4, #1)**  
  - [x] Replace single “critical-only” gate with policy-aware gating: immediate path calls existing enqueue functions with **filtered** decisions; digest path receives the complement.  
  - [x] Keep `IN_APP_ALLOWED_SEVERITIES` / external parity **or** rename/document superseding policy (avoid two conflicting sources of truth — one module-level policy object).

- [x] **Digest persistence (AC: #2)**  
  - [x] Alembic migration + ORM model + repository for digest queue rows.  
  - [x] Indexes for: pending-by-time, team, dedupe keys.

- [x] **Digest worker (AC: #3, #5)**  
  - [x] APScheduler job registration (new module under `workers/` or extend scheduler pattern — **do not** block FastAPI startup indefinitely; mirror `PollScheduler` lifecycle from `main.py` if that is where poll scheduler starts).  
  - [x] Flush routine: select pending → group → call in-app / external adapters with **batched** payloads.

- [x] **Observability (AC: #3, #5)**  
  - [x] Structured log events for: digest_enqueued, digest_flush_start, digest_flush_complete, digest_flush_partial_failure.  
  - [x] Merge `delivery_events` dict shapes with Story 5.2/5.3 reducers (`operator.add` lists).

- [x] **Tests (AC: #7)**  
  - [x] Replay/idempotency: second `node_route` pass does not duplicate digest rows.  
  - [x] Policy matrix: critical→immediate only; high→immediate when policy includes high; medium→digest when immediate excludes it.

- [x] **Docs (AC: #6)**  
  - [x] `.env.example` + short module docstring on policy defaults.

### Review Findings

_Generated 2026-04-21 via `bmad-code-review` (Blind Hunter + Edge Case Hunter + Acceptance Auditor)._

#### Decisions needed (resolve before patching)

- [x] [Review][Decision→Resolved] **Cross-module private imports from `external.py`** — *Jack, 2026-04-21: Option 1 — extract `_claim_attempt`, `_finalize_attempt`, `_safe_detail`, `_slack_descriptor`, `_slack_escape` into `services/notifications/_attempts.py` (shared module); `external.py` and `digest_flush.py` import from there.*
- [x] [Review][Decision→Resolved] **Two sources of truth for immediate severities** — *Jack, 2026-04-21: Option 1 — policy-only source of truth. Remove/deprecate static `IN_APP_ALLOWED_SEVERITIES` / `IN_APP_MIN_SEVERITY`; gate via `load_notification_policy().immediate_severities` everywhere; update tests accordingly.*
- [x] [Review][Decision→Resolved] **Digest payload exposes full `item_url` to broad audiences** — *Jack, 2026-04-21: Option 2 — keep full URLs for MVP; document explicitly in story/dev notes and `.env.example` guidance.*
- [x] [Review][Decision→Resolved] **Digest batching can span one team across jobs** — *Jack, 2026-04-21: Option 2 — accept split behavior for MVP; document that high-volume teams may receive multiple digests within cadence.*
- [x] [Review][Decision→Resolved] **Digest unique key ignores `channel_slug`** — *Jack, 2026-04-21: Option 1 — keep current dedupe key `(run_id, item_url, team_slug)`; treat `channel_slug` as informational hook only in Story 5.4; document explicitly.*
- [x] [Review][Decision→Resolved] **External immediate cap scope is misleading** — *Jack, 2026-04-21: Option 1 — keep behavior but rename to `...PER_RUN` semantics with backward-compatible alias for existing env key(s).*
- [x] [Review][Decision→Resolved] **`title` column collected but never rendered** — *Jack, 2026-04-21: Option 1 — render title in digest body when present, with URL/severity fallback.*
- [x] [Review][Decision→Resolved] **`severity="digest"` sentinel bypasses policy + taxonomy** — *Jack, 2026-04-21: Option 1 — reuse in-app enqueue helper/shared path so severity remains canonical and duplicate user-resolution/SAVEPOINT logic is removed.*

#### Patches (fixable)

- [x] [Review][Patch] **Decision 1 — shared `_attempts.py`** — create `src/sentinel_prism/services/notifications/_attempts.py` with the five helpers; re-export or update `external.py` and `digest_flush.py`; extend `verify_imports.py` if needed; run tests.
- [x] [Review][Patch] **Decision 2 — immediate severity source-of-truth cleanup** — remove or deprecate `IN_APP_ALLOWED_SEVERITIES` / `IN_APP_MIN_SEVERITY`; use `load_notification_policy().immediate_severities` as the only gate; update enqueue tests to pin policy via env + `reload_notification_policy`.
- [x] [Review][Patch] **Decision 3 — full-URL policy documentation** — keep full `item_url` in digest payload; add explicit operator note in docs (`.env.example` / module comments) about potential sensitivity and expected use.
- [x] [Review][Patch] **Decision 4 — split-batch behavior documentation** — keep flat `batch_max`; document that a single team can receive multiple digests in high-volume windows.
- [x] [Review][Patch] **Decision 5 — `channel_slug` is informational for 5.4** — keep dedupe key unchanged; document that `channel_slug` is reserved for future routing and not part of digest idempotency yet.
- [x] [Review][Patch] **Decision 6 — cap naming accuracy + compatibility alias** — rename policy field/env semantics from `...PER_ROUTE` to `...PER_RUN`; support legacy key for compatibility and document precedence.
- [x] [Review][Patch] **Decision 7 — render `title` in digest body** — update formatter to include title when available, fallback to existing severity+URL line format.
- [x] [Review][Patch] **Decision 8 — remove `severity=\"digest\"` sentinel path** — reuse existing in-app enqueue helper/shared utility for digest in-app fanout while preserving canonical severities.
- [x] [Review][Patch] **Scheduler hygiene: no `max_instances`/`coalesce`/`misfire_grace_time`** [`src/sentinel_prism/workers/digest_scheduler.py`]
- [x] [Review][Patch] **External immediate cap slices by list order, not severity priority** [`src/sentinel_prism/services/notifications/scheduling.py`]
- [x] [Review][Patch] **`enqueue_digest_item_ignore_conflict` returns `True` when `rowcount is None`, inflating insert counts + `digest_enqueued` events** [`src/sentinel_prism/db/repositories/digest_queue.py:enqueue_digest_item_ignore_conflict`]
- [x] [Review][Patch] **Missing tests for flush path (AC #7 gap): replay-idempotency, `list_pending_batch` + `by_team` grouping, batched in-app/external delivery** [`tests/test_notification_scheduling.py`]
- [x] [Review][Patch] **Non-deterministic `digest_run_id` on partial failure** — hash of row-id set changes when new rows enqueue before retry, defeating `(run_id, item_url, user_id)` dedupe on the in-app side [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_run_id_for_rows`]
- [x] [Review][Patch] **Case-sensitive team grouping** — `by_team[row.team_slug]` vs `team_slug_key = team_slug.strip().lower()` mismatch [`src/sentinel_prism/services/notifications/digest_flush.py:flush_digest_queue_once`]
- [x] [Review][Patch] **Stale Slack claim treated as success; rows deleted without delivery** — `no_new_rows` branch emits no error; outer loop sees empty `ext_err` and deletes [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_slack`]
- [x] [Review][Patch] **Silent digest body truncation** — `_truncate_body` adds `…` with no "and N more" indicator; default `digest_flush_batch_max` almost guarantees clipping [`src/sentinel_prism/services/notifications/digest_flush.py:_build_digest_body`]
- [x] [Review][Patch] **Lifespan leaks poll scheduler if digest start raises** — `push_async_callback(_shutdown_scheduler)` only registered after both schedulers start [`src/sentinel_prism/main.py`]
- [x] [Review][Patch] **`DigestScheduler.start` is not re-entry safe** — second call orphans the previous scheduler [`src/sentinel_prism/workers/digest_scheduler.py:start`]
- [x] [Review][Defer] **`load_notification_policy` is `lru_cache`d; env changes don't take effect without explicit reload** — deferred, pre-existing process-restart configuration model accepted for MVP.
- [x] [Review][Patch] **`digest_flush_partial_failure` logged twice** — per-phase inside loop and again after loop, with `"teams": len(by_team)` reporting total not failed [`src/sentinel_prism/services/notifications/digest_flush.py:flush_digest_queue_once`]
- [x] [Review][Patch] **`test_enqueue_skips_non_immediate_severity` no longer pins policy** — severity `medium` passes by default without monkeypatching `NOTIFICATIONS_IMMEDIATE_SEVERITIES` + `reload_notification_policy` [`tests/test_notifications_enqueue.py`]
- [x] [Review][Patch] **`provider_message_id` column stuffed with `_safe_detail(hint)` instead of a real provider id** — pass `None` [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_slack`]
- [x] [Review][Patch] **`enqueue_digest_decisions` silently drops malformed/missing-field decisions** — no counter, no log, no `errors[]` [`src/sentinel_prism/services/notifications/digest_flush.py:enqueue_digest_decisions`]
- [x] [Review][Patch] **`monkeypatch.setattr` calls on `sentinel_prism.graph.nodes.route.process_routed_notification_deliveries` lack `raising=True`** — future renames silently no-op [`tests/test_graph_route.py`]
- [x] [Review][Patch] **SMTP send failure / empty-recipient path leaves `out_err` empty, so rows get deleted anyway** [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_smtp`]
- [x] [Review][Patch] **Env immediate list accepts unknown severity tokens silently** — typos misroute without operator-visible validation [`src/sentinel_prism/services/notifications/notification_policy.py`]
- [x] [Review][Patch] **Digest disabled + only non-immediate decisions → items vanish with only an info log** — append to `errors[]` or emit a persistent warning with counts [`src/sentinel_prism/services/notifications/scheduling.py`]
- [x] [Review][Patch] **`node_route`'s broad `try/except` around `process_routed_notification_deliveries` collapses all structured step errors into one `routed_notifications_unhandled` entry** — preserve partial `delivery_events`/`errors` before rethrowing [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] **Truncated immediate-external decisions silently dropped (cap overflow)** — log only; not surfaced in `errors[]`/`delivery_events` and not shunted to digest [`src/sentinel_prism/services/notifications/scheduling.py`]
- [ ] [Review][Patch] **`test_graph_route` regression: renamed `test_node_route_invokes_in_app_enqueue_and_merges_delivery_events` no longer asserts `enqueue_critical_in_app_for_decisions` is invoked** — only exercises the new coordinator, losing the Story 5.2 contract [`tests/test_graph_route.py`]

#### Dismissed as noise

- `_digest_run_id_for_rows` uses `uuid.NAMESPACE_URL` for a non-URL key — semantic nit; subsumed by the run-id determinism patch.
- Auditor finding that "digest flush `delivery_events` never reach `AgentState`" — persistence via `notification_delivery_attempts` rows (`_claim_attempt`/`_finalize_attempt`) already satisfies the attempt-audit contract; in-memory dict events from an out-of-graph worker have no `AgentState` to reduce into.

#### Re-review Findings (2026-04-20)

- [x] [Review][Patch] **Digest flush path coverage still incomplete (AC #7)** [`tests/test_notification_scheduling.py`, `src/sentinel_prism/services/notifications/digest_flush.py`]
- [x] [Review][Patch] **`digest_run_id` remains unstable across partial-failure retries** [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_run_id_for_rows`]
- [x] [Review][Patch] **`DigestScheduler.start` can leak a running scheduler if `add_job(...)` raises** [`src/sentinel_prism/workers/digest_scheduler.py:start`]
- [x] [Review][Patch] **`reset_digest_scheduler_for_tests()` drops singleton without shutdown** [`src/sentinel_prism/workers/digest_scheduler.py:reset_digest_scheduler_for_tests`]
- [x] [Review][Patch] **Digest SMTP finalize failure not propagated** — send succeeds but finalize can fail and still count as recorded [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_smtp`]
- [x] [Review][Patch] **`split_decisions_for_policy` silently drops invalid matched inputs** — no observability envelope for malformed decision shape [`src/sentinel_prism/services/notifications/scheduling.py:split_decisions_for_policy`]
- [x] [Review][Patch] **Digest SMTP all-blank recipients can be treated as non-failure and rows are deleted** [`src/sentinel_prism/services/notifications/digest_flush.py:_digest_smtp`]
- [x] [Review][Patch] **`node_route` broad catch still collapses partial notification context on coordinator exception** [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] **`sprint-status.yaml` comment/header `last_updated` mismatch** [`_bmad-output/implementation-artifacts/sprint-status.yaml`]

- [x] [Review][Defer] **Policy cache runtime mutability (`lru_cache`)** — deferred, pre-existing (process-restart semantics accepted for now).
- [x] [Review][Defer] **Immediate external overflow not auto-rerouted to digest** — deferred, pre-existing (budget behavior currently explicit and logged).

## Dev Notes

### Architecture compliance

- **Canonical locations:** `graph/nodes/route.py`, `services/notifications/`, `workers/`, `db/models.py`, `db/repositories/`, `alembic/versions/`. [Source: `_bmad-output/planning-artifacts/architecture.md` — FR21–FR25 mapping]
- **Boundary:** Graph **nodes** call **services**; **services** do not import `graph.graph` or node modules.
- **State:** Append to **`delivery_events`** with the same list-of-dicts contract as Stories 5.2–5.3 so LangGraph **`operator.add`** reducers stay predictable. [Source: `src/sentinel_prism/graph/state.py`]

### Dependency on Stories 5.1–5.3

- **5.1 — Routing:** `routing_decisions` carry `team_slug`, `channel_slug`, `item_url`, `severity` — policy split reads these. Resolver: `services/routing/resolve.py`, `graph/nodes/route.py`.
- **5.2 — In-app:** `enqueue_critical_in_app_for_decisions` (`in_app.py`) — severity gate, team targeting, idempotency `(run_id, item_url, user_id)`, `delivery_events`, SAVEPOINT batching. **Extend** rather than fork.
- **5.3 — External:** `enqueue_external_for_decisions` (`external.py`) — mirrors in-app severity gate today; **two-phase** idempotency (`pending` → terminal) on `notification_delivery_attempts`. Digest flush must **not** bypass idempotency or leak secrets in `detail`. Story 5.3 **reserved** `channel_slug` for this story — implement bounded use or document stub.

### Previous story intelligence (5.3)

- **MVP external policy** today: `NOTIFICATIONS_EXTERNAL_CHANNEL` + critical-only + team membership. Story 5.4 should **generalize severity** and **digest** without breaking claim/finalize idempotency.
- **Deferred review item:** unbounded SMTP loop duration — **batch budget / concurrency** belongs here (AC #5).
- **Replay limitation:** in-app transient failures still documented in `in_app.py`; digest enqueue should surface failures in `errors[]` similarly.

### Project structure notes

- Extend **`verify_imports.py`** if new public modules are added.
- Register any new scheduler startup/shutdown hooks consistently with **`main.py`** (search `PollScheduler` / `get_poll_scheduler`).

### References

- Epics: `_bmad-output/planning-artifacts/epics.md` — Epic 5, Story 5.4.
- PRD: `_bmad-output/planning-artifacts/prd.md` — **FR22**, alert-fatigue success metric.
- Architecture: `_bmad-output/planning-artifacts/architecture.md` — §6 table (FR21–FR25), job scheduling (APScheduler).
- Code: `src/sentinel_prism/graph/nodes/route.py`, `src/sentinel_prism/services/notifications/in_app.py`, `src/sentinel_prism/services/notifications/external.py`, `src/sentinel_prism/workers/poll_scheduler.py`.

## Dev Agent Record

### Agent Model Used

Composer (dev-story workflow)

### Debug Log References

### Completion Notes List

- Implemented `notification_policy.py` (env: `NOTIFICATIONS_IMMEDIATE_SEVERITIES`, digest enable/interval, external immediate cap, batch max).
- `scheduling.process_routed_notification_deliveries` splits decisions; immediate path calls existing in-app + external with **truncated** external list per cap; digest path enqueues `notification_digest_queue` table.
- `digest_flush.py` + `workers/digest_scheduler.py` (APScheduler interval) flush queue per team with batched in-app rows (`severity=digest`) and external SMTP/Slack using existing idempotency helpers.
- `node_route` calls `process_routed_notification_deliveries` inside broad try/except (same safety as prior external wrapper).
- `channel_slug` stored on digest rows for future routing; delivery still uses global `NOTIFICATIONS_EXTERNAL_CHANNEL`.
- Migration `a8b9c0d1e2f3_add_notification_digest_queue.py`; Alembic head test updated.
- Full suite: 257 passed, 10 skipped.

### File List

- `alembic/versions/a8b9c0d1e2f3_add_notification_digest_queue.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/digest_queue.py`
- `src/sentinel_prism/graph/nodes/route.py`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/services/notifications/digest_flush.py`
- `src/sentinel_prism/services/notifications/external.py`
- `src/sentinel_prism/services/notifications/in_app.py`
- `src/sentinel_prism/services/notifications/notification_policy.py`
- `src/sentinel_prism/services/notifications/scheduling.py`
- `src/sentinel_prism/workers/digest_scheduler.py`
- `.env.example`
- `verify_imports.py`
- `tests/test_alembic_cli.py`
- `tests/test_graph_route.py`
- `tests/test_notifications_enqueue.py`
- `tests/test_notification_scheduling.py`

### Change Log

- 2026-04-21: Story 5.4 — immediate vs digest policy, digest queue + flush worker, external cap, tests and docs.

---

## Technical requirements (guardrails)

| Requirement | Detail |
|-------------|--------|
| Stack | FastAPI, SQLAlchemy 2.x async, Alembic, LangGraph 1.1.x (`requirements.txt`) |
| Scheduler | **APScheduler 3.10.4** — already pinned; align with `AsyncIOScheduler` patterns in `poll_scheduler.py` |
| HTTP / SMTP | Reuse **`httpx`** and existing adapters — no new HTTP client for MVP |
| Auth | Digest **admin** APIs only if needed for ops; RBAC same as existing admin routes if exposed |
| Secrets | Env-only config; document placeholders in **`.env.example`** |

## Architecture extraction (story-specific)

- **Connector swap:** Notification delivery stays behind **service** interfaces; digest flush calls the same adapter/orchestration layer as immediate sends. [Source: `prd.md` — Technical Direction / outbound adapters]
- **Workers folder:** `workers/` is the documented home for **scheduled jobs → trigger graph**; digest flush fits as a **scheduled job** that **does not** have to invoke the full graph if it only sends notifications. [Source: `architecture.md` — repo layout]

## Library / framework notes

- **APScheduler:** Use `AsyncIOScheduler` + cron or interval triggers; ensure graceful shutdown on app lifecycle (mirror poll scheduler tests if any).
- **LangGraph 1.1.6:** Do not upgrade in-story unless required for a security fix.

## File structure requirements

- New: `workers/digest_scheduler.py` (name flexible), `db/repositories/*digest*.py`, Alembic revision for digest tables.
- Modify: `src/sentinel_prism/graph/nodes/route.py`, `services/notifications/*.py`, `main.py` (scheduler wiring), `db/models.py`, `.env.example`, `verify_imports.py`, `tests/…`

## Testing requirements

- Pytest + pytest-asyncio; mock external I/O at adapter boundaries.
- Follow patterns in **`tests/test_external_notifications.py`** and notification API tests for ASGI + DB setup.

## Git intelligence (recent commits)

- `feat(epic-5): Slack/SMTP external notifications, delivery log…` — `external.py`, adapters, `node_route` try/except safety net.
- `feat(epic-5): in-app notifications…` — `in_app.py`, `delivery_events` reducer contract.
- `feat(epic-5): routing rules engine…` — `routing_decisions` shape and `node_route` audit idempotency.

## Latest technical information

- **APScheduler 3.10.x** — stable; use async job executors consistent with existing poll worker.
- **FR22 PRD wording** — “critical/high” per **policy**: story defaults should include both unless product narrows to critical-only for MVP (if narrowed, document explicitly in Dev Notes and AC #1).

## Project context reference

- No `project-context.md` in repo; rely on Architecture + this story + codebase patterns above.

## Story completion status

- **review** — Implementation complete; ready for code-review workflow.
