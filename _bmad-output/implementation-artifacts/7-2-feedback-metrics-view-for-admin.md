# Story 7.2: Feedback metrics view for admin

Status: done

<!-- Ultimate context engine analysis completed - comprehensive developer guide created -->

## Story

As an **admin**,
I want **override rate and category distributions (and the ability to export or view them)**,
so that **I can monitor model health** (**FR28**).

## Acceptance Criteria

1. **Given** persisted **user feedback** rows (`update_feedback` from Story 7.1) **When** an **admin** requests feedback metrics **Then** the API returns an aggregate **category distribution**: counts (and, for the UI, percentages of total feedback in the window) for each `UpdateFeedbackKind` (`incorrect_relevance`, `incorrect_severity`, `false_positive`, `false_negative`).

2. **Given** **human review** decisions recorded in **`audit_events`** (Story 4.2) **When** an **admin** requests the same metrics payload **Then** the response includes an **override rate** derived from **review decisions**: e.g.  
   `human_review_overridden_count / (human_review_approved_count + human_review_rejected_count + human_review_overridden_count)` in the selected **time window**, with **safe handling** when the denominator is **zero** (return `null` or `0.0` with a clear contract—document the choice in OpenAPI and UI).

3. **RBAC:** **Given** a user who is not **admin** **When** they call the metrics endpoint(s) **Then** the API returns **403** (same family as `GET /admin/routing-rules`).

4. **UI:** **Given** an **admin** session in the **web console** **When** they open the new **Feedback metrics** section **Then** the **category distribution** and **review override rate** (and any supporting totals, e.g. total feedback rows, total review decisions) are **readable in one view** (dense but scannable table or summary cards—match **Dashboard** / **RoutingRulesAdmin** inline styling; **solid** backgrounds for data, **NFR11**-friendly labels; no toast-only for load failures per UX form/patterns [Source: `_bmad-output/planning-artifacts/ux-design-specification.md`]).

5. **FR28 export:** **Given** the same aggregates **When** the admin uses **export** (e.g. **CSV** download or a clearly labeled control) **Then** a file downloads containing at least the **same** summary numbers / breakdowns the UI shows for the current window (minimum viable: one CSV with sections or columns for feedback-by-kind and review-decision counts + computed override rate).

6. **Tests:** Integration tests assert **403** for **viewer** / **analyst** (whichever the project standard is for “not admin”) and **200** for **admin** with shape checks; avoid regressing existing **dashboard** or **feedback POST** tests.

## FR / product references

- **FR28:** Admin can **export** or **view** aggregated **feedback metrics** (e.g. **override rate**, **category distribution**). [Source: `_bmad-output/planning-artifacts/prd.md` — Feedback & Improvement]

## Tasks / Subtasks

- [x] **Repository / SQL** (AC: 1, 2)
  - [x] Add `db/repositories/feedback_metrics.py` (or extend `feedback.py` with read helpers): async queries for (a) `COUNT(*)`, `GROUP BY kind` on `update_feedback` with optional `created_at` range; (b) `COUNT` grouped by `PipelineAuditAction` for `human_review_approved`, `human_review_rejected`, `human_review_overridden` on `audit_events` with the **same** optional time window (filter on `created_at` or the audit model’s timestamp column—verify in `models.py`).

- [x] **API** (AC: 1, 2, 3, 5)
  - [x] New router, e.g. `prefix="/admin/feedback-metrics"`, `tags=["admin","feedback"]`, `include_router` in `src/sentinel_prism/main.py`.
  - [x] Use **`get_db_for_admin`** from `api/deps.py` (admin-only + DB), matching **`routing_rules`** / **sources** admin pattern [Source: `src/sentinel_prism/api/routes/routing_rules.py`].
  - [x] Pydantic response model(s): `extra="forbid"`; include `window` metadata (`start`/`end` or `since`/`until`) if you accept query params for the range.
  - [x] `GET` JSON for aggregates; add **`GET` CSV** (e.g. `Accept: text/csv` or `?format=csv`) **or** a dedicated `GET .../export` returning `StreamingResponse` with `text/csv; charset=utf-8`.

