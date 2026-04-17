# Story 3.5: Conditional edges for review vs continue

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **branching after classify based on policy (including `needs_human_review`)**,
so that **ambiguous or high-risk items follow a human review path** while others continue (**FR16** partial, **FR37**, **FR38** correlation via `run_id`).

## Acceptance Criteria

1. **Conditional routing after `classify`**  
   **Given** the pipeline has executed **`classify`** and `AgentState` includes merged **`flags`** (including **`needs_human_review`** set by Story 3.4 when any classification row requires review)  
   **When** the graph chooses the next step  
   **Then** it uses **`add_conditional_edges`** (or equivalent supported API) from **`classify`** with at least two destinations: **human review** vs **continue**  
   **And** the routing condition is **deterministic** from checkpoint-visible state (prefer **`flags["needs_human_review"]`** as the primary signal so it stays aligned with Story 3.4; document if you also consult per-row `needs_human_review` in `classifications`)

2. **Human review path — placeholder or interrupt**  
   **Given** **`flags["needs_human_review"]` is true** (truthy) after classify  
   **When** the graph follows the review branch  
   **Then** execution reaches a dedicated node (stable graph id, e.g. **`human_review_gate`**) that implements **one** of:  
   - **Preferred (Architecture §3.1):** **`interrupt(...)`** from **`langgraph.types`** with a **JSON-serializable** payload that includes **`run_id`** (and optional context e.g. `step`, `source_id` if present in state) so clients can resume later; **checkpointer required** (already true for this graph)  
   - **Acceptable MVP alternative:** a **placeholder** node that sets an explicit flag (e.g. `flags["awaiting_human_review"]=True`), logs a structured event with **`run_id`**, and edges to **`END`** — only if you document why interrupt is deferred and still satisfy AC #3 for the continue path  
   **And** the node lives under **`src/sentinel_prism/graph/nodes/`** with naming consistent with existing nodes (`node_human_review_gate` or `node_*` pattern)

3. **Continue path**  
   **Given** **`needs_human_review` is false** (falsy) after classify  
   **When** the graph runs  
   **Then** the pipeline proceeds to **`END`** (no **`brief`** / **`route`** nodes yet — those are future stories; do not invent full downstream topology)

4. **Correlation id (FR38)**  
   **Given** either branch is taken  
   **When** routing and the review node run  
   **Then** **`run_id`** in state is **unchanged** (same string as thread/checkpoint correlation); no new run id is minted inside the gate node  
   **And** new logs use **`extra={"event": "...", "ctx": {...}}`** including **`run_id`** on the review path (mirror Stories 3.1–3.4)

5. **Architecture boundaries**  
   **Given** Architecture §5–§6  
   **When** implemented  
   **Then** routing helpers may live in **`graph/graph.py`** or a small **`graph/routing.py`** — **do not** move orchestration into **`services/`**  
   **And** **`services/`** does not import **`graph/`**

6. **Tests**  
   **Given** CI without live providers  
   **When** tests run  
   **Then** extend or add tests (e.g. **`tests/test_graph_shell.py`**, **`tests/test_graph_classify.py`**, or a focused **`tests/test_graph_conditional_edges.py`**) that:  
   - Compile with **`MemorySaver`** / existing **`dev_memory_checkpointer`** pattern and **`thread_id == str(run_id)`** where applicable  
   - **Continue path:** `needs_human_review` false → result has **no** `__interrupt__` (when using interrupt on the other path) and reaches terminal state as today  
   - **Review path:** `needs_human_review` true → either **`__interrupt__` present** on `ainvoke` output with payload containing **`run_id`**, **or** documented placeholder flag + log assertion  
   **And** tighten **shape assertions** called out in Story 3.4 deferrals for **`classifications`** / routing in **`test_graph_shell.py`** / **`test_graph_scout_normalize.py`** now that branching exists

