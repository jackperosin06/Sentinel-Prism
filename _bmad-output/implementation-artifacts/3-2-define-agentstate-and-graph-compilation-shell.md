# Story 3.2: Define AgentState and graph compilation shell

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **`AgentState` with reducers and a compiled `StateGraph`**,
so that **all orchestration flows through one graph** (**FR36**).

## Acceptance Criteria

1. **FR36 — Single shared orchestration state**  
   **Given** a typed **`AgentState`** aligned with Architecture §3.2 (includes at minimum **`run_id`**, and scaffold fields for downstream nodes: e.g. **`tenant_id`**, **`raw_items`**, **`normalized_updates`**, **`classifications`**, **`routing_decisions`**, **`briefings`**, **`delivery_events`**, **`errors`**, **`flags`**, optional **`llm_trace`**)  
   **When** the schema is imported  
   **Then** append-style list fields use **`Annotated[..., operator.add]`** (or another Architecture-approved reducer) so parallel / multi-writer merges are safe later  
   **And** the pattern (TypedDict + Annotated **or** Pydantic + explicit merge rules) is chosen **once** and documented in `state.py` for the rest of Epic 3.

2. **Compiled graph shell**  
   **Given** a **minimal** `StateGraph` (no Scout/Normalize/Classify business logic yet — those are **3.3+**)  
   **When** it is **compiled** with a **checkpointer** from `checkpoints.py`  
   **Then** invocation with `config["configurable"]["thread_id"] == str(run_id)` completes  
   **And** a checkpoint exists afterward that can be read back (e.g. `get_state` / `aget_state` or equivalent for the pinned LangGraph API) proving persistence (**Architecture** §3.5).

3. **NFR8 — `run_id` in logs**  
   **Given** an invocation with **`run_id` set in input state**  
   **When** the shell node runs  
   **Then** at least **one structured log line** includes **`run_id`** in `extra` (match Story 3.1 style: namespaced `extra`, no collision with `LogRecord` builtins).

4. **Boundaries**  
   **Given** Architecture §6  
   **When** this story is implemented  
   **Then** **`services/`** modules do **not** import `graph/` (nodes will call **into** services in **3.3**, not the reverse)  
   **And** graph build + compile live under **`src/sentinel_prism/graph/`** per Architecture §5.

5. **Dependencies & verification**  
   **Given** `requirements.txt` still deferred graph pins (comment only today)  
   **When** this story lands  
   **Then** **`langgraph`**, **`langchain-core`**, and transitive graph packages needed for compile/checkpoint are **pinned** (reproducible CI)  
   **And** `verify_imports.py` (or equivalent) imports the graph stack successfully.

6. **Tests**  
   **Given** CI without live LLM/network for this story  
   **When** tests run  
   **Then** unit tests cover **state typing / reducer behavior** where meaningful  
   **And** an integration-style test compiles the graph with **`MemorySaver`** (or **`InMemorySaver`** — use the class name shipped by the pinned `langgraph`; both may exist in 1.x) and asserts **checkpoint round-trip** + **log capture** for `run_id`.

## Tasks / Subtasks

