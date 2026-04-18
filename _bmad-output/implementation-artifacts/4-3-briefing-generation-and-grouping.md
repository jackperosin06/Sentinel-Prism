# Story 4.3: Briefing generation and grouping

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **briefings grouped by configured dimensions**,
so that **I can scan related updates** (**FR18**–**FR20**).

## Acceptance Criteria

1. **Briefing content model (FR20)**  
   **Given** in-scope classified updates available after the pipeline path that feeds briefing  
   **When** a briefing is produced  
   **Then** each briefing (or each **group** within a briefing document) includes structured sections at minimum: **what changed**, **why it matters**, **who should care**, **confidence**, **suggested actions** (may be **null** / **N/A** where not applicable, but the fields must exist in the persisted/API contract)  
   **And** prose is derived from **normalized** + **classification** fields already in **`AgentState`** (and/or joined **`normalized_updates`** rows); **do not** leak raw LLM prompts or Tavily payloads into stored briefing text (**NFR12**).

2. **Grouping (FR18)**  
   **Given** multiple updates in scope for briefing  
   **When** briefing generation runs  
   **Then** updates are **partitioned into groups** using **configurable dimensions** drawn from the PRD set: **date range** (bucket by calendar period—justify bucket, e.g. `published_at` date vs `created_at` UTC day), **severity**, **jurisdiction**, **topic** (map **topic** to a stable field—**prefer** `impact_categories` from classifications, with documented fallback if empty, e.g. `document_type` from normalized row)  
   **And** the active dimension set and ordering are **not hard-coded only**: load from **configuration** (acceptable MVP: **DB table** with one “active” row, **or** env JSON validated at startup—pick one, document in Dev Notes).

