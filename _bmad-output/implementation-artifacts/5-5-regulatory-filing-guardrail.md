# Story 5.5: Regulatory filing guardrail

Status: done

<!-- Ultimate context engine analysis completed — comprehensive developer guide created -->

## Story

As a **compliance officer**,
I want **a guarantee the system never submits binding regulatory filings**,
so that **automation cannot create regulatory liability** (**FR41**).

## Acceptance Criteria

1. **Outbound surface inventory (enumerated)**  
   **Given** the deployed application and LangGraph workflow  
   **When** an engineer audits **all** network and submission-style I/O initiated by the backend (not the browser)  
   **Then** a **single authoritative document** in-repo lists every outbound **kind** (purpose, typical protocol, primary code locations) so nothing is implicit.  
   **And** the inventory explicitly states that **none** of these surfaces are used for **binding regulatory submissions** or **external filings** as defined in the PRD (**FR41**).

2. **No filing connectors**  
   **Given** the codebase after this story  
   **When** reviewers inspect `services/connectors/`, notification adapters, graph tools, and LLM integrations  
   **Then** there is **no** code path whose purpose is to **submit**, **file**, **transmit for official acceptance**, or **register** content with a **regulatory authority** (no new “filing” clients, endpoints, or batch jobs).  
   **And** existing connectors remain **read-oriented** (fetch RSS/HTTP/HTML, public search, LLM inference, user notifications) — consistent with current architecture.

3. **Allowlist + automated guard**  
   **Given** the inventory from AC #1  
   **When** CI runs  
   **Then** an **automated check** (pytest preferred; shell script only if justified) enforces that **only allowlisted modules / patterns** may perform outbound HTTP client usage (`httpx` client construction or `post`/`request` to third parties), **or** fails with a clear message naming the offending file and how to update the allowlist.  
   **And** the check is **maintainable**: adding a **new** legitimate outbound integration requires an **explicit** allowlist update and inventory edit (no silent drift).

4. **Operator-visible posture (lightweight)**  
   **Given** operators read configuration or internal docs  
   **When** they need to understand system boundaries  
   **Then** `.env.example` (or adjacent comment block) includes a **one-line reaffirmation** that the product does **not** perform automated binding filings (**FR41**), without duplicating legal disclaimers from the PRD.

5. **Tests**  
   **Given** CI  
   **When** tests run  
   **Then** the new guard test(s) pass on the current tree and **fail** if a new unapproved `httpx` usage appears outside the allowlist (adjust if project standardizes all outbound HTTP through a single wrapper — then the guard targets that wrapper + known call sites).

## Tasks / Subtasks

