---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - prd.md
  - product-brief-sentinel-prism.md
  - product-brief-sentinel-prism-distillate.md
workflowType: architecture
project_name: Sentinel Prism
user_name: Jack
date: "2026-04-12"
status: complete
completedAt: "2026-04-12T20:00:00Z"
lastStep: 8
---

# Architecture Decision Document — Sentinel Prism

_This document records architecture decisions for consistent AI-assisted implementation. Orchestration is specified using **LangGraph `StateGraph`-style** patterns as the single source of truth for multi-agent workflow behavior._

---

## 1. Project context (from PRD)

### Scope summary

- **Product:** Agentic pharma regulatory monitoring: ingest **public** sources → normalize → classify impact → brief → route → audit → feedback.
- **Scale:** ~**46** functional requirements, **14** NFR categories as of PRD; **high** domain complexity; **greenfield** codebase.
- **Non-negotiables:** Public/mock data in this phase; **no** automated binding filings; **human review** path for low-confidence / high-risk; **audit** + **replay**; **local auth** now with **pluggable** SSO later.

### Architectural drivers

| Driver | Implication |
|--------|-------------|
| **Stateful multi-agent graph** | All agent coordination goes through **one** primary **LangGraph** graph (and optional registered subgraphs), not ad-hoc scripts. |
| **Audit / replay** | Persist **run id**, **graph state checkpoints**, and **append-only domain events**. |
| **Connector swap** | Ingestion and notifications behind **interfaces**; graph **nodes** call services, not raw drivers inline. |
| **Web research tools** | **Tavily** (or equivalent) behind a **tool** contract; **public** queries only. |

---

## 2. Technology stack & starters

Decisions align with existing **Python** + **LangGraph** setup and PRD technical direction.

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Orchestration** | **LangGraph** (`StateGraph`, checkpoints) | PRD-mandated; explicit nodes/edges/state; retries/branching in graph. |
| **Runtime / API** | **FastAPI** (async) | Native async for I/O-bound connectors and LLM calls; OpenAPI for UI and tests. |
| **Persistence** | **PostgreSQL** | Relational model for updates, users, audit, feedback; JSONB for flexible metadata. |
| **Migrations** | **Alembic** | Versioned schema. |
| **Job scheduling** | **APScheduler** or **Celery** (defer heavy scale) | Poll sources on schedule; trigger graph runs. |
| **Web UI** | **React** + **Vite** + **TypeScript** | Enterprise-style console; team can swap **Next.js** later without changing graph contracts. |
| **LLM** | Vendor SDKs behind **interfaces** | OpenAI / Anthropic per config; version logged per run. |

**Version pinning:** Implementers should pin **`langgraph`**, **`langchain-core`**, and **FastAPI** in `requirements.txt` / lockfile at build time—do not hardcode versions in this document.

---

## 3. Core decision: LangGraph `StateGraph` orchestration

### 3.1 Principles

1. **One primary graph** per major workflow (e.g., `regulatory_update_pipeline`). Optional **subgraphs** only where a phase is reused (e.g., “re-classify after human edit”).
2. **Agents = nodes** — Each node is a plain Python callable (sync or async) receiving `state` and returning a **partial state update** (dict).
3. **Control flow = edges** — Linear edges for happy path; **`add_conditional_edges`** for branches (severity, confidence, review flags).
4. **Shared state** — A single **`AgentState`** schema (see §3.2); no hidden globals; side effects go through **services** / **repositories** injected or imported from a bounded context.
5. **Retries / loops** — Use graph structure: conditional edge back to a **previous node** or a dedicated **`retry_scout`** node; optionally combine with LangGraph **`RetryPolicy`** on individual nodes where appropriate.
6. **Human-in-the-loop** — **Interrupt** or **pause** pattern: graph routes to a **`awaiting_human_review`** state; resume via API with new input (override) that feeds back into the graph.

### 3.2 `AgentState` schema (conceptual)

Define with **`TypedDict`** + **`Annotated`** reducers **or** a **Pydantic** model with explicit merge rules—pick one pattern repo-wide.

**Required fields (conceptual):**

