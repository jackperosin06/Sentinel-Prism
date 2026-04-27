# Story 8.3: Cross-service observability dashboard (operator)

Status: done

<!-- Ultimate context engine analysis completed — comprehensive developer guide created. -->

## Story

As an **operator** (Sam),
I want **structured log correlation and key operational metrics surfaced in the console**,
so that **on-call can trace failures end-to-end** (**NFR8**, **NFR9**, Epic 8).

## Acceptance Criteria

1. **Run/correlation id is consistently present in logs (NFR8):**  
   **Given** API requests and background work execute pipeline steps  
   **When** logs are emitted by the API layer, workers/schedulers, and graph nodes  
   **Then** each log line includes a consistent correlation envelope (minimum):
   - `event` (stable string key)
   - `run_id` when available (graph and run-scoped flows)
   - `source_id` when available (ingestion / source-scoped flows)
   - `node_id` when logging from graph nodes (`scout`, `normalize`, `classify`, `human_review_gate`, `brief`, `route`)
   - `request_id` for HTTP requests (generated per request)

2. **Operator “Ops” view exists (dashboard or logs):**  
   **Given** an authenticated operator  
   **When** they open the console  
   **Then** they can view an **Ops** section that surfaces:
   - **Per-source ingestion health** (success rate, error rate, latency, items ingested) (NFR9)
   - A lightweight **link/jump-off** to existing operator tools:
     - Audit search (Story 8.1)
     - Run replay (Story 8.2)

3. **Per-source metrics match NFR9:**  
   **Given** the existing ingestion counters maintained on `Source` rows  
   **When** the operator loads the Ops view  
   **Then** displayed metrics reflect the backend metrics contract (success rate, error rate, latency, items ingested) and show “unknown / none yet” states correctly.

4. **RBAC:**  
   - Ops view is available to **authenticated** `admin` and `analyst` users by default.  
   - If a future decision broadens to `viewer`, it should be explicit (follow-up).

5. **Non-goals (scope guardrails):**
   - No external APM/Grafana/Datadog integration in this story.
   - No new persistence tables required; prefer reading existing counters/audit/search endpoints.
   - Do not rework pipeline semantics (audit + replay are already delivered in 8.1/8.2).

## Tasks / Subtasks

- [x] **Define a stable observability log envelope** (AC: 1)
  - [x] Add a small helper for consistent structured logging fields:
    - `event` string, plus `ctx` dict (or flat keys) including `run_id`, `source_id`, `node_id`, `request_id`, and `user_id` where available.
  - [x] Ensure graph node logs include `node_id` and propagate `run_id` from state.
  - [x] Add FastAPI middleware to assign `request_id` per request and include it in logs.
  - [x] Verify background schedulers/workers include `source_id` and/or `run_id` in their log events where applicable.

- [x] **Expose operator-readable per-source metrics API** (AC: 2–4)
  - [x] **Preferred**: add a new operator route (do not loosen admin-only routes by accident):
    - `GET /ops/source-metrics` (or similar)
    - Uses `Depends(get_db)` + `require_roles(UserRole.ANALYST, UserRole.ADMIN)`
    - Response model reuses the existing metrics shape (see `SourceMetricsResponse` in `api/routes/sources.py`)
  - [x] Ensure stable ordering + pagination consistent with existing list patterns (limit/offset).
  - [x] Tests: integration test verifies role access and basic shape.

- [x] **Implement Ops UI section** (AC: 2–4)
  - [x] Add `web/src/components/OpsDashboard.tsx`:
    - Fetches operator metrics from the new operator endpoint.
    - Displays a compact table of sources: name, success rate, error rate, last success at, last failure at, last latency, items ingested total.
    - Uses the same failure handling patterns as other components (`readErrorMessage`, 401 → logout callback).
  - [x] Wire into `web/src/App.tsx` near other operator tools:
    - Recommended placement: after `AuditEventSearch` and `RunReplay` (or above them with a short “Ops” heading).

- [x] **Quick operator affordances (optional, if low cost)** (AC: 2)
  - [x] Add filter chips in Ops view for “show failing sources only” (computed client-side from error rate/last failure).

### Review Findings

- [x] [Review][Decision] Correlation envelope scope for API logs — AC1 expects `request_id` (and optionally `user_id`) on API-layer log lines where available, but the current change only guarantees it on the middleware `http_request` line. Decision: middleware `http_request` line + `X-Request-Id` response header is sufficient for “API layer” correlation in this story.

- [x] [Review][Patch] Ensure `X-Request-Id` is present even when request handling errors [src/sentinel_prism/main.py]
- [x] [Review][Patch] Robustly handle `Source.extra_metadata` being non-dict so `/ops/source-metrics` can’t 500 on unexpected shapes [src/sentinel_prism/api/routes/ops.py]
- [x] [Review][Patch] Accept common ISO8601 `"Z"` timestamps for `last_poll_failure.at` (currently dropped) [src/sentinel_prism/api/routes/ops.py]
- [x] [Review][Patch] Enforce Ops UI RBAC by hiding the Ops section for non-admin/non-analyst (not just showing a 403 message) [web/src/App.tsx]
- [x] [Review][Patch] Avoid silently truncating Ops metrics at 500 rows (add pagination or at least a visible truncation warning) [web/src/components/OpsDashboard.tsx]

## Dev Notes

