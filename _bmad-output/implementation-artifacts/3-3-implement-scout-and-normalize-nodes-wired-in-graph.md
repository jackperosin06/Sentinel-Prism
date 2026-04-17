# Story 3.3: Implement scout and normalize nodes wired in graph

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **graph nodes calling connector and normalizer services**,
so that **a run progresses from fetch to structured updates** (**FR36**).

## Acceptance Criteria

1. **Scout node (`scout`)**  
   **Given** initial pipeline state includes **`run_id`** and enough context to identify a **single source** (see Dev Notes — `AgentState` extension)  
   **When** the `scout` node runs  
   **Then** it loads **enabled** source configuration from the DB (same fields needed for RSS/HTTP/fallback as `execute_poll`)  
   **And** it performs **fetch only** via existing connector entrypoints (`fetch_rss_items`, `fetch_http_page_item`, `fetch_html_page_items`, and the same fallback ordering as `poll.py`) — **do not** call `execute_poll` wholesale (that bundles dedup, metrics, and persistence)  
   **And** it returns a partial update that appends **JSON-serializable** dicts to **`raw_items`** (reuse or mirror `scout_raw_item_payload` in `db/repositories/captures.py` so payloads stay drift-aligned with Story 3.1)  
   **And** at least one structured log line includes **`run_id`** in namespaced `extra` (`event` + `ctx`, matching Story 3.1 / 3.2 style).

2. **Normalize node (`normalize`)**  
   **Given** `raw_items` produced by `scout` (possibly empty)  
   **When** the `normalize` node runs  
   **Then** for each raw dict it rehydrates a **`ScoutRawItem`** and calls **`normalize_scout_item`** with **`source_id`**, **`source_name`**, **`jurisdiction`** consistent with that fetch (load from DB once per node if not already on state)  
   **And** it returns a partial update appending **checkpoint-safe** dicts to **`normalized_updates`** (datetimes/UUIDs as strings; schema documented in Dev Notes — align field names with `NormalizedUpdate` / FR8)  
   **And** at least one structured log includes **`run_id`** in `ctx`.

3. **Graph topology (replaces shell-only path)**  
   **Given** `build_regulatory_pipeline_graph` today wires **`START → shell → END`**  
   **When** this story lands  
   **Then** the regulatory pipeline uses **`START → scout → normalize → END`** (remove the placeholder `shell` node or reduce it to a one-line forwarder only if you need a migration shim — prefer **delete** to avoid duplicate logging)  
   **And** `compile_regulatory_pipeline_graph` still accepts an optional checkpointer and completes an **`ainvoke`** round-trip with `config["configurable"]["thread_id"] == str(run_id)`.

4. **Reducer / merge behavior**  
   **Given** list channels use **`Annotated[..., operator.add]`**  
   **When** tests run  
   **Then** at least one test exercises **real** partial returns from `scout` / `normalize` (not only the stdlib `operator.add` doc test) so append merges are regression-safe.

5. **Architecture boundaries**  
   **Given** Architecture §6  
   **When** implemented  
   **Then** **`services/`** never imports **`graph/`**  
   **And** node implementations live under **`src/sentinel_prism/graph/nodes/`** per Architecture §5  
   **And** nodes may import **`services/`**, **`db/repositories/`**, **`db/session`** as needed.

6. **Tests**  
   **Given** CI without mandatory live network  
   **When** tests run  
   **Then** **unit** tests cover each node with **mocked** fetch / mocked DB session (or existing project async session test patterns)  
   **And** an **integration-style** test compiles the graph with **`MemorySaver`**, runs **`scout` → `normalize`** with mocks, and asserts **`normalized_updates`** length/content matches expectations for a synthetic raw item.

## Tasks / Subtasks

