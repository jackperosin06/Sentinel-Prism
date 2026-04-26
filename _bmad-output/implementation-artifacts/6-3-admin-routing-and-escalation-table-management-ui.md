# Story 6.3: Admin routing & escalation table management UI

Status: done

<!-- Ultimate context engine analysis completed — comprehensive developer guide created -->

## Story

As an **admin**,
I want **to edit routing/escalation mock tables in the UI**,
so that **I can tune behavior without code** (**FR32**).

## Acceptance Criteria

1. **RBAC (FR32)**  
   **Given** a user without the **admin** role  
   **When** they call routing-rule management APIs or open the admin routing screen  
   **Then** they are denied (**403** on API; console must not expose destructive controls—hide or show read-only message).  

2. **Persisted mock tables**  
   **Given** the existing PostgreSQL **`routing_rules`** model (topic vs severity rows — Story 5.1)  
   **When** an admin creates, updates, or deletes a rule via the UI  
   **Then** changes **commit** to `routing_rules` and the next pipeline **`node_route`** load sees the new ordering and keys (same repository helpers: `list_topic_rules_ordered` / `list_severity_rules_ordered`).  

3. **Escalation = severity rules (product mapping)**  
   **Given** PRD language “routing **and** escalation”  
   **When** implementing this story  
   **Then** treat **escalation** as **severity → channel** (and team backfill per resolver) rules already modeled as `rule_type = 'severity'`—**do not** invent a second table unless the PRD is formally amended; document in OpenAPI that the UI manages **two** rule kinds: **topic** (`impact_category`) and **severity** (`severity_value`).  

4. **Normalization & validation**  
   **Given** DB **CHECK** constraints require `impact_category` / `severity_value` to be **trimmed, lower-case, non-empty** when set  
   **When** an admin saves a row  
   **Then** the API **normalizes or rejects** inputs so inserts/updates cannot 500 on constraint violations; return **422** with clear field errors for empty/whitespace keys.  

5. **Audit trail (FR33)**  
   **Given** a successful **mutating** operation (create / update / delete) on routing rules  
   **When** the transaction commits  
   **Then** an **`audit_events`** row is appended that records the **admin actor** (`actor_user_id`), a **distinct** `PipelineAuditAction` value reserved for **routing configuration** (not `routing_applied`, which remains **pipeline** “rules evaluated for this run”), and **bounded** JSON metadata describing the operation (rule id, rule_type, op — **no secrets**, respect NFR12 and existing `_trim_metadata` limits in `append_audit_event`).  

6. **Audit `run_id` constraint**  
   **Given** `audit_events.run_id` is **NOT NULL** today and **`routing_applied`** uses a **real** pipeline `run_id`  
   **When** writing **config** audit rows  
   **Then** use a **single documented sentinel UUID** constant (e.g. module-level `ROUTING_CONFIG_AUDIT_RUN_ID`) for all routing-table config events so Epic 8 search can filter or group them consistently—**document** the constant in OpenAPI/Dev Notes; do **not** reuse random UUIDs per click without product justification.  

7. **Web console UX (NFR1, NFR11)**  
   **Given** an authenticated **admin**  
   **When** they open the routing section  
   **Then** they can **list** both rule types, **add/edit/delete** rows with fields matching the ORM: `priority`, `rule_type`, `impact_category` OR `severity_value`, `team_slug`, `channel_slug`  
   **And** controls are **keyboard-operable**, labeled, and errors are readable (reuse patterns from `Dashboard.tsx` / `UpdateExplorer.tsx`: `readErrorMessage`, loading states).  

8. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** cover **401/403** matrix, happy-path CRUD, constraint/validation rejection, and **at least one** assertion that a mutating call creates the expected **audit** row (action + `actor_user_id` + sentinel `run_id`).  

## Tasks / Subtasks

