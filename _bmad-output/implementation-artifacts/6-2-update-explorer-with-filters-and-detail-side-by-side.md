# Story 6.2: Update explorer with filters and detail (side-by-side)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **analyst**,
I want **filters and a detail view with original vs normalized fields shown side by side**,
so that **I can investigate updates** (**FR31**, **FR9**).

## Acceptance Criteria

1. **Given** normalized updates exist in PostgreSQL **when** an authenticated analyst opens the **Update explorer** in the web console **then** they see a **master–detail** layout: a **filterable, sortable list** of updates and a **detail** area that shows the **selected** update’s **raw capture** and **normalized record** **side by side** (two columns on wide viewports; stacked on narrow—**NFR11**, align with UX master–detail guidance).
2. **FR31 — Filter & sort (MVP contract):** The list supports **server-side** filtering and sorting for:
   - **Date:** on `normalized_updates.created_at` and/or `published_at` (document which field drives default sort; support a bounded range).
   - **Jurisdiction:** `normalized_updates.jurisdiction`.
   - **Source:** `source_id` and/or `source_name` (match how dashboard/top sources expose sources).
   - **Topic (MVP mapping):** treat **`document_type`** as the primary “topic” facet for SQL-backed filter; if `extra_metadata` carries a stable topic key used elsewhere, document it—do **not** invent a second taxonomy without PRD change.
   - **Severity:** filter **where derivable**: prefer joining **briefing members** (`briefings.groups[].members[]` contains `normalized_update_id` + `severity`) **or** delivery snapshots (`in_app_notifications` / `notification_digest_queue` on matching `run_id` + `item_url`). Rows with **no** derived severity must still appear in the list when no severity filter is applied; when a severity filter is applied, either **exclude unknowns** or include them behind an explicit `include_unknown_severity` flag—**pick one** and document in OpenAPI.
   - **Status (MVP mapping):** at minimum: **`in_human_review`** when `review_queue_items.run_id` matches the row’s `run_id`; **`briefed`** when a `briefings` row exists for `run_id`; otherwise **`processed`** (or similar single label)—document enum in API response.
3. **FR9 — Side by side:** In detail view, **raw** content comes from `raw_captures.payload` (JSON); **normalized** content reflects `NormalizedUpdateRow` fields (title, published_at, item_url, document_type, body_snippet, summary, jurisdiction, source_name, parser_confidence, extraction_quality, `extra_metadata` as applicable). Presentation must not rely on **color alone** for severity or status (**NFR11**).
4. **Performance & safety:** List endpoint uses **bounded** queries (default + max `limit`, cursor or offset pagination—match existing API conventions), **no unbounded JSON scans** in hot paths if avoidable; **no secrets** from raw payload in logs (**NFR12**). Under demo DB volume, primary list+detail round-trip should stay within **NFR1** spirit (same as 6.1: document measurement assumptions in Dev Agent Record if P95 not automated).
5. **RBAC:** Explorer endpoints require authenticated users with **viewer**-level console access (same pattern as `dashboard` / `notifications` — `get_current_user` + `require_roles`).

## Tasks / Subtasks

