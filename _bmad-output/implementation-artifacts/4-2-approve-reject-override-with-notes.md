# Story 4.2: Approve, reject, override with notes

Status: done

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

- [x] **LangGraph resume wiring (AC: #1, #9)**  
  - [x] Import **`Command`** from **`langgraph.types`** (verify exact symbol for **1.1.6**).  
  - [x] Implement **`graph.ainvoke(Command(resume=payload), config={"configurable": {"thread_id": str(run_id)}})`** from the route or a small **`services/`** helper (keep **`services/` → graph** dependency direction consistent with Architecture §5 — route may call graph factory).  
  - [x] Before resuming, assert interrupt pending / valid snapshot (use **`aget_state`** / task metadata available in this version — document the exact guard).

- [x] **`human_review_gate` semantics (AC: #2, #6)**  
  - [x] Refactor so **`interrupt(...)` return value** drives state updates: code **after** **`interrupt()`** runs only after resume (see LangGraph interrupt docs).  
  - [x] Apply approve / reject / override by returning a **`dict`** partial update merging into **`AgentState`** (`classifications`, `flags`).  
  - [x] Ensure **`record_review_queue_projection`** / **`queued_at`** behavior is correct under **double execution** on resume (see AC #6).

- [x] **HTTP API (AC: #1, #7)**  
  - [x] Add **`POST /runs/{run_id}/resume`** to **`src/sentinel_prism/api/routes/runs.py`** (same router as **`GET /runs/{run_id}`**).  
  - [x] Pydantic request body: **`decision`**, **`note`**, optional **`overrides`** structure.  
  - [x] Structured logging (**NFR8**) with **`run_id`**, **`decision`**, **`user_id`** (no PII beyond internal ids).

- [x] **Persistence (AC: #3, #4, #5, #6)**  
  - [x] Extend **`PipelineAuditAction`** + Alembic if DB enum constraint requires it (project uses **`native_enum=False`** — usually Python-only).  
  - [x] **`append_audit_event`** with **`actor_user_id`** and bounded metadata (`decision`, `note` excerpt, optional field-level override summary).  
  - [x] **`review_queue`**: **`delete_by_run_id`** (or **`delete_pending`**) after successful graph completion + audit in same request transaction where feasible.

- [x] **Tests (AC: #9)**  
  - [x] Extend **`tests/test_review_queue_api.py`** (or adjacent module).  
  - [x] Graph test module following **`tests/test_graph_conditional_edges.py`** patterns.

### Review Findings

<!-- Added 2026-04-18 — adversarial code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). -->

- [x] [Review][Patch] Override without `item_url` cascades to every row when no in-scope rows exist — drop the all-rows fallback: apply only to `in_scope` rows and no-op with a warning log when none match (resolved from decision-needed: D1) [`src/sentinel_prism/graph/nodes/human_review_gate.py:68-73`]
- [x] [Review][Patch] Override audit metadata omits before/after field-level summary — add a minimal per-item summary (`{item_url → {severity, confidence, urgency, rationale, impact_categories}}` of the patch) so the audit row reconstructs the analyst change (resolved from decision-needed: D2) [`src/sentinel_prism/api/routes/runs.py:472-481`]
- [x] [Review][Patch] `ClassificationPatchIn.impact_categories` is unvalidated free strings — validate against `services/llm/classification.IMPACT_CATEGORIES_VOCAB` (422 on unknown token) so analyst overrides cannot dilute aggregations (resolved from decision-needed: D3) [`src/sentinel_prism/api/routes/runs.py:152-180`]
- [x] [Review][Patch] Reject destroys original classification evidence — snapshot the pre-reject classification rows into the reject audit `metadata` (forensic trail) and update Dev Notes with the "out-of-scope dismissal" justification (resolves the saved question) (resolved from decision-needed: D4) [`src/sentinel_prism/graph/nodes/human_review_gate.py:37-50`, `src/sentinel_prism/api/routes/runs.py:472-481`]
- [x] [Review][Patch] Duplicate `item_url` in overrides silently last-wins — reject at API layer with 422 on duplicate `item_url` values in the `overrides` list (resolved from decision-needed: D5) [`src/sentinel_prism/api/routes/runs.py:183-196`]
- [x] [Review][Defer] Override `item_url` comparison is strict-string equality — no normalization (trailing slashes, case, query-string variants silently fail to match). `deferred — UI passes item_url verbatim from the classification row, so exact-match is expected to hold in practice; revisit if real integration produces mismatches.` (resolved from decision-needed: D6) [`src/sentinel_prism/graph/nodes/human_review_gate.py:63-67`]

- [x] [Review][Patch] Override patch with `item_url` that matches no row is silently dropped — no error row, no warning, analyst believes override applied [`src/sentinel_prism/graph/nodes/human_review_gate.py:63-67`]
- [x] [Review][Patch] Bare `except Exception` around `graph.ainvoke` swallows `asyncio.CancelledError` and returns 502 for internal errors — 502 Bad Gateway is semantically wrong (the graph is in-process) and `CancelledError` must re-raise [`src/sentinel_prism/api/routes/runs.py:457-470`]
- [x] [Review][Patch] Resume payloads lack size bounds — `note`, `overrides` list, `item_url`, `rationale`, `impact_categories` accept unbounded content (DoS / audit-bloat from an authenticated analyst) [`src/sentinel_prism/api/routes/runs.py:152-196`]
- [x] [Review][Patch] Route returns `status="completed"` even if graph re-interrupts during resume — handler discards `graph.ainvoke` return value and never re-checks `aget_state` for pending interrupts [`src/sentinel_prism/api/routes/runs.py:458-516`]
- [x] [Review][Patch] No timeout on `graph.ainvoke(Command(resume=...))` — a hung downstream node stalls the request indefinitely [`src/sentinel_prism/api/routes/runs.py:457-458`]
- [x] [Review][Patch] `_require_note_and_patches` validator doesn't reject `overrides` list on approve/reject — API contract lets callers send ignored payload fields [`src/sentinel_prism/api/routes/runs.py:188-195`]
- [x] [Review][Patch] `ClassificationPatchIn` accepts patches with all fields `None` (true no-op) — override decision silently makes no state change [`src/sentinel_prism/api/routes/runs.py:152-180`]
- [x] [Review][Patch] `ResumeRunOut.decision` typed as `str` instead of `ReviewDecision` — inconsistent with the enum declared above; handler has to pass `body.decision.value` explicitly [`src/sentinel_prism/api/routes/runs.py:198-201`]
- [x] [Review][Patch] Override graph-level test does not assert aggregate `flags.needs_human_review` is cleared (AC #9 — approve and reject tests do) [`tests/test_graph_human_review_resume.py:307-311`]
- [x] [Review][Patch] API `test_resume_run_override_ok` does not inspect `append_audit_event` kwargs `metadata` — AC #3 "note persists in audit metadata" is not exercised [`tests/test_review_queue_api.py:858-862`]
- [x] [Review][Patch] Malformed `state.classifications` (non-list) + valid resume clears `needs_human_review` flag but updates zero rows — should record an error row [`src/sentinel_prism/graph/nodes/human_review_gate.py:30-34, 110-141`]
- [x] [Review][Patch] Non-dict `interrupt()` return / unknown `decision` at graph layer returns only `errors` and leaves `flags["needs_human_review"]=True` — run is stuck (no pending interrupt, so route returns 404 on future resumes and queue row is not cleaned by node) [`src/sentinel_prism/graph/nodes/human_review_gate.py:98-109, 189-206`]
- [x] [Review][Patch] `delete_pending_by_run_id` UUID-parse failure returns `False` (same as "not found"), hiding malformed input — route guarantees a `UUID`, so raise on bad input instead of swallowing [`src/sentinel_prism/db/repositories/review_queue.py:87-90`]
- [x] [Review][Patch] `tests/test_graph_human_review_resume.py` ends without a trailing newline [`tests/test_graph_human_review_resume.py:311`]
- [x] [Review][Patch] Magic constants `4000` (note excerpt) and `20` (override URL cap) inline in route — extract to named module-level constants with a comment [`src/sentinel_prism/api/routes/runs.py:472, 478`]
- [x] [Review][Patch] Design-intent comments removed from `human_review_gate.py` (defense-in-depth `sid=str(sid_raw)` rationale and `queued_at` pre-interrupt capture explanation) — restore them, or the rationale is lost [`src/sentinel_prism/graph/nodes/human_review_gate.py`]

- [x] [Review][Defer] Graph-mutation + audit + queue-delete are non-atomic across process failure — pre-existing projection-vs-checkpoint window from Story 4.1; resume introduces a new window (graph commits checkpoint, then DB audit/delete can fail). `deferred, pre-existing — crash window acknowledged in 4.1 Dev Notes; Epic 8 outbox work out of 4.2 tactical scope; document new window in 4.2 Dev Notes.` [`src/sentinel_prism/api/routes/runs.py:457-494`]
- [x] [Review][Defer] No concurrency control on `POST /runs/{run_id}/resume` — concurrent analyst clicks double-resume, double-audit, and race queue delete. `deferred — real new window but no advisory lock / SELECT FOR UPDATE / idempotency token in 4.2 scope; document or track separately for Epic 4.3 UX hardening.` [`src/sentinel_prism/api/routes/runs.py:382-494`]

## Dev Notes

### Reject semantics (AC #2 — resolved saved-question)

**Chosen semantics: out-of-scope dismissal.** When an analyst rejects a queued run, every **`in_scope=True`** classification row is rewritten via **`_rejected_row`** to the out-of-scope shape defined by **`classification_dict_for_state`** (`in_scope=False`, `severity=None`, `impact_categories=[]`, `urgency=None`, `confidence=0.0`, `rationale="analyst_rejected"`, `rule_reasons=["analyst_rejected"]`) and the aggregate **`flags.needs_human_review`** is cleared. Rejected runs therefore behave identically to rules-dismissed runs for every downstream consumer (routing, briefing, dashboards) and the queue row is deleted.

**Why this shape, not "wrong classification" or "discard run":**

- Downstream Epic 5/6 consumers already know how to treat `in_scope=False` rows (they are silently dropped from routing + briefing). A third "rejected but kept in-scope" mode would force every downstream node to learn a fourth classification state.
- "Discard run" would require a new run-level status column and a new migration to mask rejected runs from `GET /runs`, `audit_events_tail`, and future `briefing_groupings`. Out-of-scope dismissal is a state-only change with zero schema churn.
- The `classification_dict_for_state` invariant for `in_scope=False` rows is already tested by Story 3.4's `rules_rejected` path; we reuse that invariant rather than inventing a parallel one for analyst rejection.

**Forensic trail:** `_rejected_row` deliberately destroys the original classification fields in **state** (that is the point of an out-of-scope shape), so the pre-reject row — `severity`, `urgency`, `confidence`, `rationale`, `impact_categories` — is copied into **`audit_events.metadata.pre_reject_snapshot`** (bounded to **20 rows** and **512 chars per rationale**). Audit storage is the forensic log; state stays the downstream contract.

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

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented **`POST /runs/{run_id}/resume`** with **`Command(resume=...)`**, queue + interrupt guards (**404** when not pending), **`PipelineAuditAction`** approve/reject/override, analyst **`actor_user_id`**, and **`delete_pending_by_run_id`** after successful graph completion.
- Refactored **`human_review_gate`**: **`interrupt()`** return drives **`Overwrite`** on **`classifications`**; **reject** path sets **`analyst_rejected`** / **`in_scope: false`** per Dev Notes justification (out-of-scope dismissal); **`upsert_pending`** no longer bumps **`queued_at`** on conflict.
- Tests: API (auth, RBAC, 404 paths, override happy path, 422 note rule) + graph **`tests/test_graph_human_review_resume.py`** (approve / reject / override). Full suite **176 passed**.
- **Code review follow-up (2026-04-18):** Hardened `POST /runs/{run_id}/resume` per adversarial review — payload bounds on `note`/`overrides`/`item_url`/`rationale`/`impact_categories`, strict `IMPACT_CATEGORIES_VOCAB` validation for analyst overrides, 422 on duplicate `item_url`, rejection of `overrides` on approve/reject, rejection of all-None override patches, 504 on resume timeout, narrowed exception handling (CancelledError propagates, 500 replaces 502 for internal errors, HTTPException re-raises), post-resume `aget_state` re-check so a re-interrupted graph does not claim `status="completed"` or delete the queue row, field-level `override_patches` summary in audit metadata, `pre_reject_snapshot` in reject audit metadata. `human_review_gate` drops the "apply to all rows" fallback (in-scope only; unmatched patches surface as `override_unmatched_item_url` error rows), clears `flags["needs_human_review"]` on any error path so malformed resumes cannot orphan the queue row, guards malformed `classifications` state, and restores defense-in-depth design comments. `delete_pending_by_run_id` now raises on malformed UUID input instead of silently returning `False`.

### File List

- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/review_queue.py`
- `src/sentinel_prism/graph/nodes/human_review_gate.py`
- `src/sentinel_prism/api/routes/runs.py`
- `tests/test_review_queue_api.py`
- `tests/test_graph_human_review_resume.py`

## Change Log

- 2026-04-19 — Story 4.2 implemented: resume API, human-review state merges, audit actions, queue delete, tests (status → **review**).
- 2026-04-18 — Code-review hardening applied (21 patches + 2 deferred, 6 decision-needed resolved). Reject semantics justified in Dev Notes; override audit metadata gains field-level summary and pre-reject snapshot; resume API gains payload bounds, strict vocab validation, duplicate-URL rejection, timeout, narrowed exception handling, and post-resume interrupt re-check. See `### Review Findings`.

## Story completion status

- **Status:** done
- **Note:** Implementation + code-review hardening complete; pytest **176 passed**, **10 skipped**. All 9 ACs satisfied; 21 review patches applied, 6 decision-needed resolved, 3 items deferred (see `### Review Findings` and `deferred-work.md`).

### Saved questions / clarifications (non-blocking)

- ~~Confirm **reject** semantics with product: **out-of-scope dismissal** vs **"wrong classification"** vs **discard run**.~~ **Resolved 2026-04-18** during code review: **out-of-scope dismissal** (see Dev Notes → "Reject semantics" for justification).
- Confirm whether **VIEWER** may ever **POST** resume (default: **no**, match 4.1 analyst/admin gate).