| Field | Purpose |
|-------|---------|
| `run_id` | Correlation id for audit (NFR8). |
| `tenant_id` | Future multi-tenant; single value in MVP. |
| `source_ids` / `raw_items` | Scout output. |
| `normalized_updates` | Normalizer output (list). |
| `classifications` | Impact Analyst output. |
| `routing_decisions` | Routing agent output. |
| `briefings` | Briefing agent output. |
| `delivery_events` | Notification results. |
| `errors` | Structured failures per step. |
| `flags` | e.g. `needs_human_review: bool`, `retry_count: int`. |
| `llm_trace` (optional) | Prompt/version ids for reproducibility. |

**Reducers:** Use **`operator.add`** or custom reducers for **append-only** lists (e.g., `errors`, `delivery_events`) so parallel branches (if added later) merge predictably.

### 3.3 Node ↔ agent mapping

| PRD agent | Graph node id(s) | Responsibility |
|-----------|------------------|----------------|
| **Scout** | `scout` | Fetch feeds/HTTP; dedupe; emit `raw_items`. |
| **Normalizer** | `normalize` | Parse to canonical `NormalizedUpdate` records. |
| **Impact Analyst** | `classify` | Rules + LLM; set severity, confidence, `needs_human_review`. |
| **Briefing** | `brief` | Build briefing objects from classified items (skip or empty if routed to review only). |
| **Routing** | `route` | Apply mock routing tables; enqueue notifications. |
| **Feedback** (async path) | `record_feedback` **or** separate graph triggered from UI | Persist feedback; does not silently mutate prompts—**FR29**. |

**Tool nodes (not agents):** Implement as **nodes** or **tools** bound to LLM in `classify` / `brief`:

- `web_search` → **Tavily** adapter implementing **`SearchToolProtocol`** (public queries only—**NFR12**).

### 3.4 Graph topology (reference)

```text
[START]
   → scout
   → normalize
   → classify
        ├─(conditional: needs_human_review?)──→ human_review_gate ─┐
        │                                        (interrupt/wait)   │
        └─(else)──→ brief ─→ route ─→ [END]                          │
                    ↑___________________________resume after review____┘
```

- **`human_review_gate`:** Sets state to **awaiting review**; API resumes with analyst override → **`classify`** (re-run) or **`brief`** with frozen labels.
- **Loops:** `scout` ← conditional edge if `retry_count < max` and transient fetch error.
- **Parallelism (optional later):** `normalize` could map over items with a **send()** fan-out—defer until needed.

### 3.5 Checkpointers & persistence

- Use LangGraph **checkpointer** (`MemorySaver` for dev; **`PostgresSaver`** or project’s chosen SQL checkpointer for prod-like) so **state is resumable** and **replayable** (**FR35**).
- Persist **domain events** (classification created, notification sent) in **`audit_events`** table **in addition** to graph checkpoints for **queryable** audit UX.

### 3.6 API integration

- **FastAPI** routes: `POST /runs` starts a graph invocation with `thread_id = run_id`; `GET /runs/{id}` returns state + audit; `POST /runs/{id}/resume` for human-review continuation.
- **Streaming:** Use `graph.astream` / `astream_events` for operator dashboards (optional).

### 3.7 What not to do

- Do **not** implement the pipeline as a single giant function with implicit order.
- Do **not** fork separate processes per agent without going through **state** + **checkpointer** (breaks replay).
- Do **not** pass **non-public** content to **Tavily** (**NFR12**).

---

## 4. Architectural decisions (summary)

### Data

- **PostgreSQL** as system of record for **updates**, **classifications**, **briefings**, **users**, **audit**, **feedback**, **source registry**.
- **Idempotent** ingestion keys: `(source_id, content_fingerprint)` (**FR3**).

### Auth (PRD)

- **Local** users (password or magic link) **+ RBAC**; **`auth_provider`** interface for future **OIDC/SAML** (**FR46**, **NFR14**).
- JWT or session cookies for API; **same** identity in audit logs.

### API

- **REST** JSON for UI; **OpenAPI** generated from FastAPI.
- **Webhooks** optional later for outbound integrations.

### Observability

- **Structured logging** with `run_id` on every line (**NFR8**).
- **Metrics:** per-source scrape success, graph node latency histograms, LLM token usage (optional).

---

## 5. Implementation patterns (consistency for AI agents)

### Graph module layout