- [x] **State input contract (AC: #1–#3)** — `src/sentinel_prism/graph/state.py`  
  - [x] Extend **`AgentState`** with the minimum fields needed for single-source scout (e.g. **`source_id: NotRequired[str]`** — UUID string at the graph boundary to match JSON/checkpoints; alternatively a `source_ids: NotRequired[list[str]]` if you prefer forward-compat — pick one and document).  
  - [x] Update **`new_pipeline_state`** (or add a dedicated factory) so tests and callers can construct a valid initial state without ad-hoc dict assembly.  
  - [x] Keep list channels initialized to `[]` on first invoke.

- [x] **Shared fetch orchestration (AC: #1, #5)** — `src/sentinel_prism/services/connectors/`  
  - [x] Extract the **fetch + fallback** decision tree from `poll.py` into a **pure service helper** (e.g. `fetch_scout_items_for_source(...) -> list[ScoutRawItem]`) that takes **already-loaded** source primitives (type, url, enabled, fallback mode/url) so **`poll.py`** and **`graph/nodes/scout.py`** share one implementation without importing `graph` from `services`.  
  - [x] Refactor `execute_poll` to call the helper (behavior-preserving for Epic 2 paths).

- [x] **Scout node (AC: #1, #5)** — `src/sentinel_prism/graph/nodes/scout.py`  
  - [x] Async node function; load `Source` via `get_session_factory()` + `sources_repo.get_source_by_id`.  
  - [x] Handle **missing / disabled** source: return **`{"errors": [...]}`** (structured dict with step + message) and **empty** `raw_items` append — do not crash the graph; align with Architecture §5 error-handling guidance.  
  - [x] Serialize each **`ScoutRawItem`** with **`scout_raw_item_payload`** (import from captures repo) or move payload helper to a neutral module if you need to avoid `db` → `graph` awkwardness — **no duplicated field lists**.

- [x] **Normalize node (AC: #2, #5)** — `src/sentinel_prism/graph/nodes/normalize.py`  
  - [x] Add **`scout_raw_item_from_payload(dict) -> ScoutRawItem`** (or equivalent) in a **single** shared place used by tests + node — eliminate copy-paste from `tests/test_captures_persist.py`.  
  - [x] Add **`normalized_update_to_state_dict(NormalizedUpdate) -> dict[str, Any]`** (or similar) next to **`NormalizedUpdate`** with explicit datetime → ISO8601 and UUID → str rules.  
  - [x] If `raw_items` empty, append nothing and log once at INFO/DEBUG with `run_id`.

- [x] **Graph wiring (AC: #3)** — `src/sentinel_prism/graph/graph.py`  
  - [x] Register nodes **`scout`**, **`normalize`**; edges **`START → scout → normalize → END`**.  
  - [x] Remove **`shell`** node from the default regulatory graph.

- [x] **Package exports (AC: #5)** — `src/sentinel_prism/graph/__init__.py`  
  - [x] Export node callables only if needed by tests or workers; avoid bloating public API.

- [x] **Tests (AC: #4, #6)** — `tests/test_graph_scout_normalize.py` (name to taste)  
  - [x] Extend/replace assertions in `tests/test_graph_shell.py` where the topology changed (checkpoint round-trip + `run_id` logging).  
  - [x] Mock HTTP/RSS at the connector boundary **or** mock the new fetch helper — no real outbound network.

- [x] **Imports / CI**  
  - [x] Update `verify_imports.py` if new graph subpackages require explicit smoke coverage.

### Intentionally out of scope (defer)

- **Dedup ledger + `persist_new_items_after_dedup`** inside the graph (poll path keeps ownership for now; a future story can add a **persist** node or unify run lifecycle).  
- **`classify`**, conditional edges, retries, Tavily, audit events tables.  
- **REST** `POST /runs` wiring.

### Review Findings

_Code review 2026-04-17 — 3 layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor returned clean; all findings below come from Blind + Edge layers. See `3-3-review.diff` for the reviewed scope._

**Patches** — all applied 2026-04-17 (full suite 94 passed / 9 skipped, matches pre-review baseline).

- [x] [Review][Patch] `node_scout` hardcoded `trigger="manual"` — added `trigger: NotRequired[PipelineTrigger]` to `AgentState`, `trigger` kwarg to `new_pipeline_state`, and `node_scout` now reads `state.get("trigger") or "manual"` before calling `fetch_scout_items_with_fallback`. [`src/sentinel_prism/graph/state.py`, `src/sentinel_prism/graph/nodes/scout.py`]
- [x] [Review][Patch] `last_error_reason` format contract now documented in `poll.py` with the four stage prefixes (`clear_poll_failure` / `dedup` / `persist` / `metrics` "after <outcome> success: ..."); `dedup` prefix preserved for Story 2.4 compatibility and the existing `test_connectors_rss_http.py:1501` assertion. [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] `failed_stage` sentinel now initialised to `"clear_poll_failure"` and advanced to `"dedup"` *after* the `clear_poll_failure` call, so a DB error during the clear step is no longer mislabelled. [`src/sentinel_prism/services/connectors/poll.py`]
- [x] [Review][Patch] `test_build_regulatory_pipeline_graph_takes_no_kwargs` now asserts `pytest.raises(TypeError)` when `checkpointer=` is passed to the builder (plus the existing no-arg compile smoke). [`tests/test_graph_shell.py`]
- [x] [Review][Patch] Replaced tautological `test_append_reducer_uses_operator_add` with `test_list_channels_annotated_with_operator_add` — inspects `AgentState.__annotations__` via `typing.get_type_hints(..., include_extras=True)` and asserts `operator.add` is the reducer on all seven list channels. Runtime AC #4 coverage stays in `test_graph_scout_normalize.test_graph_multi_item_fetch_exercises_list_reducers`. [`tests/test_graph_shell.py`]
- [x] [Review][Patch] `new_pipeline_state` now stores `sid.strip()` (canonical form), so checkpoint state and the nodes' `str(sid).strip()` agree. [`src/sentinel_prism/graph/state.py`]
- [x] [Review][Patch] `node_scout` and `node_normalize` run_id guards tightened to `if not run_id or not str(run_id).strip()`, matching the factory's strip-check so bypass paths can't deliver a whitespace `thread_id`. [`src/sentinel_prism/graph/nodes/scout.py`, `src/sentinel_prism/graph/nodes/normalize.py`]
- [x] [Review][Patch] Session / repo access in both nodes now wrapped in `try/except Exception` → `errors` channel with `step`, `message="db_error"`, `error_class`, `detail`, plus a `graph_scout_db_error` / `graph_normalize_db_error` structured log event carrying `run_id`. Architecture §5 "errors appended, not swallowed silently" restored. [`src/sentinel_prism/graph/nodes/scout.py`, `src/sentinel_prism/graph/nodes/normalize.py`]
- [x] [Review][Patch] `scout_raw_item_from_payload` now validates `source_id` as `str | UUID` and raises a clear `TypeError` for any other shape, so decoders fail fast at the payload boundary instead of crashing inside `ScoutRawItem.__post_init__`. [`src/sentinel_prism/services/connectors/scout_raw_item.py`]

**Deferred (pre-existing or out of scope)**

- [x] [Review][Defer] `normalize` does not verify `raw.source_id` matches state `source_id` — deferred, MVP assumes single-source-per-run (see "Saved questions" below). [`src/sentinel_prism/graph/nodes/normalize.py:83-102`]
- [x] [Review][Defer] `_tz_aware_or_none` silently coerces naive datetimes with no audit log despite docstring promise — deferred, pre-existing Story 3.1 code. [`src/sentinel_prism/services/ingestion/normalize.py:67-81`]
- [x] [Review][Defer] Scoring heuristic vs `_clean_text` disagree on NUL-only strings — `_mvp_confidence_scores` awards title credit for `"\x00\x00"` but `_clean_text` returns `None`, violating the stated docstring invariant. Deferred, pre-existing Story 3.1 code. [`src/sentinel_prism/services/ingestion/normalize.py:45-64`]
- [x] [Review][Defer] `AgentState.flags` has no reducer; last-writer-wins is undocumented in the module docstring — deferred, acknowledged in Story 3.2 review (restated here for 3.3 audit trail). [`src/sentinel_prism/graph/state.py:34`]
- [x] [Review][Defer] Per-node source row lookups in `scout` + `normalize` are redundant and TOCTOU-risky — an admin rename/jurisdiction change between nodes produces raw items tagged under one jurisdiction and normalized rows under another. Deferred, cache `source_name`/`jurisdiction` on state after scout (small follow-up story). [`src/sentinel_prism/graph/nodes/scout.py:57-58`, `src/sentinel_prism/graph/nodes/normalize.py:63-79`]
- [x] [Review][Defer] `operator.add` reducer accumulates duplicate `normalized_updates` on repeated `ainvoke` of the same checkpoint thread — deferred, already acknowledged in this story's Completion Notes (pending a future delta/idempotent normalize story). [`src/sentinel_prism/graph/nodes/normalize.py:52-102`]
- [x] [Review][Defer] `node_normalize` does `list(state.get("raw_items") or [])` — if upstream state is mutated to a dict this silently iterates keys. Deferred, defensive type safety. [`src/sentinel_prism/graph/nodes/normalize.py:52`]

## Dev Notes

### Epic 3 context

- **Epic goal:** LangGraph **StateGraph** with **AgentState**, nodes **scout → normalize → classify**, checkpoints, branching, retries, tools — `_bmad-output/planning-artifacts/epics.md` (Epic 3 header).  
- **This story** replaces the **3.2 shell** with **real** scout + normalize nodes while keeping **services ↔ graph** dependency direction.

### Previous story intelligence (3.2)

- **Graph spine:** `AgentState` list channels use **`operator.add`**; **`new_pipeline_state`** validates **`run_id`** — extend carefully.  
- **Compile:** `compile_regulatory_pipeline_graph(checkpointer=...)`; **`thread_id == str(run_id)`**.  
- **Logging:** `extra={"event": "...", "ctx": {...}}` including **`run_id`** — replicate on new nodes.  
- **Deferred from 3.2 review:** exercise **real** reducer merges once list-producing nodes exist (**this story**). **`flags`** remains last-writer-wins — acceptable while graph is a single linear path; document if any node sets overlapping keys.

### Previous story intelligence (3.1)

- **Authoritative DTO:** `ScoutRawItem` — `src/sentinel_prism/services/connectors/scout_raw_item.py`.  
- **Normalizer:** `normalize_scout_item` + **`NormalizedUpdate`** — `src/sentinel_prism/services/ingestion/normalize.py`.  
- **Payload serialization:** `scout_raw_item_payload` — `src/sentinel_prism/db/repositories/captures.py`.  
- **Poll path:** still owns **dedup + DB persist** — `src/sentinel_prism/services/connectors/poll.py` + `services/ingestion/persist.py`.

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Dependency direction** | Nodes → services/repos/session; **never** services → graph. |
| **Checkpoint JSON** | State values must be JSON-serializable; prefer **dicts** on `raw_items` / `normalized_updates`. |
| **DB sessions in nodes** | Use the same **`get_session_factory()`** async pattern as `execute_poll`; avoid long-lived sessions across unrelated awaits. |
| **Errors** | Append to **`errors`** with stable shape (e.g. `step`, `message`, optional `detail`); include **`run_id`** in logs, not necessarily inside every error dict. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR36 | Shared orchestration state — extend `AgentState` minimally; single compiled graph. |
| FR8 / FR10 | Normalized dicts should remain mappable from `NormalizedUpdate` (fields expected by future persist/API stories). |
| NFR8 | Structured logs with **`run_id`** on scout + normalize. |
| Architecture §3.3–§3.4 | Node ids **`scout`**, **`normalize`**; linear **`START → scout → normalize → …`**. |
| Architecture §5 | Files under `graph/nodes/`; errors appended, not swallowed silently. |

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Layout | `graph/nodes/scout.py`, `graph/nodes/normalize.py`, update `graph/graph.py`. |
| Fetch logic | Shared service-layer helper; `poll.py` stays thin. |
| Testing | Mocked I/O; MemorySaver round-trip; **no** live network requirement. |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **langgraph** (pinned, Story 3.2) | `StateGraph`, `START`, `END`, `compile`, async nodes. |
| **SQLAlchemy async** | Existing session factory + `sources_repo` patterns. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/nodes/__init__.py` | New package; export node callables if useful. |
| `src/sentinel_prism/graph/nodes/scout.py` | Scout node. |
| `src/sentinel_prism/graph/nodes/normalize.py` | Normalize node. |
| `src/sentinel_prism/graph/graph.py` | Wire nodes; remove `shell`. |
| `src/sentinel_prism/graph/state.py` | Source id(s) on state + factory update. |
| `src/sentinel_prism/services/connectors/poll.py` | Call shared fetch helper. |
| `src/sentinel_prism/services/connectors/<new helper module>.py` | Optional — shared fetch orchestration. |
| `src/sentinel_prism/services/ingestion/normalize.py` or `scout_raw_item.py` | Payload ↔ DTO helpers as needed. |
| `tests/test_graph_shell.py` | Update for new topology. |
| `tests/test_graph_scout_normalize.py` | New coverage. |

### Testing requirements

- **Async** `ainvoke` / `aget_state` consistent with Story 3.2.  
- **`caplog`** (or project logging helpers) asserts **`run_id`** appears for new events.  
- Assert **`normalized_updates`** after normalize matches **count** of **`raw_items`** for happy path mocks.

### Project structure notes

- Package root: `src/sentinel_prism/`.  
- No `project-context.md` in repo — Architecture + prior story files + this file are authoritative.

### References

- `_bmad-output/planning-artifacts/epics.md` — Epic 3, Story 3.3  
- `_bmad-output/planning-artifacts/architecture.md` — §3.1–§3.4, §5–§6  
- `_bmad-output/implementation-artifacts/3-2-define-agentstate-and-graph-compilation-shell.md` — graph shell, reducers, logging  
- `_bmad-output/implementation-artifacts/3-1-persist-raw-captures-and-normalized-records.md` — DTOs, normalizer, payload patterns  
- `src/sentinel_prism/services/connectors/poll.py` — current fetch + fallback ordering  
- [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) — state updates, reducers  

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Introduced `scout_fetch.py` with `fetch_scout_items_with_fallback`, shared by `execute_poll` and `node_scout`; poll failure logging and `poll_primary_failed_try_fallback` preserved (log emitted from scout_fetch on fallback attempt).
- `AgentState.source_id` + `new_pipeline_state(..., source_id=...)`; graph topology `START → scout → normalize → END`.
- `scout_raw_item_from_payload`, `normalized_update_to_state_dict`; captures integration test uses shared rehydrate helper.
- Tests mock `fetch_scout_items_with_fallback` and `get_session_factory` / `get_source_by_id`; connector tests retarget monkeypatches to `scout_fetch.*`.
- `verify_imports.py` prepends `src/` to `sys.path` and imports graph node modules.
- **Idempotency:** `node_normalize` maps over **all** `raw_items` in state each invocation; repeating a full `ainvoke` on the same checkpoint thread would append duplicate `normalized_updates` unless a future story adds delta/idempotent normalize.

### File List

- `src/sentinel_prism/services/connectors/scout_fetch.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `src/sentinel_prism/services/connectors/scout_raw_item.py`
- `src/sentinel_prism/services/ingestion/normalize.py`
- `src/sentinel_prism/graph/state.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/nodes/__init__.py`
- `src/sentinel_prism/graph/nodes/scout.py`
- `src/sentinel_prism/graph/nodes/normalize.py`
- `verify_imports.py`
- `tests/test_graph_shell.py`
- `tests/test_graph_scout_normalize.py`
- `tests/test_connectors_rss_http.py`
- `tests/test_captures_persist.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-3-implement-scout-and-normalize-nodes-wired-in-graph.md`

## Git intelligence summary

Recent commits on `main` are Epic 2–oriented (`bdbd636`, `85cd667`, `131b586`): async `execute_poll`, repositories, structured logging. Epic 3 graph + ingestion work lives in the current tree — **mirror** Story 3.1/3.2 patterns for logging, typing, and test discipline when adding nodes.

## Latest technical information

- **LangGraph 1.1.6** (pinned): node callables return **`dict`** partial state; list channels merged with **`operator.add`** when annotated in `AgentState`.  
- Keep **`config={"configurable": {"thread_id": str(run_id)}}`** for checkpoint tests.  
- Prefer **async** nodes throughout so future connector and DB calls stay non-blocking.

## Project context reference

No `project-context.md` found; use Architecture + epic + prior implementation artifacts.

## Change Log

- 2026-04-17 — Story 3.3 implemented: scout/normalize nodes, `scout_fetch` shared helper, tests, sprint → `review`.
- 2026-04-17 — Code review (3 layers): 0 decision-needed, 9 patches applied, 7 deferred, 11 dismissed. Suite still 94/9. Sprint → `done`.

## Story completion status

**done** — Implementation complete and code review applied; full test suite green (94 passed, 9 skipped).

### Saved questions / clarifications (non-blocking)

- Whether **multi-source** fan-out belongs in **`source_ids`** vs repeated graph invocations — MVP assumes **one source per run** unless you extend state in this story.  
- Whether **dedup** should run inside **`scout`** before normalize — deferred to avoid conflicting with poll’s transactional dedup+persist; revisit when **`POST /runs`** owns ingestion.
