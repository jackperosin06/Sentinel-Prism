# Story 4.1: Review queue API and workflow state integration

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **analyst**,
I want **items flagged for human review discoverable via an authenticated API with enough classification and provenance context to triage**,
so that **I can resolve low-confidence and high-risk cases** (**FR16**, **NFR8**, **NFR12** partial — API must not leak secrets or raw LLM prompts).

## Acceptance Criteria

1. **Review queue list (FR16)**  
   **Given** one or more pipeline runs have reached **`human_review_gate`** with **`flags.needs_human_review`** true (LangGraph **`interrupt`** path — Story 3.5)  
   **When** an authenticated client calls the review-queue list endpoint  
   **Then** the response includes **only** runs that are **currently awaiting human review** (interrupted at the gate), not completed runs  
   **And** each list item exposes at minimum: **`run_id`**, **`source_id`** (if known), **queued / interrupted timestamp** (from durable workflow metadata — see Dev Notes), and **summary classification fields** per normalized item (e.g. **severity**, **confidence**, **needs_human_review**, **rationale** excerpt, **item_url**, **in_scope**) sufficient for triage without opening raw checkpoint blobs in the client  

2. **Run detail = workflow state + domain context (Architecture §3.6 read path)**  
   **Given** a **`run_id`** that exists in the **durable** checkpointer for the regulatory pipeline  
   **When** an authenticated client requests run detail for that id  
   **Then** the response returns a **structured projection** of **`AgentState`** at the interrupt boundary: at minimum **`classifications`**, **`normalized_updates`** (or stable references to **`normalized_updates` DB rows** joined by `run_id` / `item_url`), **`flags`**, **`errors`** (non-secret summaries), **`llm_trace`** safe subset (model/prompt version ids — **no** full prompts, **no** Tavily raw payloads per **NFR12**)  
   **And** optionally includes a **short tail** of **`audit_events`** for that **`run_id`** (reuse `append_audit_event` / repository read — Story 3.8); do **not** duplicate raw capture payloads from **`audit_events.metadata`**  

3. **Durable workflow state (Architecture §3.5)**  
   **Given** the API server and background workers may be **separate processes**  
   **When** the application runs against PostgreSQL  
   **Then** the compiled regulatory pipeline uses a **PostgreSQL-backed LangGraph checkpointer** (not a fresh **`MemorySaver` per request**) so **`thread_id = str(run_id)`** checkpoints **survive process restarts**  
   **And** **`MemorySaver`** remains available for **unit tests** / CI that do not need cross-process durability (same pattern as today, but centralize factory selection — see Tasks)  

4. **RBAC**  
   **Given** JWT authentication (Story 1.3)  
   **When** a **viewer** or unauthenticated caller hits review endpoints  
   **Then** list + detail are **denied** (401/403) unless you explicitly document a **read-only viewer** exception aligned with UX (**prefer** `ANALYST` + `ADMIN` only for Epic 4 triage APIs until product confirms viewer access)  

5. **API surface & wiring**  
   **Given** `src/sentinel_prism/api/routes/runs.py` is currently a stub  
   **When** this story is complete  
   **Then** review-queue + run-detail routes are **registered** from `create_app()` (alongside existing routers)  
   **And** OpenAPI tags and Pydantic response models follow **`sources.py`** style (explicit schemas, no raw `dict` responses)  

6. **Out of scope (explicit)**  
   **Given** this story  
   **Then** **do not** implement **`POST /runs/{id}/resume`**, approve/reject/override, or analyst-attributed **`audit_events`** — those belong to **Story 4.2** (**FR17**)  
   **And** **do not** build the React review UI (Epic 6) — API only  

7. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** there is at least one **async API test** (httpx + `asgi-lifespan` pattern used elsewhere) proving **403/401** without auth and **200** with an **`ANALYST`** (or chosen role) token  
   **And** there is a **graph + checkpointer** test that creates an **interrupted** run (reuse patterns from `tests/test_graph_conditional_edges.py`) and asserts the **listing logic** would include that **`run_id`** when backed by a **shared** checkpointer instance  

