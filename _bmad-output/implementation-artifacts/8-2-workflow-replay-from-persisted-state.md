# Story 8.2: Workflow replay from persisted state

Status: done

<!-- Ultimate context engine analysis completed — comprehensive developer guide created. -->

## Story

As an **operator**,
I want **non-destructive replay of a run segment from persisted graph state**,
so that **I can debug classification and routing issues with continuous run correlation** (**FR35**, **NFR8**, Epic 8).

## Acceptance Criteria

1. **Replay API exists (operator tool):** **Given** an authenticated user with permission to operate runs **When** they request a replay for an existing `run_id` **Then** the API performs a replay using the persisted LangGraph checkpoint state and returns a structured replay result.

2. **Replay is explicitly non-destructive:** **Given** a replay request **When** the replay executes **Then** it must not:
   - mutate existing production domain rows (normalized updates, classifications, briefings, routing rules, notifications, review-queue items),
   - emit new external notifications,
   - overwrite the original run’s checkpoint state.

3. **Replay supports segment selection:** **Given** a stored checkpoint for `run_id` **When** the operator specifies a replay segment **Then** the system re-executes only the requested portion of the pipeline (minimum support: **`classify → (human_review_gate?) → brief → route`**; optional support: include `normalize`).
   - Segment is identified using the existing graph node ids from `src/sentinel_prism/graph/graph.py` (`scout`, `normalize`, `classify`, `human_review_gate`, `brief`, `route`).
   - The API contract must reject unknown node ids with a 422/400 (no silent fallback).

4. **Correlation is continuous and explicit:** **Given** a replay request **When** results are returned **Then** the response includes:
   - `original_run_id`
   - `replay_run_id` (new UUID, distinct from the original)
   - `replayed_nodes` (ordered list)
   - `started_at` / `finished_at` timestamps
   - `status` (`completed`, `failed`, `partial`, or `unsupported`)
   - allowlisted `errors[]` (same trust-boundary approach as `GET /runs/{run_id}` in `src/sentinel_prism/api/routes/runs.py`)

5. **Checkpoint-backed replays require Postgres saver:** **Given** the system is configured with `PIPELINE_CHECKPOINTER=memory` (or no `DATABASE_URL`) **When** an operator requests replay for a run that depends on persisted state **Then** the API returns a clear 409/503 describing that replay requires a persistent checkpointer (Postgres) and cannot be served from MemorySaver after restart.

6. **Replay state uses the original checkpoint as input:** **Given** an existing run with persisted state **When** replay begins **Then** the replay uses the retrieved checkpoint snapshot values as the initial `AgentState` input for the replay (not recomputed from current DB).

7. **Safety rails are enforced in code, not only docs:** **Given** replay mode is enabled **When** pipeline nodes attempt side effects (DB writes, audit append, notification send, review-queue projection) **Then** those side effects are suppressed deterministically by a “replay guard” that is difficult to bypass accidentally.

8. **UI affordance (minimal) exists for operators:** **Given** the existing console shell **When** an operator views a run’s detail (or a new “Replay” page) **Then** they can initiate a replay and see the replay summary + result payload (no need for a full ops dashboard; Story 8.3 will own observability dashboards).

## Tasks / Subtasks

- [x] **Define replay contract & RBAC** (AC: 1, 4, 5)
  - [x] Choose endpoint shape consistent with existing runs API:
    - Recommended: `POST /runs/{run_id}/replay`
    - Request body includes `from_node`, `to_node` (or `segment` enum), and optional `include_inputs` boolean.
  - [x] Decide who can replay:
    - Recommended baseline: `admin` + `analyst` (align with existing `/runs/*` guarded by `require_roles(UserRole.ANALYST, UserRole.ADMIN)` in `runs.py`).
    - If you broaden to `viewer`, explicitly justify (operators may be viewers later).

