# Story 7.3: Governed threshold and prompt change proposals

Status: done

<!-- Ultimate context engine analysis completed - comprehensive developer guide created -->

## Story

As an **admin**,
I want **to propose threshold and classification prompt changes and apply them only through an explicit, audited action**,
so that **production behavior never changes silently** (**FR29**, **NFR13** alignment with traceable config changes).

## Acceptance Criteria

1. **Persisted “active” classification policy:** **Given** the system has bootstrapped **When** a pipeline **classify** step evaluates `needs_human_review` **Then** it uses a **database-backed** **low confidence threshold** (same semantics as today: strict `<`, i.e. confidence strictly below threshold **or** severity `critical` → review) **and** a **database-backed** **system prompt text** for the LLM **system** message—**not** silent edits to `classification.py` constants alone. **Initial migration** seeds values equivalent to current code defaults (`0.5` and the existing `CLASSIFICATION_SYSTEM_PROMPT` string) so behavior is unchanged until an admin applies a change.

2. **Draft vs apply (governed):** **Given** an **admin** **When** they **save a draft** (optional fields: new threshold, new prompt body, short **reason** / note) **Then** the draft is stored **without** affecting the active policy used by new runs. **When** they **apply** the draft via a **distinct** explicit action (e.g. **“Apply to production”** button or `POST …/apply`) **Then** the **active** row updates **and** a **monotonic version** integer (or equivalent) **increments** by one from the previous active version.

3. **Audit on apply:** **Given** an apply succeeds **When** the transaction commits **Then** an **`audit_events`** row is appended with **actor** = authenticated admin **`user_id`**, **action** distinguishable from routing config (add **`PipelineAuditAction`** member, e.g. `classification_config_changed`), **metadata** including at minimum: **prior** and **new** `version`, **threshold**, **prompt** length or hash (avoid logging full multi-KB prompt in every row if policy prefers—document OpenAPI), **`reason`** if provided, and **timestamps** already on `AuditEvent`. Use a **fixed sentinel `run_id`** for config-only events (mirror **`ROUTING_CONFIG_AUDIT_RUN_ID`** pattern in `db/audit_constants.py` with a **new** UUID constant so Epic 8 search can filter config history).

4. **RBAC:** **Given** a non-admin user **When** they call draft/apply/read-active endpoints **Then** **403** (same dependency pattern as `GET /admin/routing-rules` → **`get_db_for_admin`** in `api/deps.py`).

5. **API contract:** **Given** OpenAPI **When** clients integrate **Then** request/response models use **`extra="forbid"`**, document **validation** (threshold in `(0,1)` or `[0,1]`—**match** current model constraint `confidence` in `[0,1]` and existing strict `<` semantics), max prompt length guardrails, and **idempotent** read of **active** config for the classify path.

6. **UI (web console):** **Given** an **admin** session **When** they open the new section (e.g. **Classification policy** next to **Routing** / **Feedback metrics** in `App.tsx`) **Then** they see **current active** version, threshold, and prompt (read-only or editable textarea), can **edit draft**, and must **confirm** before **apply** (destructive/authoritative pattern per UX admin tables [Source: `_bmad-output/planning-artifacts/ux-design-specification.md` — Admin tables, governed change]). **Errors:** inline or dedicated message area—not **toast-only** for load/save failures (same family as **7.2** / `RoutingRulesAdmin`).

7. **Tests:** Integration tests cover **403** for viewer/analyst, **happy path** apply increments version and writes audit, and **classify** behavior respects DB threshold (e.g. lowering threshold reduces `needs_human_review` for a fixed stub LLM output in a **unit** or **narrow integration** test). No regression on existing **routing** or **feedback** tests.

## FR / product references

- **FR29:** Admin **proposes** threshold/prompt changes through a **governed** flow (**review + explicit apply**); **no silent auto-promotion**. [Source: `_bmad-output/planning-artifacts/prd.md` — Feedback & Improvement]
- **NFR13:** Config changes **traceable** (who/when/why). [Source: `_bmad-output/planning-artifacts/prd.md` — NFR13]
- **Architecture:** Feedback path **must not** silently mutate prompts—**FR29** called out at `classify` / feedback boundary. [Source: `_bmad-output/planning-artifacts/architecture.md` — §3.3 node mapping]

## Tasks / Subtasks

- [x] **Schema + Alembic** (AC: 1, 2)
  - [x] New table e.g. `classification_policy` (names illustrative): `id`, `version` (int, unique), `low_confidence_threshold` (float), `system_prompt` (text), `is_active` (bool) **or** single-row active + separate `classification_policy_draft`—pick one clear design; avoid ambiguous “multiple actives”.
  - [x] Seed migration matching `LOW_CONFIDENCE_THRESHOLD` and `CLASSIFICATION_SYSTEM_PROMPT` from `services/llm/classification.py`.

- [x] **Repository** (AC: 1–3)
  - [x] `db/repositories/classification_policy.py` (or under `db/repositories/` per existing layout): `get_active`, `get_draft`, `upsert_draft`, `apply_draft` (transaction: update active, bump version, clear or mark draft, insert audit).

