# Story 3.4: Classify node with rules + LLM and structured output

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **severity, impact, urgency, rationale, and confidence per normalized update** (rules + LLM with structured output),
so that **downstream routing can act** (**FR11**–**FR15**, **NFR2**).

## Acceptance Criteria

1. **`classify` node**  
   **Given** `normalized_updates` on `AgentState` (possibly empty; each dict is checkpoint-safe and matches the `normalized_update_to_state_dict` contract from Story 3.3)  
   **When** the `classify` node runs  
   **Then** for each normalized update it produces **one** append-only dict on **`classifications`** (reducer `operator.add`) that is JSON-serializable and includes at minimum:  
   - **`in_scope`** (bool) — from **deterministic rules** before any LLM call (**FR11**)  
   - **`severity`** — one of `critical` | `high` | `medium` | `low` when `in_scope` is true; when `in_scope` is false use `null` / omit per documented convention (**FR12**)  
   - **`impact_categories`** — list of strings (may be empty when not in scope); values aligned with PRD examples (safety, labeling, manufacturing, deadlines, …) (**FR13**)  
   - **`urgency`** — one of `immediate` | `time_bound` | `informational` when in scope; otherwise `null` / omit per documented convention (**FR14**)  
   - **`rationale`** (string) and **`confidence`** (float in **[0, 1]**) (**FR15**)  
   - **Correlation keys:** **`source_id`** and **`item_url`** copied from the input normalized dict so downstream nodes can join without relying on list index  
   - **`needs_human_review`** (bool) — set from policy (e.g. low confidence or ambiguous severity); Story 3.5 will route on this; for this story it is enough to **set the flag** on the classification dict and optionally mirror into `flags["needs_human_review"]` when **any** item requires review (document the chosen rule in Dev Notes)

2. **Rules + LLM split**  
   **Given** a normalized update  
   **When** rules mark **`in_scope=false`**  
   **Then** the node **does not** call the LLM for that item (append a classification with `in_scope=false`, minimal fields, and a short machine rationale e.g. `rules_rejected`)  
   **When** rules mark **`in_scope=true`**  
   **Then** the node calls an **async** LLM classification path that returns a **Pydantic-validated** structured object (see Dev Notes — `with_structured_output` or equivalent) and maps it into the dict shape above

3. **Audit / trace logging**  
   **Given** an LLM call occurs for an item  
   **When** the call completes (success or handled failure)  
   **Then** at least one structured log line includes **`run_id`** in `extra` (`event` + `ctx`, same pattern as Stories 3.1–3.3)  
   **And** the log or merged **`llm_trace`** records **`model_id`** (or logical model name) and **`prompt_version`** (string you bump when prompts change) so operators can reproduce behavior  

4. **Graph topology**  
   **Given** the graph today is **`START → scout → normalize → END`**  
   **When** this story lands  
   **Then** the regulatory pipeline is **`START → scout → normalize → classify → END`**  
   **And** `compile_regulatory_pipeline_graph` still supports `MemorySaver` / `thread_id == str(run_id)` round-trip tests  

5. **Architecture boundaries**  
   **Given** Architecture §5–§6  
   **When** implemented  
   **Then** **`services/`** does not import **`graph/`**  
   **And** `node_classify` lives in **`src/sentinel_prism/graph/nodes/classify.py`**  
   **And** LLM + rule logic invoked from the node is implemented under **`src/sentinel_prism/services/llm/`** (or submodules), behind a small **protocol** or factory for test injection

6. **Tests**  
   **Given** CI without mandatory live LLM providers  
   **When** tests run  
   **Then** unit tests cover **rules-only** path (no LLM), **in-scope + stub LLM** path (returns fixed structured output), and error handling (LLM raises → `errors` channel + structured log, no silent swallow)  
   **And** an integration-style test compiles the graph with **`MemorySaver`**, runs **`scout → normalize → classify`** with mocks at connector/normalizer/LLM boundaries as appropriate, and asserts **`classifications`** length matches **`normalized_updates`** for a synthetic multi-item run

