# Story 4.2: Approve, reject, override with notes

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **analyst**,
I want **to approve, reject, or override classifications from the review queue with notes**,
so that **decisions are authoritative, persisted, and auditable** (**FR17**, **FR33**, **NFR8**, **NFR12**).

## Acceptance Criteria

1. **Resume API (Architecture §3.6)**  
   **Given** a `run_id` that appears in **`review_queue_items`** (same eligibility as `GET /runs/{run_id}`)  
   **When** an authenticated **ANALYST** or **ADMIN** submits a valid review decision  
   **Then** the server resumes the regulatory graph for `thread_id = str(run_id)` using LangGraph’s **interrupt resume** mechanism (**`Command(resume=...)`** with **`langgraph==1.1.6`**)  
   **And** the HTTP surface matches the architecture contract: **`POST /runs/{run_id}/resume`** (same `/runs` prefix as run detail).

2. **Decisions (FR17)**  
   **Given** a queued run  
   **When** the analyst chooses **approve**, **reject**, or **override**  
   **Then** behavior is deterministic and documented in OpenAPI:  
   - **Approve** — accept current classifications; **clear** the aggregate **`flags.needs_human_review`** and per-row **`needs_human_review`** where applicable so routing policy matches a resolved review.  
   - **Reject** — record the decision; update state so the item is **not stuck** in human review (e.g. mark **out of scope** or set explicit “dismissed” semantics — **pick one** approach, justify in Dev Notes, stay consistent with `classification_dict_for_state` field shapes).  
   - **Override** — apply analyst-supplied fields (at minimum **severity** and/or **confidence** and/or **rationale**; align with keys in `classification_dict_for_state`) and clear **`needs_human_review`** after override unless product explicitly requires re-review.

3. **Notes persist**  
   **Given** any decision  
   **When** the request includes **`note`** text  
   **Then** the note is stored in **append-only** **`audit_events.metadata`** (trimmed / bounded per **`audit_events._trim_metadata`** — no secrets, no raw prompts, no Tavily payloads per **NFR12**).

4. **Analyst attribution (epic AC)**  
   **Given** a successful decision  
   **When** the audit row is written  
   **Then** **`actor_user_id`** is set from the authenticated **`User.id`** (reuse **`append_audit_event(..., actor_user_id=...)`**).

5. **Audit actions**  
   **Given** a decision completes  
   **When** persisting audit  
   **Then** extend **`PipelineAuditAction`** with explicit vocabulary for human review (e.g. separate actions for approve / reject / override **or** one action with **`decision`** in metadata — **prefer distinct enum members** for Epic 8 searchability).  
   **And** **`created_at`** is DB-backed (existing default) so timestamp is trustworthy.

6. **Queue projection consistency (Story 4.1 follow-up)**  
   **Given** a successful resume that leaves **`human_review_gate`**  
   **When** processing completes without error  
   **Then** the **`review_queue_items`** row for that **`run_id`** is **removed** (add **`delete_pending`** or equivalent in `review_queue` repository).  
   **And** **`human_review_gate`** is safe when LangGraph **re-executes the whole node** after resume: either **preserve** **`queued_at`** on projection upsert conflict (do **not** bump timestamp on every conflict) **or** skip redundant projection writes — document the chosen approach. [Source: LangGraph interrupts — node restarts from top on resume; [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)]

7. **RBAC & errors**  
   **Given** JWT auth (Story 1.3)  
   **When** a **viewer** or unauthenticated caller uses **`POST /runs/{run_id}/resume`**  
   **Then** **401/403**  
   **And** **404** when the run is **not** in **`review_queue_items`**, when checkpoint state is missing, or when the graph is **not** actually awaiting interrupt (avoid resuming arbitrary threads).

8. **Out of scope**  
   **Given** this story  
   **Then** **do not** build Epic 6 React review UI; **do not** implement Epic 4.3 briefing generation.  
   **And** **do not** silently change **`LOW_CONFIDENCE_THRESHOLD`** or rules — human decisions update **state + audit**, not production policy tables.

9. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** add async API tests (httpx + lifespan pattern from **`tests/test_review_queue_api.py`**) proving **401/403**, happy-path **200** for at least **override** (epic AC emphasis) **or** all three decisions if cheap.  
   **And** add a **graph-level** test: interrupt → **`Command(resume=...)`** → assert **`flags.needs_human_review`** / classifications reflect the decision and **queue row** deletion (in-memory checkpointer acceptable if queue repo is mocked or DB test follows existing patterns).

## Tasks / Subtasks

- [ ] **LangGraph resume wiring (AC: #1, #9)**  
  - [ ] Import **`Command`** from **`langgraph.types`** (verify exact symbol for **1.1.6**).  
  - [ ] Implement **`graph.ainvoke(Command(resume=payload), config={"configurable": {"thread_id": str(run_id)}})`** from the route or a small **`services/`** helper (keep **`services/` → graph** dependency direction consistent with Architecture §5 — route may call graph factory).  
  - [ ] Before resuming, assert interrupt pending / valid snapshot (use **`aget_state`** / task metadata available in this version — document the exact guard).

- [ ] **`human_review_gate` semantics (AC: #2, #6)**  
  - [ ] Refactor so **`interrupt(...)` return value** drives state updates: code **after** **`interrupt()`** runs only after resume (see LangGraph interrupt docs).  
  - [ ] Apply approve / reject / override by returning a **`dict`** partial update merging into **`AgentState`** (`classifications`, `flags`).  
  - [ ] Ensure **`record_review_queue_projection`** / **`queued_at`** behavior is correct under **double execution** on resume (see AC #6).

- [ ] **HTTP API (AC: #1, #7)**  
  - [ ] Add **`POST /runs/{run_id}/resume`** to **`src/sentinel_prism/api/routes/runs.py`** (same router as **`GET /runs/{run_id}`**).  
  - [ ] Pydantic request body: **`decision`**, **`note`**, optional **`overrides`** structure.  
  - [ ] Structured logging (**NFR8**) with **`run_id`**, **`decision`**, **`user_id`** (no PII beyond internal ids).

- [ ] **Persistence (AC: #3, #4, #5, #6)**  
  - [ ] Extend **`PipelineAuditAction`** + Alembic if DB enum constraint requires it (project uses **`native_enum=False`** — usually Python-only).  
  - [ ] **`append_audit_event`** with **`actor_user_id`** and bounded metadata (`decision`, `note` excerpt, optional field-level override summary).  
  - [ ] **`review_queue`**: **`delete_by_run_id`** (or **`delete_pending`**) after successful graph completion + audit in same request transaction where feasible.

- [ ] **Tests (AC: #9)**  
  - [ ] Extend **`tests/test_review_queue_api.py`** (or adjacent module).  
  - [ ] Graph test module following **`tests/test_graph_conditional_edges.py`** patterns.

## Dev Notes

### Epic 4 context

- Epic goal: analysts work **review queue** and **briefings**; **4.1** delivered **read** APIs + projection; **4.2** delivers **mutations + analyst-attributed audit** (**FR17**). [Source: `_bmad-output/planning-artifacts/epics.md` — Stories 4.1–4.2]

### Previous story intelligence (4.1)

- **Eligibility**: **`GET /runs/{run_id}`** already requires a **`review_queue_items`** row; resume should use the **same gate** so you cannot resume arbitrary runs.  
- **Projection vs checkpoint**: **`review_queue_items`** is a **triage index**; **`AgentState`** in the checkpointer is **source of truth** for full context.  
- **Deferred from 4.1** (still true): projection vs checkpoint **crash window** — do not solve transactional outbox here; document any new window introduced by resume.  
- **NFR12**: run detail allowlisting for **`errors`/`llm_trace`** — resume responses should **not** echo raw checkpoint blobs; return a **small structured result** (e.g. `run_id`, `decision`, `status`).  
- **4.1 completion**: `GET /review-queue`, `GET /runs/{run_id}`, Postgres checkpointer, **`human_review_gate`** projection + interrupt. [Source: `_bmad-output/implementation-artifacts/4-1-review-queue-api-and-workflow-state-integration.md`]

### UX alignment

- Review **sticky actions**: approve / reject / override + **note** when policy requires; API should **validate non-empty `note`** at least for **override** (and preferably for **reject**). [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — Review queue, `ReviewActionBar`]

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **§3.2** | Mutations must keep **`AgentState`** coherent (`classifications`, `flags`, list reducers). |
| **§3.4–3.5** | Resume continues from **checkpointer**; **`audit_events`** records narrative for operators. |
| **§3.6** | **`POST /runs/{id}/resume`** is explicitly in scope for this story. |
| **§5** | **`services/`** must not import **`graph/`**; routes may orchestrate graph + repos. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| **FR17** | Approve / reject / override with **notes**. |
| **FR33** | Audit trail for **override** (and sibling decisions). |
| **FR36** | **`AgentState`** remains the shared workflow contract. |
| **NFR8** | Structured logs on resume path. |
| **NFR12** | Audit metadata stays non-secret; no prompt / tool payload leakage. |

### Library / framework requirements

| Library | Version | Notes |
| --- | --- | --- |
| **langgraph** | 1.1.6 (pinned) | **`interrupt` / `Command(resume=...)`** HITL — confirm against installed docs. |
| **langgraph-checkpoint-postgres** | 3.0.5 | Production resume must use the **same** checkpointer instance as the rest of the app lifespan. |
| **FastAPI** | 0.115.x | Match **`sources.py`** / **`runs.py`** patterns. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/api/routes/runs.py` | Add **`POST /runs/{run_id}/resume`** + schemas. |
| `src/sentinel_prism/graph/nodes/human_review_gate.py` | Post-**`interrupt`** merge logic + idempotent projection behavior. |
| `src/sentinel_prism/db/repositories/review_queue.py` | Add **delete** helper for completed reviews. |
| `src/sentinel_prism/db/models.py` | Extend **`PipelineAuditAction`**. |
| `src/sentinel_prism/db/repositories/audit_events.py` | Reuse **`append_audit_event`**; ensure new actions pass **`_coerce_action`**. |
| `tests/test_review_queue_api.py` | Resume endpoint coverage. |
| `tests/test_graph_*.py` (or new) | Interrupt + resume coverage. |

### Testing requirements

- Prefer **async** tests; reuse auth fixtures from **`tests/conftest.py`**.  
- When touching Alembic head, update **`tests/test_alembic_cli.py`** expected head id.

### Project structure notes

- **`project-context.md`**: not present — use Architecture + epics + **4.1** artifact.  
- Current **`regulatory` graph** ends at **`human_review_gate → END`** (no **`brief` / `route`** yet); resume should still leave **`AgentState`** internally consistent for future stories.

### References

- `_bmad-output/planning-artifacts/epics.md` — Story 4.2  
- `_bmad-output/planning-artifacts/prd.md` — **FR17**, **FR33**  
- `_bmad-output/planning-artifacts/architecture.md` — §3.2, §3.4–3.6  
- `_bmad-output/planning-artifacts/ux-design-specification.md` — Review queue  
- `_bmad-output/implementation-artifacts/4-1-review-queue-api-and-workflow-state-integration.md`  
- `src/sentinel_prism/services/llm/classification.py` — `classification_dict_for_state`  
- [LangGraph: Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

### Git intelligence summary

- Recent mainline history is pre–Epic 4 API work in **`git log`**; treat **4.1** implementation files as the **canonical** patterns for FastAPI + repositories + graph lifespan.

### Latest tech information

- **Interrupt resume**: Re-invoke the compiled graph with **`Command(resume=value)`** and the **same** `thread_id`. The **`interrupt()`** call receives **`value`** when the node re-executes; side effects **before** **`interrupt()`** run again unless guarded — see AC #6. [Source: [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)]

### Project context reference

- _No `project-context.md` in repo._

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List

## Story completion status

- **Status:** ready-for-dev  
- **Note:** Ultimate context engine analysis completed — comprehensive developer guide created.

### Saved questions / clarifications (non-blocking)

- Confirm **reject** semantics with product: **out-of-scope dismissal** vs **“wrong classification”** vs **discard run** — pick one and encode in state + audit.  
- Confirm whether **VIEWER** may ever **POST** resume (default: **no**, match4.1 analyst/admin gate).
