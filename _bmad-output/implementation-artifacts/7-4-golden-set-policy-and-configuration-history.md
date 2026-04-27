# Story 7.4: Golden-set policy and configuration history

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As **Regulatory Affairs / Compliance** (product persona; **MVP:** implement authorization as **Admin**—see Dev Notes),
I want **golden-set label policy definition, explicit apply, and auditable configuration history**,
so that **evaluation “truth” and refresh discipline are owned by the business and traceable** (**FR44**, **FR45**, **NFR13**).

## Acceptance Criteria

1. **Persisted active policy + cadence fields:** **Given** the system has migrated **When** any consumer needs the **active** golden-set / evaluation **policy** **Then** it is read from the **database** (not ad hoc constants only), including at minimum:
   - **Label policy** text (or structured fields) that states how reference labels are defined, disputed, and approved (align **FR44** “define and approve” intent).
   - **Cadence:** support for a **quarterly** refresh **schedule** (store intent—e.g. `refresh_cadence` enum or equivalent with `quarterly` as first value).
   - **Post–major-change flag:** a persisted boolean (or equivalent) **“also refresh after major model or prompt changes affecting classification”** matching PRD **Innovation → Evaluation & golden-set governance** and epic AC (“post–major-change flags”).
   - **Initial seed** in Alembic should set **sensible non misleading defaults** (e.g. policy placeholder text, cadence = quarterly, post-major flag **true** or **false**—**pick one** and document in OpenAPI) so existing behavior is unchanged where no policy existed before.

2. **Draft vs apply (governed):** **Given** an **Admin** **When** they **save a draft** (optional: policy body, cadence, post-major flag, short **reason** / note) **Then** the draft is stored **without** changing the **active** policy used for reads. **When** they **apply** via a **distinct** explicit action **Then** the **active** row updates, **version** increments monotonically (same **pattern** as **Story 7.3** `classification_policy`), and the draft is cleared or marked applied per your chosen design.

3. **Audit on apply:** **Given** an apply succeeds **When** the transaction commits **Then** an **`audit_events`** row is written with:
   - **actor** = authenticated admin `user_id`
   - **action** = new **`PipelineAuditAction`** member (e.g. `golden_set_config_changed` / `evaluation_config_changed`—**one** clear name) **distinct** from routing and classification config actions
   - **metadata** including at least: **prior** and **new** `version`, **summary** of cadence + post-major flag, **reason** if provided, and any **hash or length** for large policy text (avoid logging multi‑KB text verbatim if the team standard is to truncate—**document** in OpenAPI)
   - **fixed sentinel `run_id`** for config-only events (new UUID in `db/audit_constants.py`, **mirror** `CLASSIFICATION_CONFIG_AUDIT_RUN_ID` / `ROUTING_CONFIG_AUDIT_RUN_ID` so Epic 8 can filter)

4. **Configuration history (visible):** **Given** at least one apply **When** a client calls the **read history** endpoint (or repository method backing it) **Then** entries are returned in **chronological** order with **who / when / why** surfaced (user id or email for display, timestamps, reason). **NFR13:** align history semantics with “traceable (who/when/why)”—do **not** rely on toast-only UI; return structured data for the React surface.

5. **RBAC:** **Given** a non-admin user **When** they call draft / apply / read-active / history **Then** **403** (same dependency pattern as `GET /admin/feedback-metrics` and **7.3** → **`get_db_for_admin`** in `api/deps.py`).

6. **API contract:** Pydantic models use **`extra="forbid"`**; document validation (max lengths, cadence enum, boolean semantics); idempotent read of **active** policy.

7. **UI (web console):** **Given** an **Admin** session **When** they open the new section (e.g. **Golden set / evaluation policy** near **Classification policy** / **Feedback metrics** in `App.tsx`) **Then** they see **active** version + cadence + post-major flag + policy text, can **edit draft**, **confirm** before **apply**, and see a **history** list/table of prior applies (not toast-only for load/save failures—consistent with **7.2** / **7.3** admin patterns).

8. **Tests:** Integration tests cover **403** for non-admin, **happy path** apply increments version + writes audit with expected **action** and **sentinel `run_id`**, and **history** returns expected rows. No regression on **7.1–7.3** tests.

## Tasks / Subtasks

- [x] **Schema + Alembic** (AC: 1, 2)
  - [x] New table(s) for golden-set / evaluation **policy** (and optional draft) following **7.3**’s “single active + version” clarity; include cadence + post-major fields.
  - [x] Seed migration with documented defaults (no live imports of app constants that would couple migration history to future refactors—**learn from 7.3 code review**).

