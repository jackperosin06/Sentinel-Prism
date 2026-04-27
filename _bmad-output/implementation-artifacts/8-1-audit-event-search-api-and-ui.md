# Story 8.1: Audit event search API and UI

Status: done

<!-- Ultimate context engine analysis completed — comprehensive developer guide created. -->

## Story

As an **operator**,
I want **to search audit history by update id, source, time range, and user**,
so that **I can investigate incidents** (**FR34**, Epic 8).

## Acceptance Criteria

1. **Search API with filters and pagination:** **Given** authenticated users with a role that can operate the console **When** they call the audit search endpoint with any combination of supported filters **Then** matching `audit_events` rows are returned in a stable sort (recommend **`created_at` descending**, then **`id`**) with **cursor- or offset-based pagination** (pick one pattern consistent with existing list APIs such as `UpdateExplorer` / `GET /updates`).

2. **FR34 filter surface (minimum):**
   - **`run_id`** (UUID) — exact match on `AuditEvent.run_id`.
   - **`source_id`** (UUID) — exact match on `AuditEvent.source_id` (nullable column: SQL must treat “filter omitted” vs “no match” correctly).
   - **`created_after` / `created_before`** (timezone-aware datetimes) — half-open or inclusive range documented in OpenAPI.
   - **`actor_user_id`** (UUID) — exact match on `AuditEvent.actor_user_id` (nullable; same SQL care as `source_id`).
   - **`normalized_update_id`** (UUID) — resolves `NormalizedUpdateRow` by primary key; if **`run_id` is non-null** on that row, filter audits by that `run_id`; if **`run_id` is null** (today’s default from `insert_normalized_update`), **fall back** to `source_id` match on the update row **and** a **time window** around `normalized_updates.created_at` (e.g. ±24h, bounded and documented) so operators still get a useful slice, **or** return only audits strictly linked by `run_id` if you first land a small plumbing story—**choose one coherent behavior and document it** in OpenAPI (no silent wrong results).

3. **Optional `action` filter:** Allow filtering by `PipelineAuditAction` (string enum values as persisted, e.g. `human_review_approved`) so Epic 8 operators can narrow to config vs pipeline events.

4. **Response contract:** Each item exposes at least: `id`, `created_at`, `run_id`, `action`, `source_id`, `actor_user_id`, `metadata` (JSON object as stored—column name `metadata` in DB maps to `event_metadata` in ORM). Use Pydantic v2 with **`model_config = ConfigDict(extra="forbid")`** on public schemas. Do **not** leak raw captures, prompts, or secrets in new fields (**NFR12** — metadata is already bounded at write time; this story must not change trust boundaries).

5. **RBAC:** Any **authenticated** `UserRole` (`admin`, `analyst`, `viewer`) may **read** audit search (UX persona **Sam / operator**; aligns with “investigate incidents” for engineers and read-only viewers). **No** anonymous access. If product later restricts viewers, that is a follow-up—default to **all roles** unless PM objects.

6. **Repository layer:** Implement query in `src/sentinel_prism/db/repositories/` (extend `audit_events.py` or add `audit_search.py`) using **indexed columns** where possible (`ix_audit_events_run_id_created_at`, `ix_audit_events_action`, `ix_audit_events_source_id`). If JSONB predicates are added later, document index needs; **avoid full table scan** on hot paths for typical page sizes.

7. **Web UI:** New React section (e.g. `AuditEventSearch.tsx`) reachable from the signed-in console: filter inputs for the same dimensions the API supports (at minimum run id, source id, time range, user id, optional update id, optional action), **Load** / **Next page** behavior, tabular results, and **`readErrorMessage`** from `web/src/httpErrors.ts` on failure—patterns consistent with `UpdateExplorer` and admin surfaces. Place the section where operators will find it (e.g. after `UpdateExplorer` in `App.tsx`, with a short heading explaining **Audit search (FR34)**).