### Existing implementation context (reuse, don’t reinvent)

- **NFR9 per-source metrics already exist (admin route):**  
  `GET /sources/metrics` returns `SourceMetricsResponse` with:
  `poll_attempts_success`, `poll_attempts_failed`, `items_ingested_total`, `success_rate`, `error_rate`, `last_success_latency_ms`, `last_success_fetch_path`, and `last_poll_failure`.  
  [Source: `src/sentinel_prism/api/routes/sources.py` — `SourceMetricsResponse`, `list_source_metrics`]

- **Dashboard aggregation already uses audit metadata:**  
  The Overview dashboard merges `severity_histogram` from `PIPELINE_CLASSIFY_COMPLETED` audit events, selecting the latest per `run_id` to avoid double-counting retries.  
  [Source: `src/sentinel_prism/db/repositories/dashboard.py`]

- **Operator tools already exist in the web shell:**  
  `AuditEventSearch` (Story 8.1) and `RunReplay` (Story 8.2) are already mounted in `web/src/App.tsx`.  
  [Source: `web/src/App.tsx`]

### Architecture compliance

- Use the existing stack: **FastAPI (async)**, **SQLAlchemy async**, **Pydantic v2**, **React + Vite + TypeScript**.
- Prefer adding a small new router module for operator ops endpoints (e.g. `src/sentinel_prism/api/routes/ops.py`) and including it from `main.py`, rather than loosening admin dependencies globally.  
  [Source: `_bmad-output/planning-artifacts/architecture.md` §3.6, §4 “Observability”]

### Logging requirements (NFR8) — practical guidance

- This repo already uses `logging.getLogger(__name__)` and emits events with `extra={ "event": ..., "ctx": {...} }` in multiple modules.
- Standardize the following keys wherever possible:
  - `event`: stable event name (snake_case)
  - `ctx.run_id`: `str(UUID)` when present
  - `ctx.source_id`: `str(UUID)` when present
  - `ctx.node_id`: one of the graph node ids when logging inside nodes
  - `ctx.request_id`: per-request id for FastAPI logs
  - `ctx.user_id`: `str(UUID)` for authenticated requests (optional if available at log site)

### Testing requirements

- Add backend integration tests for:
  - Ops metrics endpoint RBAC and JSON shape.
  - (If middleware is added) request_id presence can be unit-tested by asserting the header is set and/or logger call includes it.

### UX alignment

- The UX spec explicitly calls out an **ops/APM monitoring** inspiration for Sam’s workflow and emphasizes **correlation ids** and **time-range investigation** patterns. Keep the Ops section **dense but scannable**, with solid backgrounds (no “glass” on tables) and accessible labels (NFR11).  
  [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — “Ops / APM / monitoring”, “Operator — Audit search and replay”, “Tables/audit: solid backgrounds”]

## Previous story intelligence (Epic 8 — 8.2)

- **Replay is already non-destructive and has a UI component**; do not duplicate replay mechanics here. The Ops view should **link to** or **compose** existing operator tools.  
  [Source: `_bmad-output/implementation-artifacts/8-2-workflow-replay-from-persisted-state.md`]

## Git intelligence (recent work patterns)

- Recent commits show a consistent pattern: implement a repository helper + API route + minimal React component + integration tests. Follow the same pattern for Ops metrics.  
  [Source: `git log -5 --oneline`]

## Project context reference

- No `project-context.md` discovered; rely on PRD/Architecture/UX and current code references above.

## Dev Agent Record

### Agent Model Used

GPT-5.2 (bmad-dev-story)

### Debug Log References

### Completion Notes List

- Selected story from `sprint-status.yaml`: `8-3-cross-service-observability-dashboard-operator` (first backlog story).
- Added operator-facing metrics endpoint `GET /ops/source-metrics` (admin/analyst; viewer forbidden) with pagination.
- Added request id middleware that sets `X-Request-Id` and emits an HTTP log line with `request_id`, method, path, and status.
- Standardized graph-node and scheduler logs to include `ctx.node_id` / `ctx.run_id` / `ctx.source_id` using `observability.obs_ctx`.
- Added `OpsDashboard` console section with a “failing sources only” filter and jump links to existing operator tools.
- Tests: `pytest` full suite passes; `npm -C web run build` passes.

### File List

- `_bmad-output/implementation-artifacts/8-3-cross-service-observability-dashboard-operator.md`
- `src/sentinel_prism/observability.py`
- `src/sentinel_prism/api/routes/ops.py`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/graph/nodes/scout.py`
- `src/sentinel_prism/graph/nodes/normalize.py`
- `src/sentinel_prism/graph/nodes/classify.py`
- `src/sentinel_prism/graph/nodes/brief.py`
- `src/sentinel_prism/graph/nodes/route.py`
- `src/sentinel_prism/graph/nodes/human_review_gate.py`
- `src/sentinel_prism/workers/poll_scheduler.py`
- `src/sentinel_prism/workers/digest_scheduler.py`
- `web/src/components/OpsDashboard.tsx`
- `web/src/components/AuditEventSearch.tsx`
- `web/src/components/RunReplay.tsx`
- `web/src/App.tsx`
- `tests/test_ops_source_metrics_api.py`

## Change Log

- **2026-04-27:** Implemented Story 8.3 — operator ops metrics endpoint + request-id correlation + log envelope standardization + Ops UI section; story status → review.

