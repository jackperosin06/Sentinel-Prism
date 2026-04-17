# Deferred work tracker

## Deferred from: code review of 3-3-implement-scout-and-normalize-nodes-wired-in-graph.md (2026-04-17)

- **`normalize` does not verify `raw.source_id` matches state `source_id`:** A raw item whose payload `source_id` diverges from `state["source_id"]` is still normalized and stamped with the state's `source_name` / `jurisdiction`. Acceptable under MVP single-source-per-run assumption; revisit when multi-source fan-out or `POST /runs` wiring lands. [`src/sentinel_prism/graph/nodes/normalize.py:83-102`]
- **`_tz_aware_or_none` silently coerces naive datetimes with no audit log:** Docstring promises "with an audit-log note" but `normalize_scout_item` emits none. Naive `published_at` values from lax RSS producers are pinned to UTC with no forensic trail — address during a normalization hygiene pass. Pre-existing Story 3.1 code. [`src/sentinel_prism/services/ingestion/normalize.py:67-81`]
- **Scoring heuristic vs `_clean_text` disagree on NUL-only strings:** `_mvp_confidence_scores` awards title credit when `item.title.strip()` is truthy (e.g. `"\x00\x00"`), but `_clean_text` scrubs NULs and stores `None`, violating the stated invariant in `_clean_text`'s docstring ("so the scoring heuristic and persisted value agree"). Pre-existing Story 3.1 code. [`src/sentinel_prism/services/ingestion/normalize.py:45-64`]
- **`AgentState.flags` has no reducer; module docstring only discusses list channels:** Concurrent branches each emitting a single flag will last-writer-wins clobber each other. Acknowledged by Story 3.2 review — restated here so Epic 3 branching work (Story 3.5) does not forget it. [`src/sentinel_prism/graph/state.py:34`]
- **Per-node source row lookups in `scout` + `normalize` are redundant and TOCTOU-risky:** Both nodes open their own session to re-read the same `Source` row; an admin rename / jurisdiction change between the two nodes produces raw items tagged under one jurisdiction and normalized rows under another. `execute_poll` avoided this by snapshotting once while attached. Consolidate by threading `source_name` / `jurisdiction` on state after scout. [`src/sentinel_prism/graph/nodes/scout.py:57-58`, `src/sentinel_prism/graph/nodes/normalize.py:63-79`]
- **`operator.add` reducer accumulates duplicate `normalized_updates` on repeated `ainvoke` of the same checkpoint thread:** Completion Notes acknowledge this — `node_normalize` maps over all `raw_items` each invocation, so resuming the same thread appends duplicate normalized rows. Will require a delta/idempotent normalize or a "pending" vs "processed" raw-items split in the state channel. [`src/sentinel_prism/graph/nodes/normalize.py:52-102`]
- **`node_normalize` does `list(state.get("raw_items") or [])` without type assertion:** If upstream state is mutated to a dict (or any non-list iterable), `list(...)` silently iterates keys. Defensive type safety; add `isinstance(raws, list)` guard when ingestion state contract is tightened. [`src/sentinel_prism/graph/nodes/normalize.py:52`]

## Deferred from: code review of 3-2-define-agentstate-and-graph-compilation-shell.md (2026-04-17)