- [x] **Web** (AC: 4, 5)
  - [x] New component, e.g. `web/src/components/FeedbackMetricsAdmin.tsx`, `fetch` with bearer token, error surfaces consistent with `RoutingRulesAdmin` (403 message for non-admin should not apply when component only renders for `me.role === "admin"`—still handle 401).
  - [x] **Mount in** `web/src/App.tsx` **immediately after** (or before) `RoutingRulesAdmin` for **admin** users so both **routing** and **feedback metrics** are reachable without extra routing library.

- [x] **Tests** (AC: 6)
  - [x] New `tests/test_feedback_metrics_api.py` (or under existing admin test module): `create_app`, token overrides for `admin` / `analyst` / `viewer`, assert JSON body or CSV for small fixture data. Reuse patterns from `tests/test_feedback_api.py` and any dashboard tests.

### Review Findings

- [x] [Review][Patch] Validate `since` / `until` query params before aggregating metrics [`src/sentinel_prism/api/routes/feedback_metrics.py`:113] — fixed with timezone-aware window validation and 400 responses for reversed ranges.
- [x] [Review][Patch] Add non-admin RBAC coverage for the CSV export endpoint [`tests/test_feedback_metrics_api.py`:45] — fixed with analyst/viewer 403 coverage for `/admin/feedback-metrics/export`.
- [x] [Review][Patch] Exercise the real feedback/audit aggregation queries with persisted fixture rows [`tests/test_feedback_metrics_api.py`:82] — fixed with an integration test that seeds `update_feedback` and `audit_events` rows and verifies windowed aggregate counts.

## Dev Notes

### What “override rate” and “category distribution” mean here

- **Category distribution (feedback):** Straightforward aggregation over **`update_feedback.kind`** (Story 7.1). This is the **user feedback** taxonomy, not the same as **severity** histogram on the **dashboard** (that comes from **classify** audit metadata per Story 6.1).

- **Override rate (human review):** PRD’s **e.g.** pairs **feedback** with **operational** review behavior. The codebase already records **human review** outcomes as **distinct audit actions** [Source: `src/sentinel_prism/db/models.py` — `PipelineAuditAction.HUMAN_REVIEW_APPROVED | REJECTED | OVERRIDDEN`]. Use these for **analyst / review-queue override rate**; do **not** invent a second definition unless product expands FR28.

- If **no** `HUMAN_REVIEW_*` events exist in the window, denominator **zero** is expected on fresh dev DBs—UI should show “—” or “No review decisions in window” without crashing.

### Reuse, do not fork

- **Auth / admin DB access:** `get_db_for_admin` + router prefix `/admin/...` like **`routing_rules`**.
- **HTTP client + errors:** `readErrorMessage` from `web/src/httpErrors.ts` as in **`RoutingRulesAdmin`** / **`UpdateExplorer`**.
- **Styling:** Inline styles consistent with **`Dashboard`**, **`RoutingRulesAdmin`**, **`UpdateExplorer`**—no new component library for a read-only table.

### Out of scope (later epics)

- **FR29** (governed prompt/threshold proposals) and **7.3** are **separate**; this story is **read-only** aggregates + export.
- **Epic 8** audit **search** UI: optional cross-link in copy only; do not build a second audit browser here.

### Project structure (architecture reference)

- Backend: `src/sentinel_prism/api/routes/`, `db/repositories/`, `main.py`
- Web: `web/src/components/`, `web/src/App.tsx`
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §2, §4, §5]

## Architecture compliance

- **Stack:** FastAPI, async SQLAlchemy, Pydantic v2, React + Vite + TypeScript.
- **RBAC:** **Admin** for configuration / metrics per PRD; enforce on **API** and reflect in **UI** visibility.
- [Source: `_bmad-output/planning-artifacts/architecture.md`]