7. **Performance note (NFR2)**  
   **Given** NFR2 (single update classify P95 **≤2 minutes** when model is available)  
   **When** designing the LLM call  
   **Then** avoid unnecessary serial round-trips (e.g. one structured call per in-scope item in MVP); document any intentional batching deferral

## Tasks / Subtasks

- [x] **Classification schema + state mapping (AC: #1, #2)** — `src/sentinel_prism/services/llm/`  
  - [x] Define a **Pydantic v2** model (e.g. `StructuredClassification`) with fields matching AC #1 for the LLM path; use **enums** or `Literal[...]` for severity / urgency to match FR12/FR14 vocabulary.  
  - [x] Add `classification_to_state_dict(...)` (or equivalent) that emits **checkpoint-safe** dicts for `AgentState.classifications` (only JSON-native values; no `datetime` objects).  
  - [x] Document how **out-of-scope** rows are represented (nulls vs omission) and keep it consistent in tests.

- [x] **Rules engine (AC: #1–#2, FR11)** — `src/sentinel_prism/services/llm/rules.py` (name flexible)  
  - [x] Implement **pure** functions: input = normalized update dict (+ optional tenant/source metadata later); output = `RuleOutcome(in_scope: bool, reasons: list[str])`.  
  - [x] Start with **explicit, reviewable** MVP rules (e.g. jurisdiction allowlist, `document_type`, keyword hints) — avoid hidden magic strings; constants at module top or small config object.  
  - [x] Unit tests: at least **one** clearly in-scope and **one** clearly out-of-scope fixture.

- [x] **LLM adapter + protocol (AC: #2, #3, #5)** — `src/sentinel_prism/services/llm/`  
  - [x] Define a protocol, e.g. `ClassificationLLM`, with an **async** method accepting normalized context (title, summary, body_snippet, jurisdiction, …) and returning `StructuredClassification`.  
  - [x] Provide **`StubClassificationLLM`** for tests/dev that returns deterministic structured output without network.  
  - [x] Provide optional **`build_classification_llm()`** (or env-gated factory) that wires a real `BaseChatModel` with **`with_structured_output(StructuredClassification)`** when provider deps + API keys exist; **do not** require live keys for default CI — gate integration tests.  
  - [x] Centralize **prompt text** + **`PROMPT_VERSION`** constant; include **`model_id`** string from settings/env default.

- [x] **Classify node (AC: #1–#5)** — `src/sentinel_prism/graph/nodes/classify.py`  
  - [x] Async `node_classify`: guard **`run_id`** same pattern as `node_scout` / `node_normalize` (empty/whitespace → `errors`, no crash).  
  - [x] Iterate **`state.get("normalized_updates") or []`**; verify elements are **mappings** (if not, append `errors` and skip or fail per Architecture §5 — choose one and document).  
  - [x] For each item: run rules → optionally LLM → append one dict to **`classifications`** via partial return.  
  - [x] On LLM failure: append **`errors`** with `step="classify"`, `message` / `error_class` / `detail`; log `graph_classify_llm_error` (or similar) with `run_id`.  
  - [x] Merge **`llm_trace`**: last-writer-wins dict update with latest `model_id`, `prompt_version`, optional token metadata — **or** rely on structured logs only if you document why `llm_trace` stays unset in MVP.

- [x] **Graph wiring (AC: #4)** — `src/sentinel_prism/graph/graph.py`  
  - [x] Register **`classify`** node; edges **`normalize → classify → END`**.  
  - [x] Export in `graph/nodes/__init__.py` if consistent with 3.3.

- [x] **Settings / env (AC: #3)** — `src/sentinel_prism/` (follow existing config patterns)  
  - [x] Add minimal settings for **default model name** and **prompt version** if not already present; avoid storing secrets in code.

- [x] **Tests (AC: #6, #7)** — `tests/test_graph_classify.py` (and unit tests under `tests/` for rules + llm adapter)  
  - [x] Graph test: mock **`node_scout` / `node_normalize`** outputs **or** inject stub LLM via factory monkeypatch — **no** live network.  
  - [x] Assert **`classifications`** keys and join keys (`source_id`, `item_url`).  
  - [x] Update `verify_imports.py` if new modules need smoke coverage.

### Review Findings

- [x] [Review][Patch] Emit placeholder classification row on LLM error to preserve AC #1 one-to-one guarantee [src/sentinel_prism/graph/nodes/classify.py; tests/test_graph_classify.py] — On `except Exception` around `llm.classify(...)`, append an `errors` entry **and** a classification row with `in_scope=True, severity=None, urgency=None, impact_categories=[], rationale="llm_error", confidence=0.0, needs_human_review=True`; flip `any_review=True`. Test `test_node_classify_llm_error_emits_placeholder_row` asserts all fields. Fixed via new `classification_dict_for_llm_error` helper.
- [x] [Review][Patch] Document permissive-jurisdiction policy and add `None`/empty test [src/sentinel_prism/services/llm/rules.py; tests/test_llm_classification_rules.py] — Module docstring now documents the policy ("allowlist only rejects known-but-disallowed regions"). Two new test fixtures cover `jurisdiction=None` and `jurisdiction=""` → `in_scope=True`.
- [x] [Review][Patch] `model_id` in logs / `llm_trace` drifts from the actual model used on the OpenAI path [src/sentinel_prism/services/llm/classification.py; src/sentinel_prism/graph/nodes/classify.py] — Added `model_id` attribute to `ClassificationLLM` protocol and both impls. `StubClassificationLLM` takes `model_id=settings.model_id` from env; `LangChainStructuredClassificationLlm.model_id` is the actual `SENTINEL_OPENAI_MODEL` passed to `ChatOpenAI`. `node_classify` reads `getattr(llm, "model_id", None) or settings.model_id` so logs and trace reflect the real call.
- [x] [Review][Patch] `needs_human_review` policy rule extracted, documented, tested [src/sentinel_prism/services/llm/classification.py] — `LOW_CONFIDENCE_THRESHOLD = 0.5` module constant + module docstring. Three new tests cover low-confidence, critical-severity, and benign branches.
- [x] [Review][Patch] `state.get("flags", {}).get(...)` defensive guard aligned [src/sentinel_prism/graph/nodes/classify.py] — Now `bool((state.get("flags") or {}).get("needs_human_review"))`. Test `test_node_classify_handles_flags_set_to_none` pins the behavior.
- [x] [Review][Patch] `assert llm is not None` replaced with explicit `ValueError` [src/sentinel_prism/services/llm/classification.py] — Safe under `python -O`.
- [x] [Review][Patch] `impact_categories` vocabulary enumerated in prompt [src/sentinel_prism/services/llm/classification.py] — `IMPACT_CATEGORIES_VOCAB` tuple (safety, labeling, manufacturing, deadlines, reporting, licensing, pricing, other) now interpolated into `CLASSIFICATION_SYSTEM_PROMPT`. Type left as `list[str]` for forward-compat; bucketing deferred to post-MVP aggregation pass.
- [x] [Review][Patch] Dead `PROMPT_VERSION` module constant removed [src/sentinel_prism/services/llm/classification.py] — Single source of truth is `ClassificationLlmSettings.prompt_version`.
- [x] [Review][Patch] `@lru_cache` dropped from `get_classification_llm_settings` [src/sentinel_prism/services/llm/settings.py] — Settings now read env fresh on each call; `monkeypatch.setenv` works as expected without cache-clear dance.
- [x] [Review][Patch] `verify_imports.py` extended [verify_imports.py] — Now smoke-imports `services.llm.rules` and `services.llm.settings`.
- [x] [Review][Patch] Non-`Mapping` branch test added [tests/test_graph_classify.py::test_node_classify_skips_non_mapping_items] — Injects a `str` element, asserts `errors[0].error_class == "TypeError"` and no classification row.
- [x] [Review][Patch] `llm_trace` is only emitted when an LLM call was actually attempted [src/sentinel_prism/graph/nodes/classify.py] — `status="ok"` when ≥1 call succeeded, `status="all_failed"` when every call errored; pure validation errors or empty inputs emit no trace.
- [x] [Review][Defer] Prompt-injection surface: `format_classification_user_message` concatenates untrusted title/summary/body [src/sentinel_prism/services/llm/classification.py:37–46] — deferred, hardening for Story 3.6/3.7 scope.
- [x] [Review][Defer] `JURISDICTION_ALLOWLIST` hard-coded; config-driven allowlist punted [src/sentinel_prism/services/llm/rules.py:9–11] — deferred per inline comment, future story.
- [x] [Review][Defer] `ChatOpenAI` instantiated per pipeline invocation (no reuse) [src/sentinel_prism/services/llm/classification.py:184–186] — deferred, perf optimization.
- [x] [Review][Defer] `build_classification_llm` only catches `ImportError`, not invalid-key / init errors [src/sentinel_prism/services/llm/classification.py:171–186] — deferred, integration-test scope.
- [x] [Review][Defer] `err_accum` rows do not carry `item_url` / `source_id` for post-hoc correlation [src/sentinel_prism/graph/nodes/classify.py:89–96] — deferred, observability improvement.
- [x] [Review][Defer] Autouse fixture `tests/conftest.py` force-clears `OPENAI_API_KEY` with no opt-out knob — deferred, will revisit when a live integration test lands.
- [x] [Review][Defer] `source_id` UUID-vs-str coercion not contract-tested [src/sentinel_prism/services/llm/classification.py:64] — deferred, add contract test alongside Story 3.5.
- [x] [Review][Defer] `classifications` assertions in `tests/test_graph_scout_normalize.py` / `tests/test_graph_shell.py` are shape-only — deferred, tighten when Story 3.5 adds conditional edges.
- [x] [Review][Defer] System prompt has no few-shot examples or severity/urgency rubric [src/sentinel_prism/services/llm/classification.py:20–22] — deferred, prompt-engineering pass post-MVP.
- [x] [Review][Defer] Confidence boundary at exactly `0.5` is ambiguous (`< 0.5` means 0.5 is NOT flagged) — deferred, revisit with policy tuning.
- [x] [Review][Defer] Prompt-injection surface: `format_classification_user_message` concatenates untrusted title/summary/body [src/sentinel_prism/services/llm/classification.py:37–46] — deferred, hardening for Story 3.6/3.7 scope.
- [x] [Review][Defer] `JURISDICTION_ALLOWLIST` hard-coded; config-driven allowlist punted [src/sentinel_prism/services/llm/rules.py:9–11] — deferred per inline comment, future story.
- [x] [Review][Defer] `ChatOpenAI` instantiated per pipeline invocation (no reuse) [src/sentinel_prism/services/llm/classification.py:184–186] — deferred, perf optimization.
- [x] [Review][Defer] `build_classification_llm` only catches `ImportError`, not invalid-key / init errors [src/sentinel_prism/services/llm/classification.py:171–186] — deferred, integration-test scope.
- [x] [Review][Defer] `err_accum` rows do not carry `item_url` / `source_id` for post-hoc correlation [src/sentinel_prism/graph/nodes/classify.py:89–96] — deferred, observability improvement.
- [x] [Review][Defer] Autouse fixture `tests/conftest.py` force-clears `OPENAI_API_KEY` with no opt-out knob — deferred, will revisit when a live integration test lands.
- [x] [Review][Defer] `source_id` UUID-vs-str coercion not contract-tested [src/sentinel_prism/services/llm/classification.py:64] — deferred, add contract test alongside Story 3.5.
- [x] [Review][Defer] `classifications` assertions in `tests/test_graph_scout_normalize.py` / `tests/test_graph_shell.py` are shape-only — deferred, tighten when Story 3.5 adds conditional edges.
- [x] [Review][Defer] System prompt has no few-shot examples or severity/urgency rubric [src/sentinel_prism/services/llm/classification.py:20–22] — deferred, prompt-engineering pass post-MVP.
- [x] [Review][Defer] Confidence boundary at exactly `0.5` is ambiguous (`< 0.5` means 0.5 is NOT flagged) — deferred, revisit with policy tuning.

### Intentionally out of scope (defer)

- **Conditional edges** / `human_review_gate` (**Story 3.5**) — graph remains linear to **`END`**.  
- **Retry policy** on LLM (**Story 3.6**).  
- **Tavily / web search** (**Story 3.7**).  
- **Persisting classifications** to PostgreSQL domain tables + **audit_events** (**Story 3.8** / Epic 4) unless a minimal write is already required by an existing migration — **if** no table exists, keep results **in graph state only** for this story.

## Dev Notes

### Epic 3 context

- Epic goal: **StateGraph** with **scout → normalize → classify → …**; shared **`AgentState`**; checkpointer-ready state (**`epics.md`**, Epic 3 header).  
- This story adds **Impact Analyst** node per Architecture §3.3–§3.4.

### Previous story intelligence (3.3)

- **Topology:** extend **`START → scout → normalize → END`** with **`classify`**; keep **`thread_id == str(run_id)`**.  
- **Normalized dict shape:** use fields from `normalized_update_to_state_dict` in `services/ingestion/normalize.py` — do not invent parallel field names.  
- **Logging:** `extra={"event": "...", "ctx": {...}}` must include **`run_id`** on new events.  
- **Errors:** append to **`errors`** with stable keys (`step`, `message`, optional `detail`, `error_class`); DB/session failures in prior nodes use `graph_*_db_error` patterns — mirror for classify LLM errors.  
- **List reducers:** `classifications` already **`Annotated[..., operator.add]`** in `state.py` — append **one dict per normalized item** on the happy path.  
- **Deferred from 3.3:** redundant source row lookups / TOCTOU — acceptable to continue loading context from the normalized dict for MVP; optional small optimization later.

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Dependency direction** | `graph/nodes/classify.py` → `services/llm/*`; never `services` → `graph`. |
| **Structured output** | Prefer LangChain **`BaseChatModel.with_structured_output(MyPydanticModel)`** (langchain-core 1.2.x already pinned) — see [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output) and [API reference](https://reference.langchain.com/python/langchain-core/language_models/chat_models/BaseChatModel/with_structured_output). |
| **No CI hard-dependency on OpenAI** | Use **stub** LLM by default in tests; optional `langchain-openai` (or another provider package) may be added **only** if you implement a real provider path — gate imports so `verify_imports` / unit tests pass without API keys. |
| **`flags` reducer** | `AgentState.flags` is a plain dict (last-writer-wins). **`node_classify`** merges prior flags and sets **`needs_human_review`** to **True** if **any** classification row has **`needs_human_review`** (OR semantics). Prior-state read uses `state.get("flags") or {}` so `flags=None` does not crash. |
| **`needs_human_review` policy** | For in-scope rows: `needs_human_review = llm.confidence < LOW_CONFIDENCE_THRESHOLD (0.5) or llm.severity == "critical"`. Constant lives in `services/llm/classification.py`. Boundary at exactly `0.5` is deliberately NOT flagged — revisit after real scores are observed. |
| **Jurisdiction rule (permissive)** | Missing/empty `jurisdiction` → `in_scope=True`. Rationale: allowlist rejects **known-but-disallowed** regions; items with *unknown* provenance still reach the LLM, which will set low confidence or flag review when context is insufficient. Rationale also documented in `services/llm/rules.py` module docstring. |
| **LLM error → placeholder row** | On `llm.classify` exception, `node_classify` appends BOTH an `errors` entry AND a placeholder classification with `in_scope=True, severity=null, rationale="llm_error", confidence=0.0, needs_human_review=True`, preserving the AC #1 1:1 `normalized_updates → classifications` invariant. |
| **`model_id` source of truth** | `ClassificationLLM` protocol exposes `model_id`. Logs / `llm_trace` prefer `llm.model_id` over `settings.model_id`, so the real OpenAI model name (e.g. `gpt-4o-mini`) is recorded even when `SENTINEL_CLASSIFICATION_MODEL_ID` is unset or stale. |
| **`llm_trace` emission** | Only written when at least one LLM call was **attempted**. `status="ok"` when ≥1 call succeeded, `status="all_failed"` when every in-scope call errored, absent when only validation errors or empty input. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR11–FR15 | Schema fields on each classification dict as in AC #1. |
| NFR2 | One LLM structured call per in-scope item in MVP; document tradeoff. |
| NFR8 | Structured logs with **`run_id`** on classify path (**AC #3**). |
| FR36 | Single compiled graph; partial returns only. |
| Architecture §3.3 | Node id **`classify`**; responsibilities: rules + LLM + `needs_human_review`. |

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Layout | `graph/nodes/classify.py`; `services/llm/*.py`; update `graph/graph.py`. |
| State | Use existing `classifications`, `errors`, `flags`, `llm_trace` channels — extend only if unavoidable. |
| Testing | Mocked LLM + graph compile with **MemorySaver**; no mandatory live network. |

### Library / framework requirements

| Library | Notes |
| --- | --- |
| **langgraph** 1.1.6 | Async node returning `dict` partial state. |
| **langchain-core** 1.2.31 | `BaseChatModel`, `with_structured_output`, messages. |
| **Pydantic v2** | Available transitively via FastAPI/langchain — prefer explicit models in `services/llm`. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/nodes/classify.py` | **New** — `node_classify`. |
| `src/sentinel_prism/graph/graph.py` | Add node + edges. |
| `src/sentinel_prism/graph/nodes/__init__.py` | Export if needed. |
| `src/sentinel_prism/services/llm/__init__.py` | Replace placeholder; export public factories/protocols. |
| `src/sentinel_prism/services/llm/classification.py` | Schema + protocol + stub (suggested split). |
| `src/sentinel_prism/services/llm/rules.py` | MVP rules (suggested). |
| `tests/test_graph_classify.py` | **New** integration-style coverage. |
| `verify_imports.py` | Extend if new entry points warrant smoke import. |

### Testing requirements

- Use **`pytest-asyncio`** patterns already in repo for `ainvoke`.  
- Use **`caplog`** to assert **`run_id`** appears for classify events when LLM runs.  
- Cover **empty** `normalized_updates` (no LLM calls; optional single log at DEBUG/INFO).

### Project structure notes

- Package root: `src/sentinel_prism/`.  
- No `project-context.md` — Architecture + epic + Stories 3.1–3.3 + this file are authoritative.

### References

- `_bmad-output/planning-artifacts/epics.md` — Epic 3, Story 3.4  
- `_bmad-output/planning-artifacts/architecture.md` — §3.2–§3.4, §5 (layout), Impact Analyst row  
- `_bmad-output/planning-artifacts/prd.md` — FR11–FR15, NFR2  
- `_bmad-output/implementation-artifacts/3-3-implement-scout-and-normalize-nodes-wired-in-graph.md` — graph + normalization contracts  
- `src/sentinel_prism/graph/state.py` — `classifications`, `llm_trace`, `flags`  
- `src/sentinel_prism/services/ingestion/normalize.py` — `normalized_update_to_state_dict`  

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented **`classification_dict_for_state`**, **`classification_dict_for_llm_error`**, **`StructuredClassification`**, **`ClassificationLLM`** protocol (with `model_id` attribute), **`StubClassificationLLM`**, optional **`LangChainStructuredClassificationLlm`** when `OPENAI_API_KEY` and `langchain-openai` are present; default CI uses stub via **`tests/conftest.py`** clearing the API key.
- **`evaluate_classification_rules`** (jurisdiction allowlist with region prefix, excluded `document_type`s, minimum text signal); **permissive** for `None`/empty jurisdiction — documented in module docstring.
- **`node_classify`**: mapping validation, rules-before-LLM, **`graph_classify_llm_done`** / **`graph_classify_llm_error`** logs with **`run_id`**, effective **`model_id`** (from LLM, not stale env), **`prompt_version`**.
- Graph topology **`normalize → classify → END`**; **`flags["needs_human_review"]`** OR’d when any row needs review (`flags=None` safe).
- **On LLM exception:** node appends BOTH an `errors` entry AND a placeholder classification row (`in_scope=True, severity=null, rationale="llm_error", confidence=0.0, needs_human_review=True`). Preserves AC #1 1:1 invariant: `len(classifications) == len(normalized_updates)` on the happy path and on partial-LLM-failure path.
- **`llm_trace`** only written when an LLM call was attempted: `status="ok"` on ≥1 success, `status="all_failed"` if every call errored.
- **`needs_human_review` policy:** `confidence < LOW_CONFIDENCE_THRESHOLD (0.5) or severity == "critical"` — constant + tests for both branches + benign case.
- Post-review: full suite **110 passed**, 9 skipped; `verify_imports.py` smoke-covers `rules` + `settings`; NFR2: one structured LLM call per in-scope item in MVP.

### File List

- `src/sentinel_prism/services/llm/settings.py`
- `src/sentinel_prism/services/llm/rules.py`
- `src/sentinel_prism/services/llm/classification.py`
- `src/sentinel_prism/services/llm/__init__.py`
- `src/sentinel_prism/graph/nodes/classify.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/nodes/__init__.py`
- `tests/conftest.py`
- `tests/test_llm_classification_rules.py`
- `tests/test_graph_classify.py`
- `tests/test_graph_shell.py`
- `tests/test_graph_scout_normalize.py`
- `verify_imports.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-4-classify-node-with-rules-llm-and-structured-output.md`

## Git intelligence summary

Latest `main` includes Epic 3 scout/normalize pipeline (`ce8ca12`). Mirror its **logging**, **error dict shape**, **async node** style, and **graph test** patterns when adding `classify`.

## Latest technical information

- **LangGraph 1.1.6** / **langchain-core 1.2.31** (pinned in `requirements.txt`): use async nodes and `with_structured_output` for Pydantic models.  
- Prefer **provider-specific** packages (e.g. OpenAI) only behind optional extras or lazy imports so default test runs stay lightweight.

## Project context reference

No `project-context.md` found; use Architecture + epic + prior implementation artifacts.

## Change Log

- 2026-04-17 — Story created via create-story workflow; status `ready-for-dev`.
- 2026-04-18 — Implemented classify node, LLM services, tests; status `review`.
- 2026-04-17 — Code review completed; 2 decisions resolved, 12 patches applied, 10 deferred; full suite 110 passed / 9 skipped; status `done`.

## Story completion status

**done** — Implementation + code review complete; full test suite green (110 passed, 9 skipped). 12 patches applied, 10 items deferred to later stories (see `deferred-work.md`).

### Saved questions / clarifications (non-blocking)

- Whether **batch multi-item** LLM calls are worth the complexity vs **one call per item** for MVP audit clarity.  
- Whether **`flags["needs_human_review"]`** should **OR** across items or reflect **only the last** item — pick one and test it.