- **Strengthen reducer-behavior test coverage (Story 3.3):** `test_append_reducer_uses_operator_add` asserts only stdlib list concatenation, and `test_compile_shell_round_trip_checkpoint` never appends to an `Annotated[..., operator.add]` channel. Real list-producing nodes in Story 3.3 will drive proper merge assertions (seed a non-empty list channel, invoke twice on the same `thread_id`, assert append semantics). [`tests/test_graph_shell.py`]
- **No reducer on `flags` channel:** `flags: dict[str, bool]` has no `Annotated[..., merge]` reducer, so future parallel nodes will last-writer-wins. Must be decided before Story 3.3 adds branching. [`src/sentinel_prism/graph/state.py`]
- **`llm_trace` is a replace channel with no merge contract:** Multiple writers will race. Define append vs. namespaced merge semantics before any node emits traces. [`src/sentinel_prism/graph/state.py`]
- **Default `compile_regulatory_pipeline_graph` creates a fresh `MemorySaver` per call:** Two compiles in the same process cannot share state. Acceptable for dev/CI; introduce a selector when Postgres saver story lands. [`src/sentinel_prism/graph/graph.py`]
- **Re-invocation with same `thread_id` is undefined by the shell:** `_node_shell` unconditionally sets `graph_shell_seen=True`; no idempotency/replay test. Revisit when `POST /runs/{id}/resume` lands. [`src/sentinel_prism/graph/graph.py`]
- **Unjustified explicit pins for `langgraph-sdk` and `langgraph-prebuilt`:** Neither is imported by the diff. Add a justifying comment or drop to transitive resolution. [`requirements.txt`]
- **Submodule import paths may be private:** `langgraph.checkpoint.base.BaseCheckpointSaver` and `langgraph.graph.state.CompiledStateGraph` — verify they are supported public surfaces for 1.1.6 and consider local re-export shims. [`src/sentinel_prism/graph/graph.py`, `src/sentinel_prism/graph/checkpoints.py`]
- **`new_pipeline_state` hardcodes the channel initializer list:** Any future `AgentState` field must be mirrored manually or the append-reducer precondition silently breaks. Derive from `AgentState.__annotations__` or add a compile-time test. [`src/sentinel_prism/graph/state.py`]
- **`tenant_id` absent from structured log payload and optional in state:** Multi-tenant traceability is not enforced at the graph boundary. Revisit when tenant-scoped RBAC reaches the graph. [`src/sentinel_prism/graph/state.py`, `src/sentinel_prism/graph/graph.py`]
- **`tenant_id` accepts empty/whitespace strings in `new_pipeline_state`:** Minor; tighten when tenant validation arrives. [`src/sentinel_prism/graph/state.py`]
- **Prod/SQL checkpointer follow-up not anchored:** Docstrings reference Architecture §3.5 / FR35 but there is no interface seam, factory selector, or config toggle. Capture explicitly when the Postgres saver story is scheduled. [`src/sentinel_prism/graph/checkpoints.py`]

## Deferred from: code review of 2-4-deduplication-and-retry-with-backoff.md (2026-04-16)

