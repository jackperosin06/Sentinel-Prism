# Story 5.1: Routing rules engine

Status: review

<!-- Ultimate context engine analysis completed — comprehensive developer guide created -->

## Story

As the **system**,
I want **to apply topic/severity → team/channel rules**,
so that **the right stakeholders get alerts** (**FR21**).

## Acceptance Criteria

1. **Mock routing tables in PostgreSQL (FR21)**  
   **Given** durable **mock** routing configuration in the DB (not hard-coded-only)  
   **When** the routing engine evaluates an update  
   **Then** rules express at minimum: **topic** (map to **`impact_categories`** from classifications per existing product conventions—see Dev Notes) → **team** and **channel** identifiers, and **severity** → **channel** (or equivalent normalized targets—justify schema in Dev Notes)  
   **And** Alembic migration(s) create the table(s) with indexes appropriate for lookup by rule keys.

2. **Deterministic resolution for fixtures**  
   **Given** seeded or inserted routing rows (tests may use transactions/fixtures)  
   **When** the same classification inputs (severity + impact categories) are evaluated  
   **Then** resolved targets are **identical across runs** (no randomness, no time dependence)  
   **And** tie-break / precedence rules are **documented and tested** (e.g., first matching row by explicit `priority` column, or severity overrides topic—pick one and encode in tests).

3. **`route` graph node (Architecture §3.3–3.4, FR36)**  
   **Given** the pipeline currently ends with **`brief` → END** in `graph/graph.py`  
   **When** this story is complete  
   **Then** add **`graph/nodes/route.py`** implementing **`node_route`** that reads **`classifications`** (and **`run_id`**) from **`AgentState`**, loads applicable rules from the DB via a **service/repository** (nodes must not embed raw SQL—match **`brief` / `classify` patterns**)  
   **And** append partial state updates to **`routing_decisions`** (already `Annotated[list[dict], operator.add]` in `state.py`) with a **stable JSON-serializable shape** per item (e.g. `item_url`, `severity`, matched rule ids, resolved `team_id` / `channel` / labels—define the contract once and reuse in tests).