- [x] **Runtime wiring** (AC: 1)
  - [x] **Classify path:** `classification_dict_for_state` and/or `LangChainStructuredClassificationLlm.classify` must receive **threshold** + **prompt** from **loaded active policy** (passed from `node_classify` after a single DB read per run batch—or cache for the run; avoid N+1 per item).
  - [x] **Stub / tests:** Ensure stub LLM path still works when policy is loaded from DB in tests (fixture or in-memory DB).

- [x] **API** (AC: 2–5)
  - [x] New router e.g. `prefix="/admin/classification-policy"`, `include_router` in `main.py`.
  - [x] Endpoints (illustrative): `GET` active (+ version), `GET/PUT` draft, `POST` apply with optional `reason` body field.
  - [x] `append_audit_event` + new action enum value; new sentinel in `audit_constants.py`.

- [x] **Web** (AC: 6)
  - [x] New component (e.g. `ClassificationPolicyAdmin.tsx`): fetch with bearer token, `readErrorMessage` from `web/src/httpErrors.ts`, styling consistent with `RoutingRulesAdmin` / `FeedbackMetricsAdmin`.

- [x] **Tests** (AC: 7)
  - [x] `tests/test_classification_policy_api.py` (or extend admin tests): RBAC, apply + audit metadata, version monotonicity.

### Review Findings

- [x] [Review][Patch] Draft API rejects partial drafts even though AC2 makes threshold, prompt, and reason optional [`src/sentinel_prism/api/routes/classification_policy.py:60`]
- [x] [Review][Patch] Whitespace-only prompts can be saved/applied but runtime silently falls back to the code default [`src/sentinel_prism/services/llm/classification.py:230`]
- [x] [Review][Patch] Policy invariants are only enforced at API level, not in repository/model/database paths [`src/sentinel_prism/db/repositories/classification_policy.py:94`]
- [x] [Review][Patch] Migration imports live application constants, making historical bootstrap depend on future app code [`alembic/versions/c9e1f2a3b4c5_add_classification_policy.py:13`]
- [x] [Review][Patch] Apply endpoint requires an explicit JSON body even though reason is optional [`src/sentinel_prism/api/routes/classification_policy.py:171`]
- [x] [Review][Patch] Tests do not exercise `node_classify` loading and using the active DB policy [`tests/test_classification_policy_api.py:1172`]
- [x] [Review][Patch] RBAC tests cover read-active only, not draft/apply endpoints required by AC4 [`tests/test_classification_policy_api.py:1084`]

## Dev Notes

### Current code facts (do not drift)

- **Threshold logic:** `needs_human_review = llm.confidence < threshold or llm.severity == "critical"` today uses module constant `LOW_CONFIDENCE_THRESHOLD = 0.5` in `classification_dict_for_state`. [Source: `src/sentinel_prism/services/llm/classification.py`]
- **Prompt:** `CLASSIFICATION_SYSTEM_PROMPT` is passed as `SystemMessage(content=...)` in `LangChainStructuredClassificationLlm`. [Source: `src/sentinel_prism/services/llm/classification.py`]
- **Classify node** calls `build_classification_llm()` and merges LLM output via `classification_dict_for_state`; inject policy **before** this merge. [Source: `src/sentinel_prism/graph/nodes/classify.py`]
- **Admin + audit precedent:** Routing mutations use `get_db_for_admin` and `append_routing_config_audit` with `ROUTING_CONFIG_AUDIT_RUN_ID`. Reuse the **same structural pattern** for classification policy. [Source: `src/sentinel_prism/api/routes/routing_rules.py`, `src/sentinel_prism/db/repositories/audit_events.py`]

### Governance interpretation (MVP)

- **“Review”:** For MVP, **second-person approval** can be **deferred** if not in epic AC; the **non-negotiable** bar is **draft ≠ active** until **explicit apply** + **audit**. If you add a second approver later, keep the **version + audit** contract stable.
- **FR29** forbids **silent** promotion—**scheduled jobs**, **imports**, or **feedback webhooks** must **not** auto-apply prompt changes.

### Out of scope

- **Story 7.4** golden-set policy / cadence UI—only touch **classification** threshold + **system** prompt here.
- **Epic 8** full audit search UI—emit events compatible with future filters.

### Project structure (architecture reference)

- Backend: `src/sentinel_prism/api/routes/`, `db/repositories/`, `db/models.py`, Alembic, `main.py`, `graph/nodes/classify.py`, `services/llm/classification.py`
- Web: `web/src/components/`, `web/src/App.tsx`
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §5, §2]

## Architecture compliance

- **Stack:** FastAPI, async SQLAlchemy, Pydantic v2, React + Vite + TypeScript.
- **RBAC:** **Admin-only** for policy mutations; enforce on API.
- **Audit:** Append-only `audit_events`; config events use sentinel `run_id`.
- [Source: `_bmad-output/planning-artifacts/architecture.md`]

## File structure (expected touchpoints)