8. **Tests:** Integration tests under `tests/` cover happy path pagination, **403 never** for authenticated roles on read (smoke one role if redundant), filter combination, and **404/empty** behavior for unknown `normalized_update_id` per your chosen contract.

## Tasks / Subtasks

- [x] **Repository** (AC: 1–3, 6)
  - [x] `search_audit_events(...)` with typed filters + limit/offset (or cursor) + total count if the API pattern requires it.
  - [x] Efficient `select` with `where` clauses; avoid loading unbounded rows.
- [x] **API route** (AC: 1–5)
  - [x] New router module, e.g. `api/routes/audit_events.py`, prefix e.g. `/audit-events` or `/audit/events` (pick one; document in OpenAPI).
  - [x] `Depends(get_db)`, `Depends(require_roles(UserRole.ADMIN, UserRole.ANALYST, UserRole.VIEWER))` (or `get_current_user` only if you intentionally allow any authenticated user).
  - [x] `include_router` in `main.py`.
- [x] **Web** (AC: 7)
  - [x] New component + `App.tsx` wiring; reuse styling conventions (system-ui, maxWidth consistent with `App`).
- [x] **Tests** (AC: 8)
  - [x] Mirror patterns from `tests/test_golden_set_policy_api.py` / `tests/test_feedback_metrics_api.py` for auth + DB fixtures.
- [x] **Optional hardening (if time):** Plumb `run_id` into `persist_new_items_after_dedup` / `insert_normalized_update` when the ingestion path has a correlation id, so **`normalized_update_id` → `run_id`** becomes precise without time-window heuristics—**only if** you can do it without ballooning scope; otherwise document the heuristic in OpenAPI.
  - [x] **Done as:** ±24h inclusive window + `source_id` when `normalized_updates.run_id` is null; documented on `GET /audit-events` query params. Run-id plumbing deferred.

### Review Findings

- [x] [Review][Patch] No test asserts API result ordering (created_at desc, then id desc) — AC #1. [`tests/test_audit_event_search_api.py`]

- [x] [Review][Defer] Pagination if `total` shrinks between page fetches can strand or confuse offset-based UX — pre-existing list pattern; not required by 8.1. [`web/src/components/AuditEventSearch.tsx:130-137`] — deferred, pre-existing

- [x] [Review][Decision] **`run_id` + `normalized_update_id` when the update has no `run_id`:** Resolved as **400** — reject when both are supplied and the resolved update has `run_id is null`; OpenAPI description updated. [`src/sentinel_prism/api/routes/audit_events.py`]

- [x] [Review][Patch] Integration coverage for `normalized_update_id` when `NormalizedUpdateRow.run_id` is null (±24h + `source_id`) and unit test for new **400**. [`tests/test_audit_event_search_api.py`]

## Dev Notes

### Product / scope guardrails

- **Story 8.2** owns **replay**; do **not** implement replay or checkpointer mutation here.
- **Story 8.3** owns **cross-service observability dashboard**; this story is **SQL audit log search + UI** only.
- **FR33** is satisfied by **emitting** audit rows (Epic 3 / 6 / 7); **8.1** makes them **discoverable**.

### Architecture compliance

- **Stack:** FastAPI, async SQLAlchemy 2.x, Pydantic v2, React + Vite + TypeScript. REST + OpenAPI from FastAPI models. [Source: `_bmad-output/planning-artifacts/architecture.md` §4–5]
- **Audit model:** Append-only `audit_events`; **reads** are allowed; no new update/delete helpers. [Source: `src/sentinel_prism/db/models.py` `AuditEvent`]
- **Config / sentinel `run_id` values:** Routing, classification, and golden-set config applies use fixed UUIDs in `src/sentinel_prism/db/audit_constants.py`—search should return these rows when filters match; document in dev help text for operators.
- **Existing read helper:** `list_recent_for_run` in `audit_events.py` is **run-scoped**; general search **extends** read patterns, not duplicate ad hoc SQL in the route. [Source: `src/sentinel_prism/db/repositories/audit_events.py`]

### File structure (expected touchpoints)