4. **Topology**  
   **Given** Architecture reference: `… → brief → route → [END]`  
   **When** compiled graph runs  
   **Then** **`brief` → `route` → END** (replace the direct **`brief` → END** edge)  
   **And** **`human_review_gate` → `brief` → `route` → END`** remains valid (no bypass of `brief`).

5. **Audit (FR33, Epic 8 searchability)**  
   **Given** routing is a significant pipeline action  
   **When** routing completes successfully for a run (or per batch—justify granularity)  
   **Then** append **`audit_events`** using **`PipelineAuditAction`** extended with a **new** distinct value (e.g. `ROUTING_APPLIED`)—follow the pattern in `graph/pipeline_audit.py` and `db/models.py` as used by `BRIEFING_GENERATED`.

6. **Explicitly out of scope**  
   **Given** Epic 5 split  
   **Then** **do not** implement in-app UI notifications (**5.2**), external email/Slack send (**5.3**), digest vs immediate scheduling (**5.4**), filing guardrail (**5.5**), or **React** admin table editor (**6.3**)—this story is **rules + graph node + persistence + audit + tests** only.  
   **And** **`delivery_events`** may remain empty; do not block the graph on notification delivery.

7. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** add **unit tests** for the rule resolver (pure deterministic inputs → outputs)  
   **And** add a **graph-level** test (compiled subgraph or full graph with stubbed DB session if that is the project pattern) proving **`routing_decisions`** is populated after **`route`**  
   **And** if Alembic head changes, update **`tests/test_alembic_cli.py`** per existing convention.

## Tasks / Subtasks

- [x] **Schema + migration (AC: #1)**  
  - [x] Design normalized tables for mock routing rules (severity rows, topic/impact rows, or unified with `rule_type`—document choice).  
  - [x] Alembic revision; seed **optional** dev data via migration or documented SQL—**tests** must not rely on prod-only seeds.

- [x] **Repository + routing service (AC: #1–#2)**  
  - [x] `db/repositories/` module for loading rules and resolving targets for `(severity, impact_categories)`.  
  - [x] Keep **`services/notifications/`** for future delivery; **new** `services/routing/` (or `services/routing_rules/`) is acceptable for pure evaluation logic to avoid mixing Epic 5.2+ delivery code.

- [x] **`node_route` (AC: #3–#4)**  
  - [x] Implement async `node_route` consistent with other nodes (`node_brief`, `node_classify`).  
  - [x] Merge into `routing_decisions`; on rule miss, emit an explicit structured “no match” or default route—**document** behavior and cover in tests.

- [x] **Graph wiring (AC: #4)**  
  - [x] `build_regulatory_pipeline_graph`: add node `"route"`, edge `brief` → `route`, `route` → `END`.  
  - [x] Export in `graph/nodes/__init__.py` if that is the package pattern.

- [x] **Audit enum + emit (AC: #5)**  
  - [x] Extend `PipelineAuditAction`; ensure pipeline audit helper accepts the new action.

- [x] **Tests (AC: #7)**  
  - [x] Resolver unit tests + graph integration test; follow `tests/test_graph_*.py` / `test_briefings_api.py` patterns.

### Review Findings

<!-- Code review 2026-04-20 — bmad-code-review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) -->

- [x] [Review][Patch] (resolved from D1) Update severity-rule docstrings so the `team_slug` backfill is the documented contract — aligned `services/routing/resolve.py` module docstring and `RoutingRule` class docstring in `db/models.py`. Locked in by `test_severity_only_match_backfills_team_from_severity_row`.
- [x] [Review][Patch] (resolved from D2) Remove the contradicting sentence from `services/routing/resolve.py` module docstring — rewritten to state out-of-scope short-circuits are terminal.
- [x] [Review][Patch] (resolved from D3, subsumes Critical P1) Skip `ROUTING_APPLIED` audit when no decisions were evaluated — `node_route` returns `{"routing_decisions": []}` on the empty-classifications path, and `_emit_routing_audit_if_needed` is only invoked when `decisions` is non-empty. Covered by `test_node_route_empty_classifications_returns_dict_and_skips_audit` + `test_node_route_skips_audit_when_all_items_deduped`.
- [x] [Review][Patch] (resolved from D4) Added "Routing audit helper divergence from `pipeline_audit`" Dev-Notes paragraph documenting the once-per-run semantics, the partial-unique-index safety net, and when to promote the dedupe logic into a shared helper.

- [x] [Review][Patch] Non-deterministic routing on equal `priority` — both repository helpers now order by `(priority ASC, id ASC)`. [`src/sentinel_prism/db/repositories/routing_rules.py`]
- [x] [Review][Patch] TOCTOU window on ROUTING_APPLIED — added partial unique index `uq_audit_events_routing_applied_run_id` (migration + ORM); `_emit_routing_audit_if_needed` catches the race-losing `IntegrityError` and treats as idempotent. Covered by `test_node_route_audit_integrity_error_is_idempotent`. [`alembic/versions/b2c3d4e5f6a7_add_routing_rules_table.py`, `db/models.py AuditEvent.__table_args__`, `graph/nodes/route.py`]
- [x] [Review][Patch] Broaden exception handling in `node_route` — both the rule-load block and `_emit_routing_audit_if_needed` now catch `Exception` with a structured `errors[]` envelope (plus a dedicated `IntegrityError` clause for the idempotent race). Covered by `test_node_route_rule_load_non_db_exception_does_not_crash`. [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] `in_scope` normalization — new `_is_out_of_scope` helper recognizes `False`, numeric `0`, and stringified falsy forms while preserving the missing-field default. Covered by `test_in_scope_stringified_false_treated_as_out_of_scope`. [`src/sentinel_prism/services/routing/resolve.py`]
- [x] [Review][Patch] Non-string `impact_categories` filter — new `_norm_impact_categories` helper drops non-string entries. Covered by `test_non_string_impact_categories_are_filtered`. [`src/sentinel_prism/services/routing/resolve.py`]
- [x] [Review][Patch] `item_url` normalization for dedupe — both existing-decision set construction and the classification loop now go through `_norm_item_url` (strip). Covered by `test_node_route_normalizes_item_url_against_existing_decisions`. [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] Missing `item_url` surfaces an observable signal — per-row `graph_route_missing_item_url` log + aggregate `errors[]` entry with `message="classifications_missing_item_url"`. Covered by `test_node_route_missing_item_url_is_reported_not_silently_dropped`. [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] DB-level CHECK constraints on `routing_rules.impact_category` / `severity_value` — rows are rejected unless they are `lower(trim(...))` and non-empty, so case/whitespace variants cannot alias through the resolver. [`alembic/versions/b2c3d4e5f6a7_add_routing_rules_table.py`, `db/models.py RoutingRule.__table_args__`]
- [x] [Review][Patch] Graph-level tests for `SQLAlchemyError` rule-load and audit-write-failure branches — `test_node_route_rule_load_sqlalchemy_error_returns_structured_error` + `test_node_route_audit_write_failure_surfaces_error_and_preserves_decisions`. [`tests/test_graph_route.py`]
- [x] [Review][Patch] Output `severity` echoes the normalized form — resolver now returns `{"severity": sev, ...}` (post-`_norm_severity`). Covered by `test_output_severity_is_normalized`. [`src/sentinel_prism/services/routing/resolve.py`]
- [x] [Review][Patch] Rule-load failure now returns `{"routing_decisions": [], "errors": [...]}` — consistent shape. Covered by `test_node_route_rule_load_sqlalchemy_error_returns_structured_error`. [`src/sentinel_prism/graph/nodes/route.py`]
- [x] [Review][Patch] Compiled-graph topology test — `test_compiled_pipeline_brief_route_end_wiring` asserts `(brief, route)` and `(route, END)` are present and `(brief, END)` is gone. [`tests/test_graph_route.py`]

- [x] [Review][Defer] Audit row committed in a separate transaction from the state checkpoint — deferred, pre-existing Epic 4.3 pattern shared by `node_brief`; cross-cutting outbox/transactional-hardening work. [`src/sentinel_prism/graph/nodes/route.py:125-134, 149-196`]
- [x] [Review][Defer] Audit metadata `items_processed` becomes misleading after a replayed audit-write failure (first attempt writes decisions, retry sees them in state and records `items_processed=0, skipped_duplicate_urls=N`) — deferred, subsumed by the pre-existing split-transaction pattern above. [`src/sentinel_prism/graph/nodes/route.py:125-134`]
- [x] [Review][Defer] Audit-write failure leaves state and audit divergent (decisions returned even when audit insert raised) — deferred, subsumed by the same split-transaction architectural concern. [`src/sentinel_prism/graph/nodes/route.py:125-134, 180-196`]
- [x] [Review][Defer] No `server_default` for `routing_rules.id` — raw SQL seed / CSV imports will fail with `null value`; defer to a project-wide UUID default pass (other tables share this pattern). [`alembic/versions/b2c3d4e5f6a7_add_routing_rules_table.py`]
- [x] [Review][Defer] CHECK allows negative `priority`, empty `team_slug`/`channel_slug`, whitespace-only `impact_category`/`severity_value` — hardening, not required by spec; revisit when admin rule-editor UI lands (Story 6.3). [`alembic/versions/b2c3d4e5f6a7_add_routing_rules_table.py`]
- [x] [Review][Defer] `ix_routing_rules_impact_category` / `ix_routing_rules_severity_value` are write-amplifying single-column indexes unused by the current resolver (filters on `rule_type`, iterates in Python) — revisit during query-pattern tuning once admin edit/search patterns are known. [`alembic/versions/b2c3d4e5f6a7_add_routing_rules_table.py`]

## Dev Notes

### Architecture compliance

- **Canonical locations:** `graph/nodes/route.py`, `services/...` for evaluation, `db/models.py` + `db/repositories/`, migrations under `alembic/versions/`. [Source: `_bmad-output/planning-artifacts/architecture.md` §6 layout + §3.3 node mapping]
- **Boundary:** Graph **nodes** call **services/repositories**; **services** do not import `graph.graph` or node modules (Architecture §6 Boundaries).
- **Orchestration changes only in `graph/graph.py`** for topology (FR36–FR38).

### Critical naming disambiguation

- **`sentinel_prism/graph/routing.py`** — conditional router **`route_after_classify`** (Story 3.5: human review vs continue). **Do not** merge Epic 5 routing logic into this file; that module is **control-flow only**.
- **`sentinel_prism/graph/nodes/route.py`** — **Epic 5** `node_route` applying **mock business routing** tables (**FR21**). Imports should read clearly (e.g. `from sentinel_prism.graph.nodes.route import node_route`).

### Mapping “topic” for FR21

- PRD **topic** aligns with classification **`impact_categories`** (see `services/llm/classification.py` and Story 4.3 briefing grouping). If multiple categories apply, precedence must match the deterministic rule in AC #2.

### Existing state fields

- **`routing_decisions`** is initialized empty in `new_pipeline_state` and listed in `tests/test_graph_shell.py` as an `operator.add` channel—preserve reducer semantics.

### NFR reminders

- **NFR5:** Mock routing / test endpoints only; no real PII in notification payloads in later epics—keep rule tables identifier-based (`team_id`, channel slugs).
- **NFR7 / NFR10:** Routing rule evaluation should not depend on external notification vendors (none in this story).

### Routing audit helper divergence from `pipeline_audit`

`node_route` does **not** call `graph.pipeline_audit.record_pipeline_audit_event`; it opens its own session via a private `_emit_routing_audit_if_needed` helper. The divergence is intentional (resolved from Story 5.1 review D4):

- **Once-per-run semantics.** Scout / normalize / classify / brief audit rows are emitted once per *retry*: every execution of those nodes appends a new completion row, which is useful for tracing transient retries. `ROUTING_APPLIED` is the first audit action that must be emitted once per *run* regardless of retries / replays. Folding that gate into `record_pipeline_audit_event` would introduce a cross-cutting flag (`once_per_run=True`) used by exactly one action, and would require every other caller to pass `False` explicitly.
- **TOCTOU safety lives at the DB.** The application-side `has_audit_event_for_run` check is only the fast path; the authoritative guarantee is the partial unique index `uq_audit_events_routing_applied_run_id` introduced by migration `b2c3d4e5f6a7`. `_emit_routing_audit_if_needed` catches the race-losing `IntegrityError` from that index and treats it as idempotent success. `record_pipeline_audit_event` today does not participate in that contract and expanding it to do so would leak the once-per-run invariant into every other node's audit path.
- **Fits existing patterns.** The helper mirrors `node_brief`'s pattern of a node-local async factory + structured `errors[]` envelope for audit-write failures, so error propagation remains consistent with Epic 4.3 even though the dedupe semantics differ.

If a second action ever needs once-per-run audit semantics (e.g. `NOTIFICATION_DELIVERED` in Story 5.3), promote the dedupe-by-run block into a shared helper at that point — until then, the local implementation keeps the shared `pipeline_audit` API narrow.

### References

- Epics: `_bmad-output/planning-artifacts/epics.md` — Epic 5, Story 5.1.
- PRD: `_bmad-output/planning-artifacts/prd.md` — **FR21**.
- Prior implementation patterns: `_bmad-output/implementation-artifacts/4-3-briefing-generation-and-grouping.md` (graph wiring, audit, Alembic, idempotency lessons).
- Code: `src/sentinel_prism/graph/graph.py`, `src/sentinel_prism/graph/state.py`, `src/sentinel_prism/graph/pipeline_audit.py`, `src/sentinel_prism/db/models.py`.

### Project structure notes

- **`services/notifications/__init__.py`** is a stub placeholder for Epic 5+; routing **evaluation** can live beside it without implementing delivery.
- Follow existing **repository + async session** patterns used by briefings and review queue.

## Dev Agent Record

### Agent Model Used

Composer (dev-story workflow)

### Debug Log References

### Completion Notes List

- Implemented `routing_rules` table with topic vs severity rows (CHECK constraint), `RoutingRuleType`, `PipelineAuditAction.ROUTING_APPLIED`.
- Pure resolver `resolve_routing_decision` documents precedence: topic rules set team + channel (first by `priority`); severity rules override `channel_slug` only; severity-only rows supply both slugs when no topic match.
- `node_route` loads rules via repository, dedupes by `item_url` against existing `routing_decisions`, emits audit once per run via `has_audit_event_for_run`.
- Graph topology: `brief` → `route` → `END`. Conftest stubs `node_route` session factory for CI; Alembic head `b2c3d4e5f6a7`.
- Full suite: `192 passed, 10 skipped`.

### File List

- `alembic/versions/b2c3d4e5f6a7_add_routing_rules_table.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/routing_rules.py`
- `src/sentinel_prism/db/repositories/audit_events.py`
- `src/sentinel_prism/services/routing/__init__.py`
- `src/sentinel_prism/services/routing/resolve.py`
- `src/sentinel_prism/graph/nodes/route.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/nodes/__init__.py`
- `tests/conftest.py`
- `tests/test_routing_resolve.py`
- `tests/test_graph_route.py`
- `tests/test_alembic_cli.py`
- `verify_imports.py`

### Change Log

- 2026-04-20: Story 5.1 — routing rules engine (DB, resolver, `node_route`, graph wiring, audit, tests).

---

## Technical requirements (guardrails)

| Requirement | Detail |
|-------------|--------|
| Stack | FastAPI, SQLAlchemy 2.x async, Alembic, LangGraph 1.1.x (see `requirements.txt`) |
| State shape | `routing_decisions`: list of dicts, append-only merge |
| DB | PostgreSQL; JSONB only if needed for flexible rule metadata—prefer clear columns for mock tables |
| API | No new public REST requirement for 5.1 unless you add a small **admin read-only** debug endpoint—**default: skip** to stay in scope |

## Architecture extraction (story-specific)

- **Graph topology:** `brief` → `route` → END [Source: architecture.md §3.4 diagram]
- **FR21–FR25 mapping:** Route + notifications folder layout [Source: architecture.md §6 table]
- **Audit:** Domain events in `audit_events` alongside checkpoints [Source: architecture.md §3.5]

## Library / framework notes

- **langgraph==1.1.6** — add node with `builder.add_node("route", node_route)`; no upgrade required for this story unless a security fix mandates it—pin changes belong in a separate chore.

## File structure requirements

- New: `src/sentinel_prism/graph/nodes/route.py`
- Modify: `src/sentinel_prism/graph/graph.py`, `src/sentinel_prism/db/models.py`, `alembic/versions/*.py`, `tests/…`
- Optional new: `src/sentinel_prism/services/routing/` (or equivalent), `src/sentinel_prism/db/repositories/routing_rules.py` (name to match repo conventions)

## Testing requirements

- Pytest + pytest-asyncio (existing project standard).
- Extend Alembic CLI test if migration count/version expectations are asserted.

## Previous story intelligence (Epic 4.3)

- **Graph:** Introduced `brief` and **`brief` → END**; this story **inserts** `route` after `brief`. Preserve **idempotent** patterns from `node_brief` when combining with routing (routing should not double-append duplicate decisions on resume unless architecturally required—**prefer idempotent** `route` keyed by `run_id` + `item_url`).
- **Audit:** `BRIEFING_GENERATED` fires only on true insert in some paths—mirror “emit once per logical completion” where applicable for routing audit noise control.
- **Review:** Code review fixed DB-authoritative loading and broad `except` blocks—use **typed exceptions** (`SQLAlchemyError`) in the hot path.

## Git intelligence (recent commits)

- `feat(epic-4): briefings pipeline, APIs, and sprint closure` — briefings graph + API landed; routing node is the next graph extension.
- Prior graph work: `feat(graph): Epic 3 classify, review routing, and transient retry policy` — establishes **`graph/routing.py`** naming; Epic 5 `route` **node** is additive.

## Latest technical information

- No new third-party dependency is required for mock table evaluation; use existing SQLAlchemy/async patterns.

## Project context reference

- No `project-context.md` found in repo; rely on Architecture + this story + codebase patterns above.

## Story completion status

- **review** — Implementation complete; ready for code review workflow.
