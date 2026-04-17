# Story 3.6: Retry policy on transient node failures

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **retries on defined transient failures without minting a new run**,
so that **audit correlation holds across flaky LLM/HTTP steps** (**FR38**, **NFR8**).

## Acceptance Criteria

1. **Transient failure → retry, same correlation**  
   **Given** a **transient** failure during a pipeline node (e.g. LLM timeout, rate limit, connection reset, or retryable HTTP/5xx as defined below)  
   **When** the graph executes  
   **Then** the system **retries** the failing work according to a **declared max attempts** policy  
   **And** **`run_id`** in state and structured logs remains **unchanged** for the entire invocation (no new run id on retry; **FR38**)

2. **Max attempts enforced**  
   **Given** retries are configured with **`max_attempts = N`** (project-chosen N ≥ 2, document in code/settings)  
   **When** the transient failure persists across attempts  
   **Then** the run **stops retrying** after **N** attempts and surfaces failure in a **predictable** way (raised error **or** structured `errors` entries—pick one approach per node and document it; do not silently drop the failure)

3. **Non-transient failures are not endlessly retried**  
   **Given** a **non-retryable** condition (e.g. validation / bad input / deterministic client errors your policy classifies as permanent)  
   **When** the node runs  
   **Then** behavior matches existing patterns (**errors** channel and continue, or fail fast) **without** graph-level retry storms  

4. **Architecture alignment**  
   **Given** [Architecture section 3.1 item 5](_bmad-output/planning-artifacts/architecture.md) (retries / loops + optional **`RetryPolicy`**)  
   **When** implemented  
   **Then** prefer **`langgraph.types.RetryPolicy`** on **`StateGraph.add_node(..., retry_policy=...)`** **where the node callable actually raises** on the failures you want retried  
   **And** alternatively (or additionally) use a **small inner retry loop** with backoff for **per-item** LLM calls if full-node retries are too expensive—**still** enforce the same **max attempts** and log **`run_id`** each time

5. **No duplicate state from retries**  
   **Given** list channels on **`AgentState`** use **`operator.add`** (e.g. **`classifications`**, **`errors`**)  
   **When** a node is retried (LangGraph **or** inner loop)  
   **Then** a **single logical outcome** is recorded—no **double-append** of the same logical rows because a partial return was committed before a retry (if using node-level **`RetryPolicy`**, ensure failed attempts **do not** `return` partial merges mid-flight; **raise** or complete cleanly)

6. **Tests**  
   **Given** CI without live providers  
   **When** tests run  
   **Then** add focused tests that:  
   - Simulate a **transient** failure (e.g. stub LLM or patch) that succeeds on a later attempt  
   - Assert **final** state is correct and **`run_id`** is unchanged across the invocation  
   - Assert retry **stops** at **max attempts** when the failure never resolves

7. **Observability**  
   **Given** retries occur  
   **When** operators inspect logs  
   **Then** structured logs include **`run_id`**, **`step`**, attempt index or clear “retry” event naming (extend existing `extra={"event": "...", "ctx": {...}}` pattern from Stories 3.3–3.5)

## Tasks / Subtasks