- [x] **Implement replay engine (core)** (AC: 2, 3, 6, 7)
  - [x] Load persisted state using the compiled graph:
    - Use `graph.aget_state({"configurable": {"thread_id": str(run_id)}})` like `get_run_detail`.
  - [x] Create a new `replay_run_id` and a replay config with a distinct `thread_id` so the replay checkpoint cannot overwrite the original.
  - [x] Seed replay state from the snapshot values:
    - Copy relevant keys from `snap.values` (must be a dict) into a fresh dict.
    - Force `state["run_id"] = str(replay_run_id)` so NFR8 correlation and downstream audit metadata (if any) clearly separate replay from original.
    - Add an explicit replay flag in `flags`, e.g. `flags["replay_mode"]=True` and `flags["replay_original_run_id"]=True` (or a dedicated key/value) so nodes can enforce non-destructive behavior.
  - [x] Segment execution strategy (pick one and document):
    - **Option A (preferred):** add a small graph utility that can start execution “at” a node by using a `Command(goto=...)`/resume pattern if supported by your LangGraph version; otherwise fall back to Option B.
    - **Option B (safe MVP):** replay “tail segments” only by re-invoking the graph from `classify` onward by constructing a specialized replay subgraph that begins at `classify` and reuses the same node callables.
      - Keep graph/node ids identical where possible for operator clarity.
      - Avoid importing services from nodes in a way that violates architecture boundary (nodes call services; services do not import graph).
  - [x] Enforce non-destructive side effects:
    - Implement a single “replay guard” helper that all side-effect emitters check.
    - Minimum guardrails:
      - In `node_route`, suppress external delivery attempts and in-app notification inserts.
      - In audit writers (`append_audit_event` / `record_pipeline_audit_event`), suppress inserts or write to a separate “replay only” channel that never mixes with production audit history.
      - In review-queue projection writers, suppress inserts/deletes.
    - Prefer an explicit “replay mode” boolean passed down (or in `state.flags`) over “detect by thread_id prefix” hacks.

- [x] **API route implementation** (AC: 1, 3, 4, 5)
  - [x] Add route(s) under `src/sentinel_prism/api/routes/runs.py` (or a sibling `replay.py` router included by `main.py`).
  - [x] Reuse existing allowlisting patterns for errors and `llm_trace` (see `_safe_error`, `_safe_llm_trace`).
  - [x] Return a stable, explicit response model using Pydantic v2 with `extra="forbid"` for public schemas.
  - [x] Ensure “no checkpoint state found” and “MemorySaver restart” cases are handled explicitly (match patterns in `get_run_detail`).

- [x] **Web UI (minimal)** (AC: 8)
  - [x] Add a basic replay trigger and results panel:
    - Either a small section under the existing run detail UX (if present), or a new component under `web/src/components/`.
  - [x] Display operator-facing fields: original run id, replay run id, status, replayed nodes, and errors.
  - [x] Keep copy “forensic/professional” per UX spec; expose clear disclaimer: replay is non-destructive and does not send notifications.

- [x] **Tests** (AC: 1–7)
  - [x] Integration tests that:
    - create a run with a persisted checkpoint (requires Postgres saver in test env or a saver stub),
    - request replay and assert:
      - `replay_run_id != original_run_id`
      - original checkpoint unchanged (re-fetch `aget_state` for original thread id)
      - side-effect writers were not invoked (assert DB tables unchanged for notifications/audit/review-queue where applicable).
  - [x] Unit tests for segment validation and request schema.

### Review Findings

- [x] [Review][Patch] Replay must be offline/deterministic: in replay mode, do not call web search/LLM; replay should operate on persisted checkpoint outputs (start at `human_review_gate/brief/route` using checkpointed `classifications`) — Decision: offline/deterministic replay.