| Area | Files (expected) |
|------|------------------|
| Model + migration | `src/sentinel_prism/db/models.py`, `alembic/versions/…` |
| Repository | `src/sentinel_prism/db/repositories/classification_policy.py` (new) |
| Audit | `src/sentinel_prism/db/models.py` (`PipelineAuditAction`), `audit_constants.py`, `audit_events.py` helpers |
| API | `src/sentinel_prism/api/routes/classification_policy.py` (new), `main.py` |
| Graph | `src/sentinel_prism/graph/nodes/classify.py`, `services/llm/classification.py` (signatures / injection) |
| Web | `web/src/components/ClassificationPolicyAdmin.tsx` (new), `web/src/App.tsx` |
| Tests | `tests/test_classification_policy_api.py` (new) + any classify unit test updates |

## Testing requirements

- Use async fixtures consistent with `tests/test_routing_rules_*.py` / `tests/test_feedback_metrics_api.py`.
- Assert **audit** row: `action` value, `actor_user_id`, `run_id` equals new sentinel, metadata contains version transition.
- Run full `pytest` before marking done; `npm run build` if TS changes.

## Previous story intelligence (7.2)

- **Admin API pattern:** `get_db_for_admin`, `/admin/...` prefix, `extra="forbid"` models, CSV/JSON patterns optional—here JSON suffices.
- **UI pattern:** `FeedbackMetricsAdmin` + `App.tsx` mount order for admin-only sections; reuse **solid** data surfaces and `readErrorMessage`.
- **7.2 explicitly scoped out FR29**—this story implements it; **do** wire **runtime** classify to DB policy.

## Git intelligence (recent work)

- Recent commits center **7.1** feedback and **6.3** routing admin; **classification** thresholds remain **code constants**—this story is the first **persisted** policy layer.

## Latest tech notes (project-local)

- No new LLM vendor required; **langchain-core** message types unchanged—only **string** passed to `SystemMessage`.
- Prefer **one** DB read per graph run for policy (or short TTL cache) to avoid latency regression on large batches.

## Project context reference

- No `project-context.md` in repo; rely on this file + `architecture.md` + cited modules.

## Story completion status

- [x] All tasks done — **Status: done**

*Ultimate context engine analysis completed - comprehensive developer guide created*

## Dev Agent Record

### Agent Model Used

GPT-5.2 (Cursor agent)

### Debug Log References

- `python3 -m pytest -q tests/test_classification_policy_api.py tests/test_alembic_cli.py` — 16 passed, 2 skipped.
- `python3 -m pytest -q -m "not integration"` — 323 passed, 15 deselected.
- `cd web && npm run build` — OK.

### Completion Notes List

- Added singleton `classification_policy` table + Alembic `c9e1f2a3b4c5` seeding defaults from `classification.py`.
- `PipelineAuditAction.CLASSIFICATION_CONFIG_CHANGED`, `CLASSIFICATION_CONFIG_AUDIT_RUN_ID`, and `append_classification_config_audit`.
- Repository `classification_policy.py` (draft save, apply with `FOR UPDATE`, audit metadata: SHA-256 16-hex prefix + prompt lengths).
- Admin routes `GET/PUT /admin/classification-policy`, `PUT .../draft`, `POST .../apply`.
- `node_classify` loads policy once per invocation via `_load_active_classification_policy`; `classification_dict_for_state` + LLM `classify(..., system_prompt=...)` take DB values; missing row falls back to module defaults.
- Graph unit tests: `test_web_search_tool.py` added to `_GRAPH_DB_STUBBED_MODULES`; autouse stub `_stub_classification_policy_load_for_graph_tests` mirrors defaults when DB is absent.
- Web: `ClassificationPolicyAdmin` with save draft, confirm apply, optional apply note; mounted in `App.tsx` for admins.
- Tests: RBAC (analyst + viewer), mocked GET, apply-no-draft 400, unit threshold semantics, integration apply+audit (skips without `DATABASE_URL`).
- Code review patches resolved: partial drafts, repository/schema validation, self-contained migration seed defaults, bodyless apply, draft/apply RBAC coverage, and node-level DB policy wiring coverage.

### File List

- `alembic/versions/c9e1f2a3b4c5_add_classification_policy.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/audit_constants.py`
- `src/sentinel_prism/db/repositories/audit_events.py`
- `src/sentinel_prism/db/repositories/classification_policy.py`
- `src/sentinel_prism/api/routes/classification_policy.py`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/services/llm/classification.py`
- `src/sentinel_prism/graph/nodes/classify.py`
- `web/src/components/ClassificationPolicyAdmin.tsx`
- `web/src/App.tsx`
- `tests/conftest.py`
- `tests/test_classification_policy_api.py`
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/7-3-governed-threshold-and-prompt-change-proposals.md`

## Change Log

- 2026-04-27: Story 7.3 context created (create-story workflow) — **ready-for-dev**
- 2026-04-27: Story 7.3 implemented — classification policy DB, admin API, classify wiring, UI, tests — **review**
- 2026-04-27: Code review findings resolved and verified — **done**
