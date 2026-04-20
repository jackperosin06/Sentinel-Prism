# Story 6.1: Overview dashboard widgets

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **analyst**,
I want **counts by severity, new items, review backlog, and top sources**,
so that **I can prioritize** work (**FR30**, **NFR1**).

## Acceptance Criteria

1. **Given** seeded or demo data exists **when** an authenticated analyst opens the console dashboard **then** the UI shows distinct widgets (or clearly labeled sections) for: **severity distribution**, **new items** (time-bounded count), **items in human review** (backlog), and **top sources** (by volume or ingestion totals—see Dev Notes).
2. **Primary dashboard content loads within NFR1**: under nominal demo load, the **first meaningful paint** of these widgets’ data (after auth) completes fast enough to meet **P95 &lt; 3 seconds** for the dashboard API round-trip + render (measure in dev with seeded DB; document the assumption in Dev Agent Record if full P95 measurement is out of scope).
3. **NFR11 (WCAG 2.1 Level A minimum)** for this flow: keyboard reachability for interactive controls, visible focus, form inputs and live regions associated with labels where applicable; no keyboard traps in the dashboard shell.
4. **RBAC**: Dashboard data is only available to authenticated users with at least **viewer**-level console access (same pattern as other protected routes—**FR40**). Use existing JWT + `get_current_user` / `require_roles` patterns.

## Tasks / Subtasks