- [x] [Review][Patch] Segment selection is validated but ignored (and `normalize` is accepted but unsupported) [`src/sentinel_prism/api/routes/runs.py:302`, `:767`] 
- [x] [Review][Patch] Correlation flag `replay_original_run_id` is a boolean, not the original run id (store `str(run_id)`) [`src/sentinel_prism/api/routes/runs.py:795`]
- [x] [Review][Patch] `replayed_nodes` is hard-coded and can be inaccurate when `human_review_gate` is skipped [`src/sentinel_prism/api/routes/runs.py:815`]
- [x] [Review][Patch] Replay status contract incomplete: no `failed`/`unsupported` responses; `ainvoke` exceptions bubble as 500 instead of a structured replay result [`src/sentinel_prism/api/routes/runs.py:802`]
- [x] [Review][Patch] Add timeout around replay invocation (mirror `resume_run`/other bounded graph calls) to avoid a hung replay tying up the worker [`src/sentinel_prism/api/routes/runs.py:802`]

## Dev Notes

### Existing implementation context (don’t reinvent)

- **Run detail uses checkpoint state today:** `GET /runs/{run_id}` already reads checkpoint state via `graph.aget_state(...)` and applies a strict allowlist for `errors` and `llm_trace` to preserve **NFR12** boundaries. Reuse these patterns for replay result serialization. [Source: `src/sentinel_prism/api/routes/runs.py`]
- **Checkpointer selection is centralized:** Postgres checkpointer is selected via `PIPELINE_CHECKPOINTER` and `DATABASE_URL`. Replay-from-persisted-state should assume Postgres saver for real operator workflows; MemorySaver is dev/CI only. [Source: `src/sentinel_prism/graph/checkpoints.py`]
- **Graph node ids are stable and already defined:** `scout`, `normalize`, `classify`, `human_review_gate`, `brief`, `route`. Use these exact strings for segment selection and validation. [Source: `src/sentinel_prism/graph/graph.py`]

### Replay safety (critical)

- **Most nodes have side effects today:** audit events, projections, notifications, DB upserts. Replay must not silently re-run those writes.
- **Do not reuse the original `thread_id`:** any replay should use a new `thread_id` to prevent checkpoint overwrite.
- **Avoid “partial fixes” scattered across nodes:** implement one clear “replay guard” function and route all side-effect checks through it.

### Scope guardrails

- **Story 8.1 already delivered audit search**; do not expand it.
- **Story 8.3 owns observability dashboards**; 8.2 should ship a minimal operator replay action + payload.
- **No need to perfect transactional outbox/idempotency here:** keep replay strictly read-only / no-write so it can be correct without a full durability refactor.

### References

- [Source: `_bmad-output/planning-artifacts/prd.md` — FR35, NFR8]
- [Source: `_bmad-output/planning-artifacts/architecture.md` §3.5–3.6 (checkpointers, runs APIs)]
- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 8.2 AC]
- [Source: `src/sentinel_prism/api/routes/runs.py` — checkpoint read patterns and trust-boundary allowlists]
- [Source: `src/sentinel_prism/graph/checkpoints.py` — Postgres saver selection]

## Dev Agent Record

### Agent Model Used

GPT-5.2 (bmad-dev-story)

### Debug Log References

### Completion Notes List

- Implemented replay-mode guardrails via a context flag (`replay_context`) so replay runs suppress DB/audit/projection/notification side effects.
- Added `POST /runs/{run_id}/replay` (tail replay: `classify → human_review_gate → brief → route`) with strict request validation and allowlisted error projection.
- Added minimal web UI `RunReplay` section to trigger replay and render results.
- Tests: added `tests/test_run_replay_api.py`; full suite passes (`pytest`).

### File List

- `src/sentinel_prism/graph/replay_context.py`
- `src/sentinel_prism/graph/replay.py`
- `src/sentinel_prism/graph/pipeline_audit.py`
- `src/sentinel_prism/graph/pipeline_review.py`
- `src/sentinel_prism/graph/nodes/human_review_gate.py`
- `src/sentinel_prism/graph/nodes/route.py`
- `src/sentinel_prism/graph/nodes/brief.py`
- `src/sentinel_prism/api/routes/runs.py`
- `tests/test_run_replay_api.py`
- `web/src/components/RunReplay.tsx`
- `web/src/App.tsx`

## Change Log

- **2026-04-27:** Implemented Story 8.2 — non-destructive workflow replay API + replay guardrails + minimal UI + tests.