- [x] **Backend: updates list API** (AC: #2, #4, #5)
  - [x] Add `api/routes/updates.py` (or `explorer.py`) with `GET /updates` (name per REST consistency) registered in `main.py`.
  - [x] Pydantic response models: list row (id, timestamps, source, jurisdiction, document_type, derived `severity` | null, derived `status`, title snippet, `run_id`, etc.) + pagination envelope.
  - [x] Repository/service: SQLAlchemy async; compose filters; **document** join strategy for severity/status (briefings JSON vs notifications vs review queue).
- [x] **Backend: update detail API** (AC: #3, #4, #5)
  - [x] `GET /updates/{normalized_update_id}` returns normalized fields + **raw** `payload` + optional **classification overlay** when derivable (same sources as list—do not block FR9 if overlay missing).
- [x] **Frontend: explorer experience** (AC: #1–#3)
  - [x] New component(s) under `web/src/components/` (e.g. `UpdateExplorer.tsx`) and wire into `App.tsx` below dashboard or behind a simple anchor navigation—keep **Dashboard** + **Notifications** working.
  - [x] Master–detail: keyboard selection where practical (`aria-selected`, focus management); loading/error states with accessible messaging (reuse `readErrorMessage` / patterns from `Dashboard.tsx`).
  - [x] Side-by-side layout: CSS grid/flex; collapse to single column &lt;768px (or project breakpoint).
- [x] **Tests** (AC: #2–#5)
  - [x] `tests/test_updates_api.py` (or similar): auth 401, list with seeded `normalized_updates` + `raw_captures`, filter smoke, detail 404, detail happy path.
  - [x] If JSONB joins for briefing-derived severity: seed a minimal `briefings` row and assert filter behavior.

### Review Findings

- [x] [Review][Patch] Explorer endpoints do not enforce viewer-level RBAC [`src/sentinel_prism/api/routes/updates.py:143`]
- [x] [Review][Patch] Web explorer does not expose required date, source, status, or unknown-severity filters [`web/src/components/UpdateExplorer.tsx:68`]
- [x] [Review][Patch] Web explorer is not user-sortable despite hard-coding the API sort [`web/src/components/UpdateExplorer.tsx:81`]
- [x] [Review][Patch] Web explorer has no pagination controls, so updates beyond the first 50 are unreachable [`web/src/components/UpdateExplorer.tsx:79`]
- [x] [Review][Patch] Briefing member UUID casts can 500 on malformed JSON values [`src/sentinel_prism/db/repositories/updates.py:98`]
- [x] [Review][Patch] Briefing group expansion can 500 when `briefings.groups` is not a JSON array [`src/sentinel_prism/db/repositories/updates.py:88`]
- [x] [Review][Patch] Classification confidence cast can 500 on non-numeric JSON values [`src/sentinel_prism/db/repositories/updates.py:268`]
- [x] [Review][Patch] Detail endpoint silently drops valid non-object raw JSON payloads [`src/sentinel_prism/api/routes/updates.py:99`]
- [x] [Review][Patch] Date range filters accept inverted `from`/`to` bounds without a client error [`src/sentinel_prism/api/routes/updates.py:118`]
- [x] [Review][Patch] Source-name substring filter treats `%` and `_` as wildcards [`src/sentinel_prism/db/repositories/updates.py:179`]
- [x] [Review][Patch] Aborted stale fetches can clear loading state for newer explorer requests [`web/src/components/UpdateExplorer.tsx:120`]
- [x] [Review][Patch] Selection accessibility does not use `aria-selected` or focus management as specified [`web/src/components/UpdateExplorer.tsx:247`]
- [x] [Review][Patch] Happy-path API coverage is skipped in ordinary test runs and lacks RBAC/pagination regression tests [`tests/test_updates_api.py:42`]

## Dev Notes

### Epic 6 context

- **Epic goal:** Analyst console: **dashboard** (6.1 ✓), **explorer** (this story), **routing config UI** (6.3)—**NFR1**, **NFR11** [Source: `_bmad-output/planning-artifacts/epics.md` §Epic 6].
- **UX:** Master–detail explorer, filters, **side-by-side** raw vs normalized; severity = semantic **color + icon + text**; reduced-motion aware transitions where motion is used [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` §2.2, §2.5, UpdateDetailLayout / ProvenanceBlock patterns].

### Technical requirements (must follow)

- **Stack:** FastAPI async, SQLAlchemy async, React 19 + Vite + TypeScript [Source: `architecture.md` §2, `web/package.json`].
- **No first-class `classification` table** today; dashboard used **audit histogram** only (Story 6.1). Explorer **must not** pretend per-item severity exists in SQL unless you implement a **documented** join/projection (briefings members, notifications, or a **new** migration—only if product agrees scope) [Source: `6-1-overview-dashboard-widgets.md` Dev Notes gap].
- **Domain model:** `NormalizedUpdateRow` ↔ `RawCapture` via `raw_capture_id` (1:1) [Source: `src/sentinel_prism/db/models.py`].
- **Briefing member shape** includes `normalized_update_id`, `severity`, `impact_categories` [Source: `src/sentinel_prism/api/routes/briefings.py` `BriefingMemberOut`].

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| Auth | JWT bearer; `deps.get_current_user` / `require_roles` [Source: `src/sentinel_prism/api/deps.py`] |
| API | REST JSON, OpenAPI; mirror `dashboard.py` / `briefings.py` patterns |
| DB | PostgreSQL; new indexes only if profiling shows need—justify in PR/commit message |
| UI | OpenAPI-driven types; reuse `VITE_API_URL` / `API_BASE` pattern [Source: `web/src/App.tsx`] |

### Library / framework requirements

- Prefer **no** new heavy FE deps; use native fetch like existing views. Add `react-router-dom` only if routing split is cleaner than in-page sections—if added, pin compatible with React 19.
- Backend: stay within `requirements.txt`; avoid N+1 list queries.

### File structure requirements

- Python: `src/sentinel_prism/api/routes/updates.py`, `src/sentinel_prism/db/repositories/updates.py` (or `explorer.py`), register router in `src/sentinel_prism/main.py`.
- Web: `web/src/components/UpdateExplorer.tsx` (and small subcomponents if needed).

### Testing requirements

- Async test client + DB fixtures per `tests/conftest.py`.
- Cover authz, pagination bounds, and at least one filter + detail path.

### References

- **FR9, FR31:** `_bmad-output/planning-artifacts/prd.md` (Normalization & Storage; Web Console & UX).
- **Epic 6.2 AC:** `_bmad-output/planning-artifacts/epics.md`.
- **Architecture:** `_bmad-output/planning-artifacts/architecture.md` §2 (stack), §6 (structure).
- **Prior art:** `src/sentinel_prism/api/routes/dashboard.py`, `db/repositories/dashboard.py`, `web/src/components/Dashboard.tsx`.

### Previous story intelligence (6.1)

- Dashboard established **GET `/dashboard/summary`**, severity histogram via audit, and **React** dashboard above notifications in `App.tsx`.
- **Option A** histogram is **aggregate-only**—explorer needs **per-item** semantics; **reuse** briefing/notification/review projections rather than misusing histogram metadata.
- **Tests:** `tests/test_dashboard_api.py` patterns for seeded data and auth.
- **Files touched in 6.1** (for pattern reference): `main.py`, `Dashboard.tsx`, `dashboard` repository—mirror structure for updates.

### Git intelligence (recent commits)

- Latest: `feat(epic-6): overview dashboard API…` — follow same commit prefix discipline for this story.

### Latest tech information (snapshot)

- React 19 / Vite 6 / FastAPI already pinned—verify compatibility when adding any dependency.

### Project context reference

- No `project-context.md` in repo; this file + architecture + epics are authoritative.

## Dev Agent Record

### Agent Model Used

GPT-5.2 (Cursor agent, bmad-dev-story workflow)

### Debug Log References

### Completion Notes List

- **GET `/updates`:** Server-side filters (created/published range, jurisdiction, `source_id`, `source_name` ILIKE, `document_type`, derived `severity` with `include_unknown_severity`, `explorer_status`), sort (`created_at_*`, `published_at_*`), offset pagination with `total`, default sort `created_at_desc` (documented on schema).
- **Derived severity:** COALESCE briefing member (`briefings.groups` → `members` JSONB) → `in_app_notifications` → `notification_digest_queue` on `run_id` + `item_url` / `normalized_update_id`.
- **Derived status:** `in_human_review` if `review_queue_items` has `run_id`; else `briefed` if `briefings` row exists; else `processed`.
- **GET `/updates/{id}`:** Normalized row + `raw_captures.payload` + optional briefing-member classification overlay.
- **RBAC:** Same as dashboard — `get_current_user` (any authenticated role).
- **Web:** `UpdateExplorer` master–detail; filters refetch on **Apply filters**; side-by-side JSON panels; responsive grid in `index.css`; `.sr-only` severity text for NFR11.
- **Tests:** `tests/test_updates_api.py` — 401 smoke + `@pytest.mark.integration` seeded path (briefing severity filter + detail). Full suite **273 passed** (integration skipped without `DATABASE_URL`).
- **NFR1:** No automated P95; bounded `limit`/`offset` caps mirror notifications-style guardrails.
- **Code review fixes:** Added explicit viewer/analyst/admin RBAC, date-range validation, safer JSONB projection guards, literal source-name matching, non-object raw payload preservation, expanded explorer filters/sort/pagination, and non-integration route regression tests.

### File List

- `src/sentinel_prism/db/repositories/updates.py`
- `src/sentinel_prism/api/routes/updates.py`
- `src/sentinel_prism/main.py`
- `web/src/components/UpdateExplorer.tsx`
- `web/src/App.tsx`
- `web/src/index.css`
- `tests/test_updates_api.py`

## Change Log

- 2026-04-26 — Story context created (bmad-create-story).
- 2026-04-26 — Implemented explorer API, repository, UI, tests; status → review (bmad-dev-story).
- 2026-04-26 — Addressed code review findings; status → done (bmad-code-review).