3. **Pipeline integration (Architecture §3.3–3.4)**  
   **Given** the regulatory graph today ends at **`human_review_gate` → END** or **`classify` → END** (no **`brief`** node)  
   **When** this story is complete  
   **Then** introduce **`graph/nodes/brief.py`** (`node_brief`) and wire **`AgentState.briefings`** (already **`Annotated[list[dict], operator.add]`** in `state.py`)  
   **And** topology matches the architecture reference: **`classify` → … → `brief` → END** on the non-review path, and **`human_review_gate` → `brief` → END`** after resume completes (so a reviewed run still produces a briefing from **final** classifications)  
   **And** **`brief` skips or emits empty** groups when there are **no** in-scope rows worth briefing (document behavior; avoid writing duplicate empty DB rows).

4. **Persistence (Architecture §4 Data)**  
   **Given** PostgreSQL is the system of record for briefings  
   **When** briefings are generated  
   **Then** durable rows exist (new table(s)—**Alembic** migration) keyed for list/detail: at minimum **`id`**, **`created_at`**, **grouping key / dimensions** (JSON or columns), **structured sections** (JSON matching API), **references** to member updates (`run_id`, `item_url` and/or `normalized_update_id`—justify join strategy)  
   **And** append **`audit_events`** for briefing generation (**extend `PipelineAuditAction`** with a distinct value, e.g. `briefing_generated`, for Epic 8 searchability).

5. **API — list & detail (FR19)**  
   **Given** JWT authentication (Story 1.3)  
   **When** an authenticated **VIEWER** (or wider—**align with product**: default **VIEWER+** read-only) calls **`GET /briefings`** and **`GET /briefings/{briefing_id}`**  
   **Then** list returns **paginated** briefings (reuse patterns from **`GET /review-queue`**: limit/offset caps)  
   **And** detail returns the **grouped updates** payload (ids, titles/snippets, severity, confidence, jurisdiction, links) plus the **section** blocks  
   **And** **401/403** for anonymous / unauthorized roles if you restrict below VIEWER.

6. **Out of scope (explicit)**  
   **Given** this story  
   **Then** **do not** implement **`route`** / **`delivery_events`** (Epic 5) or **React** briefing UI (Epic 6)—**REST + OpenAPI** only  
   **And** **do not** change **`LOW_CONFIDENCE_THRESHOLD`** or rules tables silently.

7. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** add **async API tests** (httpx + lifespan) for list/detail auth + happy path  
   **And** add **graph-level** test: state with multiple classified items → **`node_brief`** (or small compiled graph fragment) asserts **grouping** + **section keys**; prefer **MemorySaver** / in-memory patterns consistent with `tests/test_graph_*.py`  
   **And** if Alembic head changes, update **`tests/test_alembic_cli.py`**.

## Tasks / Subtasks

- [x] **Config for grouping dimensions (AC: #2)**  
  - [x] Define schema (ordered dimension list + optional bucket params for date).  
  - [x] Load in API + graph factory or `node_brief` via existing settings pattern (`services/llm/settings.py` style).

- [x] **`node_brief` (AC: #1–#3)**  
  - [x] Join `normalized_updates` ↔ `classifications` by stable key (**`item_url`** within `run_id`, matching classify/normalize conventions).  
  - [x] Filter **`in_scope is not False`** (exclude analyst-rejected / rules-out-of-scope).  
  - [x] Build groups; fill FR20 sections (template or deterministic string build for MVP—LLM optional **only** if you can bound cost and respect NFR12).  
  - [x] Return partial state `{"briefings": [...]}` for checkpointer; persist in same node **or** immediately after via repository called from node.

- [x] **DB + repository (AC: #4)**  
  - [x] New model(s) in `db/models.py`, repository in `db/repositories/`.  
  - [x] Migration: follow existing Alembic style.

- [x] **Graph wiring (AC: #3)**  
  - [x] `src/sentinel_prism/graph/graph.py`: replace **`CLASSIFY_NEXT_CONTINUE: END`** with **`brief`**; add edge **`brief` → END**; **`human_review_gate` → `brief` → END**.  
  - [x] Verify **interrupt + resume** still works: **`human_review_gate`** runs twice on resume—ensure **`brief`** does not double-persist duplicate briefings (idempotency: e.g. **delete-then-insert** by `run_id`, **unique constraint**, or “briefing only if none exists for run”).

- [x] **HTTP routes (AC: #5)**  
  - [x] New router module or extend `runs.py`—prefer **`api/routes/briefings.py`** + `main.py` include.  
  - [x] Pydantic models mirroring DB projection; OpenAPI tags.

- [x] **Tests (AC: #7)**  
  - [x] `tests/test_briefings_api.py` (or extend existing).  
  - [x] Graph tests for grouping + idempotency.

### Review Findings

**Review date:** 2026-04-18 — bmad-code-review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Acceptance Auditor: **No findings** (spec alignment clean). Triage below covers adversarial + edge-case findings only.

#### Decision needed (resolved 2026-04-18)

- [x] [Review][Decision] **DB-vs-state fallback semantics when ORM rows exist but all are out-of-scope** — **Resolved: DB is authoritative.** If `orm_rows` was non-empty, return `[]` (produces an empty/skipped briefing) instead of falling through to state. Converted to patch below.
- [x] [Review][Decision] **Multiple rationales aggregation in `why_it_matters`** — **Resolved: pick highest-severity member's rationale** (ties → highest-confidence → longest). Converted to patch below.
- [x] [Review][Decision] **`BriefingListItemOut.summary` source group** — **Resolved: most-severe group** (`high > medium > low`; ties → largest group by member count). Converted to patch below.
- [x] [Review][Decision] **`upsert_briefing_for_run` destructive replace vs append** — **Resolved: keep destructive upsert; fire `BRIEFING_GENERATED` only on true insert.** `upsert_briefing_for_run` must return a "created/updated" signal; `node_brief` skips the audit emit when the row already existed. Converted to patch below.
- [x] [Review][Decision] **`BriefingSectionsOut.confidence` nullable vs sentinel** — **Resolved: non-nullable string** (`confidence: str`), always populated; keep the `"Confidence not available."` sentinel when no confidences exist. Converted to patch below.

#### Patches (applied 2026-04-18 — 17/17)

- [x] [Review][Patch] **DB-authoritative filter: return `[]` when ORM rows exist but all filtered out** [`src/sentinel_prism/graph/nodes/brief.py`] *(from Decision 1)* — `_load_norm_cls_members` now treats ORM rows as authoritative; state-channel fallback fires only when the DB returned zero rows for the run.
- [x] [Review][Patch] **`why_it_matters` uses highest-severity member's rationale** [`src/sentinel_prism/graph/nodes/brief.py`] *(from Decision 2)* — added `_rationale_rank` sort key (severity → confidence → length); picks the top member's rationale deterministically.
- [x] [Review][Patch] **`BriefingListItemOut.summary` sources from most-severe group** [`src/sentinel_prism/api/routes/briefings.py`] *(from Decision 3)* — `_summary_from_groups` now ranks by `(_group_severity_rank, _group_member_count)` descending; stored `groups` ordering unchanged.
- [x] [Review][Patch] **`upsert_briefing_for_run` returns `(id, created)`; audit fires once per run** [`src/sentinel_prism/db/repositories/briefings.py` + `src/sentinel_prism/graph/nodes/brief.py`] *(from Decision 4)* — Postgres `INSERT ... ON CONFLICT (run_id) DO UPDATE ... RETURNING id, (xmax = 0) AS created`; `node_brief` skips `BRIEFING_GENERATED` on update. `test_graph_brief.fake_upsert` updated; conftest brief-db factory supports `session.execute(...).one()`.
- [x] [Review][Patch] **`BriefingSectionsOut.confidence` is non-nullable** [`src/sentinel_prism/api/routes/briefings.py`] *(from Decision 5)* — schema is now `confidence: str`; detail endpoint coerces legacy NULL rows to the documented `"Confidence not available."` sentinel.
- [x] [Review][Patch] **Upsert SELECT-then-INSERT race against `uq_briefings_run_id`** [`src/sentinel_prism/db/repositories/briefings.py`] — replaced with atomic `pg_insert(...).on_conflict_do_update(...)`; mirrors the `review_queue.upsert_pending` pattern.
- [x] [Review][Patch] **`_load_norm_cls_members` runs outside `node_brief`'s `try/except`** [`src/sentinel_prism/graph/nodes/brief.py`] — loading is now wrapped with `ValueError` → `briefing_invalid_run_id` and `SQLAlchemyError` → `briefing_load_failed` error rows; no unhandled exceptions escape the node.
- [x] [Review][Patch] **`_bucket_dt` formats tz-aware values in their own offset instead of UTC** [`src/sentinel_prism/graph/nodes/brief.py`] — added `dt = dt.astimezone(timezone.utc)` branch so buckets are always UTC-day/UTC-month.
- [x] [Review][Patch] **Empty `BRIEFING_GROUPING_DIMENSIONS=[]` accepted** [`src/sentinel_prism/services/briefing/settings.py`] — loader now raises `ValueError` on empty list.
- [x] [Review][Patch] **`BRIEFING_DATE_BUCKET` silently coerces unknown values** [`src/sentinel_prism/services/briefing/settings.py`] — loader now raises `ValueError` for values outside `{"day","month"}`.
- [x] [Review][Patch] **Duplicate grouping dimensions accepted** [`src/sentinel_prism/services/briefing/settings.py`] — loader now raises `ValueError` on duplicates with the offending names in the message.
- [x] [Review][Patch] **`except Exception` in persist block is too broad** [`src/sentinel_prism/graph/nodes/brief.py`] — persist block now catches `SQLAlchemyError` → `briefing_persist_failed` (WARN) and unexpected `Exception` → `briefing_persist_unexpected` (ERROR with `exc_info=True`).
- [x] [Review][Patch] **`list_briefings` has no secondary sort key** [`src/sentinel_prism/db/repositories/briefings.py`] — added `.order_by(created_at.desc(), id.desc())`.
- [x] [Review][Patch] **`what_changed` bullet list is unbounded** [`src/sentinel_prism/graph/nodes/brief.py`] — capped at 8000 chars to match `why_it_matters`.
- [x] [Review][Patch] **`settings.py` module docstring is broken mid-sentence** [`src/sentinel_prism/services/briefing/settings.py`] — rewrote docstring to communicate the UTC-day fallback rule without requiring the story artifact.
- [x] [Review][Patch] **`tests/conftest.py` autouse allowlist is fragile** [`tests/conftest.py`] — consolidated the two basename lists into a single `_GRAPH_DB_STUBBED_MODULES` and added an explicit `@pytest.mark.graph_db_stubbed` opt-in for new tests; both fixtures route through `_should_stub_graph_db`.
- [x] [Review][Patch] **`HTTPException` uses positional status code** [`src/sentinel_prism/api/routes/briefings.py`] — changed to keyword form `status_code=status.HTTP_404_NOT_FOUND`.

**Validation:** `pytest` → 183 passed, 10 skipped (same as pre-review baseline); `ruff` / lint clean on all changed files.

#### Deferred (real but not actionable now)

- [x] [Review][Defer] **API and node tests are happy-path only; repository layer is fully mocked** [`tests/test_briefings_api.py`, `tests/test_graph_brief.py`] — no coverage for pagination bounds, non-UUID path params, malformed JSONB, real upsert race, or role separation beyond VIEWER. Deferred — spec AC7 was satisfied by existing tests; deeper integration coverage is a follow-up that pairs with the upsert and error-handling patches above.
- [x] [Review][Defer] **`record_pipeline_audit_event` re-emits `BRIEFING_GENERATED` on any second `node_brief` execution** [`src/sentinel_prism/graph/nodes/brief.py:388-396`] — no "already emitted for this run" guard. Consistent with the audit design in scout/normalize/classify which also lacks such guards. Deferred to a cross-cutting audit idempotency story rather than fixing only the brief node.

#### Dismissed (matches spec / established convention)

- `in_scope is not False` filter only rejects the literal `False` — exactly matches spec AC2 wording and 4.2's reject flow which sets `in_scope=False` explicitly.
- `item_url` join between normalized rows and classifications — spec mandates "item_url within run_id, matching classify/normalize conventions".
- `briefings` channel uses `operator.add` (state duplication on re-execution) — spec architecture table §3.2 explicitly requires `operator.add`; DB layer handles persistence idempotency.
- `BriefingListOut` returns only `items` with no total/next metadata — matches `ReviewQueueListOut` pattern per spec AC5 ("reuse patterns from GET /review-queue").
- Audit event written after `session.commit()` — established convention in `scout.py`, `normalize.py`, `classify.py`; changing only brief would be inconsistent.

## Dev Notes

### Epic 4 cross-story context

- **4.1** established **`GET /review-queue`**, **`GET /runs/{run_id}`**, **`review_queue_items`**, Postgres checkpointer, **`human_review_gate`** projection.  
- **4.2** established **`POST /runs/{run_id}/resume`**, **`Command(resume=...)`**, **`Overwrite`** on classifications, **reject → `in_scope=False`**, queue delete on success, **`PipelineAuditAction`** human-review actions, payload bounds + validation patterns in **`runs.py`**.  
- **This story** connects the pipeline to **durable briefings** and **read APIs**—the architecture diagram’s **`brief`** node is currently **unimplemented**; **`route`** remains future work.

### Previous story intelligence (4.2)

- **Reject / out-of-scope rows** use `classification_dict_for_state` invariants; **briefing must not resurrect** rejected items for user-facing narrative.  
- **LangGraph** re-executes **`human_review_gate`** from the top on resume—any **new** side effect in that node or after must be **idempotent** (same lesson as `queued_at` / projection). **`brief`** persistence must tolerate **double invocation** if the graph is ever retried.  
- **Structured logging (NFR8)** on briefing generation: include `run_id`, `briefing_id`, group counts—no secrets.  
- **Deferred from 4.2**: concurrent resume races—briefing idempotency should not make duplicates worse; consider **unique (run_id, …)** or single briefing per run for MVP.

### UX alignment

- **BriefingDocument** pattern: document-style sections, readable hierarchy, **severity** + **confidence** surfaced (**ux-design-specification.md**). API JSON should map cleanly to future UI (stable section keys).

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| **§3.2** | **`briefings`** list channel uses **`operator.add`**; return **lists** from node. |
| **§3.3** | **`Briefing`** agent maps to **`brief`** node. |
| **§3.4** | Conditional edges from **`classify`** unchanged; **add** **`brief`** after **`human_review_gate`**. |
| **§5** | **`services/`** must not import **`graph/`**; LLM helpers stay callable from **`node_brief`**. |
| **§4** | Briefings persisted in PostgreSQL. |

### Technical requirements

| ID | Requirement |
| --- | --- |
| **FR18** | Configurable grouping dimensions. |
| **FR19** | List + detail API. |
| **FR20** | Five section fields minimum. |
| **FR36** | **`AgentState`** remains the workflow contract. |
| **NFR8** | Structured logs. |
| **NFR12** | No prompt/tool secrets in briefing store. |

### Library / framework requirements

| Library | Version | Notes |
| --- | --- | --- |
| **langgraph** | 1.1.6 (pinned) | Edits to **`StateGraph`** wiring; same checkpointer as 4.1/4.2. |
| **FastAPI** | 0.115.8 | Match **`sources.py`** / **`runs.py`** patterns. |
| **SQLAlchemy** | 2.0.40 | New models + async session usage consistent with existing repos. |

### File structure requirements

| Path | Action |
| --- | --- |
| `src/sentinel_prism/graph/nodes/brief.py` | **Create** — `node_brief`. |
| `src/sentinel_prism/graph/graph.py` | Wire **`brief`** node and edges. |
| `src/sentinel_prism/graph/nodes/__init__.py` | Export if pattern requires. |
| `src/sentinel_prism/db/models.py` | Briefing model(s); **`PipelineAuditAction`** extension. |
| `alembic/versions/` | New migration. |
| `src/sentinel_prism/db/repositories/` | Briefing repository. |
| `src/sentinel_prism/api/routes/briefings.py` | **Create** — list + detail. |
| `src/sentinel_prism/main.py` | Register router. |
| `tests/test_briefings_api.py` | **Create** (or equivalent). |
| `tests/test_graph_*.py` | Graph coverage for **`brief`**. |

### Testing requirements

- Reuse auth fixtures from **`tests/conftest.py`**.  
- Follow httpx **`AsyncClient`** + lifespan from **`tests/test_review_queue_api.py`**.  
- Graph tests: **`new_pipeline_state`**, populate **`normalized_updates`** + **`classifications`** shapes consistent with **`classification_dict_for_state`**.

### Project structure notes

- **`project-context.md`**: not present—use this story + Architecture + epics.  
- **`normalized_updates`**: **`jurisdiction`**, **`published_at`**, **`item_url`**, **`title`**, **`document_type`**—use for grouping and “what changed”.  
- Classifications: **`severity`**, **`confidence`**, **`impact_categories`**, **`rationale`**, **`item_url`**.

### References

- `_bmad-output/planning-artifacts/epics.md` — Epic 4, Story 4.3  
- `_bmad-output/planning-artifacts/prd.md` — **FR18**–**FR20**  
- `_bmad-output/planning-artifacts/architecture.md` — §3.2–3.4, §5, FR18–FR20 mapping  
- `_bmad-output/planning-artifacts/ux-design-specification.md` — **BriefingDocument**, briefing sections  
- `_bmad-output/implementation-artifacts/4-2-approve-reject-override-with-notes.md` — resume, reject semantics, idempotency lessons  
- `src/sentinel_prism/graph/state.py` — **`briefings`** field  
- `src/sentinel_prism/services/llm/classification.py` — **`classification_dict_for_state`**

### Git intelligence summary

- Recent commits center on **Epic 4** review queue + **`POST /runs/{id}/resume`** (`runs.py`, **`human_review_gate.py`**, **`review_queue` repo**). Extend the **same** API package patterns and pytest style.

### Latest tech information

- Runtime pins are authoritative in **`requirements.txt`** (`langgraph==1.1.6`, `langgraph-checkpoint-postgres==3.0.5`, `fastapi==0.115.8`). No upgrade drift required for this story unless a **brief**-specific dependency is added—justify any new package.

### Project context reference

- _No `project-context.md` in repo._

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

### Completion Notes List

- Implemented **`node_brief`** with env-driven **`BRIEFING_GROUPING_DIMENSIONS`** / **`BRIEFING_DATE_BUCKET`** (`services/briefing/settings.py`), deterministic FR20 sections, DB fallback from **`normalized_updates`** rows then **`AgentState.normalized_updates`**, **`briefings`** table + **`upsert_briefing_for_run`** (unique **`run_id`**), **`PipelineAuditAction.BRIEFING_GENERATED`**, graph edges **`classify → brief`** and **`human_review_gate → brief → END`**, **`GET /briefings`** and **`GET /briefings/{id}`** (VIEWER+).  
- Test infra: **`_brief_graph_db_factory`** in **`conftest.py`** mocks **`async_sessionmaker`** shape (`factory()` → async CM) for pipeline tests; integration audit test asserts **`BRIEFING_GENERATED`** and deletes **`briefings`** on cleanup.  
- **`pytest`:** 183 passed, 10 skipped.

### File List

- `alembic/versions/a1b2c3d4e5f7_add_briefings_table.py`
- `.env.example`
- `src/sentinel_prism/api/routes/briefings.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/briefings.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/nodes/brief.py`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/services/briefing/__init__.py`
- `src/sentinel_prism/services/briefing/settings.py`
- `tests/conftest.py`
- `tests/test_alembic_cli.py`
- `tests/test_audit_events.py`
- `tests/test_briefings_api.py`
- `tests/test_graph_brief.py`
- `tests/test_graph_conditional_edges.py`
- `tests/test_graph_human_review_resume.py`
- `tests/test_graph_shell.py`

## Change Log

- 2026-04-18 — Story context created (create-story workflow); status **ready-for-dev**.
- 2026-04-18 — Story 4.3 implemented: brief node, briefings API, migration, tests; status **review**.
- 2026-04-18 — Code review (bmad-code-review): Acceptance Auditor clean; 5 decisions resolved + 17 patches applied (upsert race, tz-aware bucketing, config strictness, audit-once-per-run, most-severe summary, etc.); status **done**.

## Story completion status

- **Status:** done  
- **Note:** All tasks + review patches complete; full **`pytest`** green (183 passed, 10 skipped). Apply Alembic revision **`a1b2c3d4e5f7`** to environments that persist briefings.

### Saved questions / clarifications (non-blocking)

- Confirm **VIEWER** may **read** briefings (recommended) vs **ANALYST-only**—PRD says “user”; default in AC is **VIEWER+**.  
- If product expects **cross-run** daily digests in MVP, clarify whether **4.3** only covers **per-run** briefings (recommended scope) with a follow-up for batch jobs.