7. **Contract test (from Story 3.4 deferral)**  
   **Given** `source_id` may appear as **UUID or str** at the graph boundary  
   **When** normalization/classification consume it  
   **Then** add or extend a **contract test** ensuring **`source_id`** correlation keys in classifications remain consistent (Story 3.4 deferred item — implement alongside this story)

## Tasks / Subtasks

- [x] **Routing function (AC: #1, #5)** — `src/sentinel_prism/graph/graph.py` (or `graph/routing.py`)  
  - [x] Pure function of **`AgentState`** → destination key (e.g. `Literal["human_review_gate", "end"]` mapped to node name / `END`).  
  - [x] Document precedence: **`flags["needs_human_review"]`** vs scanning **`classifications`** (pick one source of truth; avoid drift from `node_classify`).

- [x] **`human_review_gate` node (AC: #2, #4, #5)** — `src/sentinel_prism/graph/nodes/human_review_gate.py` (name flexible but stable id in graph)  
  - [x] If using **`interrupt`**: payload must include **`run_id`**; log **`run_id`** at INFO/DEBUG with existing logging pattern.  
  - [x] Note in Dev Notes: **`interrupt` re-executes the node body on resume** — keep node logic safe for re-entry or document resume contract for Epic 4.

- [x] **Graph wiring (AC: #1, #3)** — `src/sentinel_prism/graph/graph.py`  
  - [x] Remove direct **`classify → END`**; add **`add_conditional_edges("classify", route_fn, path_map)`**.  
  - [x] Register new node; export from **`graph/nodes/__init__.py`** if consistent with 3.3/3.4.

- [x] **Tests (AC: #6, #7)** — `tests/`  
  - [x] Minimal state-only test: inject state with **`flags={"needs_human_review": True/False}`** and stub/mocked upstream nodes if needed — or drive **`classify`** with **`StubClassificationLLM`** to flip the flag.  
  - [x] Assert **`__interrupt__`** payload when using interrupt API (LangGraph returns interrupt info on **`ainvoke`** result — verify with pinned **`langgraph==1.1.6`**).  
  - [x] **`source_id`** UUID/str contract test tied to classification join keys.

- [x] **Imports smoke (if new modules)** — `verify_imports.py` only if new public entry points warrant it.

### Review Findings

- [x] [Review][Patch] Coerce `source_id` to `str` in `human_review_gate` payload and log ctx for defense in depth [src/sentinel_prism/graph/nodes/human_review_gate.py:22-39]
- [x] [Review][Patch] Tighten review-path test: assert exactly one interrupt (`len(intr) == 1`) [tests/test_graph_conditional_edges.py:232]
- [x] [Review][Patch] Strengthen continue-path assertion: use strict `is False` or explicit absence check on `flags["needs_human_review"]` [tests/test_graph_conditional_edges.py:160]
- [x] [Review][Patch] Add router unit-test cases for `flags=None` and `flags` key absent to lock `state.get("flags") or {}` behavior [tests/test_graph_conditional_edges.py:43-91]
- [x] [Review][Patch] Review-path integration test also asserts row-level `classifications[0]["needs_human_review"] is True` to tie routing back to classify policy [tests/test_graph_conditional_edges.py:163-244]
- [x] [Review][Defer] Interrupt return value discarded (`return {}` unreachable on pause; resume value ignored) [src/sentinel_prism/graph/nodes/human_review_gate.py:41-42] — deferred, Epic 4 resume contract (explicitly noted in Dev Notes)
- [x] [Review][Defer] Magic constant `CLASSIFY_NEXT_CONTINUE = "end"` could collide with a future node named `"end"` [src/sentinel_prism/graph/routing.py:17] — deferred, hygiene; rename or use `END` directly when topology grows
- [x] [Review][Defer] Hardcoded `confidence == pytest.approx(0.85)` with no fixture anchor [tests/test_graph_shell.py:99] — deferred, test hygiene; tie to stub fixture constant in 3.6+
- [x] [Review][Defer] Gate logging fires on every re-entry (resume re-executes node) — not idempotent despite docstring [src/sentinel_prism/graph/nodes/human_review_gate.py:26-32] — deferred, Epic 4 will coordinate resume semantics
- [x] [Review][Defer] `node_classify` returns `{}` on empty `normalized_updates`, leaving stale `flags["needs_human_review"]=True` from restored state → spurious interrupt [src/sentinel_prism/graph/nodes/classify.py:32-41] — deferred, Story 3.4 scope / restored-state concern
- [x] [Review][Defer] `any_review` seeded from incoming flags makes the aggregate sticky across rows that are all benign [src/sentinel_prism/graph/nodes/classify.py:45,157-160] — deferred, Story 3.4 OR-semantics decision (revisit if run-scoped aggregation is needed)

## Dev Notes

### Epic 3 context

- Epic goal: **StateGraph** with **scout → normalize → classify**, then **conditional edges**, **retry**, **Tavily**, **audit events** ([Source: `_bmad-output/planning-artifacts/epics.md` — Epic 3 header]).  
- This story introduces **branching** matching Architecture §3.4 topology (classify → review gate vs continue).

### Previous story intelligence (3.4)

- **`flags["needs_human_review"]`:** OR semantics — **True** if **any** classification row has **`needs_human_review`**; **`node_classify`** merges with `(state.get("flags") or {})` so **`flags=None`** does not crash. **Do not** contradict this in the router.  
- **Classification rows** carry **`source_id`**, **`item_url`**, **`needs_human_review`** per row; routing may rely on aggregate flag only for MVP.  
- **Deferred explicitly for 3.5:** tighten **`classifications`** assertions in shell/scout-normalize tests; **`source_id`** UUID-vs-str contract test.  
- **Logging:** `extra={"event": "...", "ctx": {...}}` with **`run_id`** on new events.  
- **Topology before this story:** `START → scout → normalize → classify → END` in [`src/sentinel_prism/graph/graph.py`](../../src/sentinel_prism/graph/graph.py).

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **LangGraph API** | Use **`StateGraph.add_conditional_edges`**. **Human-in-the-loop:** `from langgraph.types import interrupt` — requires checkpointer; **`ainvoke`** may return partial state with **`__interrupt__`** list containing **`Interrupt(value=...)`** (verify in tests under **langgraph 1.1.6**). |
| **FR16 / FR37** | Branch on classification-derived policy; **UX** expects honest uncertainty and a real review path ([Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — trust / review queue themes]). |
| **FR38** | Same **`run_id`** across branches; retry without new correlation id is **Story 3.6** — do not implement retry policy here. |
| **No Epic 4 API yet** | Resume/`POST /runs/{id}/resume` is future; this story only needs **interrupt surface** or **placeholder flag** + logs. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR16 | Route to human review when policy says so (confidence/severity handled in classify; **this story** enforces **graph branch**). |
| FR37 | Workflow branches on classification / confidence **signal** (`needs_human_review` flag). |
| FR38 | Preserve **`run_id`** as correlation id on both branches. |
| FR36 | Single compiled graph; partial state updates only. |
| NFR8 | Structured logs with **`run_id`** on review path. |

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **§3.1** | Conditional edges; human-in-the-loop via **interrupt** or awaiting state. |
| **§3.4** | Reference topology: classify → **human_review_gate** vs continue toward eventual **brief** ( **`END`** for now). |
| **§5 Graph layout** | New file under **`graph/nodes/`**; **`graph.py`** owns topology. |
| **§6 Boundaries** | No **`services` → `graph`** imports. |

### Library / framework requirements

| Library | Version (pinned) | Notes |
| --- | --- | --- |
| **langgraph** | 1.1.6 | `add_conditional_edges`, `interrupt`, compiled graph with checkpointer. |
| **langgraph-checkpoint** | 4.0.2 | Memory / dev checkpointer already in use. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/graph.py` | Add conditional edges; register review node; continue → `END`. |
| `src/sentinel_prism/graph/nodes/human_review_gate.py` | **New** — interrupt or placeholder implementation. |
| `src/sentinel_prism/graph/nodes/__init__.py` | Export if pattern matches prior stories. |
| `tests/test_graph_*.py` | Branch coverage + contract test. |

### Testing requirements

- Prefer **async** `ainvoke` patterns already used in **`tests/test_graph_shell.py`** / **`test_graph_classify.py`**.  
- Use **`caplog`** for structured log assertions when the review node logs.  
- For interrupt: assert **`result.get("__interrupt__")`** and **`run_id`** inside **`Interrupt.value`** (or equivalent attribute access on returned objects).

### Project structure notes

- Package root: `src/sentinel_prism/`.  
- No **`project-context.md`** in repo; Architecture + epics + implementation artifacts are authoritative.

### References

- `_bmad-output/planning-artifacts/epics.md` — Story 3.5  
- `_bmad-output/planning-artifacts/architecture.md` — §3.1, §3.4, §5–§6  
- `_bmad-output/planning-artifacts/prd.md` — FR16, FR37, FR38  
- `_bmad-output/implementation-artifacts/3-4-classify-node-with-rules-llm-and-structured-output.md` — `needs_human_review`, flags, classifications schema  
- `src/sentinel_prism/graph/state.py` — `AgentState`, `flags`  
- `src/sentinel_prism/graph/nodes/classify.py` — upstream behavior  

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented **`route_after_classify`** in **`graph/routing.py`** — single source of truth **`flags["needs_human_review"]`** (documented; no scan of **`classifications`**).
- Added **`node_human_review_gate`** with **`langgraph.types.interrupt`**, structured log **`graph_human_review_gate_interrupt`**, payload includes **`run_id`**, **`step`**, optional **`source_id`**.
- **`graph.py`:** **`add_conditional_edges`** from **`classify`** to **`human_review_gate`** or **`END`**; gate edges to **`END`** for post-resume completion.
- Tests: **`tests/test_graph_conditional_edges.py`** (routing unit, continue vs interrupt paths, UUID **`source_id`** coercion via **`node_classify`**); tightened **`test_graph_shell`** / **`test_graph_scout_normalize`** classification assertions.
- Full suite: **114 passed**, 9 skipped; **`verify_imports.py`** extended for **`routing`** and **`human_review_gate`**.

### File List

- `src/sentinel_prism/graph/routing.py`
- `src/sentinel_prism/graph/nodes/human_review_gate.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/nodes/__init__.py`
- `tests/test_graph_conditional_edges.py`
- `tests/test_graph_shell.py`
- `tests/test_graph_scout_normalize.py`
- `verify_imports.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-5-conditional-edges-for-review-vs-continue.md`

## Git intelligence summary

Recent `main` commits center on Epic 2–3 ingestion and graph scaffolding (`ce8ca12` and earlier). For this story, mirror **graph test** style from **`tests/test_graph_shell.py`** and **classify** patterns from Story 3.4 files.

## Latest technical information

- **LangGraph 1.1.6:** `interrupt(value)` pauses execution; client resumes with **`Command`**; node may re-run from entry on resume — design accordingly ([in-tree dependency: `requirements.txt`](../../requirements.txt)).  
- **`ainvoke` behavior:** With interrupt, the returned state can include **`__interrupt__`** rather than raising **`GraphInterrupt`** — assert on the return value in tests (validated in environment).

## Project context reference

No `project-context.md` found; use Architecture + epic + prior story artifacts.

## Change Log

- 2026-04-17 — Implemented Story 3.5 (conditional edges, human review interrupt, tests); story and sprint status set to **review**.

## Story completion status

**done** — Code review complete (2026-04-17): 5 patch findings applied, 6 deferred to `deferred-work.md`, 12 dismissed as noise. Full suite: 115 passed, 9 skipped.

### Saved questions / clarifications (non-blocking)

- Whether resume in Epic 4 will **re-enter `classify`** vs **skip to `brief`** after human override (affects idempotency of **`human_review_gate`**).  
- Whether **`needs_human_review`** on **`flags`** should ever diverge from a re-scan of **`classifications`** (recommend **single source of truth**).