- [x] **Backend: dashboard summary API** (AC: #1, #2, #4)
  - [x] Add a dedicated route module (e.g. `api/routes/dashboard.py`) and register it in `main.py`.
  - [x] Define a stable JSON response schema (Pydantic models) for overview metrics: `severity_counts`, `new_items_count`, `review_queue_count`, `top_sources` (each with explicit field names and types).
  - [x] Implement a repository/service layer that performs **bounded** SQL (prefer one or few round-trips; avoid N+1). Document query strategy in Dev Notes.
- [x] **Data model / pipeline note for severity** (AC: #1)
  - [x] **Gap (must resolve in implementation):** There is **no** first-class `classification` table keyed by `normalized_update_id`. Severity today lives in graph state and in **briefing** JSON (`briefings.groups`) / notification rows—not globally queryable for all items. Pick **one** approach and implement end-to-end:
    - **Option A (recommended minimal):** Extend `PIPELINE_CLASSIFY_COMPLETED` audit metadata in `graph/nodes/classify.py` to include a **small histogram** (e.g. `severity_histogram: dict[str, int]`) derived from the in-memory `classifications` list before the node returns. Aggregate in the dashboard repository over `audit_events` with **deduplication per `run_id`** (take latest `created_at` for `PIPELINE_CLASSIFY_COMPLETED` per run, or equivalent—justify in code comments if graph retries create duplicates).
    - **Option B:** Persist per-item classification snapshots via a **new Alembic migration** + writes from the classify node (heavier scope; only if product requires SQL-native per-item history now).
  - [x] If Option A: keep metadata **non-secret** and bounded (counts only—**NFR12** aligns with existing audit guidance).
- [x] **“New items” definition** (AC: #1)
  - [x] Implement a clear, documented window (e.g. last **24 hours** or last **7 days** based on `normalized_updates.created_at`). Encode the window in API contract or server config so UI and tests stay aligned.
- [x] **Review backlog** (AC: #1)
  - [x] `COUNT(*)` (or equivalent) from `review_queue_items` for open queue rows (see [Source: `src/sentinel_prism/db/models.py` `ReviewQueueItem`]).
- [x] **Top sources** (AC: #1)
  - [x] Prefer **`sources.items_ingested_total`** (already maintained—Story 2.6 path) **or** `COUNT`/`GROUP BY` on `normalized_updates.source_id`—pick one and document; return top **N** (e.g. 5) with `source_id`, display `name`, and the metric used.
- [x] **Frontend: dashboard view** (AC: #1–#3)
  - [x] Evolve `web/src/App.tsx` (or split into components under `web/src/`) so post-login landing is a **dashboard** (notifications from Story 5.2 can move to a secondary section, tab, or route—preserve behavior).
  - [x] Fetch dashboard summary with `Authorization: Bearer <token>` and `VITE_API_URL` / `API_BASE` pattern already used in `App.tsx`.
  - [x] Loading and error states must not block accessibility (e.g. announce errors without relying on color alone).
- [x] **Tests** (AC: #1, #4)
  - [x] API integration tests with async DB fixtures: seeded `normalized_updates`, `review_queue_items`, `sources`, and (if Option A) representative `audit_events` rows—or mark `pytest` expectations against whatever aggregation you implement.
  - [x] Optional: lightweight frontend smoke if the project adds a FE test runner later; otherwise manual test steps in Dev Agent Record.

### Review Findings

- [x] [Review][Patch] Enforce bounded severity labels in classify audit histogram [src/sentinel_prism/graph/nodes/classify.py:23]
- [x] [Review][Patch] Ignore invalid/negative severity histogram values during dashboard merge [src/sentinel_prism/db/repositories/dashboard.py:116]
- [x] [Review][Patch] Stabilize top-sources ordering for ties with deterministic secondary sort [src/sentinel_prism/db/repositories/dashboard.py:179]
- [x] [Review][Patch] Replace mocked dashboard-summary happy-path test with seeded DB integration coverage [tests/test_dashboard_api.py:361]

## Dev Notes

### Epic 6 context

- **Epic goal:** Analyst console: **dashboard** (this story), **explorer** (6.2), **routing config UI** (6.3)—**NFR1**, **NFR11** [Source: `_bmad-output/planning-artifacts/epics.md` §Epic 6].
- **Downstream:** Story **6.2** adds filters and detail; keep dashboard layout extensible (shell/header area for future nav).

### Technical requirements (must follow)

- **Stack:** FastAPI async, SQLAlchemy async sessions, React 19 + Vite 6 + TypeScript—pin versions in existing manifests when adding deps [Source: `architecture.md` §2, `web/package.json`].
- **API style:** REST JSON, OpenAPI from FastAPI; mirror patterns in `api/routes/notifications.py`, `briefings.py` (dependencies, response models).
- **CORS:** Already configured for `localhost:5173` in `main.py`—no regression.
- **Boundaries:** Dashboard reads via **repositories/services**; **do not** import graph definitions into API routes except if you intentionally add a small, documented hook in `classify.py` for Option A metadata.

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| Auth | JWT bearer; `deps.get_current_user` / `require_roles` [Source: `src/sentinel_prism/api/deps.py`] |
| DB | PostgreSQL; Alembic for schema changes if Option B or new indexes [Source: `architecture.md` §4] |
| UI | React+Vite; OpenAPI-only integration [Source: `architecture.md` §2] |
| Audit | If extending classify metadata, remain append-only audit semantics; no secrets in JSON [Source: `AuditEvent` docstring] |

### Library / framework requirements

- **Backend:** Use existing `requirements.txt` stack; no new heavy deps unless justified (e.g. avoid adding a charting library until needed—plain semantic HTML + CSS is acceptable for MVP widgets).
- **Frontend:** Prefer **no** new dependencies for routing in 6.1 if a single-page layout suffices; introduce `react-router-dom` only if you split routes cleanly and pin a version consistent with React 19.

### File structure requirements

- Python: `src/sentinel_prism/api/routes/dashboard.py`, optional `src/sentinel_prism/db/repositories/dashboard.py` or `services/dashboard/`.
- Web: `web/src/` components colocated (e.g. `web/src/components/Dashboard.tsx`).
- Register router in `src/sentinel_prism/main.py` alongside existing routers.

### Testing requirements

- Follow `tests/conftest.py` patterns for DB and async client.
- New test file e.g. `tests/test_dashboard_api.py` covering auth (401 without token), success path with seeded data, and forbidden role if you restrict below `viewer`.

### References

- FR30 / NFR1 / NFR11: `_bmad-output/planning-artifacts/epics.md` (Requirements Inventory + Story 6.1).
- Architecture: `_bmad-output/planning-artifacts/architecture.md` §2 (stack), §6 (structure).
- Domain tables: `src/sentinel_prism/db/models.py` — `NormalizedUpdateRow`, `ReviewQueueItem`, `Source`, `AuditEvent`, `Briefing`.
- Prior UI shell: `web/src/App.tsx` (login + notifications).

### Previous story intelligence (Epic 5 tail)

- Epic 5 delivered routing, in-app notifications, external channels, digest scheduling, and regulatory outbound guardrail. **Web shell** currently focuses on **notifications** after login—dashboard should **compose** with that feature rather than deleting it without replacement.
- **Git pattern:** Recent commits use `feat(epic-5): …` prefixes and story completion notes; follow the same discipline for Epic 6.

### Git intelligence (recent commits)

- Latest work: Epic 5 stories 5.1–5.5 (routing, notifications, delivery, digest, compliance allowlist). Touchpoints for this story: `notifications` API, `App.tsx` fetch patterns, RBAC dependencies.

### Latest tech information (snapshot)

- **React 19** / **Vite 6** are already pinned in `web/package.json`—keep compatibility when adding any new FE package.
- **FastAPI** OpenAPI remains the contract for the web app—verify responses against generated schema after adding routes.

### Project context reference

- No `project-context.md` in repo; use this file + architecture + epics as authoritative.

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent, bmad-dev-story workflow)

### Debug Log References

### Completion Notes List

- Implemented **Option A**: `severity_histogram` on `PIPELINE_CLASSIFY_COMPLETED` audit metadata; dashboard aggregates latest-per-run histograms via windowed subquery in `db/repositories/dashboard.py`.
- **GET `/dashboard/summary`** — JWT via `get_current_user` (viewer+); response includes `new_items_window_hours`, `top_sources_metric`, and `top_sources[].value`.
- **New items window:** default **24h** from `DASHBOARD_NEW_ITEMS_HOURS` (documented in `.env.example`); overridable per request via `new_items_window_hours` query param.
- **Frontend:** `Dashboard` component above notifications; shared `httpErrors.ts`; `:focus-visible` outline for buttons in `index.css` (NFR11).
- **NFR1:** Bounded queries; no automated P95 load test in CI — validate under demo DB manually if needed.
- **Tests:** `tests/test_dashboard_api.py` (401 + mocked summary); `tests/test_audit_events.py` updated for `severity_histogram`; full suite **272 passed**.

### File List

- `src/sentinel_prism/graph/nodes/classify.py`
- `src/sentinel_prism/db/repositories/dashboard.py`
- `src/sentinel_prism/api/routes/dashboard.py`
- `src/sentinel_prism/main.py`
- `tests/test_dashboard_api.py`
- `tests/test_audit_events.py`
- `web/src/App.tsx`
- `web/src/components/Dashboard.tsx`
- `web/src/httpErrors.ts`
- `web/src/index.css`
- `.env.example`

## Change Log

- 2026-04-20 — Story 6.1 implemented: dashboard API + UI, classify audit histogram, tests (dev-story).
