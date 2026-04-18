# Story 3.8: Pipeline-generated audit events

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **auditor**,
I want **append-only audit rows for key pipeline steps (scout, normalize, classify)**,
so that **Epic 8 search/replay and operator forensics have durable, queryable domain events** tied to **`run_id`** (**FR33** partial, **NFR8**).

## Acceptance Criteria

1. **Durable `audit_events` table (append-only)**  
   **Given** the application database  
   **When** migrations are applied  
   **Then** an **`audit_events`** (or architecturally equivalent name) table exists with at minimum:  
   - surrogate **`id`** (UUID PK)  
   - **`created_at`** (timestamptz, server default `now()`)  
   - **`run_id`** (UUID, indexed — parse from `AgentState.run_id` which is a canonical string at the graph boundary; reject invalid UUID strings at the write boundary with a structured log, do not crash the whole pipeline)  
   - **`action`** (short string or `StrEnum` — stable snake_case values, e.g. `pipeline_scout_completed`, `pipeline_normalize_completed`, `pipeline_classify_completed`)  
   - **`source_id`** (nullable UUID FK to `sources.id` **RESTRICT** or nullable UUID without FK if circular migration risk — prefer FK + RESTRICT to match `raw_captures` / audit-chain discipline)  
   - **`actor_user_id`** (nullable UUID FK to `users.id` — **null** for automated pipeline events in this story)  
   - **`metadata`** JSONB (nullable) for **non-secret** summary only: counts, flags, `item_url` samples (bounded list length), **`llm_trace.model_id` / `prompt_version`** for classify, error **classes** (not full stack traces), web-search summary keys from `llm_trace.web_search` if present — **no** raw capture payloads, **no** secrets, **no** full LLM prompts  

2. **Repository + boundary**  
   **Given** a running async SQLAlchemy session  
   **When** code calls a single **`append_audit_event(...)`** (or equivalent) helper  
   **Then** exactly one **INSERT** is issued — **no** UPDATE/DELETE paths in application code for this table  
   **And** the helper lives under **`src/sentinel_prism/db/repositories/`** (new module is fine), matching Architecture’s **`db/audit`** mapping intent  

3. **Graph node instrumentation (minimal scope)**  
   **Given** a compiled regulatory pipeline run  
   **When** **`node_scout`**, **`node_normalize`**, and **`node_classify`** each **completes its successful path** (returns a partial state update that advances the pipeline, including partial success paths that intentionally continue — follow the same “done” semantics used for structured logging today)  
   **Then** at least one audit row is appended per node completion with the correct **`action`** enum and **`run_id`**  
   **And** on **DB write failure**, the node **logs** a structured warning (`event` + `ctx` with `run_id`, `action`, `error_class`) and appends to **`errors[]`** with `step` scoped to audit (e.g. `audit_write`) — **do not** silently drop failures  

4. **Retry / duplicate semantics (Story 3.6 alignment)**  
   **Given** LangGraph **full-node retries** on transient errors  
   **When** a node completes again after a retry  
   **Then** **additional** audit rows may be appended for the same logical step (append-only trail)  
   **And** **`metadata`** SHOULD include a monotonic **`attempt`** or **`completed_at`** timestamp so Epic 8 consumers can order events — if LangGraph exposes retry index in state in your version, prefer threading it; otherwise document ordering by **`created_at`** only  

5. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** repository unit tests prove INSERT-only contract and JSONB shape constraints  
   **And** at least one **graph-level** test (existing `MemorySaver` + stub LLM patterns) asserts **≥1** audit row exists per expected **`action`** after a short pipeline invocation **when DB is available** — follow the project’s established integration-test pattern (if Postgres is required, use the same skip/fixture approach as other DB tests; do not require Docker for pure unit tests)  

6. **Out of scope (explicit)**  
   **Given** this story  
   **Then** **do not** implement **GET /runs/{id}** audit aggregation, Epic 8 search API, or UI — **`api/routes/runs.py`** remains a stub unless a prior story already wired it  
   **And** **do not** record **human_review_gate** / analyst overrides here — that belongs to **Story 4.2** (actor_user_id + user-attributed events)  