- [x] **Dependencies (AC: #5)**  
  - [x] Add pinned **`langgraph`** + **`langchain-core`** (and any required **`langgraph-checkpoint`** / related pins if CI does not resolve them deterministically) to `requirements.txt`.  
  - [x] Example set verified together: `langgraph==1.1.6`, `langchain-core==1.2.31`, `langgraph-checkpoint==4.0.2` — adjust only if compatibility requires; keep pins tight.  
  - [x] Update `verify_imports.py` to import `langgraph`, `langgraph.graph`, and `langgraph.checkpoint.memory` (or the module that exposes the chosen saver).

- [x] **`AgentState` + reducers (AC: #1, #4)** — `src/sentinel_prism/graph/state.py`  
  - [x] Replace the placeholder module docstring with the real schema.  
  - [x] Include **`run_id: str`** (use **`uuid.UUID` → `str`** at the graph boundary to match JSON/checkpoint serialization and Story 3.1 nullable DB `run_id` intent).  
  - [x] For list fields not yet produced by nodes, prefer **empty-list defaults on first invoke** documented in dev notes (LangGraph merge semantics).  
  - [x] Add **`flags: dict[str, bool]`** (or a small TypedDict) for future `needs_human_review`, `retry_count`, etc. — keep MVP minimal but typed.

- [x] **Checkpointer factory (AC: #2)** — `src/sentinel_prism/graph/checkpoints.py`  
  - [x] Implement `dev_memory_checkpointer()` → returns a **`MemorySaver`** (or **`InMemorySaver`**) instance per pinned API.  
  - [x] Docstring: **prod** path will use Postgres/SQL saver later (**Architecture** §3.5, FR35); this story is **dev/CI**.

- [x] **Graph build + compile (AC: #2, #3, #4)** — `src/sentinel_prism/graph/graph.py`  
  - [x] Export `build_regulatory_pipeline_graph(*, checkpointer: BaseCheckpointSaver | None = None)` (name to taste) that:  
    - [x] Instantiates `StateGraph(AgentState)`.  
    - [x] Adds **one** shell node, e.g. `shell` / `bootstrap`, that logs `run_id` and returns a **no-op partial update** `{}` or a harmless tick (e.g. increment a counter in `flags` if you want observable state change).  
    - [x] Wires `START → shell → END`.  
  - [x] `compile_graph(...)` returns **`CompiledStateGraph`** with checkpointer passed through.

- [x] **Package exports (AC: #4)**  
  - [x] Update `src/sentinel_prism/graph/__init__.py` to re-export the minimal public surface (`build_*`, `compile_*`, `AgentState` type) as needed by tests and future `workers/` / API.

- [x] **Tests (AC: #6)**  
  - [x] New `tests/test_graph_shell.py` (or split unit/integration):  
    - [x] `async` `ainvoke` if nodes are async-friendly; use sync `invoke` only if all nodes sync — **be consistent** with FastAPI async direction.  
    - [x] Assert checkpoint readable after run (API per LangGraph version).  
    - [x] Assert log line contains `run_id` (`caplog` or logging handler fixture).

- [x] **Intentionally out of scope (defer)**  
  - [x] `POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/resume` — still placeholder in `api/routes/runs.py`; wire in a later story when HITL + run lifecycle is implemented.  
  - [x] Scout/normalize/classify nodes — **3.3+**.  
  - [x] Writing `run_id` onto `raw_captures` / `normalized_updates` rows — optional enhancement; Story 3.1 already has nullable columns. Only add if trivial and covered by tests.

### Review Findings

- [x] [Review][Patch] Remove unused `checkpointer` kwarg from `build_regulatory_pipeline_graph` — **fixed** 2026-04-17: dropped the kwarg and the `_ = checkpointer` placeholder; deleted `test_build_accepts_checkpointer_kwarg_unused` and replaced with `test_build_regulatory_pipeline_graph_takes_no_kwargs` that asserts the clean signature. [`src/sentinel_prism/graph/graph.py`, `tests/test_graph_shell.py`]
- [x] [Review][Defer] Strengthen reducer-behavior test coverage — resolved from decision, deferred to Story 3.3: `test_append_reducer_uses_operator_add` asserts only stdlib list concatenation and the round-trip test never exercises an `Annotated[..., operator.add]` channel. Real list-producing nodes (Scout/Normalize) arrive in Story 3.3 and will drive proper merge assertions then. [`tests/test_graph_shell.py:67-75`, `tests/test_graph_shell.py:20-35`]
- [x] [Review][Patch] Validate `run_id` in `new_pipeline_state` — **fixed** 2026-04-17: `TypeError` for non-UUID/non-str, `ValueError` for empty or whitespace-only strings; docstring updated with `Raises` section; added `test_new_pipeline_state_rejects_empty_run_id` and `test_new_pipeline_state_rejects_non_string_non_uuid_run_id`. [`src/sentinel_prism/graph/state.py`, `tests/test_graph_shell.py`]
- [x] [Review][Patch] `_node_shell` uses defensive state access — **fixed** 2026-04-17: reads `state.get("run_id")` and `state.get("flags") or {}`, raises `ValueError` with context when `run_id` is missing/empty instead of an opaque `KeyError`. [`src/sentinel_prism/graph/graph.py`]
- [x] [Review][Defer] No reducer on `flags` channel — `dict[str, bool]` without `Annotated[..., merge]` will last-writer-wins across parallel branches. Safe for the single-node shell; must be resolved before Story 3.3 adds branching nodes that set flags. [`src/sentinel_prism/graph/state.py:32`]
- [x] [Review][Defer] `llm_trace` is a replace channel with no documented merge contract — multiple writers will race. Define reducer semantics (append vs. namespaced merge) before any node emits traces. [`src/sentinel_prism/graph/state.py:33`]
- [x] [Review][Defer] `compile_regulatory_pipeline_graph` instantiates a fresh `MemorySaver` when no checkpointer is supplied — two compiles in the same process cannot share state. Acceptable for the dev/CI scope of Story 3.2; introduce a checkpointer selector when the Postgres saver story lands. [`src/sentinel_prism/graph/graph.py:48-56`]
- [x] [Review][Defer] Re-invocation with the same `thread_id` is undefined by this shell — `_node_shell` unconditionally re-sets `graph_shell_seen=True` and there is no idempotency/replay test. Revisit when real run lifecycle arrives with `POST /runs/{id}/resume`. [`src/sentinel_prism/graph/graph.py:14-27`]
- [x] [Review][Defer] `requirements.txt` pins `langgraph-sdk==0.3.13` and `langgraph-prebuilt==1.0.9` even though the diff does not import them. They may be transitive pins for reproducibility, but there is no comment justifying the explicit lock. Either add a justifying comment or drop to transitive resolution. [`requirements.txt:31-38`]
- [x] [Review][Defer] Imports reach into submodule paths that look private — `langgraph.checkpoint.base.BaseCheckpointSaver` and `langgraph.graph.state.CompiledStateGraph`. Verify these are the supported public surfaces for the pinned 1.1.6 and consider re-exporting shims locally so a minor bump is contained. [`src/sentinel_prism/graph/graph.py:6-8`, `src/sentinel_prism/graph/checkpoints.py:9-10`]
- [x] [Review][Defer] `new_pipeline_state` hardcodes the list of channels to initialize — any future `AgentState` addition must be mirrored manually or the append-reducer precondition ("pass empty list on first invoke") silently breaks. Either derive the initializer from `AgentState.__annotations__` or add a compile-time test that asserts every list channel is initialized. [`src/sentinel_prism/graph/state.py:48-65`]
- [x] [Review][Defer] `tenant_id` is `NotRequired` and is not included in the structured log payload — multi-tenant traceability is not enforced at the state boundary or in graph logs. Revisit when tenant-scoped RBAC enforcement reaches the graph. [`src/sentinel_prism/graph/state.py:23`, `src/sentinel_prism/graph/graph.py:17-24`]
- [x] [Review][Defer] `tenant_id` accepts empty/whitespace strings via `new_pipeline_state` — would record a blank tenant in state if a caller passes `""`. Minor; tighten when tenant validation arrives. [`src/sentinel_prism/graph/state.py:40-65`]
- [x] [Review][Defer] Prod/SQL checkpointer path is referenced in docstrings with no interface seam, factory selector, or config toggle in this change. Capture the follow-up explicitly against Architecture §3.5 / FR35 when the Postgres saver story is scheduled. [`src/sentinel_prism/graph/checkpoints.py:1-8`]

## Dev Notes

### Epic 3 context

- **Epic goal:** LangGraph **StateGraph** with checkpoints, branching, retries, tools (**Epic 3 intro** in `_bmad-output/planning-artifacts/epics.md`).  
- **This story** establishes the **typing + compile + checkpoint** spine so **3.3** can add real nodes without reshaping state.

### Previous story intelligence (3.1)

- **Persistence exists:** `RawCapture`, `NormalizedUpdateRow`, `run_id` nullable UUID on both — `_bmad-output/implementation-artifacts/3-1-persist-raw-captures-and-normalized-records.md`.  
- **Logging pattern:** single structured event with `extra={"event": ..., "ctx": {...}}` — replicate for graph shell, include `run_id` in `ctx`.  
- **DTO:** `ScoutRawItem` is authoritative for future `raw_items`; until **3.3**, `raw_items` in `AgentState` may stay **`list[Any]`** or **`list[dict]`** with a comment to narrow to serialized scout items later — avoid circular imports (`graph` must not depend on heavy service types if it creates cycles; **prefer JSON-serializable dicts or lightweight TypedDicts** for checkpoint-friendly state).

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Single graph** | FR36 / Architecture §3.1 — all pipeline orchestration enters through this compiled graph; no parallel ad-hoc orchestration in `workers/` long-term. |
| **Checkpointer** | Required for AC2; **Memory** saver only proves the wiring — document Postgres saver follow-up. |
| **Thread id** | Use **`thread_id == str(run_id)`** in `configurable` now to match future **Architecture** §3.6 wording. |
| **No service imports from graph** | Services stay callable from nodes later; never `from sentinel_prism.graph import ...` inside `services/`. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR36 | Shared state across processing stages — `AgentState` + `StateGraph`. |
| NFR8 | Structured logs include correlation / **`run_id`** across services — extend to graph shell. |
| Architecture §3.2 | Fields: `run_id`, `tenant_id`, lists for pipeline artifacts, `errors`, `flags`, optional `llm_trace`. |
| Architecture §3.5 | Checkpointer persists state for resume/replay. |
| Architecture §5–6 | Files under `graph/state.py`, `graph/graph.py`, `graph/checkpoints.py`. |

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Orchestration | LangGraph `StateGraph`, compiled with checkpointer — **only** in `graph/graph.py`. |
| State | `state.py` owns schema + reducer definitions. |
| Testing | Memory checkpointer in tests; no network — **Architecture** §5 testing guidance. |
| API | `runs.py` remains stub; do not block **3.2** on REST. |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **langgraph** (pin) | `StateGraph`, `START`, `END`, compile + checkpointer. |
| **langchain-core** (pin) | Pulled by LangGraph; pin explicitly for reproducible CI. |
| **stdlib** | `typing.TypedDict`, `typing.NotRequired`, `typing.Annotated`, `operator.add`. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/state.py` | Implement `AgentState`. |
| `src/sentinel_prism/graph/graph.py` | Build + compile shell graph. |
| `src/sentinel_prism/graph/checkpoints.py` | Dev checkpointer factory. |
| `src/sentinel_prism/graph/__init__.py` | Public exports. |
| `requirements.txt` | Add graph pins. |
| `verify_imports.py` | Validate imports. |
| `tests/test_graph_shell.py` | New tests. |

### Testing requirements

- Prefer **async** graph invocation if the shell node is `async` (aligns with FastAPI + future connector calls).  
- Use **`pytest` + `caplog`** (or existing project logging fixtures if any).  
- No `DATABASE_URL` requirement for this story’s tests unless you optionally test DB saver (don’t — defer).

### Project structure notes

- Package: `sentinel_prism` under `src/sentinel_prism/`.  
- No `project-context.md` in repo — Architecture + epics + prior story files are authoritative.

### References

- `_bmad-output/planning-artifacts/epics.md` — Epic 3, Story 3.2  
- `_bmad-output/planning-artifacts/architecture.md` — §3.1–3.6, §5 graph layout, §6 FR36 mapping  
- `_bmad-output/implementation-artifacts/3-1-persist-raw-captures-and-normalized-records.md` — persistence, `run_id`, logging  
- `src/sentinel_prism/graph/state.py`, `graph.py`, `checkpoints.py` — current placeholders  
- `src/sentinel_prism/api/routes/runs.py` — future run API (stub)  
- [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) — reducers, compilation  
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/add-memory) — checkpointers, `thread_id`

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Pinned LangGraph stack in `requirements.txt` (`langgraph`, `langchain-core`, `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk`).
- Implemented `AgentState` (TypedDict + `operator.add` list channels), `new_pipeline_state`, `dev_memory_checkpointer` (`MemorySaver`), `build_regulatory_pipeline_graph` / `compile_regulatory_pipeline_graph` with async `shell` node (structured log `graph_shell_entered` + `ctx.run_id`, `flags.graph_shell_seen`).
- Tests: checkpoint round-trip via `aget_state`, caplog assertion on `event` / `ctx`, UUID normalization, builder API smoke, reducer doc test.
- Full suite: 87 passed, 9 skipped (2026-04-18).

### File List

- `requirements.txt`
- `verify_imports.py`
- `src/sentinel_prism/graph/state.py`
- `src/sentinel_prism/graph/checkpoints.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/__init__.py`
- `tests/test_graph_shell.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Git intelligence summary

Recent commits on `main` are Epic 2–oriented (`bdbd636`, `85cd667`, `131b586`): async `execute_poll`, repositories, structured logging. Epic 3 persistence landed in working tree per Story 3.1 artifacts — **mirror** logging and typing discipline when adding the graph shell.

## Latest technical information

- **LangGraph 1.x** uses `langgraph.checkpoint.memory.MemorySaver` / `InMemorySaver` (verify exact export for the pinned version).  
- Always pass **`config={"configurable": {"thread_id": ...}}`** when exercising checkpointers.  
- **Pinned combo (verified installable):** `langgraph==1.1.6`, `langchain-core==1.2.31`, `langgraph-checkpoint==4.0.2` — bump together if needed.

## Project context reference

No `project-context.md` found; use Architecture + this file.

## Change Log

- 2026-04-18 — Story 3.2 implemented: AgentState, MemorySaver checkpointer, compiled shell graph, tests, dependency pins.
- 2026-04-17 — Code review complete: 3 patches applied (`run_id` validation in `new_pipeline_state`, defensive state access in `_node_shell`, removed unused `checkpointer` kwarg from builder + canonizing test). Status → `done`. Full suite 89 passed, 9 skipped.

## Story completion status

**done** — Implementation complete; all acceptance criteria covered by code and tests. Code review completed 2026-04-17 (3 patches applied, 11 items deferred to future stories / deferred-work tracker, no acceptance violations).

### Saved questions / clarifications (non-blocking)

- Whether `raw_items` should hold `ScoutRawItem` dataclass instances vs dicts — **dict/TypedDict** is safer for checkpoint serialization until LangGraph serializer behavior is validated with dataclasses.  
- Exact Postgres checkpointer package name for production — defer to story that introduces `PostgresSaver` / SQL integration.