- [x] **Classify / LLM path (AC: #1–#5, #7)** — `src/sentinel_prism/graph/nodes/classify.py`, possibly `src/sentinel_prism/services/llm/`  
  - [x] **Critical:** Today **`node_classify`** catches LLM **`Exception`** and emits degraded rows—**no exception reaches LangGraph**, so **`add_node(..., retry_policy=...)` alone does nothing** until **transient** errors **propagate** (re-raise) **or** you implement **inner** retries around `llm.classify`. Choose one strategy and document it in Dev Notes.  
  - [x] If using **node-level `RetryPolicy`**: on **transient** subclasses only, **raise** after optional logging; keep **non-transient** behavior (degraded row / `errors`) as today.  
  - [x] If using **inner retry**: backoff + jitter consistent with **`RetryPolicy`** defaults (`initial_interval`, `backoff_factor`, `max_interval`, `jitter`) where practical.

- [x] **Graph wiring (AC: #4)** — `src/sentinel_prism/graph/graph.py`  
  - [x] Apply **`RetryPolicy`** to **`classify`** (required) and optionally **`scout`** / **`normalize`** **only if** those nodes **raise** on the failures you intend to retry—**do not** add useless policies on nodes that only return `{"errors": [...]}`.

- [x] **Policy helper (AC: #3)** — e.g. `src/sentinel_prism/graph/retry.py` or under `services/llm/`  
  - [x] Centralize **`max_attempts`**, backoff constants, and **`retry_on`** predicate (LangGraph **`RetryPolicy(retry_on=...)`** supports a callable). Reuse / align with **`langgraph.types.default_retry_on`** semantics where possible—note it treats many **`RuntimeError`** subclasses as **non-retryable**; confirm your LLM client’s transient errors are actually retryable under your predicate.

- [x] **Settings (optional)** — `src/sentinel_prism/services/llm/settings.py` or env  
  - [x] Expose **`CLASSIFICATION_MAX_ATTEMPTS`** (or graph-wide **`NODE_RETRY_MAX_ATTEMPTS`**) with safe defaults; document in `.env.example` if added.

- [x] **Tests (AC: #6)** — `tests/test_graph_*` or new `tests/test_graph_retry_policy.py`  
  - [x] Use **`MemorySaver`** / existing **`dev_memory_checkpointer`** and **`thread_id == str(run_id)`** where applicable (Story 3.5 pattern).  
  - [x] Cover **success-after-retry** and **exhausted retries**.

- [x] **Imports smoke (if new modules)** — extend `verify_imports.py` only if new public entry points warrant it.

### Review Findings

- [x] [Review][Decision] Full-node retry recomputes successful items on each attempt [src/sentinel_prism/graph/nodes/classify.py:75-97] — **resolved (2026-04-17):** accept full-node retry as designed; trade-off documented in Completion Notes. Per-item inner retry deferred to a future story if cost becomes material.
- [x] [Review][Patch] `llm_trace.status="all_failed"` misleading on partial failure [src/sentinel_prism/graph/nodes/classify.py:132-147] — **fixed:** added `partial` status for mixed success/error runs.
- [x] [Review][Patch] `llm_trace` omitted entirely when no LLM call attempted [src/sentinel_prism/graph/nodes/classify.py:130-147] — **fixed:** always emit `llm_trace` (after the empty-updates early return) with a stable `status` field; new `no_attempt` status covers the all-non-Mapping / all-out-of-scope case. Existing Story 3.4 test updated.
- [x] [Review][Patch] `@lru_cache` removed from `get_classification_llm_settings()` — undocumented [src/sentinel_prism/services/llm/settings.py] — **fixed (doc-only):** rationale added to Completion Notes; re-caching deferred until profiling justifies adding a `cache_clear()` test fixture.
- [x] [Review][Patch] Settings silent clamp + silent `ValueError` fallback [src/sentinel_prism/services/llm/settings.py:268-283] — **fixed:** WARNING log emitted on both parse error and clamp; named constants exposed for tests.
- [x] [Review][Patch] OpenAI SDK single-symbol `ImportError` swallows partial breakage + missing 5xx family [src/sentinel_prism/services/llm/classification_retry.py] — **fixed:** per-symbol `getattr` lookup with WARNING log on missing names; whitelist extended with `APIStatusError` and `InternalServerError` for the "retryable HTTP/5xx" spec clause.
- [x] [Review][Patch] Lazy `default_retry_on` import inside hot predicate [src/sentinel_prism/services/llm/classification_retry.py] — **fixed:** import hoisted to module scope; openai transient classes loaded once at import time.
- [x] [Review][Patch] Transient-warning log omits `str(exc)` [src/sentinel_prism/graph/nodes/classify.py:82-95] — **fixed:** `"detail": str(exc)` added to the transient warning `ctx`; test asserts it. Attempt index is not plumbed from LangGraph's retry engine and remains a deferred follow-up.
- [x] [Review][Patch] `model_id = getattr(llm, "model_id", None) or settings.model_id` — unguarded [src/sentinel_prism/graph/nodes/classify.py:28-30] — **fixed:** added `isinstance(m, str) and m.strip()` guard so non-string / blank `llm.model_id` values always fall back to `settings.model_id`.
- [x] [Review][Patch] `test_classify_transient_exhausts_retry_policy` asserts neither call count nor state hygiene [tests/test_graph_retry_policy.py] — **fixed:** `AlwaysDown` now tracks invocations; test asserts `len(calls) == max_attempts` AND that the checkpointer did not leak partial `classifications` / `llm_trace`.
- [x] [Review][Patch] No unit test for `get_classification_retry_settings` env parsing / clamping [tests/test_graph_retry_policy.py] — **fixed:** added `TestClassificationRetrySettingsEnvParsing` covering unset, empty, whitespace, non-integer (with warning), out-of-range clamping (with warning), and in-range (no-warning) cases.
- [x] [Review][Patch] Full-pipeline retry test omits classifications-shape assertion (AC #5) [tests/test_graph_retry_policy.py] — **fixed:** added `assert len(result["classifications"]) == 1` and rationale check to the full-pipeline test.
- [x] [Review][Patch] Success-after-retry test does not verify `run_id` across both attempts in logs (AC #1) [tests/test_graph_retry_policy.py] — **fixed:** `caplog.set_level(logging.INFO)`; test now captures `graph_classify_llm_done` on the successful retry and asserts its `run_id` equals both the state `run_id` and the transient event's `run_id`.
- [x] [Review][Patch] Non-transient path now appends a placeholder `classifications` row [src/sentinel_prism/graph/nodes/classify.py:104-113] — **fixed (doc-only):** behavior change documented in Completion Notes as a deliberate extension of Story 3.4 to hold the AC #1 1:1 invariant.
- [x] [Review][Patch] `llm_trace` schema changed [src/sentinel_prism/graph/nodes/classify.py:125-147] — **fixed (doc-only):** full schema (`ok` / `partial` / `all_failed` / `no_attempt`, always emitted after the empty-updates early return) documented in Completion Notes.
- [x] [Review][Defer] Transient retries exhausted → no placeholder row emitted [src/sentinel_prism/graph/nodes/classify.py:81-96] — deferred; acceptable per AC #2 "raise" strategy and tied to the full-node retry decision above.
- [x] [Review][Defer] RetryPolicy backoff knobs (`initial_interval`, `backoff_factor`, `max_interval`, `jitter`) are not env-tunable [src/sentinel_prism/graph/retry.py] — deferred; only `max_attempts` is required by the story.
- [x] [Review][Defer] Tests construct `RetryPolicy` with `initial_interval=0.0` / `max_interval=0.0` [tests/test_graph_retry_policy.py:329-339] — deferred; speculative future-LangGraph rejection.
- [x] [Review][Defer] No structured `graph_classify_retry_exhausted` event at the retry boundary [src/sentinel_prism/graph/nodes/classify.py:82-97] — deferred; not obtainable from within the node on re-raise.
- [x] [Review][Defer] `default_retry_on` behavior for generic `Exception` not tightened [src/sentinel_prism/services/llm/classification_retry.py] — deferred; Dev Notes already flag "confirm your LLM client's transient errors are actually retryable under your predicate".
- [x] [Review][Defer] Direct `ClassificationRetrySettings(...)` construction bypasses clamp/validation [src/sentinel_prism/services/llm/settings.py:258-266] — deferred; env path is the only caller today.
- [x] [Review][Defer] `classify_node_retry_policy()` captures settings at graph-compile time, not per-invoke [src/sentinel_prism/graph/retry.py:10-21] — deferred; graph is compiled per process.

## Dev Notes

### Epic 3 context

- Epic goal: **StateGraph** with **checkpointer**, **conditional edges**, **retry without losing `run_id`**, then Tavily and audit events ([Source: `_bmad-output/planning-artifacts/epics.md` — Epic 3 header]).  
- **Story 3.5** established **`human_review_gate`** with **`interrupt`**; **resume** semantics are Epic 4—do not break interrupt/resume when adding retries.

### Previous story intelligence (3.5)

- **Routing:** `route_after_classify` reads **`flags["needs_human_review"]` only**—do not break that contract.  
- **Deferred items** from 3.5 reviews: stale **`needs_human_review`** when **`normalized_updates`** empty; **`any_review` OR** semantics; gate logging on re-entry—these may interact with retries if classify re-runs; note in tests if you re-execute the full **`classify`** node.  
- **Pinned stack:** **langgraph 1.1.6**, **langgraph-checkpoint 4.0.2** ([Source: `requirements.txt`](../../requirements.txt)).

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Epic 2 vs Epic 3** | **Connector HTTP retry** already lives in **`services/connectors/fetch_retry.py`** (Story 2.4). This story is **graph/node-level** resilience (especially **LLM**), not re-implementing fetch backoff. |
| **`AgentState.flags`** | Typed as **`dict[str, bool]`** in [`src/sentinel_prism/graph/state.py`](../../src/sentinel_prism/graph/state.py)—do not stuff **`retry_count`** there without a deliberate typing/schema change; prefer logs, **`llm_trace`**, or **`errors`** entries for attempt metadata. |
| **Architecture conceptual `retry_count`** | [Architecture section 3.2](_bmad-output/planning-artifacts/architecture.md) mentions **`retry_count`** in flags—**optional** to align in a follow-up; not required to pass AC if **`RetryPolicy`** + logs satisfy operators. |
| **Human review path** | Retries must **not** replace **human review** policy; they only address **transient infrastructure** failures. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR38 | Retry defined steps **without losing correlation ids**. |
| FR36 | Shared state across stages; single compiled graph. |
| NFR8 | Structured logs with **`run_id`**. |

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **Section 3.1** | Retries via graph structure and/or **`RetryPolicy`**. |
| **Section 3.2** | Respect list reducers—no duplicate merges on retry. |
| **Sections 5–6** | Keep **`services/`** free of **`graph/`** imports; retry helpers callable from nodes only. |

### Library / framework requirements

| Library | Version (pinned) | Notes |
| --- | --- | --- |
| **langgraph** | 1.1.6 | **`StateGraph.add_node(..., retry_policy=RetryPolicy(...))`** — signature verified in environment. |
| **langgraph.types.RetryPolicy** | (bundled) | Params: `initial_interval`, `backoff_factor`, `max_interval`, `max_attempts`, `jitter`, `retry_on`. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/graph.py` | Attach **`retry_policy`** to **`classify`** (and others only if justified). |
| `src/sentinel_prism/graph/nodes/classify.py` | Implement raise-vs-inner-retry strategy for transient LLM failures. |
| `tests/test_graph_*.py` | Retry behavior + **`run_id`** stability. |

### Testing requirements

- Prefer **async** `ainvoke` patterns from **`tests/test_graph_shell.py`** / **`tests/test_graph_conditional_edges.py`**.  
- Use **stubs** / **`monkeypatch`** for LLM failure injection—no live API.

### Project structure notes

- Package root: `src/sentinel_prism/`.  
- No **`project-context.md`** in repo; Architecture + epics + prior story artifacts are authoritative.

### References

- `_bmad-output/planning-artifacts/epics.md` — Story 3.6  
- `_bmad-output/planning-artifacts/architecture.md` — sections 3.1, 3.2, 3.4 (loops)  
- `_bmad-output/planning-artifacts/prd.md` — FR38  
- `_bmad-output/implementation-artifacts/3-5-conditional-edges-for-review-vs-continue.md` — graph + interrupt patterns  
- `src/sentinel_prism/graph/graph.py` — node registration  
- `src/sentinel_prism/graph/nodes/classify.py` — current LLM error handling  

## Dev Agent Record

### Agent Model Used

Cursor agent (Composer)

### Debug Log References

### Completion Notes List

- **Strategy:** LangGraph **`RetryPolicy`** on the **`classify`** node plus **re-raise** of **`is_transient_classification_error`** in **`node_classify`** (after **`graph_classify_llm_transient`** log). Non-transient errors still use degraded rows + **`errors`** (e.g. **`RuntimeError`**).
- **Full-node retry trade-off (resolved 2026-04-17 review):** When a transient error occurs on item *K* of a multi-item `norms` batch, LangGraph retries the entire node, so items `1..K−1` are re-classified on the next attempt (re-billed LLM calls, duplicate INFO logs, possible model-output divergence for non-idempotent providers). Accepted as-designed for simplicity; per-item inner retry is deferred until operator cost telemetry justifies the added complexity. The spec's *Saved questions* item on this trade-off is closed.
- **`llm_trace` schema (review patch):** `llm_trace` is now always emitted when at least one `normalized_update` is processed, with a `status` discriminator: `ok` (all succeeded), `partial` (some succeeded, some errored), `all_failed` (no successes, at least one LLM error), or `no_attempt` (no LLM call attempted, e.g. only non-Mapping items or all out-of-scope). Downstream consumers can rely on the key being present whenever the node actually ran past the empty-updates early return.
- **Non-transient LLM error → placeholder row (review patch):** On a non-transient error, `node_classify` now appends a placeholder `classifications` row (via `classification_dict_for_llm_error`) in addition to the existing `errors` entry, preserving the AC #1 1:1 invariant between normalized updates and classification rows. This extends Story 3.4 behavior (which only populated `err_accum`).
- **`get_classification_llm_settings()` not cached (review patch):** `@lru_cache` is intentionally omitted so `monkeypatch.setenv(...)` lands without a `cache_clear()` helper. Call site is once per `node_classify` invocation — `os.getenv` + dataclass construction is negligible vs. the downstream LLM call. Re-introduce caching (with a test fixture that clears it) if profiling ever justifies.
- **`SENTINEL_CLASSIFICATION_MAX_ATTEMPTS`** (2–10, default 3) drives **`max_attempts`**; LangGraph backoff matches **`RetryPolicy`** defaults. Exhausted transient retries **raise** the last exception (no silent drop).
- **Tests:** `tests/test_graph_retry_policy.py` (classify-only subgraph + full pipeline **`run_id`** check); full suite **119 passed**, 9 skipped.

### File List

- `src/sentinel_prism/services/llm/classification_retry.py`
- `src/sentinel_prism/services/llm/settings.py`
- `src/sentinel_prism/graph/retry.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/nodes/classify.py`
- `tests/test_graph_retry_policy.py`
- `verify_imports.py`
- `.env.example`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Change Log

- 2026-04-17 — Story 3.6 implemented: transient LLM retry policy on **`classify`**, settings + tests; story and sprint status set to **review** (pytest: 119 passed, 9 skipped).
- 2026-04-17 — Code review complete: 1 decision resolved (accept full-node retry), 15 patches applied across `node_classify` (`llm_trace` schema with `ok`/`partial`/`all_failed`/`no_attempt` status, `model_id` guard, `detail` in transient log), `get_classification_retry_settings` (WARNING on parse error / clamp, exported bounds constants), `is_transient_classification_error` (per-symbol openai imports + 5xx whitelist, module-scope `default_retry_on`), and retry-policy tests (call-count + state-hygiene assertions, `run_id`-in-logs correlation across attempts, full env-parsing test class). Story set to **done** (pytest: 132 passed, 9 skipped).

## Git intelligence summary

Recent commits on the branch emphasize ingestion + graph scaffolding (`scout`/`normalize` persistence). For this story, extend the same **graph test** and **logging** patterns; avoid bypassing **`services/`** boundaries established in Epic 2–3.

## Latest technical information

- **LangGraph 1.1.6 `RetryPolicy`:** configured on **`add_node`**; **`default_retry_on`** treats common client/validation errors as non-retryable and many network/5xx-style errors as retryable—**validate** against your LLM SDK’s exception types.  
- **Introspection used while authoring this story:** `RetryPolicy(initial_interval=0.5, backoff_factor=2.0, max_interval=128.0, max_attempts=3, jitter=True, retry_on=...)`.

## Project context reference

No `project-context.md` found; use Architecture + epic + prior implementation artifacts.

## Story completion status

**done** — Implementation complete; code review complete (1 decision resolved, 15 patches applied, 7 deferred, 6 dismissed). Post-review pytest: 132 passed, 9 skipped.

### Saved questions / clarifications (non-blocking)

- ~~Whether **full-node** retry on **`classify`** is acceptable operationally vs **per-item** inner retry (cost vs simplicity).~~ **Resolved 2026-04-17 (code review):** accept full-node retry; per-item inner retry deferred.
- Whether **`scout`** / **`normalize`** should start **raising** selected transient **DB** errors to benefit from **`RetryPolicy`**, or keep returning **`errors`** only.
