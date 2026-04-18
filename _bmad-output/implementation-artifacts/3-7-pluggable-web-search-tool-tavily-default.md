# Story 3.7: Pluggable web search tool (Tavily default)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the **system**,
I want **optional Tavily-backed search behind a stable tool interface**,
so that **classify (and later brief) nodes can enrich from public web context** without coupling to one vendor (**FR43**, **NFR12**).

## Acceptance Criteria

1. **Public-query contract (NFR12)**  
   **Given** query text is derived only from **public** normalized fields (e.g. title, summary, body_snippet, item_url, jurisdiction, document_type — same class of data already ingested from public sources)  
   **When** the search tool runs  
   **Then** the outbound request carries **no** tenant secrets, JWTs, internal analyst notes, or other non-public payloads   **And** the implementation documents how callers must build queries (and what to **exclude**).

2. **Pluggable interface**  
   **Given** a small **`SearchToolProtocol`** (or equivalent `typing.Protocol`) defining search input/output   **When** a **Tavily** adapter and a **stub/no-op** (or in-memory fake) adapter both implement it  
   **Then** tests can run **without** network or API keys  
   **And** a second adapter (e.g. **DuckDuckGo**-style or other HTTP API) can be added later **without** changing call sites — only wiring/DI.

3. **Tavily as default implementation**  
   **Given** `TAVILY_API_KEY` (or project-prefixed env name you choose — document in `.env.example`) is set in a real environment  
   **When** the feature flag / setting enables web enrichment  
   **Then** the default adapter uses **Tavily** (official **`tavily-python`** client, **`AsyncTavilyClient`** preferred inside `async` nodes)  
   **And** errors/timeouts are handled consistently with existing logging (`run_id`, `step`, `extra={"event": "...", "ctx": {...}}`).

4. **Optional integration path (minimal graph disruption)**  
   **Given** the current pipeline is **`scout` → `normalize` → `classify`** with **`RetryPolicy`** on **`classify`** (Story 3.6)  
   **When** enrichment is **off** (default in tests / CI)  
   **Then** behavior matches today’s classification path (no new external calls)  
   **When** enrichment is **on**  
   **Then** search results are used only to augment **LLM input** (e.g. extra context block in the user message) or a clearly documented alternative — **do not** silently change rule-engine outcomes.

5. **Architecture placement**  
   **Given** [Architecture FR42–FR43 mapping](_bmad-output/planning-artifacts/architecture.md) (`graph/tools/tavily_search.py`)  
   **When** implemented  
   **Then** Tavily-specific code lives under **`src/sentinel_prism/graph/tools/`** (or split: protocol in `graph/tools/`, thin service in `services/` only if you need shared HTTP — **avoid** `services/` importing `graph/`).

6. **Tests**  
   **Given** CI without live Tavily  
   **When** tests run  
   **Then** unit tests cover: protocol wiring, stub adapter, **query builder** rejects/guards obviously unsafe keys if you add validation helpers, and at least one async test path for “enrichment off vs on” behavior.

## Tasks / Subtasks

