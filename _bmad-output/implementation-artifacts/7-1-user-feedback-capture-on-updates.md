# Story 7.1: User feedback capture on updates

Status: done

<!-- Ultimate context engine analysis completed - comprehensive developer guide created -->

## Story

As a **user**,
I want **to flag incorrect relevance/severity with comments**,
so that **quality improves** (**FR26**, **FR27**).

**RBAC (implementation):** The API should allow **analysts and admins** to submit feedback and deny **viewers**, matching console personas where **Analyst** carries review/override/feedback [Source: `_bmad-output/planning-artifacts/ux-design-specification.md`]. If product later extends submission to **Viewer**, adjust `require_roles` only.

## Acceptance Criteria

1. **Given** an open **update detail** view in the explorer with classification context (when available) **When** the user submits **feedback** with a valid **category** and **comment** **Then** the API persists a row and returns a stable identifier and timestamps.
2. **Given** a persisted feedback row **When** inspected **Then** it includes **`user_id`** (submitter) and a **durable link** to the **classification** the user reacted to: at minimum `normalized_update_id` + `run_id` (nullable if the update has no pipeline run) plus a **JSON snapshot** of the classification overlay (severity, impact categories, confidence) *as shown at submit time*—so later briefing edits do not rewrite feedback semantics (**FR27**).
3. **Given** a user without permission **When** they call the submit endpoint **Then** the API responds **403** (not a silent no-op).
4. **UI:** **Given** a selected update in **Update explorer** **When** the user uses the new feedback panel **Then** success or failure follows UX form patterns (inline or banner, retry on failure; no toast-only for blocked work per [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` §Form patterns / Feedback patterns]).
5. **Tests:** New integration tests cover create + forbidden role + validation (400) + happy-path persistence; existing explorer tests stay green.

## FR / product references

- **FR26:** User can submit **feedback** on an update (incorrect **relevance**, **severity**, false positive/negative) with **comments**. [Source: `_bmad-output/planning-artifacts/prd.md` — Feedback & Improvement]
- **FR27:** System persists feedback with **links** to the **classification decision** and **user identity**.

## Tasks / Subtasks

- [x] **Schema & migration** (AC: 1, 2)
  - [x] Add Alembic migration: new table (name suggestion: `update_feedback` or `classification_feedback`) with UUID PK, FK to `users.id`, FK to `normalized_updates.id` (`ON DELETE RESTRICT` to preserve chain), `run_id` UUID nullable (index with `normalized_update_id` as needed for lookups), `classification_snapshot` JSONB nullable (store overlay dict or explicit null if no overlay at submit), feedback **kind** (enum: e.g. `incorrect_relevance`, `incorrect_severity`, `false_positive`, `false_negative`—align string values with PRD vocabulary), `comment` `Text` with reasonable max length enforced in Pydantic, `created_at` timestamptz.
  - [x] Register SQLAlchemy model in `src/sentinel_prism/db/models.py` with docstring referencing Story 7.1.
- [x] **Repository** (AC: 1, 2)
  - [x] `src/sentinel_prism/db/repositories/feedback.py` (or `update_feedback.py`): `insert_feedback(session, user_id, normalized_update_id, run_id, snapshot, kind, comment)`.
- [x] **API** (AC: 1, 2, 3)
  - [x] `POST` route under `updates` (e.g. `POST /updates/{normalized_update_id}/feedback`) or dedicated router included from `main.py`—**prefer colocation** with `src/sentinel_prism/api/routes/updates.py` *or* a small `feedback.py` router prefixed consistently with `/updates/...` so OpenAPI stays clear.
  - [x] Pydantic request/response models (`extra="forbid"` like `updates.py`).
  - [x] On submit: load `NormalizedUpdateRow` by id; resolve overlay via existing `fetch_classification_overlay(db, run_id=row.run_id, normalized_update_id=row.id)`; **store that dict** (or `null` if overlay is `None`) as `classification_snapshot` together with `run_id` from the row.
  - [x] **RBAC:** `require_roles(UserRole.ANALYST, UserRole.ADMIN)` for POST. **Rationale:** UX maps **feedback** to **Analyst** (and admin tooling); **Viewer** remains read-only on submission paths [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — RBAC personas]. Document in dev notes if product later extends to Viewer.
- [x] **Web** (AC: 4)
  - [x] `web/src/components/UpdateExplorer.tsx`: add a **Feedback** subsection in the **Detail** column (below normalized/classification text): category control (select/radio), **required** multiline comment with visible label, **Submit** button, loading and error states, `aria-*` for accessibility (**NFR11** alignment).
  - [x] `POST` with bearer token; on success, clear form or show inline success; handle 401/403 distinctly.
- [x] **Tests** (AC: 5)
  - [x] `tests/test_feedback_api.py` (or extend `test_updates_api.py` if you keep routes in one module): use patterns from `tests/test_updates_api.py` (`create_app`, `LifespanManager`, `get_current_user` override, real DB or repo mock consistent with project style). Cover 403 for `VIEWER`, 404 for missing update, 400 for invalid body, 201/200 for success with assert on DB row if integration DB is used.

### Review Findings

- [x] [Review][Patch] Happy-path persistence is mocked, not verified [tests/test_feedback_api.py:213]
- [x] [Review][Patch] Feedback state can cross selected updates during draft or in-flight submit [web/src/components/UpdateExplorer.tsx:205]
- [x] [Review][Patch] Client feedback comment length is not aligned with the server cap [web/src/components/UpdateExplorer.tsx:566]

## Dev Notes

### Classification link model (no separate `classifications` table)

There is **no** first-class `classifications` table. The explorer’s “classification” overlay is derived from **briefing JSON** for `(run_id, normalized_update_id)` in `fetch_classification_overlay` [Source: `src/sentinel_prism/db/repositories/updates.py` — `fetch_classification_overlay`]. Feedback must **not** assume a future table; it must pin **`normalized_update_id` + `run_id` + snapshot** for FR27.

### Reuse, don’t fork

- **Detail payload:** `GET /updates/{id}` already returns `classification: ClassificationOverlayOut | null` [Source: `src/sentinel_prism/api/routes/updates.py`]. The POST handler should use the same overlay resolution path the GET uses so feedback matches what the user saw.
- **Auth:** follow `require_roles` + JWT patterns in existing routes; mirror `RoutingRulesAdmin` / `updates` for token headers on the client.

### Graph / FR29

Architecture allows a **`record_feedback` node** or **UI-triggered** persistence [Source: `_bmad-output/planning-artifacts/architecture.md` §3.3]. **This story** is **direct API persistence**; do **not** silently change prompts or thresholds (**FR29** is Epic 7 later stories). No new LangGraph node is required for MVP of 7.1 unless you choose to emit a **non-mutating** optional audit line—**optional** and not in AC; skip unless you need observability parity.

### Optional audit (out of scope unless time permits)

`audit_events` is append-only for pipeline actions; human feedback could later get a new `PipelineAuditAction` in Epic 8. **Not required** for 7.1 AC; prefer shipping schema + API + UI + tests first.

### Project structure (architecture reference)

- Backend: `src/sentinel_prism/api/routes/`, `db/models.py`, `db/repositories/`, `alembic/versions/`
- Web: `web/src/components/`, `web/src/App.tsx` if new nav is needed (**unlikely**—explorer already mounted)

## Architecture compliance

- **Stack:** FastAPI, async SQLAlchemy, Pydantic v2, Alembic, React + Vite + TS [Source: `_bmad-output/planning-artifacts/architecture.md` §2, §4].
- **Data:** PostgreSQL system of record including **feedback** [Source: `_bmad-output/planning-artifacts/architecture.md` §4 Data].
- **API:** REST + OpenAPI; JSON contracts [Source: `_bmad-output/planning-artifacts/architecture.md` §3.6, §4 API].

## File structure (expected touchpoints)

| Area | Files (expected) |
|------|-------------------|
| Model + migration | `src/sentinel_prism/db/models.py`, new `alembic/versions/*.py` |
| Repo | `src/sentinel_prism/db/repositories/feedback.py` (new) |
| API | `src/sentinel_prism/api/routes/updates.py` and/or new `feedback.py` + `main.py` `include_router` |
| Web | `web/src/components/UpdateExplorer.tsx` |
| Tests | `tests/test_feedback_api.py` or extended `tests/test_updates_api.py` |

## Testing requirements

- Prefer **async** tests consistent with `tests/test_updates_api.py`.
- Assert **DB state** for at least one happy path if the suite uses a disposable DB; otherwise use repository mocks with the same care as other route tests.
- Do not regress **list/detail** GET behavior.

## Cross-epic context (Epic 6 handoff)

- **Story 6.2** delivered explorer list/detail, side-by-side raw vs normalized, and classification overlay in UI [Source: `web/src/components/UpdateExplorer.tsx`, `src/sentinel_prism/api/routes/updates.py`].
- **Story 6.3** established **admin** routing CRUD with **audit** (`ROUTING_CONFIG_CHANGED`) [Source: `src/sentinel_prism/db/repositories/audit_events.py`]. Feedback does **not** reuse routing audit helpers; it is a new domain table.

**Git intelligence (recent work patterns):** Recent commits focus on **routing rules admin API/UI** and **dashboard**—follow the same **FastAPI router + Pydantic + `require_roles`** and **React fetch + `readErrorMessage`** patterns for consistency.

## Latest tech notes (project-local)

- Pin dependencies via existing `requirements.txt` / web `package.json`; do not introduce new major UI libraries for a single form—use the same **inline style / semantic HTML** approach as `UpdateExplorer` unless the repo already added a form component library (verify before adding **shadcn**-level deps).

## Project context reference

- No `project-context.md` found in repo; rely on this file + architecture + code citations above.

## Story completion status

- [x] Ultimate context engine analysis completed — **Status: review**

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

- Full `pytest` suite: 300 passed, 12 skipped.
- `web`: `npm run build` (tsc + vite) succeeded.

### Completion Notes List

- Implemented `update_feedback` table, `UpdateFeedback` / `UpdateFeedbackKind` ORM, `insert_feedback` repository, and `POST /updates/{id}/feedback` (201) on existing updates router; snapshot uses the same `fetch_classification_overlay` path as `GET` detail; RBAC analyst+admin; Pydantic strips comments and enforces 10k max length; validation errors return **422** (standard FastAPI).
- Explorer: feedback form for analyst/admin, viewer notice, inline success/error, `aria` on fields.
- `tests/test_alembic_cli.py` head revision updated to `b3c4d5e6f7a8`.

### File List

- `alembic/versions/b3c4d5e6f7a8_add_update_feedback_table.py` (new)
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/feedback.py` (new)
- `src/sentinel_prism/api/routes/updates.py`
- `web/src/components/UpdateExplorer.tsx`
- `web/src/App.tsx`
- `tests/test_feedback_api.py` (new)
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/7-1-user-feedback-capture-on-updates.md` (this file)

## Change Log

- 2026-04-26: Story 7.1 — feedback schema, API, UI, tests, Alembic head test update; status **review**.

## Story completion status

- [x] Implementation complete; definition of done passed — **Status: done**