- [x] **Backend: audit vocabulary + writer** (AC: #5, #6)  
  - [x] Add `PipelineAuditAction` member(s) for routing **configuration** mutations; ensure Alembic/DB string width still fits (`length=64`).  
  - [x] Add documented `ROUTING_CONFIG_AUDIT_RUN_ID` (or equivalent) and helper to append config audit rows via `append_audit_event` (or thin wrapper) with `actor_user_id=current_user.id`, `source_id=None`.  

- [x] **Backend: repository** (AC: #2, #4)  
  - [x] Extend or add `db/repositories/routing_rules.py` (or `routing_rules_admin.py`) with CRUD aligned to `RoutingRule` columns; keep **list** ordering consistent with Story 5.1 for display (`priority`, `id`).  

- [x] **Backend: FastAPI routes** (AC: #1–#6)  
  - [x] New router (e.g. `api/routes/routing_rules.py`) with `Depends(get_db_for_admin)` or equivalent **`require_roles(UserRole.ADMIN)`** pattern used by `sources.py`.  
  - [x] REST shapes: list (by type or combined), create, update (`PATCH`/`PUT`—pick one and match project conventions), delete.  
  - [x] Register router in `main.py`; export OpenAPI models.  

- [x] **Frontend** (AC: #7)  
  - [x] New component(s) under `web/src/components/` (e.g. `RoutingRulesAdmin.tsx`)—tabs or two sections: **Topic → team/channel** and **Severity → channel** (with help text tying severity rules to “escalation”).  
  - [x] Wire into `App.tsx` for **admin** users only: call existing **`GET /auth/me`** after login (returns `role`) to gate the routing admin section—**do not** trust a role cached in localStorage without refreshing from the API.  
  - [x] Use `API_BASE` + bearer token consistent with `UpdateExplorer`.  

- [x] **Tests** (AC: #8)  
  - [x] New `tests/test_routing_rules_admin_api.py` (name to fit repo).  
  - [x] Use seeded admin user + `routing_rules` fixtures per `conftest.py` patterns.  

### Review Findings

- [x] [Review][Patch] DB-bound routing-rule fields can still raise 500 instead of 422 [`src/sentinel_prism/api/routes/routing_rules.py`:51]
- [x] [Review][Patch] OpenAPI does not document severity-as-escalation or the routing config audit sentinel [`src/sentinel_prism/api/routes/routing_rules.py`:98]
- [x] [Review][Patch] Admin routing UI can leave destructive controls visible after auth/RBAC failure [`web/src/components/RoutingRulesAdmin.tsx`:37]
- [x] [Review][Patch] Tests miss required mutating authorization, update/delete CRUD, and API-level sentinel audit coverage [`tests/test_routing_rules_admin_api.py`:47]

## Dev Notes

### Epic 6 context

- **Epic goal:** Dashboard (6.1 ✓), explorer (6.2 ✓), **routing config UI** (this story)—**NFR1**, **NFR11** [Source: `_bmad-output/planning-artifacts/epics.md` §Epic 6].  
- **FR32 / FR33:** Admin-managed mock tables + **audit** on save [Source: `prd.md` FR32–FR33; epics.md Story 6.3 AC].  

### Technical requirements (must follow)

- **Stack:** FastAPI async, SQLAlchemy async, React + Vite + TypeScript [Source: `architecture.md` §2].  
- **Domain:** `RoutingRule` / `routing_rules` table; `RoutingRuleType` **`topic`** vs **`severity`**; resolver semantics and precedence live in `services/routing/resolve.py` and `RoutingRule` docstring in `db/models.py`—**UI copy** should not contradict “severity overrides channel when topic matched” behavior.  
- **Do not confuse modules:** `graph/routing.py` is **conditional graph edges** (human review); **`graph/nodes/route.py`** is **mock business routing** [Source: `5-1-routing-rules-engine.md` Critical naming disambiguation].  
- **Audit:** Reuse `db/repositories/audit_events.append_audit_event`; metadata must remain **non-secret** [Source: `audit_events.py`, `AuditEvent` model].  
- **RBAC:** `UserRole.ADMIN`; mirror **`get_db_for_admin`** / `sources` router patterns [Source: `api/deps.py`, `api/routes/sources.py`].  

### Architecture compliance

| Topic | Requirement |
| --- | --- |
| API | REST + OpenAPI; JSON request/response models with Pydantic v2 patterns used elsewhere |
| DB | PostgreSQL; respect existing CHECK constraints; avoid N+1 on simple list endpoints |
| UI | Accessible labels, no color-only status cues for critical messaging (**NFR11**) |

### Library / framework requirements

- Prefer **no** new heavy frontend dependencies; match `UpdateExplorer` / `Dashboard` fetch style.  
- Backend: stay within `requirements.txt`.  

### File structure requirements

- Python: `src/sentinel_prism/api/routes/` (new module), `src/sentinel_prism/db/repositories/` (extend), `src/sentinel_prism/main.py` router include.  
- Optional: small `audit_constants.py` or colocate sentinel UUID with audit helper—**one** canonical definition.  
- Web: `web/src/components/` + `App.tsx` wiring.  

### Testing requirements

- Async test client + DB fixtures per `tests/conftest.py`.  
- Verify admin-only access and audit side effects without relying on integration DB if project convention uses markers—mirror `test_updates_api.py` patterns.  

### References

- **FR32, FR33:** `_bmad-output/planning-artifacts/prd.md`  
- **Story 6.3:** `_bmad-output/planning-artifacts/epics.md`  
- **Routing engine / schema:** `_bmad-output/implementation-artifacts/5-1-routing-rules-engine.md`, `src/sentinel_prism/db/models.py` (`RoutingRule`), `src/sentinel_prism/db/repositories/routing_rules.py`  
- **UX / admin persona:** `_bmad-output/planning-artifacts/ux-design-specification.md` (Jordan admin, configurable without code, audit-native)  
- **Architecture:** `_bmad-output/planning-artifacts/architecture.md` §2, §3.5, §6  

### Previous story intelligence (6.2)

- **Explorer** established **`GET /updates`**, **`GET /updates/{id}`**, repository `db/repositories/updates.py`, and **`UpdateExplorer`** with filters, pagination, master–detail, RBAC via authenticated roles [Source: `6-2-update-explorer-with-filters-and-detail-side-by-side.md`].  
- **Patterns to reuse:** `readErrorMessage`, bounded API queries, explicit **Apply** vs debounced refetch where appropriate, **keyboard/a11y** lessons from code review on `UpdateExplorer`.  
- **Defer items from 5.1** explicitly mention **admin rule-editor UI** and validation hardening—this story is the right place to tighten **priority** / empty `team_slug` **if** you can do so without breaking existing seeds (justify in Dev Agent Record).  

### Git intelligence (recent commits)

- Recent epic work uses prefixes like `feat(epic-6): …` — continue for this story’s commits.  

### Latest tech information (snapshot)

- React 19 / Vite / FastAPI are already pinned in-repo; verify lockfiles before adding any new dependency.  

### Project context reference

- No `project-context.md` in repo; this file + `architecture.md` + epics are authoritative.  

## Dev Agent Record

### Agent Model Used

GPT-5.2 (Cursor agent, bmad-dev-story workflow)

### Debug Log References

### Completion Notes List

- **`PipelineAuditAction.ROUTING_CONFIG_CHANGED`** + **`ROUTING_CONFIG_AUDIT_RUN_ID`** sentinel; **`append_routing_config_audit`** wraps `append_audit_event` with bounded metadata (`op`, `rule_id`, `rule_type`; update includes `before` snapshot).
- **Admin API** under **`/admin/routing-rules`**: `GET` (optional `rule_type`), `POST`, `PATCH /{id}`, `DELETE /{id}` returning **204** via `Response`; normalization prevents CHECK constraint 500s (**422** on bad keys/slugs).
- **Repository:** `list_rules_admin`, `get_rule_by_id`, `create_rule`, `delete_rule`, `normalize_routing_key` / `normalize_slug`.
- **Web:** **`RoutingRulesAdmin`** + **`GET /auth/me`** alongside notifications load; non-admins see read-only copy only (**FR32** UI gate).
- **Tests:** `tests/test_routing_rules_admin_api.py` — 401, 403, list, create+audit, validation, patch cross-type rejection, sentinel unit check; full suite **283 passed**.

### File List

- `src/sentinel_prism/db/audit_constants.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/audit_events.py`
- `src/sentinel_prism/db/repositories/routing_rules.py`
- `src/sentinel_prism/api/routes/routing_rules.py`
- `src/sentinel_prism/main.py`
- `web/src/components/RoutingRulesAdmin.tsx`
- `web/src/App.tsx`
- `tests/test_routing_rules_admin_api.py`

## Change Log

- 2026-04-26 — Story context created (bmad-create-story); status **ready-for-dev**.
- 2026-04-27 — Implemented admin routing API, audit, UI, tests; status **review** (bmad-dev-story).
- 2026-04-26 — Code review findings fixed; status **done** (bmad-code-review).