| Area | Files |
|------|--------|
| Repository | `src/sentinel_prism/db/repositories/audit_events.py` (or new sibling) |
| API | `src/sentinel_prism/api/routes/audit_events.py`, `src/sentinel_prism/main.py` |
| Web | `web/src/components/AuditEventSearch.tsx` (name flexible), `web/src/App.tsx` |
| Tests | `tests/test_audit_event_search_api.py` (or equivalent) |

### Testing requirements

- Use existing async DB test fixtures; create audit rows via `append_audit_event` or factories if available.
- Assert sort order and pagination boundaries.
- Run `pytest` for new module; `npm run build` if TypeScript changes.

### UX alignment

- **Sam (Engineer / operator)** needs **scannable** tabular audit results and obvious **correlation** fields (`run_id`, `action`, timestamps). Desktop-first; keyboard-focusable controls; labels on inputs (**WCAG 2.1 Level A** minimum on primary flows). [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — personas, operator confidence]

## Previous story intelligence (Epic 7 — 7.4)

- **7.4** added **`GOLDEN_SET_CONFIG_AUDIT_RUN_ID`**, **`PipelineAuditAction.GOLDEN_SET_CONFIG_CHANGED`**, and rich **admin** patterns (draft/apply, history). Audit search will surface those rows; **action** filter should include `golden_set_config_changed`.
- **Review learnings:** partial-draft merging, history ordering, and **integration tests that assert real API JSON**—apply the same rigor to audit search list responses. [Source: `_bmad-output/implementation-artifacts/7-4-golden-set-policy-and-configuration-history.md`]
- **RBAC contrast:** Admin-only routes use `get_db_for_admin`; **this story is intentionally broader** (all authenticated roles) per FR34 operator persona—do not copy admin-only deps blindly.

## Git intelligence (recent work)

- Recent commits: Epic **7.4** golden-set policy, **7.2/7.3** metrics and classification policy—patterns for **admin API modules**, `append_*_audit`, and React admin sections are fresh in `main.py` and `web/src/App.tsx`.

## Latest tech notes (project-local)

- Stay on the repo’s **pinned** FastAPI / Pydantic / SQLAlchemy versions; no drive-by upgrades.
- Prefer **uuid.UUID** query params with validation over raw strings.

## Project context reference

- No `project-context.md` found in repo; rely on this story + `architecture.md` + code cited above.

## Dev Agent Record

### Agent Model Used

Composer (dev-story workflow)

### Debug Log References

- Route dependency order: `require_roles` before `get_db` so unauthenticated requests return **401** without `DATABASE_URL`.

### Completion Notes List

- Implemented `search_audit_events` in `audit_events.py` with offset pagination, `total` count, sort `created_at DESC, id DESC`, and `normalized_update` resolution (run_id or ±24h + source_id).
- Added `GET /audit-events` with Pydantic `extra="forbid"` response models; 404 for unknown normalized update; 400 for conflicting `run_id` vs resolved update.
- Added `AuditEventSearch.tsx` after `UpdateExplorer` in `App.tsx`.
- Tests: unit-style with `get_db` overrides where needed; `@pytest.mark.integration` seeds DB and asserts filters/pagination/404.

### File List

- `src/sentinel_prism/db/repositories/audit_events.py`
- `src/sentinel_prism/api/routes/audit_events.py`
- `src/sentinel_prism/main.py`
- `web/src/components/AuditEventSearch.tsx`
- `web/src/App.tsx`
- `tests/test_audit_event_search_api.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Change Log

- **2026-04-27:** Story 8.1 implemented — audit search API, repository query, React UI, tests; sprint status → review.
- **2026-04-27:** Code review (bmad-code-review) — open patch items; status → in-progress.
- **2026-04-27:** Review patch applied — integration test asserts `ORDER BY created_at DESC, id DESC` vs DB; status → done.
- **2026-04-27:** Review follow-up — **400** when `run_id` + `normalized_update_id` and update has no `run_id`; integration + unit tests for heuristic and conflict; status → done.