## File structure (expected touchpoints)

| Area | Files (expected) |
|------|------------------|
| Repository | `src/sentinel_prism/db/repositories/feedback_metrics.py` (or extend `feedback.py`) |
| API | `src/sentinel_prism/api/routes/feedback_metrics.py` (name illustrative) + `src/sentinel_prism/main.py` |
| Web | `web/src/components/FeedbackMetricsAdmin.tsx`, `web/src/App.tsx` |
| Tests | `tests/test_feedback_metrics_api.py` (new) |

## Testing requirements

- Use **async** test style and fixtures consistent with `tests/test_feedback_api.py` / admin route tests.
- Cover **403** for non-admin and **200** for admin; assert **key fields** present (`kind_counts`, review decision counts, `override_rate` or equivalent).
- Run **full** `pytest` before marking story done; `npm run build` for web if TS changes.

## Previous story intelligence (7.1)

- **Table:** `update_feedback` with **`kind`**, **`created_at`**, **`classification_snapshot`**, etc. [Source: `_bmad-output/implementation-artifacts/7-1-user-feedback-capture-on-updates.md`]
- **Repository:** `insert_feedback` in `feedback.py`—add read-side alongside or in a new module to keep **write** and **read** concerns clear.
- **Explorer:** `UpdateExplorer` is **analyst/admin** for **submit**; this story is **admin-only** for **read** metrics—different RBAC than POST feedback.

## Git intelligence (recent work)

- Latest relevant commit: **Story 7.1** — `updates.py`, `feedback` repo, `UpdateExplorer`.
- **Pattern:** Admin UIs and **`/admin/...`** routes from **6.3** (routing rules).

## Latest tech notes (project-local)

- Pin via existing `requirements.txt` / `package.json`. Prefer **`sqlalchemy` `select` + `func.count`** or raw aggregates—avoid N+1 for metrics.

## Project context reference

- No `project-context.md` in repo; rely on this file + `architecture.md` + code paths above.

## Story completion status

- [x] All tasks done; review findings fixed; focused feedback metrics tests green — **Status: done**

*Ultimate context engine analysis completed - comprehensive developer guide created*

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

- `pytest` 305 passed, 13 skipped; `web`: `npm run build` success.
- Code review fixes: `python3 -m pytest tests/test_feedback_metrics_api.py` — 9 passed, 1 skipped.

### Completion Notes List

- Added `db/repositories/feedback_metrics.py` to aggregate `update_feedback` by kind and `audit_events` for `human_review_*` actions with optional `since`/`until` on `created_at`.
- New routes `GET /admin/feedback-metrics` (JSON) and `GET /admin/feedback-metrics/export` (CSV) via `get_db_for_admin`; response includes `kind_percent`, `total_feedback`, and `human_review_override_rate` as `null` when no review decisions in window.
- `FeedbackMetricsAdmin` for admin users in `App.tsx` with tables + Export CSV (fetch with bearer, blob download).
- Tests: RBAC 403 for analyst/viewer, JSON shape, null override rate, CSV contains key rows.
- Review fixes: added invalid-window 400 validation, CSV export RBAC coverage, and a DB-backed integration test for real `update_feedback` / `audit_events` aggregation.

### File List

- `src/sentinel_prism/db/repositories/feedback_metrics.py` (new)
- `src/sentinel_prism/api/routes/feedback_metrics.py` (new)
- `src/sentinel_prism/main.py`
- `web/src/components/FeedbackMetricsAdmin.tsx` (new)
- `web/src/App.tsx`
- `tests/test_feedback_metrics_api.py` (new)
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/7-2-feedback-metrics-view-for-admin.md` (this file)

## Change Log

- 2026-04-27: Story 7.2 context created (create-story workflow) — **ready-for-dev**
- 2026-04-27: Story 7.2 implemented — admin feedback metrics API + CSV export + UI; **review**
- 2026-04-27: Code review findings fixed; story marked **done**