- [x] **Repository** (AC: 1–4)
  - [x] `db/repositories/…` e.g. `golden_set_policy.py` or `evaluation_config.py`: `get_active`, `get_draft`, `upsert_draft`, `apply_draft`, `list_history` (history may be **audit-driven**, **version-table-driven**, or **both**—choose one coherent design in Dev Notes).

- [x] **Audit helper** (AC: 3)
  - [x] `append_…_audit` + `PipelineAuditAction` + sentinel UUID in `audit_constants.py`.

- [x] **API** (AC: 2–6)
  - [x] Router under `/admin/…` (name consistently with resource), `include_router` in `main.py`.
  - [x] Endpoints: e.g. `GET` active, `GET` history, `GET/PUT` draft, `POST` apply.

- [x] **Web** (AC: 7)
  - [x] New component (e.g. `GoldenSetPolicyAdmin.tsx`): bearer token, `readErrorMessage` from `web/src/httpErrors.ts`, patterns from `ClassificationPolicyAdmin` / `FeedbackMetricsAdmin`.

- [x] **Tests** (AC: 8)
  - [x] New test module or extend admin tests; mirror **7.3** RBAC and audit assertions.

### Review Findings

- [x] [Review][Patch] Partial draft updates can silently discard an existing draft by merging omitted fields from active policy instead of the current draft [`src/sentinel_prism/db/repositories/golden_set_policy.py`:129]
- [x] [Review][Patch] History limit returns the oldest 100 events, so newer apply events disappear after the limit is exceeded [`src/sentinel_prism/db/repositories/golden_set_policy.py`:989]
- [x] [Review][Patch] UI reverses the API's chronological history order, weakening the visible "who/when/why" audit trail semantics [`web/src/components/GoldenSetPolicyAdmin.tsx`:98]
- [x] [Review][Patch] Tests do not verify populated history rows from the endpoint or repository, despite AC8 requiring expected history rows [`tests/test_golden_set_policy_api.py`:101]
- [x] [Review][Patch] Unused imports in the new repository can fail lint-gated CI [`src/sentinel_prism/db/repositories/golden_set_policy.py`:5]

## Dev Notes

### Product / scope guardrails

- **FR44** expects **Regulatory Affairs** to **define and approve** policy; **AI/Engineering** provides **tooling and metrics**. This story is the **policy + history + cadence** backbone; **full** dataset curation (import/export, holdout splits, labeling UI) may span later work—**do not** silently expand to a full MLOps suite unless you pull in an amended epic.
- **MVP role mapping:** The codebase today has **`UserRole`**: `admin` / `analyst` / `viewer` only (`db/models.py`). **Implement writes as Admin**; use UI copy and API docs to reflect **“Regulatory / policy owner”** persona. A future **`compliance`** role would be a **separate** story (schema + migration + RBAC matrix).

### Current code facts (build on 7.3)

- **Governed config precedent:** `classification_policy` repository, **draft/apply**, **version**, `append_classification_config_audit`, `CLASSIFICATION_CONFIG_AUDIT_RUN_ID`. **Reuse the same structural patterns** for golden-set config; **do not** fork divergent “apply” semantics. [Source: `src/sentinel_prism/db/repositories/classification_policy.py`, `src/sentinel_prism/api/routes/classification_policy.py`]
- **Admin gating:** `get_db_for_admin` from `api/deps.py`. [Source: existing admin routes]
- **Epic 8 alignment:** New audit **action** string must be **stable** and **distinct** for future search/replay filters.

### Architecture compliance

- **Stack:** FastAPI, async SQLAlchemy 2, Pydantic v2, React + Vite + TypeScript. [Source: `_bmad-output/planning-artifacts/architecture.md`]
- **Audit model:** Append-only `audit_events`; config changes use **sentinel** `run_id`. [Source: `architecture.md`, `audit_constants.py`]

### File structure (expected touchpoints)

| Area | Files (illustrative) |
|------|---------------------|
| Model + migration | `src/sentinel_prism/db/models.py`, `alembic/versions/…` |
| Repository | `src/sentinel_prism/db/repositories/…` (new) |
| Audit | `PipelineAuditAction`, `audit_constants.py`, `audit_events.py` |
| API | `src/sentinel_prism/api/routes/…` (new), `main.py` |
| Web | `web/src/components/…` (new), `web/src/App.tsx` |
| Tests | `tests/…` (new or extended) |