- **`src/sentinel_prism/graph/`** (package name illustrative)
  - `state.py` — `AgentState` definition + reducers
  - `graph.py` — builds `StateGraph`, compiles graph
  - `nodes/` — `scout.py`, `normalize.py`, `classify.py`, `brief.py`, `route.py`, `feedback.py`
  - `tools/` — `tavily_search.py` (implements search protocol)
  - `checkpoints.py` — checkpointer factory

### Naming

- **Node functions:** `node_scout`, `node_normalize`, … or single module per node with `run(state) -> PartialState`.
- **Graph ids:** `snake_case`, stable—referenced in tests and audit.

### Error handling

- Nodes **catch** expected failures, append to `errors[]`, set `flags`, return partial update—**do not** swallow without audit.
- **Conditional edge** from `errors` to `scout` retry or `dead_letter` node.

### Testing

- **Unit:** each node with **fixture state**.
- **Integration:** compiled graph with **MemorySaver** + recorded HTTP fixtures.

---

## 6. Project structure & boundaries

### Directory tree (reference)

```text
sentinel-prism/
├── README.md
├── pyproject.toml / requirements.txt
├── .env.example
├── alembic/
│   └── versions/
├── src/
│   └── sentinel_prism/
│       ├── main.py                 # FastAPI app factory
│       ├── api/
│       │   ├── routes/
│       │   │   ├── runs.py
│       │   │   ├── updates.py
│       │   │   ├── sources.py
│       │   │   └── auth.py
│       │   └── deps.py
│       ├── graph/                  # LangGraph StateGraph — canonical orchestration
│       │   ├── state.py
│       │   ├── graph.py
│       │   ├── nodes/
│       │   └── tools/
│       ├── services/               # Connectors, LLM, notifications (called from nodes)
│       │   ├── connectors/
│       │   ├── llm/
│       │   └── notifications/
│       ├── db/
│       │   ├── models.py
│       │   └── repositories/
│       └── workers/                # Scheduled jobs → trigger graph
├── web/                            # React + Vite SPA
│   ├── src/
│   └── package.json
└── tests/
    ├── unit/
    └── integration/
```

### Requirements → location mapping

| PRD area | Primary location |
|----------|------------------|
| FR1–FR6 Scout | `services/connectors/`, `graph/nodes/scout.py` |
| FR7–FR10 Normalize | `graph/nodes/normalize.py`, `db/models.py` |
| FR11–FR17 Classify | `graph/nodes/classify.py`, `services/llm/` |
| FR18–FR20 Brief | `graph/nodes/brief.py` |
| FR21–FR25 Route | `graph/nodes/route.py`, `services/notifications/` |
| FR26–FR29 Feedback | API + `record_feedback` node or async worker |
| FR33–FR35 Audit | `db/audit`, API, checkpointer replay |
| FR36–FR38 Orchestration | **`graph/graph.py`** exclusively |
| FR42–FR43 Web | `graph/tools/tavily_search.py` |
| FR39–FR41, FR46 Auth | `api/routes/auth.py`, `services/auth/` |

### Boundaries

- **UI** talks only to **REST API**, not to graph internals.
- **Graph nodes** call **services**; services do not import graph definitions (avoid cycles).
- **Connectors** return **DTOs**; normalization is always in **`normalize` node** or dedicated helpers.

---

## 7. Validation

| Check | Status |
|-------|--------|
| FR36–FR38 (orchestration) | **Covered** by §3 StateGraph design |
| Audit / replay | **Covered** by checkpointer + `audit_events` |
| Tavily public-only | **Covered** §3.7 + tool adapter |
| Local auth + future SSO | **Covered** §4 |
| Scout vs research tools | **Covered** §3.3, PRD Technical Direction |

**Open points for implementation (not blockers):** exact **PostgresSaver** vs community checkpointer package; **Celery** vs **ARQ** for workers—choose at first vertical slice.

---

## 8. Handoff & next steps

- **Implementation order:** (1) `AgentState` + **empty** graph smoke test, (2) Scout + Normalize **vertical slice**, (3) Classify + review branch, (4) Brief + Route, (5) UI + auth.
- **Next BMAD workflows:** `bmad-create-epics-and-stories` (if not done), `bmad-check-implementation-readiness`, or `bmad-help` for routing.

This architecture document is the **authority** for **how** orchestration is implemented: **LangGraph `StateGraph`** with explicit **state**, **nodes**, **conditional edges**, **checkpointers**, and **service boundaries** as specified above.