- [x] **Authoritative outbound inventory (AC: #1, #4)**  
  - [x] Add `docs/regulatory-outbound-allowlist.md` (or path agreed in Dev Notes) with a table: **Surface** | **Purpose (non-filing)** | **Protocols / APIs** | **Primary paths** (`src/sentinel_prism/...`).  
  - [x] Cover at minimum: **ingestion** (`services/connectors/*` — GET-style fetch + retry), **notifications** (`services/notifications/adapters/slack.py` webhook POST, `smtp.py`), **optional web search** (`graph/tools/tavily_search.py` → Tavily SDK), **optional LLM** (`services/llm/classification.py` → OpenAI via LangChain), **DB** (Postgres — not “filing” but list for completeness).  
  - [x] Add FR41 one-liner to `.env.example` (AC #4).

- [x] **Allowlist implementation (AC: #3)**  
  - [x] Prefer a small module under `src/sentinel_prism/` (e.g. `compliance/outbound_allowlist.py`) defining **allowed file paths or glob patterns** matching the inventory — keep in sync with `docs/regulatory-outbound-allowlist.md`.  
  - [x] Implement pytest that collects Python files under `src/sentinel_prism` (exclude `tests`, `__pycache__`) and flags **disallowed** `httpx` usage using AST (recommended) or a constrained regex with clear comments — **avoid** brittle full-repo text grep unless AST is impractical.

- [x] **Filing anti-patterns (AC: #2)**  
  - [x] Confirm no new modules introduce regulatory-submission semantics; if naming could confuse auditors, prefer neutral names (`notify`, `fetch`, `search`, `classify`).  
  - [x] If any HTTP call uses POST, document in the inventory why it is **not** a filing (e.g. Slack incoming webhook, future webhooks).

- [x] **Verification (AC: #5)**  
  - [x] Run full pytest suite; update `verify_imports.py` if new top-level package is added.  
  - [x] If Alembic or packaging changes are **not** needed, do not introduce them — this story should be mostly docs + guard tests.

### Review Findings

- [x] [Review][Patch] `httpx` submodule imports can bypass the guard (`import httpx._client`, `from httpx._client import ...`) [tests/test_outbound_allowlist.py:17] — fixed
- [x] [Review][Patch] Guard enforces import presence only, not AC #3 usage patterns (`httpx` client construction / `post` / `request`) [tests/test_outbound_allowlist.py:17] — fixed
- [x] [Review][Patch] "Lockstep" doc+allowlist policy is not CI-enforced, allowing inventory drift from `ALLOWED_HTTPX_SOURCE_FILES` [docs/regulatory-outbound-allowlist.md:7] — fixed

## Dev Notes

### Architecture compliance

- **FR21–FR25** routing/notifications stay in `graph/nodes/route.py` and `services/notifications/`; this story **does not** change routing semantics — it **constrains and documents** what outbound work is permitted. [Source: `_bmad-output/planning-artifacts/architecture.md` — §6 mapping table]
- **Connectors return DTOs**; normalization stays in graph nodes — do not add “submit” connectors under `services/connectors/`. [Source: `architecture.md` — Boundaries]
- **Graph nodes call services**; compliance guard modules must **not** import `graph.graph` (avoid cycles).

### Dependency on Epic 5 stories 5.1–5.4

- **5.1–5.3** established **mock routing**, **in-app**, and **external** (email/Slack) notifications — all are **internal alerting**, not regulatory filing. The inventory must say so explicitly for auditor clarity.
- **5.4** added **digest** scheduling and **httpx** usage patterns in the same notification stack — any new httpx call sites must remain inside allowlisted notification/connector paths or the allowlist test must be updated **with** inventory edits.

### Previous story intelligence (5.4)

- Notification stack is **policy-driven** (`notification_policy.py`, `scheduling.py`, `digest_flush.py`); guardrail work should **not** weaken idempotency or delivery logging. Prefer **additive** compliance artifacts (docs + tests) over refactors.
- Story 5.4 file notes one **open** test contract item: `test_graph_route` may need to assert Story 5.2 enqueue behavior — **optional** to fix while touching tests if you run the suite and see failure; do not expand scope unless red CI.

### Project structure notes

- **`docs/`** is the configured `project_knowledge` root in BMM config — a natural home for the outbound inventory operators may read outside BMAD artifacts.
- If the team prefers implementation artifacts only, relocate the markdown under `_bmad-output/implementation-artifacts/` but then link from README or architecture — **pick one canonical path** and reference it from the allowlist module docstring.

### PRD / product context

- **FR41:** System does **not** initiate **binding regulatory submissions** or **external filings** on behalf of users. [Source: `_bmad-output/planning-artifacts/prd.md`]
- **Non-negotiable:** “No automated binding filings” and decision-support (not filings) posture — the inventory should use the same vocabulary.

### References

- Epics: `_bmad-output/planning-artifacts/epics.md` — Epic 5, Story 5.5.
- PRD: `_bmad-output/planning-artifacts/prd.md` — FR41, compliance posture sections.
- Architecture: `_bmad-output/planning-artifacts/architecture.md` — FR21–FR25 locations, repo layout.
- Known httpx usage today (verify during implementation): `services/connectors/*`, `services/notifications/adapters/slack.py`.

## Dev Agent Record

### Agent Model Used

Composer (Cursor agent, dev-story workflow)

### Debug Log References

### Completion Notes List

- Added `docs/regulatory-outbound-allowlist.md` (FR41 inventory + Slack POST rationale).
- Added `compliance/outbound_allowlist.py` with `ALLOWED_HTTPX_SOURCE_FILES` synced to current eight `httpx` importers.
- Added `tests/test_outbound_allowlist.py` (AST import scan, stale-allowlist and missing-file checks).
- Extended `.env.example` with FR41 operator note; `verify_imports.py` imports new compliance module.
- Full suite: 269 passed, 10 skipped.

### File List

- `docs/regulatory-outbound-allowlist.md`
- `src/sentinel_prism/compliance/__init__.py`
- `src/sentinel_prism/compliance/outbound_allowlist.py`
- `tests/test_outbound_allowlist.py`
- `.env.example`
- `verify_imports.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-04-20: Story 5.5 — regulatory filing guardrail (inventory, httpx allowlist, CI test, FR41 `.env.example` note).

## Technical requirements (guardrails)

| Requirement | Detail |
|-------------|--------|
| Stack | Python 3.x, FastAPI, **httpx 0.28.x** (pinned in `requirements.txt`) — guard should target actual usage patterns |
| AST | Use `ast` module in pytest for import/call analysis where possible — clearer errors than raw `rg` |
| Scope | **Backend `src/sentinel_prism` only** for the automated guard unless extended deliberately to `web/` (out of scope unless PRD requires) |

## Architecture extraction (story-specific)

- Architecture maps **FR39–FR41** near auth in the requirements table; **FR41** in the PRD is explicitly **non-filing**. Treat the PRD + this story as authority for filing guardrails, not the single row in the architecture table. [Source: `architecture.md` §6]

## Library / framework notes

- **Tavily** (`tavily` package) and **langchain-openai** are **optional** at runtime; inventory must still mention them when env keys enable those paths.
- No version upgrades required for this story unless a security advisory blocks CI.

## File structure requirements

- New: `docs/regulatory-outbound-allowlist.md` (or equivalent), `src/sentinel_prism/compliance/outbound_allowlist.py` (names flexible), `tests/test_outbound_allowlist.py` (or similar).  
- Modify: `.env.example`, optionally `verify_imports.py` if new package root.

## Testing requirements

- Pytest-only; fast test (no DB) preferred for the AST allowlist scan.
- If AST proves too strict for dynamic imports, document the exception in the test module and narrow the rule — **do not** disable the guard without PM/architecture visibility.

## Git intelligence (recent commits)

- `feat(epic-5): immediate vs digest scheduling...` — Story 5.4; expanded notification and worker surface — **include new files** in allowlist if they add httpx usage.
- Prior: external Slack/SMTP, in-app notifications, routing — established outbound notification patterns.

## Latest technical information

- **FR41** is a **product/legal boundary**, not a library feature — enforcement is **documentation + CI allowlist**, not a third-party “compliance SDK.”
- Prefer **explicit allowlist** over blocking all POST requests: legitimate notification webhooks use POST but are not filings.

## Project context reference

- No `project-context.md` found in repo; use Architecture + this story + inventory you maintain.

## Story completion status

- **done** — Code review patch findings resolved; guard and inventory checks are in sync.

### Open questions / clarifications (optional follow-up)

- Whether **web** SPA ever calls third-party filing APIs directly (unlikely) — confirm out of scope for backend guard unless product says otherwise.