## Tasks / Subtasks

- [x] **Dependency + checkpointer factory (AC: #3, #7)**  
  - [x] Add **`langgraph-checkpoint-postgres`** (pin a version **compatible** with **`langgraph==1.1.6`** and **`langgraph-checkpoint==4.0.2`** — verify during implementation).  
  - [x] Extend `src/sentinel_prism/graph/checkpoints.py` with a selector: e.g. `get_pipeline_checkpointer()` → Postgres async saver when `DATABASE_URL` (or dedicated env) is set for runtime, else `MemorySaver` for tests.  
  - [x] Document one-time **`setup()`** / table creation for Postgres saver (in module docstring or `deferred-work.md` if ops-heavy).  
  - [x] Ensure **one shared checkpointer** (and compiled graph if required by LangGraph usage) for the API process — see `_bmad-output/implementation-artifacts/deferred-work.md` on “fresh MemorySaver per compile”.  

- [x] **Discover interrupted runs (AC: #1)**  
  - [x] Prefer **LangGraph / checkpointer APIs** to enumerate threads with a **pending interrupt** for the regulatory graph (verify against installed **langgraph 1.1.6** — `get_state`, task payloads, checkpoint metadata).  
  - [x] If enumeration is not first-class, add a **minimal append-only projection** (new table **or** reuse `audit_events` with a **new stable action** e.g. `human_review_queued`) written from `node_human_review_gate` **immediately before** `interrupt()`, keyed by **`run_id`**, **without** storing secrets — justify in Dev Notes.  

- [x] **HTTP routes (AC: #2, #5)**  
  - [x] Implement Pydantic models for list + detail responses; keep **NFR12** boundaries (truncate long rationale in list view if needed).  
  - [x] Use `Depends(require_roles(...))` from `src/sentinel_prism/api/deps.py`.  
  - [x] Wire router in `src/sentinel_prism/main.py`.  
  - [x] Path shape: align with Architecture §3.6 — e.g. **`GET /runs`** (filter `awaiting_review=true`) **and** **`GET /runs/{run_id}`**, **or** **`GET /review-queue`** + **`GET /runs/{run_id}`** — pick one consistent scheme and document it in Dev Notes.  

- [x] **Audit read path (AC: #2)**  
  - [x] Add a small read helper in `src/sentinel_prism/db/repositories/audit_events.py` (or adjacent) — **SELECT** by **`run_id`** ordered by **`created_at` DESC** with a **limit**; no write paths beyond existing `append_audit_event`.  

- [x] **Tests (AC: #7)**  
  - [x] New test module e.g. `tests/test_review_queue_api.py` (name to taste).  
  - [x] Reuse auth fixtures from existing API tests (`tests/conftest.py`).  

### Review Findings

- [x] [Review][Patch] Projection-failure audit-fallback — on `record_review_queue_projection` failure we append an `audit_events` row with action `human_review_queue_projection_failed` (run_id + source_id + error_class + trimmed error_message) before `interrupt()`; logged-warning + continue posture preserved. [`src/sentinel_prism/graph/pipeline_review.py`, `src/sentinel_prism/db/models.py`, `tests/test_audit_events.py`]
- [x] [Review][Patch] Allowlist `errors[]` in run detail — introduced `ErrorDetailRow` (`step`, `message`, `error_class`, truncated `detail`) and filter `errors` through it before serializing; `classifications` / `normalized_updates` remain under their graph-contract schema. NFR12 trust boundary documented in the module docstring and Dev Notes. [`src/sentinel_prism/api/routes/runs.py`]
- [x] [Review][Patch] Document Postgres-checkpointer startup DDL — README, `.env.example`, and `checkpoints.py` docstring now spell out that `AsyncPostgresSaver.setup()` runs on startup and creates LangGraph tables in `DATABASE_URL` (not Alembic-managed); `PIPELINE_CHECKPOINTER=memory` remains the escape hatch. [`README.md`, `.env.example`, `src/sentinel_prism/graph/checkpoints.py`]
- [x] [Review][Patch] Rename endpoint to `GET /review-queue` — dropped the `awaiting_review=true` flag and the 400 branch; introduced `review_queue_router` with pagination; `GET /runs/{run_id}` unchanged. Completion Notes + Dev Notes updated. [`src/sentinel_prism/api/routes/runs.py`, `src/sentinel_prism/main.py`, `tests/test_review_queue_api.py`]
- [x] [Review][Patch] `queued_at` = workflow-interrupted timestamp — `human_review_gate` captures `interrupted_at = datetime.now(tz=UTC)` immediately before `record_review_queue_projection(...)` and threads it through `upsert_pending(..., queued_at=interrupted_at)`. [`src/sentinel_prism/graph/nodes/human_review_gate.py`, `src/sentinel_prism/graph/pipeline_review.py`, `src/sentinel_prism/db/repositories/review_queue.py`]
- [x] [Review][Defer] `ReviewQueueItem.source_id` ondelete=RESTRICT policy [`alembic/versions/f1e2d3c4b5a6_add_review_queue_items.py`, `src/sentinel_prism/db/models.py`] — deferred: no source-delete endpoint exists yet — revisit when a deletion story lands and the retention policy for queued review rows is decided.
- [x] [Review][Patch] List endpoint has pagination — `GET /review-queue` now accepts `limit` (1–200, default 50) and `offset` (≥0); the repo enforces the same cap so other callers can't bypass it. [`src/sentinel_prism/api/routes/runs.py`, `src/sentinel_prism/db/repositories/review_queue.py`]
- [x] [Review][Patch] Per-row `ValidationError` no longer poisons listing — `ClassificationListRow.model_validate(entry)` is wrapped in `try/except ValidationError`; bad rows are skipped with a structured warning. Covered by `test_list_review_queue_skips_corrupt_summary_entries`. [`src/sentinel_prism/api/routes/runs.py`, `tests/test_review_queue_api.py`]
- [x] [Review][Patch] `items_summary` entry cap — `classification_summaries_for_queue` now enforces `_MAX_SUMMARY_ENTRIES = 50` (truncation behaviour documented in the helper docstring). [`src/sentinel_prism/graph/pipeline_review.py`]
- [x] [Review][Patch] `impact_categories` non-list case — projection builder now only propagates `impact_categories` when `isinstance(raw, list)`; any other shape becomes `[]`. [`src/sentinel_prism/graph/pipeline_review.py`]
- [x] [Review][Patch] Non-iterable `state["classifications"]` in gate — human_review_gate now applies `isinstance(raw_cls, list)` before iterating. [`src/sentinel_prism/graph/nodes/human_review_gate.py`]
- [x] [Review][Patch] UUID parse catches exotic inputs — broadened to `(ValueError, TypeError, AttributeError)` in `human_review_gate`, `audit_events._parse_run_id`, and `review_queue.get_pending_by_run_id`. [`src/sentinel_prism/graph/nodes/human_review_gate.py`, `src/sentinel_prism/db/repositories/audit_events.py`, `src/sentinel_prism/db/repositories/review_queue.py`]
- [x] [Review][Patch] `ClassificationListRow.source_id` type unified — now `UUID | None` with default `None` (was `str = ""`). [`src/sentinel_prism/api/routes/runs.py`]
- [x] [Review][Patch] Lifespan is exception-safe — FastAPI lifespan uses `AsyncExitStack`; `push_async_callback` wires scheduler shutdown so checkpointer `__aexit__` always runs even when earlier startup steps (graph compile, scheduler start) raise. [`src/sentinel_prism/main.py`]
- [x] [Review][Patch] `postgres_uri_for_langgraph` generalized — regex-based rewrite now handles `postgresql+<driver>://` and `postgres://` shorthand uniformly; unknown schemes pass through unchanged so the caller surfaces a clear error. [`src/sentinel_prism/graph/checkpoints.py`]
- [x] [Review][Patch] `RunDetailOut.errors` typed — replaced raw `list[dict[str, Any]]` with `list[ErrorDetailRow]` (allowlisted + bounded). `classifications` / `normalized_updates` remain graph-contract dicts per the documented NFR12 trust boundary; `llm_trace` already goes through `_safe_llm_trace`. [`src/sentinel_prism/api/routes/runs.py`]
- [x] [Review][Patch] NFR8 — list endpoint emits structured log — `list_review_queue` logs count / limit / offset / sample run_ids on success and `bad_summary_entry` on skips. [`src/sentinel_prism/api/routes/runs.py`]
- [x] [Review][Patch] Autouse `_mock_pipeline_audit_session_factory` scoped — fixture is now a no-op unless the test module is in `_GRAPH_TEST_MODULE_BASENAMES`; docstring spells out the scope + integration-marker escape hatch. The `integration` marker is already registered in `pyproject.toml`. [`tests/conftest.py`]
- [x] [Review][Patch] `graph.aget_state` snapshot guard — detail endpoint now uses `getattr(snap, "values", None) or {}` so a snapshot without values produces a 404 instead of HTTP 500. [`src/sentinel_prism/api/routes/runs.py`]
- [x] [Review][Patch] API tests for `GET /runs/{run_id}` — added 401/403, 404 (not in queue), 200 with projection + NFR12 filtering, and 404 (no checkpoint state) cases. [`tests/test_review_queue_api.py`]
- [x] [Review][Patch] AC #7 list-surface test — new `test_interrupt_projection_ends_up_listable_via_repo` runs the real `record_review_queue_projection` through an in-memory fake repo and asserts `list_pending_review_items` surfaces the interrupted run. [`tests/test_review_queue_api.py`]
- [x] [Review][Patch] `list_recent_for_run` now unit-tested — `test_list_recent_for_run_filters_and_limits` exercises the helper (including the invalid-UUID short-circuit). [`tests/test_audit_events.py`]
- [x] [Review][Patch] End-to-end projection serialization exercised — `test_interrupt_projection_ends_up_listable_via_repo` drives the real `record_review_queue_projection` → repo → list path, proving the `items_summary` round-trip without Postgres. [`tests/test_review_queue_api.py`]
- [x] [Review][Patch] Dev Notes justify projection vs LangGraph enumeration — new "Projection vs checkpointer enumeration" subsection in Dev Notes documents why a small `review_queue_items` projection is used instead of enumerating `aget_state` across threads. [`_bmad-output/implementation-artifacts/4-1-review-queue-api-and-workflow-state-integration.md`]
- [x] [Review][Patch] Structured log split for detail endpoint — distinct `event` keys for `get_run_detail_ok`, `get_run_detail_not_in_queue`, and `get_run_detail_no_checkpoint` (collapsed-event log replaced). [`src/sentinel_prism/api/routes/runs.py`]
- [x] [Review][Defer] `append_audit_event` silently drops rows on UUID parse failure [`src/sentinel_prism/db/repositories/audit_events.py`] — deferred, belongs to Story 3.8 (audit write contract)
- [x] [Review][Defer] `append_audit_event` commit discipline undocumented [`src/sentinel_prism/db/repositories/audit_events.py`] — deferred, belongs to Story 3.8 (audit write contract)
- [x] [Review][Defer] `_trim_metadata` size-check uses `json.dumps(default=str)` but asyncpg JSONB codec may reject the same payload [`src/sentinel_prism/db/repositories/audit_events.py`] — deferred, Story 3.8 internals
- [x] [Review][Defer] `_trim_metadata` `sorted(keys)` raises `TypeError` when metadata has mixed-type keys [`src/sentinel_prism/db/repositories/audit_events.py`] — deferred, Story 3.8 internals
- [x] [Review][Defer] `test_alembic_cli.py` hardcodes expected head migration id — forces an edit on every new migration [`tests/test_alembic_cli.py`] — deferred, pre-existing test pattern
- [x] [Review][Defer] Projection commit is in a separate transaction from the LangGraph checkpoint write; a crash window between the two can leave phantom queue rows [`src/sentinel_prism/graph/pipeline_review.py`, `src/sentinel_prism/graph/nodes/human_review_gate.py`] — deferred, requires outbox / transactional-design work beyond 4.1
- [x] [Review][Defer] MemorySaver + restart split-brain — detail endpoint returns 404 "No checkpoint state found" while the projection still lists the run [`src/sentinel_prism/graph/checkpoints.py`, `src/sentinel_prism/api/routes/runs.py`] — deferred, acceptable MemorySaver limitation (documented trade-off; only matters in tests/CI)

## Dev Notes

### Epic 4 context

- Epic goal: analysts work **review queue** and read **briefings**; this story is the **read-only API + durable checkpoint** foundation. **Story 4.2** adds **mutations + audit** (**FR17**). [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4, Stories 4.1–4.2]  

### Previous story intelligence (Epic 3.8 — `3-8-pipeline-generated-audit-events.md`)

- **`audit_events`** is **append-only**; **`run_id`** is the correlation key; **`metadata`** must stay non-secret.  
- **Do not** stuff full **`AgentState`** into **`audit_events`** — use **`GET /runs/{id}`** checkpoint projection instead.  
- Graph nodes already use **`record_pipeline_audit_event`** — any new pre-interrupt audit row must follow the same **trim / whitelist** discipline.  

### UX alignment (read path only)

- Review queue is **triage-first**: show **severity**, **confidence**, **rationale**, **provenance** (URL / source), consistent with **master–detail** and **sticky actions** patterns later. [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — Review queue, `ReviewActionBar`]  

### Projection vs checkpointer enumeration (AC #1)

The list endpoint is backed by a small **`review_queue_items`** projection upserted from `node_human_review_gate` immediately before `interrupt()` fires (`classification_summaries_for_queue` → `record_review_queue_projection`). LangGraph 1.1.6 does expose per-thread `aget_state` and pending-interrupt metadata on a checkpointer, but there is **no first-class API to enumerate all threads with a pending interrupt** — `AsyncPostgresSaver.alist` is paginated over (`thread_id`, `checkpoint_ns`) tuples and would force the API to scan every thread on every `GET /review-queue`, plus re-derive triage fields by re-deserializing each checkpoint blob. A fan-out like that does not fit NFR8 or the response-shape demanded by AC #1.

The projection:

- Keeps `GET /review-queue` a single indexed SELECT on `ix_review_queue_items_queued_at` with `limit`/`offset` pagination, bounded by the `_MAX_SUMMARY_ENTRIES` cap on JSONB size.
- Stores only triage-safe fields (no rationale in full, no raw prompts, no provenance beyond `item_url` + `source_id`) — checkpoint blobs remain the source of truth and stay behind `GET /runs/{run_id}`.
- Has an audit-failure fallback (`HUMAN_REVIEW_QUEUE_PROJECTION_FAILED`) so a projection-write failure still leaves a discoverable breadcrumb; the checkpoint is unaffected because it is written by LangGraph after the node returns.

Story 4.2 will own cleanup (row removal on resume/approve) so the projection stays consistent with checkpoint state.

### NFR12 trust boundary (run detail response)

`GET /runs/{run_id}` routes the checkpoint projection through two different trust zones:

- **Graph-contract zone** — `classifications`, `normalized_updates`, and `flags` are produced by graph nodes whose schemas are owned by `services/llm/classification.py` + `services/ingestion/normalize.py`. Those nodes are responsible for not writing raw LLM prompts or non-public web-search payloads into state, so the API returns them under the graph contract without re-filtering.
- **Allowlist zone** — `errors[]` and `llm_trace` sit closer to provider/driver boundaries where `str(exc)` could embed SQL parameter values or API keys. They go through `ErrorDetailRow` / `_safe_llm_trace` before serializing; unknown keys (including any hypothetical future `raw_prompt`) are dropped and `detail` is truncated to `_ERROR_DETAIL_MAX` chars.

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **§3.4–3.5** | **`human_review_gate`** uses **`interrupt`**; durable **checkpointer** + **`audit_events`** coexist — checkpoints for **resume/replay**; audit for **queryable** narrative. |
| **§3.6** | **`POST /runs`**, **`GET /runs/{id}`**, **`POST /runs/{id}/resume`** — implement **GET** portion + list; **POST** deferred to **4.2** unless you need an internal dev-only stub (avoid scope creep). |
| **§3.2** | **`AgentState`** fields: `classifications`, `normalized_updates`, `flags`, `errors`, `llm_trace`, `run_id`, `source_id`. |
| **Boundaries** | **`services/`** must not import **`graph/`**; route handlers may call **`graph`** factories and **`db/repositories`** — mirror **`sources.py`** layering. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| **FR16** | Expose **human review queue** entries derived from **policy-flagged** runs (`needs_human_review`). |
| **FR35** | **Replayable** / resumable state — **Postgres checkpointer** in real environments. |
| **FR36** | **`AgentState`** is the **shared** workflow state — API projections must match checkpoint contents. |
| **NFR8** | Structured logs for new routes include **`run_id`** where applicable. |
| **NFR12** | No raw prompts, no non-public web-search payloads in API responses. |

### Library / framework requirements

| Library | Version | Notes |
| --- | --- | --- |
| **FastAPI** | 0.115.8 (pinned) | Match existing route / dependency style. |
| **LangGraph** | 1.1.6 (pinned) | **`interrupt`** semantics — see `human_review_gate.py` warning on idempotent resume. |
| **langgraph-checkpoint-postgres** | TBD (pin) | Must match **`langgraph-checkpoint`** 4.x ecosystem — confirm at implementation time. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/checkpoints.py` | Add Postgres checkpointer path + factory selector. |
| `src/sentinel_prism/graph/nodes/human_review_gate.py` | Optional: emit **projection** row or **audit action** before `interrupt()`. |
| `src/sentinel_prism/api/routes/runs.py` | Replace stub with real routes. |
| `src/sentinel_prism/main.py` | `include_router` for runs/review. |
| `src/sentinel_prism/db/repositories/audit_events.py` | Add **read** helper for run detail tail. |
| `alembic/versions/` | New revision **if** you add a projection table; **or** document that Postgres saver owns its tables via **`setup()`**. |
| `requirements.txt` | Add **`langgraph-checkpoint-postgres`** (pinned). |

### Testing requirements

- Use **async** tests consistent with `tests/test_graph_*.py` and API tests.  
- When Postgres is required for an integration test, follow the **same skip/fixture pattern** as `tests/test_audit_events.py` (do not require Docker for pure unit tests).  

### Project structure notes

- No **`project-context.md`** in repo — Architecture + epics + prior story artifacts are authoritative.  
- **`route_after_classify`** must remain the **only** router of review vs continue — do not re-derive from `classifications` in API code for **routing** decisions (listing is OK). [Source: `src/sentinel_prism/graph/routing.py`]  

### References

- `_bmad-output/planning-artifacts/epics.md` — Story 4.1, Epic 4  
- `_bmad-output/planning-artifacts/architecture.md` — §3.2, §3.4–3.6, §5 graph layout  
- `_bmad-output/planning-artifacts/prd.md` — **FR16**, **FR35**, **FR36**  
- `_bmad-output/planning-artifacts/ux-design-specification.md` — Review queue patterns  
- `_bmad-output/implementation-artifacts/3-8-pipeline-generated-audit-events.md` — Audit boundaries  
- `_bmad-output/implementation-artifacts/deferred-work.md` — Checkpointer / compile lifecycle notes  
- `src/sentinel_prism/graph/nodes/human_review_gate.py` — Interrupt payload shape  
- `src/sentinel_prism/services/llm/classification.py` — `classification_dict_for_state` keys  

### Git intelligence (recent mainline pattern)

- Recent commits emphasize **graph** packaging (`feat(graph): …`), **connectors**, and **Epic 2** APIs — follow **existing FastAPI** + **repository** conventions from **`sources`** and **auth** deps.  

### Latest tech information

- **Postgres checkpointer:** `langgraph-checkpoint-postgres` provides **`AsyncPostgresSaver`**; requires compatible DB connection settings (**`autocommit`**, **`dict_row`** per upstream docs). Call **`setup()`** once for table creation. [Source: [PyPI langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres/), [AsyncPostgresSaver reference](https://reference.langchain.com/python/langgraph.checkpoint.postgres/aio/AsyncPostgresSaver)]  

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented **`GET /review-queue`** (paginated; `limit` 1–200 default 50, `offset`) and **`GET /runs/{run_id}`** with **ANALYST** + **ADMIN** RBAC, Pydantic response models, `ErrorDetailRow` + `_safe_llm_trace` allow-list for **NFR12**, and audit tail via **`list_recent_for_run`**.  
- **`review_queue_items`** Alembic migration + ORM; **`human_review_gate`** upserts projection via **`pipeline_review.record_review_queue_projection`** before **`interrupt()`**, passing the workflow-interrupted timestamp as `queued_at` and capped at 50 summary entries. Projection failures emit a `HUMAN_REVIEW_QUEUE_PROJECTION_FAILED` audit row so the run stays discoverable.
- App lifespan uses an `AsyncExitStack` so the Postgres checkpointer's `__aexit__` always runs; compiles a **single** regulatory graph with **`AsyncPostgresSaver`** + **`setup()`** when **`use_postgres_pipeline_checkpointer()`** is true (default if **`DATABASE_URL`** set; override with **`PIPELINE_CHECKPOINTER=memory`**), else **`MemorySaver`**. The Postgres-checkpointer DDL side effect is now documented in README / `.env.example`.
- Tests: **`tests/test_review_queue_api.py`** covers 401/403/200 for both endpoints, pagination forwarding, corrupt-summary skipping, NFR12 filtering of `errors[]` + `llm_trace`, 404 cases, and an end-to-end list-surface test exercising `record_review_queue_projection` → repo → list. **`tests/test_audit_events.py`** adds `list_recent_for_run` and projection-failure audit-fallback coverage. **`conftest`** mocks the `pipeline_review` / `pipeline_audit` session factory for graph tests only (module-scoped).
- Alembic CLI smoke head updated to **`f1e2d3c4b5a6`**.  

### File List

- `requirements.txt`  
- `alembic/versions/f1e2d3c4b5a6_add_review_queue_items.py`  
- `src/sentinel_prism/db/models.py`  
- `src/sentinel_prism/db/repositories/review_queue.py`  
- `src/sentinel_prism/db/repositories/audit_events.py`  
- `src/sentinel_prism/graph/checkpoints.py`  
- `src/sentinel_prism/graph/pipeline_review.py`  
- `src/sentinel_prism/graph/nodes/human_review_gate.py`  
- `src/sentinel_prism/api/routes/runs.py`  
- `src/sentinel_prism/main.py`  
- `tests/conftest.py`  
- `tests/test_review_queue_api.py`  
- `tests/test_alembic_cli.py`  
- `verify_imports.py`  

## Change Log

- 2026-04-19 — Story 4.1 implemented: review queue API, Postgres LangGraph checkpointer wiring, **`review_queue_items`**, audit read helper, tests (status → **review**).  
- 2026-04-18 — Story 4.1 marked **done** in sprint tracking (implementation + prior code review complete; Epic 4 triage APIs aligned with AC).

## Story completion status

- **Status:** done  
- **Note:** Implementation complete; code review deferred items tracked in `deferred-work.md`; full pytest suite green at time of closure.  

### Saved questions / clarifications (non-blocking)

- Confirm whether **VIEWER** may **read** the review queue list/detail without triage actions (UX suggests read-only console access; default story assumes **ANALYST + ADMIN** only until product confirms).  
- Resolved via code review (2026-04-18): URL is **`GET /review-queue`** + **`GET /runs/{run_id}`**; the `/runs?awaiting_review=true` flag shape is dropped.  