## Tasks / Subtasks

- [x] **Schema + migration (AC: #1)**  
  - [x] Add SQLAlchemy model (e.g. `AuditEvent`) in `src/sentinel_prism/db/models.py` with indexes sensible for later Epic 8 filters: `(run_id, created_at)`, `(action)`, optional `(source_id)`.  
  - [x] Alembic revision in `alembic/versions/` — additive-only; match existing migration naming/style.  

- [x] **Repository (AC: #2)**  
  - [x] `src/sentinel_prism/db/repositories/audit_events.py` (name illustrative) with `async def append_audit_event(session, *, run_id: UUID, action: str, source_id: UUID | None, metadata: dict | None, actor_user_id: UUID | None = None) -> UUID`.  
  - [x] Export from `src/sentinel_prism/db/repositories/__init__.py` if that is the project convention.  

- [x] **`node_scout` hook (AC: #3)**  
  - [x] After successful fetch path (when raw items are produced or intentional empty success), open session, append `pipeline_scout_completed` with metadata: `raw_item_count`, `trigger`, connector outcome summary, **no** per-item bodies.  

- [x] **`node_normalize` hook (AC: #3)**  
  - [x] After normalization pass completes, append `pipeline_normalize_completed` with `normalized_count`, `source_id`.  

- [x] **`node_classify` hook (AC: #3)**  
  - [x] After classify loop finishes (before return), append `pipeline_classify_completed` with `classification_count`, `llm_trace.status`, `model_id`, `prompt_version`, summarized `web_search` block if present — **no** full rationales or secrets.  

- [x] **Tests (AC: #5)**  
  - [x] `tests/test_audit_events.py` (or split) — repository + metadata bounds.  
  - [x] Extend an existing graph test file if cleaner than new module.  
  - [x] Extend `verify_imports.py` if new modules are import-sensitive.

### Review Findings

#### Decision-needed (all resolved)

- [x] [Review][Decision→Patch] **`completed_at` metadata duplicates server-side `created_at` with clock drift** — Resolved: drop `completed_at` from node metadata and rely exclusively on `created_at` for ordering (AC #4's "otherwise document ordering by `created_at` only" fallback). Tracked below as a Patch item.
- [x] [Review][Decision→Deferred] **`ix_audit_events_action` index usefulness** — Deferred until Epic 8 audit-log search stories define concrete query patterns — no evidence yet that cross-run action filtering is needed.
- [x] [Review][Decision→Dismissed] **Audit-write failure surfaced via `AgentState.errors` with `step: audit_write`** — Verified safe: `graph/routing.py:20-24` consults only `flags["needs_human_review"]`; `graph/retry.py:11-22` retry predicate is exception-type based (`is_transient_classification_error`), not state-based; `graph/state.py:36` `errors` is an accumulator reducer with no behavioral hooks. `step: audit_write` entries cannot flip node success/failure. Spec-mandated pattern is safe as-is.
- [x] [Review][Decision→Patch] **Migration `downgrade()` drops `audit_events` unconditionally** — Resolved: add an ops-note docstring warning at the top of `downgrade()` calling out that the operation is lossy on populated databases and that operators should archive first. Tracked below as a Patch item.
- [x] [Review][Decision→Dismissed] **Invalid `run_id` silently skipped with only a `WARNING` log** — Spec-literal: AC #1 requires structured log + no crash. Log-only is sufficient; no additional `errors[]` entry.

#### Patch (all applied)

- [x] [Review][Patch] **Drop `completed_at` from node audit metadata (from Decision 1)** [`src/sentinel_prism/graph/nodes/scout.py`, `src/sentinel_prism/graph/nodes/normalize.py`, `src/sentinel_prism/graph/nodes/classify.py`] — Removed `completed_at` from all three nodes; ordering now relies on server-side `created_at` (AC #4 compliant). Unused `datetime`/`timezone` imports removed from normalize and classify.
- [x] [Review][Patch] **Add lossy-downgrade warning to migration `downgrade()` (from Decision 4)** [`alembic/versions/e9f0a2b4c6d8_add_audit_events_table.py`] — Added docstring warning operators that `drop_table` is irreversible and to export `audit_events` (`pg_dump -t audit_events`) before downgrading.
- [x] [Review][Patch] **Empty-input completion paths skip audit emission** [`src/sentinel_prism/graph/nodes/classify.py`, `src/sentinel_prism/graph/nodes/normalize.py`] — Both nodes now emit their completion audit row before the empty-input early return (`normalized_count=0` / `classification_count=0` with `llm_trace.status="no_attempt"`). Matches scout's own empty-success handling.
- [x] [Review][Patch] **Audit-write failure detail uses unsafe `str(exc)`** [`src/sentinel_prism/graph/pipeline_audit.py`] — Added `_safe_error_detail` helper (mirrors `classify.py` pattern); audit-write exceptions are now truncated and newline-stripped before landing in `AgentState.errors`. Protects NFR12 from SQL parameter leaks.
- [x] [Review][Patch] **Metadata size/whitelist not enforced beyond `item_url_samples`** [`src/sentinel_prism/db/repositories/audit_events.py`] — `_trim_metadata` now caps per-URL length at 512 chars (`_MAX_URL_LENGTH`) and logs a structured `audit_metadata_oversize` warning when serialized metadata exceeds 8 KB (`_MAX_METADATA_BYTES`). Does NOT drop rows — the warning gives operators a signal without breaking AC #3.
- [x] [Review][Patch] **`_ITEM_URL_SAMPLES_CAP = 10` duplicated across two modules** [`src/sentinel_prism/graph/nodes/scout.py`, `src/sentinel_prism/db/repositories/audit_events.py`] — Promoted to public `ITEM_URL_SAMPLES_CAP` in `audit_events.py`; scout now imports it instead of defining its own local constant.
- [x] [Review][Patch] **`event_metadata` shape not validated at the boundary** [`src/sentinel_prism/db/repositories/audit_events.py`] — `_trim_metadata` now raises `TypeError` when `meta` is not `dict | None`. JSONB-by-accident regressions now fail loudly in tests rather than silently persisting malformed rows.
- [x] [Review][Patch] **`tool_injected` test-only flag persisted into audit `metadata.llm_trace`** [`src/sentinel_prism/graph/nodes/classify.py`] — Removed `tool_injected` from the `llm_meta.web_search` dict written to the audit call. Still present in `out["llm_trace"]["web_search"]` so tests and ephemeral state retain visibility.
- [x] [Review][Patch] **Integration test orphans rows in persistent DB** [`tests/test_audit_events.py`] — Added cleanup block that `DELETE`s the test's `audit_events` rows (by `run_uuid`) and the test `Source` row (by `source_uuid`) before `engine.dispose()`.
- [x] [Review][Patch] **Integration test patch asymmetry is undocumented** [`tests/test_audit_events.py`] — Added a block-comment above the `with patch(...)` stanza explaining that `pipeline_audit.get_session_factory` is intentionally NOT patched so audit rows hit the real DB.
- [x] [Review][Patch] **AC #5 metadata schema test coverage is thin** [`tests/test_audit_events.py`] — Added six new unit tests: per-URL length cap, non-dict metadata TypeError, and per-node metadata-schema assertions for scout, normalize, classify (including the empty-input audit emission for normalize and classify). Tests patch `record_pipeline_audit_event` inside each node module to capture and assert the kwargs.

#### Defer (pre-existing / consistent with project pattern)

- [x] [Review][Defer] **Structured-log message collapsing** [`src/sentinel_prism/graph/nodes/classify.py` — `logger.warning("graph_classify", …)` for multiple distinct events] — Real events are keyed in `extra.event`; the top-level message string is the node name. Consistent with pre-existing Epic 3 pattern across scout/normalize/classify. Not introduced by 3-8. Deferred for possible Epic 8 observability pass.
- [x] [Review][Defer] **`run_id` serialization inconsistency between structured logs and audit calls** [`src/sentinel_prism/graph/nodes/classify.py` et al.] — Logs emit `"run_id": run_id` (raw, may be UUID or str); audit calls use `str(run_id)`. Pre-existing across Epic 3 logs; forcing normalization here would touch many call sites outside 3-8 scope. Deferred.

## Dev Notes

### Epic 3 context

- Epic goal: StateGraph with checkpointer, conditional edges, retry, Tavily (Story 3.7 **done**), **then audit events (this story)** before Epic 4 review UX. [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 3]
- Epic 8 will add **search** and **replay** UX on top of these rows — design **`metadata`** for forward-compatible filtering (action, run_id, time range) without baking UI assumptions.

### Previous story intelligence (3.7)

- **`classify`** already emits rich structured logs and `llm_trace` including optional `web_search` — reuse **summaries** for audit metadata, not raw prompts or Tavily snippets.  
- **Full-node retry + web search memoization** (3.7 review): audit rows may repeat per retry; document ordering via `created_at` / optional attempt counter.  
- **DB session pattern**: nodes already use `get_session_factory()` + `async with factory() as session` — prefer **short sessions** per append to avoid holding transactions across LLM calls; if combining with an existing session in a node, do not widen transaction scope around slow I/O.  
- **Error handling style**: `extra={"event": "...", "ctx": {...}}` with `run_id` — keep consistent for `audit_write` failures.

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Architecture** | §3.5: persist domain events in **`audit_events`** **in addition** to checkpoints for queryable audit UX. [Source: `_bmad-output/planning-artifacts/architecture.md` — §3.5] |
| **Boundaries** | **`services/`** must not import **`graph/`**; **`db/repositories`** must not import graph nodes. Nodes call repositories with primitive IDs + dict metadata. |
| **FR33** | Significant actions include ingest/classify — this story covers **automated pipeline** steps only; analyst-attributed actions come later. |
| **NFR8** | Every structured log touching audit should include **`run_id`** where available. |
| **Secrets / NFR12** | **`metadata` JSONB is not a dump of `AgentState`** — whitelist keys; never store API keys, cookies, or full `raw_items` / `normalized_updates` blobs. |
| **Immutability** | No ORM `update()`/`delete()` helpers for `AuditEvent` in app code; migrations may alter schema, not historical rows. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR33 | Audit trail entries for significant actions — **partial** fulfillment via pipeline events. |
| FR38 | Retries must not break correlation — **`run_id`** stable; duplicate rows acceptable if documented. |
| NFR8 | Correlation id in logs — align audit append logs with `run_id`. |

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **§3.5 checkpointer vs domain events** | Checkpoints = replay mechanics; **`audit_events`** = operator-queryable narrative. Do not replace one with the other. |
| **§5 FR33–FR35 mapping** | `db/audit` + API — API portion deferred; **persistence** is in scope here. |
| **§5 error handling** | Nodes catch failures, append `errors[]` — extend that pattern for audit write failures. |

### Library / framework requirements

| Library | Version | Notes |
| --- | --- | --- |
| **SQLAlchemy** | (project pin) | Async session patterns already in `db/session.py`. |
| **Alembic** | (project pin) | Follow existing revision chain; no destructive downgrades that drop audit data without ops note. |
| **LangGraph** | 1.1.6 (pinned) | Retry semantics from Story 3.6 — audit must tolerate duplicate completions. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/db/models.py` | Add `AuditEvent` model. |
| `alembic/versions/` | New migration. |
| `src/sentinel_prism/db/repositories/audit_events.py` | New — append-only API. |
| `src/sentinel_prism/graph/nodes/scout.py` | Call repository on success path. |
| `src/sentinel_prism/graph/nodes/normalize.py` | Call repository on success path. |
| `src/sentinel_prism/graph/nodes/classify.py` | Call repository on success path. |
| `tests/` | New or extended tests. |

### Testing requirements

- Prefer **deterministic** metadata assertions (action enum, keys present, counts).  
- If full graph integration is heavy, test repository + one node in isolation with mocked session **only if** the project already uses that pattern elsewhere; otherwise use real async session against test DB.  
- Do not require live LLM or Tavily for audit tests.

### Project structure notes

- Package root: `src/sentinel_prism/`.  
- No `project-context.md` in repo; Architecture + epics + Story 3.7 artifact are authoritative for graph/DB patterns.

### References

- `_bmad-output/planning-artifacts/epics.md` — Story 3.8, Epic 3 goal, Epic 8 pointer  
- `_bmad-output/planning-artifacts/architecture.md` — §3.5, §5 mapping FR33–FR35, §5 error handling  
- `_bmad-output/planning-artifacts/prd.md` — FR33, FR38, NFR8  
- `_bmad-output/implementation-artifacts/3-7-pluggable-web-search-tool-tavily-default.md` — `llm_trace` / web_search summarization hints  
- `src/sentinel_prism/graph/state.py` — `run_id` typing  
- `src/sentinel_prism/db/models.py` — `RawCapture.run_id` nullable UUID precedent  

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented `audit_events` table (Alembic `e9f0a2b4c6d8`), ORM `AuditEvent` + `PipelineAuditAction`, and append-only `append_audit_event` repository with invalid-`run_id` logging (no pipeline crash) and bounded `item_url_samples` in metadata.
- Added `graph/pipeline_audit.record_pipeline_audit_event` for short DB sessions; scout, normalize, and classify append on successful node completion with `completed_at` in metadata for ordering; audit DB failures log `audit_write_failed` and append `errors[]` with `step: audit_write`.
- Tests: `tests/test_audit_events.py` (repository unit tests + optional Postgres integration graph test); `tests/conftest.py` mocks audit DB in non-integration graph tests; `tests/test_alembic_cli.py` head revision updated; `verify_imports.py` extended.

### File List

- `alembic/versions/e9f0a2b4c6d8_add_audit_events_table.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/audit_events.py`
- `src/sentinel_prism/graph/pipeline_audit.py`
- `src/sentinel_prism/graph/nodes/scout.py`
- `src/sentinel_prism/graph/nodes/normalize.py`
- `src/sentinel_prism/graph/nodes/classify.py`
- `tests/conftest.py`
- `tests/test_audit_events.py`
- `tests/test_alembic_cli.py`
- `verify_imports.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Change Log

- 2026-04-18 — Ultimate context engine analysis completed — comprehensive developer guide created (`ready-for-dev`).
- 2026-04-18 — Story 3.8 implemented: `audit_events` persistence, graph instrumentation, tests; status → `review`.
- 2026-04-18 — Code review completed: 5 decision-needed resolved (2 → patch, 1 → defer, 2 → dismiss), 11 patches applied, 3 deferred, 12 dismissed. All 153 unit tests pass. Status → `done`.

## Git intelligence summary

Recent commits center Epic 3 graph work: **`feat(graph): Epic 3 classify, review routing, and transient retry policy`** and prior **`feat: persist raw captures, normalize pipeline, scout/normalize graph nodes`**. Expect new work to extend **`graph/nodes/*.py`** and **`db/`** in the same style (async repos, structured logging, `errors[]` accumulation).

## Latest technical information

- **Postgres JSONB** for `metadata` is consistent with existing `RawCapture.payload` / `Source.extra_metadata` patterns; GIN indexes on JSONB are **not** required for MVP — Epic 8 search can add partial indexes if query patterns demand them.

## Project context reference

No `project-context.md` found; use Architecture + epic + prior implementation artifacts.

## Story completion status

**review** — Implementation complete; sprint status updated to `review`.

### Saved questions / clarifications (non-blocking)

- Should **`human_review_gate` entry** emit a **`pipeline_human_review_requested`** event in a thin follow-up, or wait for Epic 4? (Currently **out of scope** per AC #6.)  
- Exact **`action`** string vocabulary — keep aligned with future Epic 8 filters; prefer `pipeline_*_completed` over generic `ingest` to avoid collisions with **poll** / **connector** logs.