- **TOCTOU: `enabled` flag not atomic with dedup commit:** `execute_poll` reads `enabled` in session 1, performs network I/O, then commits dedup in session 3. A source disabled mid-poll still gets fingerprints written. Pre-existing from 2.2/2.3; no incorrect data produced, but `source_id` rows appear in the ledger. [`src/sentinel_prism/services/connectors/poll.py`]
- **Poll failure stored in JSONB blob with no query index or history:** `record_poll_failure` overwrites `last_poll_failure` each time — no consecutive-failure count, no history. Cannot query all currently-failing sources efficiently. Story 2.6 metrics work will add proper observability. [`src/sentinel_prism/db/repositories/sources.py`]
- **`Retry-After` header ignored on 429 responses:** Server-specified wait time is not consumed; exponential backoff may fire additional attempts within the header's requested delay. Enhancement; safe for MVP poll volumes. [`src/sentinel_prism/services/connectors/fetch_retry.py`]
- **Bozo RSS feed continues processing partial feedparser output:** When `bozo=True`, feedparser may return garbled entries. Currently logs a warning and continues; hard-fail option deferred. [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- **`IntegrityError` on non-fingerprint unique constraint leaves ingestion_dedup session in broken state:** Defensive concern; would only occur if a non-fingerprint constraint is violated on `source_ingested_fingerprints`. Unlikely with current schema. [`src/sentinel_prism/db/repositories/ingestion_dedup.py`]

## Deferred from: code review of 2-3-rss-http-connector-implementation-direct-path.md (2026-04-16)

- **Index-based dedup URN unstable across polls:** Fallback `urn:sentinel-prism:feed-item:{source_id}:{idx}` uses the enumeration index from a single fetch. Feed entry insertions shift all subsequent indexes, producing false dedup misses. Story 2.4 owns deduplication — address stable key strategy there. [`src/sentinel_prism/services/connectors/rss_fetch.py`]
- **TOCTOU: scheduled poll fires after source disabled:** `PollScheduler._run_scheduled_poll` checks `row.enabled` inside its own session, then `execute_poll` re-checks in a separate session. No incorrect fetch occurs, but there is a narrow window. Pre-existing from Story 2.2 review. [`src/sentinel_prism/workers/poll_scheduler.py`]

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

## Deferred from: code review of 2-6-per-source-metrics-exposure (2026-04-17)

- **Primary exception dropped on non-`ConnectorFetchFailed` fallback error (Story 2.5):** In `poll.py`'s fallback-exception branch where `fb_other_exc` is not a `ConnectorFetchFailed`, `primary_exc` is captured but never persisted; operators investigating a both-failed source lose the primary failure context entirely. Fix during 2.5 commit cleanup.
- **`_fallback_configured` silently no-ops for future `FallbackMode` variants without URL (Story 2.5):** The comment claims unknown modes surface via `_fetch_fallback`, but the function short-circuits to `False` whenever `url` is falsy. A future mode with an implicit URL (or a misconfigured row) silently bypasses fallback and is logged as a pure primary failure. Fix during 2.5 commit cleanup.
- **`error_class` uses `"primary|fallback"` pipe separator (Story 2.5):** Existing column was a single class name; the new delimiter breaks downstream `WHERE error_class=?` filters and class names containing a literal `|` become unparseable. Re-model as two columns or structured JSON during 2.5 cleanup.
- **PATCH `/sources/{id}` silently clears `fallback_url` when `fallback_mode=none` is sent alone (Story 2.5):** Two lines away, the code rejects the symmetric case (explicit `fallback_url=null`) as destructive, yet this implicit clear is allowed — inconsistent policy. Align during 2.5 cleanup.
- **PATCH `/sources/{id}` TOCTOU on fallback-pair invariant (Story 2.5):** Concurrent admin PATCHes can commit a final state violating `_validate_fallback_pair` because the invariant lives only in API code. Needs `SELECT … FOR UPDATE`, a version column, or a DB-level `CHECK ((fallback_mode='none' AND fallback_url IS NULL) OR (fallback_mode<>'none' AND fallback_url IS NOT NULL))`. Defer to Epic 2 hardening or 2.5 cleanup.
- **Alembic downgrade of `a7f6e5d4c3b2` drops accumulated metric state unconditionally:** Standard additive-migration trade-off — an ops rollback loses all ingestion counter history with no warning or backup hook. Document in the ops runbook (and, if preservation matters operationally, move counters to a separate table that survives schema rollbacks).

## Deferred from: code review of 3-1-persist-raw-captures-and-normalized-records.md (2026-04-17)

- **No `CheckConstraint` on `parser_confidence` / `extraction_quality` 0..1:** Model docstring / PRD FR10 declare the 0..1 range but the schema has no CHECK. Any future pipeline writing `1.5` or `-0.1` succeeds silently. Add CHECK constraints during confidence-scoring hardening (post-MVP).
- **`alembic/env.py` has no `target_metadata.naming_convention`:** `op.f("fk_...")` in migration `d8e9f0a1b2c3` is a no-op wrapper because no naming convention is configured. Constraint names in DB are stable, but future autogenerate diffs will produce spurious rename churn. Configure `Base.metadata.naming_convention` project-wide as part of an Alembic hygiene pass.
- **`server_default=sa.text("now()")` for `created_at` is session-TZ-dependent:** Project-wide convention predating Story 3.1; the stored `timestamptz` is still absolute, but downstream `DATE(created_at)` without a TZ cast varies by server config. Revisit during ops/timezone standardization.
- **Composite index `(source_id, created_at)` vs `published_at` ordering:** Analysts care about publication order, not insertion order. Back-filled rows will sort by insert time. Revisit when Story 3.2+ introduces query-pattern-driven index design.
- **`document_type` NOT NULL stored as `"unknown"` for MVP:** Spec explicitly permits this default, but `WHERE document_type = 'foo'` returns nothing and there is no way to distinguish "not yet classified" from "known-unknown". Reconsider when `classify` node lands (Epic 3, Story 3.3+).
- **`raw_captures` has no defense-in-depth `UNIQUE (source_id, item_url)`:** Spec AC4 delegates uniqueness to upstream dedup. A regression in `ingestion_dedup.register_new_items` would produce duplicate raw captures undetected. Consider adding a surrogate unique (e.g. `(source_id, item_url, captured_at)`) during an ingestion-hardening pass.
- **No per-item savepoint in `persist_new_items_after_dedup` (D2):** A single malformed item aborts the whole poll transaction AND drops the dedup fingerprints registered by `register_new_items`, so the source re-processes every item next tick (including the known-bad one — potential livelock). MVP tolerates atomic failure; revisit when production ingestion actually hits a malformed item. Options to consider then: (a) savepoint per item with `session.begin_nested()`, (b) pre-validate `ScoutRawItem` at normalize time and skip invalid items before persist.
- **Follow-up review reconfirmed D2 livelock risk:** `execute_poll` still runs dedup + persist in one transaction, so persistent bad items can keep reappearing after rollback. Kept deferred by design for MVP; revisit when ingestion hardening begins.