- [x] **Protocol + types (AC: #2)** — `src/sentinel_prism/graph/tools/`  
  - [x] Define `SearchToolProtocol` (or `PublicWebSearch`) with clear result type (e.g. list of `{title, url, snippet}` or raw Tavily-normalized dict — pick one documented shape).  
  - [x] Provide **`NullSearchTool`** / **`StubSearchTool`** returning empty or fixture results for tests.

- [x] **Tavily adapter (AC: #3, #5)** — `src/sentinel_prism/graph/tools/tavily_search.py` (name per Architecture)  
  - [x] Pin **`tavily-python`** in `requirements.txt` (use current stable from PyPI at implementation time; note [Tavily Python quickstart](https://docs.tavily.com/sdk/python/quick-start)).  
  - [x] Read API key from settings/env; **never** log the key.  
  - [x] Use **`AsyncTavilyClient`** in async contexts to avoid blocking the event loop.

- [x] **Query construction (AC: #1)** — e.g. `graph/tools/query_builder.py` or private helpers  
  - [x] Build query strings only from **allow-listed** normalized keys; document the allow list.  
  - [x] Explicitly **do not** pass raw `AgentState` dicts into the adapter — only derived `str` query + options.

- [x] **Settings + feature flag (AC: #4)** — `src/sentinel_prism/services/llm/settings.py` or new `services/search/settings.py`  
  - [x] e.g. `SENTINEL_WEB_SEARCH_ENABLED=0|1`, `TAVILY_API_KEY`, optional `max_results`.  
  - [x] Default **off** so CI and local dev without keys behave as today.

- [x] **Classify integration (AC: #4, #6)** — `src/sentinel_prism/graph/nodes/classify.py`  
  - [x] Inject or factory-resolve the search tool (avoid global singletons untested).  
  - [x] If enrichment runs **before** the per-item LLM call: handle **transient** Tavily failures — either swallow with structured `errors` + continue without web context, or **raise** only if you want **`RetryPolicy`** to retry the whole node (be aware of Story 3.6 full-node retry cost). **Document the choice.**

- [x] **Tests** — `tests/test_graph_*` or `tests/test_web_search_tool.py`  
  - [x] Stub adapter tests; optional **`httpx`**/`respx` mock if you test the Tavily client wrapper lightly.  
  - [x] Extend `verify_imports.py` only if new public entry points warrant it.

### Review Findings

- [x] [Review][Patch] **Memoize per-run Tavily results by `(run_id, source_id, item_url)` to avoid retry amplification** — LangGraph `RetryPolicy` re-executes the whole `node_classify` body on a transient LLM error (Story 3.6), re-issuing Tavily searches for every item in the batch. Added module-level LRU (cap 1000) keyed by `(run_id, source_id, item_url)` so retry passes reuse first-pass results. `llm_trace.web_search.cache_hits` surfaces savings. [`src/sentinel_prism/graph/nodes/classify.py`]
- [x] [Review][Patch] Guard non-dict Tavily response in adapter — added `isinstance(raw, dict)` check before `raw.get("results")`. [`src/sentinel_prism/graph/tools/tavily_search.py`]
- [x] [Review][Patch] `close()` in `finally` wrapped in `try/except`; failures downgraded to `tavily_client_close_error` log so they cannot mask the original search exception or stall the node. [`src/sentinel_prism/graph/tools/tavily_search.py`]
- [x] [Review][Defer] Per-call `AsyncTavilyClient` construction defeats connection pooling [`src/sentinel_prism/graph/tools/tavily_search.py`] — deferred, skipped during batch-apply because it requires a lifecycle decision (explicit `aclose()` on the adapter + caller wiring at node-teardown); follow-up in a connection-hygiene pass
- [x] [Review][Patch] Exception `detail` sanitized via `_safe_error_detail` — strips CR/LF and caps at 200 chars before logging / appending to `err_accum`. [`src/sentinel_prism/graph/nodes/classify.py`]
- [x] [Review][Patch] `TavilyWebSearch` constructor clamps `default_max_results` (≥1) and `timeout` (`[1.0, 120.0]`, rejects nan/inf). [`src/sentinel_prism/graph/tools/tavily_search.py`]
- [x] [Review][Patch] Same clamp applied to caller-supplied `max_results` in `TavilyWebSearch.search`. [`src/sentinel_prism/graph/tools/tavily_search.py`]
- [x] [Review][Patch] `math.isfinite` guard added to `SENTINEL_TAVILY_TIMEOUT` parsing — rejects `nan`/`inf`; emits `tavily_timeout_non_finite` warn log. [`src/sentinel_prism/services/search/settings.py`]
- [x] [Review][Patch] `asyncio.wait_for(search_tool.search(...), timeout=ws_settings.tavily_timeout)` wraps the enrichment call in `node_classify`; timeout raises `asyncio.TimeoutError` → existing catch-and-continue branch. [`src/sentinel_prism/graph/nodes/classify.py`]
- [x] [Review][Patch] `build_public_web_search_query` now restricts allow-listed field values to `str`/`int`/`float` (excluding `bool`); non-scalar values are skipped. [`src/sentinel_prism/graph/tools/query_builder.py`]
- [x] [Review][Patch] `format_web_context_for_llm` skips non-Mapping snippet entries. [`src/sentinel_prism/graph/tools/context_format.py`]
- [x] [Review][Patch] `format_web_context_for_llm` filters hits with no usable `title`/`url`/`snippet` — no header block emitted when all hits are empty. [`src/sentinel_prism/graph/tools/context_format.py`]
- [x] [Review][Patch] `StubWebSearchTool.__init__` now defensively copies the input list (`list(snippets)`). [`src/sentinel_prism/graph/tools/stub_search.py`]
- [x] [Review][Defer] Sequential per-item Tavily calls — no bounded concurrency multiplies batch latency [`src/sentinel_prism/graph/nodes/classify.py:86-124`] — deferred, performance optimization (not a correctness bug)
- [x] [Review][Defer] 500-char query truncation can drop high-signal `item_url`/`jurisdiction` when `summary`/`body_snippet` dominate [`src/sentinel_prism/graph/tools/query_builder.py:28,47-48`] — deferred, query-quality enhancement (per-field budget)
- [x] [Review][Defer] Unsanitized Tavily snippets flow into LLM user message (prompt-injection vector) [`src/sentinel_prism/graph/tools/context_format.py`, `src/sentinel_prism/services/llm/classification.py`] — deferred, restates Story 3.4 finding (prompt-injection surface), hardening pass
- [x] [Review][Defer] Empty-query path silently skipped — no counter/log distinguishes "no queryable fields" from "feature off" [`src/sentinel_prism/graph/nodes/classify.py:95`] — deferred, observability polish
- [x] [Review][Defer] No transient-vs-permanent Tavily error classification — 401/403 will log per-item for the entire batch [`src/sentinel_prism/graph/nodes/classify.py:100-122`] — deferred, mirror `is_transient_classification_error` in a hardening pass
- [x] [Review][Defer] Tavily API key stored as plain `str` — no `SecretStr`/redaction wrapper [`src/sentinel_prism/graph/tools/tavily_search.py:23`, `src/sentinel_prism/graph/tools/factory.py`] — deferred, secrets-handling pass
- [x] [Review][Defer] `_env_truthy` maps malformed values (`"Enabled"`, `"yesplease"`) to `False` with no warning [`src/sentinel_prism/services/search/settings.py:19-21`] — deferred, observability nicety
- [x] [Review][Defer] Missing-API-key warning storms — `create_web_search_tool` is called per `node_classify` invocation [`src/sentinel_prism/graph/tools/factory.py:24-33`] — deferred, log-once-at-startup (or at graph compile) requires caching seam
- [x] [Review][Defer] Tavily SDK symbol coupling — adapter assumes `AsyncTavilyClient(api_key=...)`, `.search(...)`, `.close()`; only `==` pin, no upper-bound exclusion [`src/sentinel_prism/graph/tools/tavily_search.py`, `requirements.txt:40`] — deferred, dependency-hygiene pass
- [x] [Review][Defer] `_MAX_QUERY_CHARS = 500` is hardcoded; not advertised in `.env.example`; not tunable without code change [`src/sentinel_prism/graph/tools/query_builder.py:28`] — deferred, config hygiene
- [x] [Review][Defer] `test_tavily_adapter_parses_results` patches `tavily.AsyncTavilyClient` unguarded; `pytest.importorskip("tavily")` would future-proof an optional-dep switch [`tests/test_web_search_tool.py`] — deferred, resilient-test hygiene
- [x] [Review][Defer] Injected `_web_search_tool` still receives `max_results` from env settings — test-time behaviour depends on ambient `SENTINEL_WEB_SEARCH_MAX_RESULTS` [`src/sentinel_prism/graph/nodes/classify.py:106`] — deferred, DI seam polish

## Dev Notes

### Epic 3 context

- Epic goal: StateGraph with checkpointer, conditional edges, retry, **Tavily for public queries only**, then audit events (Story 3.8 — **out of scope** here). [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 3]
- **Story 3.8** will add pipeline audit events; do **not** implement audit rows in this story unless required for debugging (prefer structured logs).

### Previous story intelligence (3.6)

- **`classify`** uses LangGraph **`RetryPolicy`** with **re-raise** on transient LLM errors; **full-node retry** re-executes the entire node body — adding expensive Tavily calls may multiply outbound traffic on retry. Prefer **idempotent** search usage, **short timeouts**, or **catch-and-continue** for search failures while still retrying LLM-only transients. [Source: `3-6-retry-policy-on-transient-node-failures.md`]
- **`llm_trace`** schema (`ok` / `partial` / `all_failed` / `no_attempt`) is consumed by tests and operators — if enrichment adds metadata, use a **separate** optional field (e.g. `web_search_trace`) or structured log events; avoid breaking existing keys.
- **Routing:** `route_after_classify` reads **`flags["needs_human_review"]` only** — do not change that contract when adding enrichment.

### Developer context (guardrails)

| Topic | Guidance |
| --- | --- |
| **Reinvention** | Do **not** duplicate **connector** fetch/retry from `services/connectors/` — search is a **new** bounded tool with its own timeouts and policy. |
| **NFR12** | Treat search as **public-query-only**. If unsure whether a field is public, **exclude** it from query construction. |
| **Boundaries** | **`services/`** must not import **`graph/`**; graph nodes may call `services/llm`, `services/connectors`, etc. |
| **Brief node** | Architecture shows a future **`brief`** node — design the protocol so **`brief`** can reuse the same adapter later without refactor. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| FR43 | Pluggable search abstraction; Tavily recommended default; swappable implementations. |
| NFR12 | External search calls transmit **only** public / public-derived query text. |
| FR36 | Single graph/state; enrichment must not fork alternate orchestration paths. |

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **§3.2 / tool nodes** | `web_search` → Tavily adapter implementing shared **`SearchToolProtocol`** (public queries only). [Source: `_bmad-output/planning-artifacts/architecture.md` — §3.2 fragment] |
| **§3.4 topology** | Current compiled graph has **no** `brief` node yet — integrate where Epic 3 needs it today (**classify**), without assuming `brief` exists. |
| **§3.7 “What not to do”** | Do **not** pass non-public content to Tavily. |
| **Requirements mapping** | FR42–FR43 → `graph/tools/tavily_search.py` per Architecture table. |

### Library / framework requirements

| Library | Version | Notes |
| --- | --- | --- |
| **tavily-python** | Pin at implementation (PyPI) | Prefer **`AsyncTavilyClient`** for async nodes. [Docs](https://docs.tavily.com/sdk/python/reference) |
| **langchain-core** | 1.2.31 (pinned) | Optional: **`langchain-tavily`** only if you adopt LC-native tools — not required if you call Tavily SDK directly. |
| **langgraph** | 1.1.6 (pinned) | No upgrade in this story unless blocked. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/tools/__init__.py` | Export protocol + factory helpers as needed. |
| `src/sentinel_prism/graph/tools/tavily_search.py` | Tavily adapter (Architecture-mandated location). |
| `src/sentinel_prism/graph/nodes/classify.py` | Optional enrichment hook. |
| `requirements.txt` | Add `tavily-python==...`. |
| `.env.example` | Document `TAVILY_API_KEY` + feature flag(s). |

### Testing requirements

- No live network in default `pytest`; stub/fake adapter is mandatory.
- Follow async patterns from `tests/test_graph_retry_policy.py` / `tests/test_graph_shell.py`.
- If you add optional integration tests behind env, mark them skipped by default.

### Project structure notes

- Package root: `src/sentinel_prism/`.
- No `project-context.md` in repo; Architecture + epics + prior story artifacts are authoritative.

### References

- `_bmad-output/planning-artifacts/epics.md` — Story 3.7, Epic 3 goal
- `_bmad-output/planning-artifacts/architecture.md` — §3.2 tools, §3.4 topology, §3.7, requirements table (FR42–FR43)
- `_bmad-output/planning-artifacts/prd.md` — FR43, NFR12
- `_bmad-output/implementation-artifacts/3-6-retry-policy-on-transient-node-failures.md` — retry + `llm_trace` conventions
- `src/sentinel_prism/graph/graph.py` — pipeline shape
- `src/sentinel_prism/graph/state.py` — `AgentState` reducers
- `src/sentinel_prism/services/llm/classification.py` — message formatting for classify

## Dev Agent Record

### Agent Model Used

Cursor agent (Composer)

### Debug Log References

### Completion Notes List

- **Enrichment policy:** Per **in-scope** normalized item, optional web search runs only when `SENTINEL_WEB_SEARCH_ENABLED` is truthy **or** tests pass `_web_search_tool=...`. Query text comes solely from `build_public_web_search_query` (allow-listed public fields). Results format via `format_web_context_for_llm` and pass to `ClassificationLLM.classify(..., web_context=...)`.
- **Tavily failures:** **Catch-and-continue** — log `graph_classify_web_search_error`, append `errors[]` with `step=classify_web_search`, classify without web context — avoids extra Tavily calls when LangGraph **RetryPolicy** retries the whole classify node (Story 3.6).
- **API key:** `get_tavily_api_key_for_search()` prefers `SENTINEL_TAVILY_API_KEY`, then `TAVILY_API_KEY`. If the feature flag is on but no key is set, `create_web_search_tool` logs `web_search_enabled_missing_api_key` and returns `NullWebSearchTool`.
- **Observability:** When relevant, `llm_trace.web_search` includes `feature_enabled`, `tool_injected`, `attempts`, `errors`.
- **Tests:** `pytest` 143 passed, 9 skipped (full suite).

### File List

- `requirements.txt`
- `.env.example`
- `verify_imports.py`
- `src/sentinel_prism/services/search/__init__.py`
- `src/sentinel_prism/services/search/settings.py`
- `src/sentinel_prism/graph/tools/__init__.py`
- `src/sentinel_prism/graph/tools/types.py`
- `src/sentinel_prism/graph/tools/stub_search.py`
- `src/sentinel_prism/graph/tools/query_builder.py`
- `src/sentinel_prism/graph/tools/context_format.py`
- `src/sentinel_prism/graph/tools/tavily_search.py`
- `src/sentinel_prism/graph/tools/factory.py`
- `src/sentinel_prism/graph/nodes/classify.py`
- `src/sentinel_prism/services/llm/classification.py`
- `tests/test_web_search_tool.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-7-pluggable-web-search-tool-tavily-default.md`

## Change Log

- 2026-04-18 — Story 3.7 implemented: pluggable `SearchToolProtocol`, Tavily adapter, public query builder, search settings, optional classify enrichment, tests; story and sprint status set to **review** (pytest: 143 passed, 9 skipped).
- 2026-04-18 — Code review pass: batch-applied 12 patches (per-run memoization by `(run_id, source_id, item_url)` via bounded LRU, `asyncio.wait_for` around search, `_safe_error_detail` sanitization, non-dict Tavily response guard, safe `close()` in `finally`, `default_max_results`/`timeout` clamps in adapter, `math.isfinite` guard on `SENTINEL_TAVILY_TIMEOUT`, scalar-only allow-list in query builder, non-Mapping/empty-hit filtering in formatter, `StubWebSearchTool` defensive list copy); 1 patch deferred (per-call `AsyncTavilyClient` lifecycle); 13 items deferred and 5 dismissed; status set to **done** (pytest: 143 passed, 9 skipped).

## Git intelligence summary

Recent work on `feat(graph): Epic 3 classify, review routing, and transient retry policy` established **`classify`** + **`RetryPolicy`** + structured logging. This story should extend that path with **optional** enrichment and **off-by-default** behavior so CI stays stable.

## Latest technical information

- **Tavily Python:** package **`tavily-python`**; **`TavilyClient`** / **`AsyncTavilyClient`**; env/API key via constructor or documented pattern. Verify return shape of **`search()`** against current SDK when implementing (normalize to your protocol output). [PyPI](https://pypi.org/project/tavily-python/), [Quickstart](https://docs.tavily.com/sdk/python/quick-start)

## Project context reference

No `project-context.md` found; use Architecture + epic + prior implementation artifacts.

## Story completion status

**review** — Implementation complete; ready for `code-review` workflow.

### Saved questions / clarifications (non-blocking)

- Should enrichment run **once per `classify` invocation** (shared context) vs **per normalized item** (higher cost, finer granularity)?
- If **`classify`** gains **tool-using** LLM agents later, should this protocol remain **direct SDK** calls vs **LangChain `bind_tools`** — pick one approach for this story and document.