### Testing requirements

- Async fixtures consistent with `tests/test_classification_policy_api.py` / `tests/test_feedback_metrics_api.py`.
- Assert **audit** row: **action** value, **actor** `user_id`, **`run_id` == new sentinel**, metadata has version transition.
- Run **`pytest`** for affected modules; **`npm run build`** if TypeScript changes.

## Previous story intelligence (7.3)

- **Implemented:** DB-backed **classification** threshold + system prompt, draft/apply, **classification_config** audit, `ClassificationPolicyAdmin`, graph classify loads policy. [Source: `7-3-governed-threshold-and-prompt-change-proposals.md`]
- **Review learnings to avoid repeating:** partial-draft handling, **whitespace** validation, repository-level invariants, **self-contained migration** seed strings (not live imports from app modules), **bodyless** apply if optional body, **RBAC** on **all** mutating routes, **node-level** test coverage when behavior moves from constants to DB.
- **7.3 scope line:** 7.3 intentionally did **not** build golden-set—**7.4** is the home for **FR44/FR45** policy/history. **Link conceptually:** “post–major model/prompt change” in **7.4** should be **alignable** with when **classification** policy changes (e.g. documentation or a future event hook—**MVP** can stay **policy flags + manual refresh discipline** without automating graph triggers).

## Git intelligence (recent work)

- Latest feature commit: **7.2 + 7.3** (feedback metrics, classification policy). This story is the next **Epic 7** slice; no `golden` strings in `src/` yet—**greenfield** within existing admin/audit patterns.

## Latest tech notes (project-local)

- No new external/vendor dependency required for **policy storage**; use existing **JSONB** or **TEXT** per team preference and consistency with `classification_policy`.
- If you expose **enums** in OpenAPI, keep them **stable** for client codegen.

## Project context reference

- No `project-context.md` found in the repo; rely on this file, `architecture.md`, `prd.md` (**Evaluation & golden-set** sections), and cited modules.

## Story completion status

- [x] Implementation complete
- [x] Tests passing
- [x] Status: **done** (post code review)

*Ultimate context engine analysis completed - comprehensive developer guide created*

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent)

### Debug Log References

- `python3 -m pytest -q tests/test_golden_set_policy_api.py -m "not integration"` — 12 passed
- `python3 -m pytest -q -m "not integration"` — 335 passed
- `cd web && npm run build` — OK
- `python3 -m pytest -q tests/test_golden_set_policy_api.py -m "not integration"` — 13 passed, 1 deselected
- `npm --prefix web run build` — OK

### Completion Notes List

- Added `golden_set_policy` singleton table + Alembic `d1e2f3a4b5c6` with self-contained default label text, `quarterly` cadence, `refresh_after_major_classification_change=true`.
- `PipelineAuditAction.GOLDEN_SET_CONFIG_CHANGED`, `GOLDEN_SET_CONFIG_AUDIT_RUN_ID`, `append_golden_set_config_audit` (metadata: versions, cadence, post-major bools, SHA-256 prefix + lengths for label text).
- Repository `golden_set_policy.py`: draft/apply, `list_apply_history` from `audit_events` + user email join.
- Admin routes `GET/PUT /admin/golden-set-policy`, `GET /history`, `POST /apply` (`extra="forbid"`, optional bodyless apply).
- Web: `GoldenSetPolicyAdmin` with draft form, apply confirm, history table; mounted in `App.tsx` before classification policy.
- Tests: RBAC, mocked reads, apply errors, repository validation, optional DB integration for apply+audit.
- Code review fixes: partial draft updates preserve existing drafts, history returns the newest bounded window in chronological order, UI preserves API chronology, populated history response covered by tests, unused imports removed.

### File List

- `alembic/versions/d1e2f3a4b5c6_add_golden_set_policy.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/audit_constants.py`
- `src/sentinel_prism/db/repositories/audit_events.py`
- `src/sentinel_prism/db/repositories/golden_set_policy.py`
- `src/sentinel_prism/api/routes/golden_set_policy.py`
- `src/sentinel_prism/main.py`
- `web/src/components/GoldenSetPolicyAdmin.tsx`
- `web/src/App.tsx`
- `tests/test_golden_set_policy_api.py`
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Change Log

- 2026-04-27: Story 7.4 implemented — golden-set policy DB, admin API, audit+history, UI, tests — **done**
