---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - prd.md
  - architecture.md
  - product-brief-sentinel-prism.md
workflowType: epics-stories
project_name: Sentinel Prism
status: complete
completedAt: "2026-04-12T21:00:00Z"
---

# Sentinel Prism — Epic Breakdown

## Overview

This document decomposes **PRD** and **Architecture** requirements into epics and user stories for implementation. **No separate UX specification** was found; UI work is driven by **PRD** functional requirements (FR30–FR32, FR9, etc.) and **NFR11** (accessibility).

---

## Requirements Inventory

### Functional Requirements

```
FR1: Admin can register a public source with metadata (name, jurisdiction, type, endpoints, schedule).
FR2: System can poll or trigger fetches per source according to schedule.
FR3: System can deduplicate new items against previously ingested fingerprints (URL + content hash or equivalent).
FR4: System can retry failed fetches with backoff and record failure reason.
FR5: System can use alternate retrieval paths when primary feed fails (e.g., fallback URL or HTML parse), when configured.
FR6: Admin can enable/disable a source without deleting history.
FR7: System can persist raw capture with timestamp and source reference.
FR8: System can produce a normalized update record with title, dates, URL, source, jurisdiction, document type, body text, summary when present, and metadata.
FR9: Analyst can view original and normalized fields side by side for any update.
FR10: System can attach extraction quality or parser confidence to normalized records.
FR11: System can determine in-scope for pharma compliance per configurable rules.
FR12: System can assign severity (at least: critical, high, medium, low) per update.
FR13: System can assign impact categories (e.g., safety, labeling, manufacturing, deadlines).
FR14: System can assign urgency (e.g., immediate, time-bound, informational).
FR15: System can store a natural-language rationale and confidence score for each classification.
FR16: System can route items to a human review queue when confidence is below threshold or severity is high and ambiguity is detected per policy.
FR17: Analyst can approve, reject, or override classification from the review queue with notes.
FR18: System can group updates into briefings by configurable dimensions (date range, severity, jurisdiction, topic).
FR19: User can view a list of briefings and open detail with grouped updates.
FR20: Briefing content includes structured sections at minimum: what changed, why it matters, who should care, confidence, suggested actions (as applicable).
FR21: System can apply routing rules from mock tables (topic → team/channel, severity → channel).
FR22: System can send immediate notifications for critical/high items per policy and batch lower-priority items into digests.
FR23: System can record delivery outcome (success, failure, error class) per notification attempt.
FR24: User can receive in-app notifications for routed items.
FR25: System can send notifications via at least one external channel (email or Slack-compatible) using sandbox credentials.
FR26: User can submit feedback on an update (incorrect relevance, severity, false positive/negative) with comments.
FR27: System can persist feedback with links to the classification decision and user identity.
FR28: Admin can export or view aggregated feedback metrics (e.g., override rate, category distribution).
FR29: Admin can propose changes to thresholds and prompt versions through a governed flow (review + explicit apply)—no silent auto-promotion.
FR30: User can view an overview dashboard with counts by severity, new items, items in review, and top sources.
FR31: User can filter and sort the update explorer by date, severity, jurisdiction, topic, source, status.
FR32: Admin can manage routing and escalation mock tables through the UI within RBAC.
FR33: System can produce an audit trail entry for each significant action (ingest, classify, override, route, notify, config change).
FR34: Operator can search audit history by update id, source, time range, and user.
FR35: Operator can replay a workflow run or segment from persisted state for debugging (non-destructive).
FR36: Workflow engine can maintain shared state across processing stages for a single run (e.g., candidate updates, classifications, routing decisions).
FR37: Workflow engine can branch based on classification and confidence.
FR38: Workflow engine can retry defined steps without losing correlation ids for audit.
FR39: User must authenticate to access the console (local application-managed auth this phase; future IdP per FR46/NFR14).
FR40: System enforces role-based permissions for view, review, configure, and administer.
FR41: System does not initiate binding regulatory submissions or external filings on behalf of users.
FR42: Scout ingestion uses direct HTTP/RSS/HTML connectors for registered public sources; generic web search is not the primary path for routine ingestion.
FR43: Optional web research tool nodes can call a pluggable search abstraction (Tavily recommended default); public context only.
FR44: Regulatory Affairs / Compliance can define and approve golden-set label policy and reference correctness; AI/Engineering provides tooling and metrics.
FR45: System records golden-set and evaluation configuration changes on agreed cadence and after major model/prompt updates, visible in audit/configuration history.
FR46: Authentication supports local users (password/magic link) and preserves stable internal user identity suitable for future OIDC/SAML mapping.
```

### NonFunctional Requirements

```
NFR1: Overview dashboard loads primary widgets in < 3 seconds at P95 under nominal load during demos.
NFR2: Classification step for a single update completes within 2 minutes P95 when model services are available (excluding deliberate queueing).
NFR3: All traffic uses TLS in transit; secrets are not stored in source control.
NFR4: Authentication sessions expire per policy; passwords or keys meet minimum complexity if local auth is used.
NFR5: PII in notifications and logs is minimized; mock routing uses test endpoints only.
NFR6: Scheduled ingestion jobs are idempotent per source per window.
NFR7: Core console remains usable (read-only mode acceptable) if notification provider fails.
NFR8: Structured logs include correlation/run id across services.
NFR9: Metrics exposed per source: success rate, error rate, latency, items ingested.
NFR10: External channels fail gracefully; failures surface to admins with actionable errors.
NFR11: Console meets WCAG 2.1 Level A minimum for primary flows (keyboard navigation, labels)—AA where low-cost.
NFR12: Calls to external web search tools only transmit public or public-derived query text; no classified internal data in this phase.
NFR13: Golden-set revisions and eval baseline changes are traceable (who/when/why) and align to cadence/post-change triggers.
NFR14: Auth implementation isolates credential verification behind a provider interface so OIDC/SAML can be added without breaking user–role bindings or audit attribution.
```

### Additional Requirements (from Architecture)

```
- Orchestration is implemented with LangGraph StateGraph: shared AgentState, nodes for Scout/Normalize/Classify/Brief/Route, conditional edges, checkpointer (Memory/Postgres), run_id correlation.
- Backend: FastAPI (async), PostgreSQL, Alembic; optional Celery/APScheduler for schedules.
- Frontend: React + Vite + TypeScript; consumes REST/OpenAPI only.
- Graph module layout: state.py, graph.py, nodes/, tools/tavily_search.py; services/ for connectors, LLM, notifications.
- API routes: POST /runs, GET /runs/{id}, POST /runs/{id}/resume for HITL.
- Nodes call services; services do not import graph definitions.
```

### UX Design Requirements

```
(No standalone UX design document.) UX-DR coverage is embedded in Epic 6 stories and NFR11 acceptance criteria for primary console flows.
```

### FR Coverage Map

| FR | Epic | Notes |
|----|------|--------|
| FR1–FR6, FR42 | Epic 2 | Source registry & ingestion |
| FR7–FR15, FR36–FR38, FR43 | Epic 3 | Pipeline & graph |
| FR16–FR17 | Epic 4 | Review queue |
| FR18–FR20 | Epic 4 | Briefings |
| FR21–FR25, FR41 | Epic 5 | Routing & notifications; FR41 guardrail |
| FR26–FR29, FR44–FR45 | Epic 7 | Feedback & golden-set governance |
| FR30–FR32, FR9 | Epic 6 | Console & config UI |
| FR33–FR35 | Epic 8 | Audit & replay |
| FR39–FR41, FR46 | Epic 1 | Auth & safety (FR41 also reinforced in Epic 5) |

**NFR coverage:** Epic 1 (NFR3–5, 14), Epic 2 (6, 9, 10 partial), Epic 3 (2, 8, 12), Epic 5 (7, 10), Epic 6 (1, 11), Epic 7 (13), Epic 8 (8).

---

## Epic List

### Epic 1: Secure platform foundation

Users and operators can access the system through **local authentication** with **role-based permissions**, on a **documented project skeleton** ready for the graph and API.

**FRs covered:** FR39, FR40, FR41, FR46

### Epic 2: Regulatory source configuration & reliable ingestion

**Compliance admins** can register and operate **public** sources; the system **polls**, **deduplicates**, **retries**, and exposes **per-source health**—using **direct connectors** (not generic search) for routine ingestion.

**FRs covered:** FR1–FR6, FR42

### Epic 3: LangGraph intelligence pipeline (fetch → normalize → classify)

The product runs an end-to-end **StateGraph** with **shared state**, **checkpoints**, **conditional branching**, and **classification** (rules + LLM), including optional **Tavily** (or equivalent) for **public** research enrichment.

**FRs covered:** FR7–FR15, FR33 (pipeline events), FR36–FR38, FR43

### Epic 4: Human review & regulatory briefings

**Analysts** resolve **review-queue** items and consume **structured briefings** grouped by policy dimensions.

**FRs covered:** FR16–FR20

### Epic 5: Intelligent routing & multi-channel notification

The system applies **mock routing rules**, sends **in-app** and **external** notifications with **delivery logging**, respects **immediate vs batched** policies, and **never** performs **binding regulatory filings** (guardrail).

**FRs covered:** FR21–FR25, FR41

### Epic 6: Analyst console & configuration UI

**Users** use the **dashboard**, **update explorer** (with **side-by-side** detail), and **admins** edit **routing/escalation** tables within **RBAC**.

**FRs covered:** FR9, FR30–FR32

### Epic 7: Feedback loops & golden-set governance

**Users** submit **feedback**; **admins** view **metrics** and **governed** prompt/threshold changes; **Regulatory Affairs** owns **golden-set** policy with **audited** config history.

**FRs covered:** FR26–FR29, FR44–FR45

### Epic 8: Audit search, replay & operational observability

**Operators** **search** audit history and **replay** workflow segments; logs carry **run/correlation** ids.

**FRs covered:** FR33–FR35 (full audit/replay; overlaps Epic 3 for pipeline audit events)

---

## Epic 1: Secure platform foundation

**Goal:** Establish repo layout, database, **local auth** with **RBAC**, and **auth provider abstraction** for future **SSO**, without implementing domain features yet.

### Story 1.1: Initialize application skeleton per architecture

As a **developer**,
I want **the monorepo structure (FastAPI backend, React web placeholder, Python package layout) documented and runnable**,
So that **subsequent stories have a consistent home for code**.

**FRs:** — (foundation; Architecture ADR)

**Acceptance Criteria:**

**Given** a clean checkout  
**When** I follow README bootstrap steps  
**Then** the API responds healthy at `/health` and the web app builds  
**And** `src/` package layout matches **Architecture** (`sentinel_prism` or agreed name)  
**And** dependencies are pinned in `requirements.txt` / `package.json`

---

### Story 1.2: PostgreSQL schema core and Alembic migrations

As a **developer**,
I want **Alembic migrations and a minimal database URL configuration**,
So that **later stories can persist users and domain data**.

**Acceptance Criteria:**

**Given** configured `DATABASE_URL`  
**When** I run migrations  
**Then** migration history applies cleanly on empty DB  
**And** `.env.example` documents required variables (**NFR3**: no secrets in repo)

---

### Story 1.3: Local user accounts and session or token auth

As a **user**,
I want **to sign in with email/password or magic link (MVP choice)**,
So that **only authenticated users access the console** (**FR39**, **FR46**).

**Acceptance Criteria:**

**Given** a registered user  
**When** they submit valid credentials  
**Then** they receive a session or token usable for API calls  
**And** passwords meet minimum complexity (**NFR4**)  
**And** stable internal `user_id` exists for audit (**FR46**)

---

### Story 1.4: RBAC enforcement on API routes

As an **administrator**,
I want **roles (Admin / Analyst / Viewer minimum)** enforced on APIs,
So that **configure vs view vs review** paths are protected (**FR40**).

**Acceptance Criteria:**

**Given** users with different roles  
**When** they call protected endpoints  
**Then** forbidden actions return 403  
**And** role checks are centralized (middleware/dependency)

---

### Story 1.5: Auth provider interface stub for future IdP

As a **developer**,
I want **credential verification behind a pluggable interface**,
So that **OIDC/SAML can be added without rewriting RBAC** (**NFR14**).

**Acceptance Criteria:**

**Given** the auth provider abstraction  
**When** local provider is wired  
**Then** user resolution returns the same `user_id` model as today  
**And** a second “stub” provider can be registered without changing route handlers

---

## Epic 2: Regulatory source configuration & reliable ingestion

**Goal:** **Admins** manage **public** sources; **Scout** path uses **direct connectors** with **dedup**, **retry**, **fallback**, **metrics**.

### Story 2.1: Source registry CRUD API and persistence

As an **admin**,
I want **to create, read, update sources** with metadata (jurisdiction, endpoints, schedule),
So that **the system knows what to poll** (**FR1**).

**Acceptance Criteria:**

**Given** authenticated Admin  
**When** they POST a valid source  
**Then** it is stored and listed  
**And** validation rejects missing required fields

---

### Story 2.2: Scheduled and manual poll triggers

As the **system**,
I want **to poll or trigger fetches per source schedule**,
So that **ingestion runs reliably** (**FR2**).

**Acceptance Criteria:**

**Given** an enabled source with schedule  
**When** the scheduler fires  
**Then** a job invokes the connector entrypoint with `source_id`  
**And** manual trigger API exists for Admin (**FR2**)

---

### Story 2.3: RSS/HTTP connector implementation (direct path)

As the **system**,
I want **to fetch public RSS/HTTP content via the connector interface**,
So that **Scout uses direct connectors, not generic web search** (**FR42**).

**Acceptance Criteria:**

**Given** a configured RSS URL  
**When** the connector runs  
**Then** raw items are returned with fetch timestamp  
**And** implementation lives under `services/connectors/` per Architecture

---

### Story 2.4: Deduplication and retry with backoff

As the **system**,
I want **to dedupe by fingerprint and retry transient failures**,
So that **we avoid duplicate processing and survive flaky sources** (**FR3**, **FR4**).

**Acceptance Criteria:**

**Given** two items with same URL+hash  
**When** ingested  
**Then** only one logical item is created  
**And** failed fetches log reason and respect backoff (**FR4**)

---

### Story 2.5: Fallback endpoints and enable/disable

As an **admin**,
I want **optional fallback URL/HTML parse and enable/disable without losing history**,
So that **sources remain operable** (**FR5**, **FR6**).

**Acceptance Criteria:**

**Given** primary fetch fails and fallback configured  
**When** connector runs  
**Then** fallback path is attempted and outcome logged  
**And** disabled sources skip polls but retain history (**FR6**)

---

### Story 2.6: Per-source metrics exposure

As an **operator**,
I want **success rate, latency, and item counts per source**,
So that **I can monitor ingestion health** (**NFR9**).

**Acceptance Criteria:**

**Given** completed polls  
**When** I query metrics endpoint or UI placeholder  
**Then** per-source counters and last success time are visible

---

## Epic 3: LangGraph intelligence pipeline

**Goal:** **StateGraph** with **AgentState**, **nodes** (scout → normalize → classify), **checkpointer**, **conditional edges**, **retry** without losing **run_id**, **Tavily** tool for **public** queries only.

### Story 3.1: Persist raw captures and normalized records

As the **system**,
I want **to store raw captures and normalized update records**,
So that **analysts have auditable structured data** (**FR7**, **FR8**, **FR10**).

**Acceptance Criteria:**

**Given** raw items from Scout  
**When** normalization completes  
**Then** records include required PRD fields where extractable  
**And** extraction quality/confidence is stored when available (**FR10**)

---

### Story 3.2: Define AgentState and graph compilation shell

As a **developer**,
I want **`AgentState` with reducers and a compiled `StateGraph`**,
So that **all orchestration flows through one graph** (**FR36**).

**Acceptance Criteria:**

**Given** empty graph with `run_id` in state  
**When** invoked  
**Then** checkpointer persists state (**Architecture**)  
**And** `run_id` appears in logs (**NFR8**)

---

### Story 3.3: Implement scout and normalize nodes wired in graph

As the **system**,
I want **graph nodes calling connector and normalizer services**,
So that **a run progresses from fetch to structured updates** (**FR36**).

**Acceptance Criteria:**

**Given** triggered run  
**When** graph executes  
**Then** state contains normalized items after normalize node  
**And** services are not imported from inside graph definitions incorrectly (per Architecture boundaries)

---

### Story 3.4: Classify node with rules + LLM and structured output

As the **system**,
I want **severity, impact, urgency, rationale, confidence** per update,
So that **downstream routing can act** (**FR11**–**FR15**, **NFR2**).

**Acceptance Criteria:**

**Given** normalized updates  
**When** classify node runs  
**Then** outputs satisfy FR11–FR15 schema  
**And** model/prompt version ids are logged for audit

---

### Story 3.5: Conditional edges for review vs continue

As the **system**,
I want **branching based on confidence and policy**,
So that **ambiguous/high-risk items follow review path** (**FR37**, **FR16** partial).

**Acceptance Criteria:**

**Given** classification with `needs_human_review=true`  
**When** graph runs  
**Then** edge routes to review branch placeholder or interrupt hook  
**And** correlation id preserved (**FR38**)

---

### Story 3.6: Retry policy on transient node failures

As the **system**,
I want **retries on defined failures without new run_id**,
So that **audit correlation holds** (**FR38**).

**Acceptance Criteria:**

**Given** transient LLM/HTTP error in a node  
**When** retry executes  
**Then** same `run_id` in logs  
**And** max attempts enforced

---

### Story 3.7: Pluggable web search tool (Tavily default)

As the **system**,
I want **optional Tavily-backed search behind a tool interface**,
So that **classify/brief nodes can enrich from public web** (**FR43**, **NFR12**).

**Acceptance Criteria:**

**Given** public query text derived from public update context  
**When** tool is invoked  
**Then** results return without sending non-public payloads (**NFR12**)  
**And** adapter can be swapped for DuckDuckGo-equivalent implementing same interface

---

### Story 3.8: Pipeline-generated audit events

As an **auditor**,
I want **audit entries for ingest/classify steps**,
So that **later search/replay has data** (**FR33** partial).

**Acceptance Criteria:**

**Given** completed graph steps  
**When** actions occur  
**Then** append-only audit records exist with `run_id` and action type

---

## Epic 4: Human review & regulatory briefings

**Goal:** **Analysts** work **review queue** and read **briefings** with required sections.

### Story 4.1: Review queue API and workflow state integration

As an **analyst**,
I want **items flagged for review visible in a queue**,
So that **I can resolve low-confidence/high-risk cases** (**FR16**).

**Acceptance Criteria:**

**Given** items meeting policy thresholds  
**When** I list review queue  
**Then** only eligible items appear with classification context

---

### Story 4.2: Approve, reject, override with notes

As an **analyst**,
I want **to approve, reject, or override with notes**,
So that **decisions are recorded** (**FR17**, audit).

**Acceptance Criteria:**

**Given** a queued item  
**When** I submit override  
**Then** classification updates and notes persist  
**And** audit log captures user and timestamp

---

### Story 4.3: Briefing generation and grouping

As a **user**,
I want **briefings grouped by configured dimensions**,
So that **I can scan related updates** (**FR18**–**FR20**).

**Acceptance Criteria:**

**Given** classified updates  
**When** briefing job runs  
**Then** briefings include sections: what changed, why it matters, who should care, confidence, suggested actions  
**And** grouping rules are configurable

---

## Epic 5: Intelligent routing & multi-channel notification

**Goal:** **Routing** from **mock tables**, **notifications** with **outcomes**, **no filings** (**FR41**).

### Story 5.1: Routing rules engine

As the **system**,
I want **to apply topic/severity → team/channel rules**,
So that **the right stakeholders get alerts** (**FR21**).

**Acceptance Criteria:**

**Given** mock routing tables in DB  
**When** an update is routed  
**Then** targets resolve deterministically for test fixtures

---

### Story 5.2: In-app notifications

As a **user**,
I want **in-app notifications for routed items**,
So that **I see work inside the console** (**FR24**).

**Acceptance Criteria:**

**Given** a routed critical item  
**When** I open the app  
**Then** notification appears for my role/team

---

### Story 5.3: External channel (email or Slack sandbox) with delivery log

As the **system**,
I want **to send sandbox email or Slack and record outcomes**,
So that **delivery is traceable** (**FR23**, **FR25**, **NFR5**, **NFR10**).

**Acceptance Criteria:**

**Given** configured sandbox credentials  
**When** notification sends  
**Then** success/failure stored per attempt (**FR23**)  
**And** failures are visible to admin (**NFR10**)

---

### Story 5.4: Immediate vs digest scheduling

As the **system**,
I want **immediate alerts for critical/high and batched for lower priority**,
So that **alert fatigue is reduced** (**FR22**).

**Acceptance Criteria:**

**Given** policy thresholds  
**When** routing runs  
**Then** critical items trigger immediate path; others enqueue digest per config

---

### Story 5.5: Regulatory filing guardrail

As a **compliance officer**,
I want **guarantee the system never submits binding filings**,
So that **automation cannot create regulatory liability** (**FR41**).

**Acceptance Criteria:**

**Given** any workflow  
**When** outbound actions are enumerated  
**Then** no connector performs regulatory submission/filing  
**And** code scan / allowlist documents permitted outbound types

---

## Epic 6: Analyst console & configuration UI

**Goal:** **PRD** console surfaces: **dashboard**, **explorer**, **routing config** (**NFR1**, **NFR11**).

### Story 6.1: Overview dashboard widgets

As an **analyst**,
I want **counts by severity, new items, review backlog, top sources**,
So that **I can prioritize** (**FR30**, **NFR1**).

**Acceptance Criteria:**

**Given** seeded data  
**When** I load dashboard  
**Then** widgets render within **NFR1** target under demo load  
**And** layout is keyboard-accessible (**NFR11**)

---

### Story 6.2: Update explorer with filters and detail (side-by-side)

As an **analyst**,
I want **filters and a detail view with original vs normalized**,
So that **I can investigate updates** (**FR31**, **FR9**).

**Acceptance Criteria:**

**Given** updates exist  
**When** I filter and open detail  
**Then** both raw and normalized fields display side by side

---

### Story 6.3: Admin routing & escalation table management UI

As an **admin**,
I want **to edit routing/escalation mock tables in UI**,
So that **I can tune behavior without code** (**FR32**).

**Acceptance Criteria:**

**Given** Admin role  
**When** I save routing rows  
**Then** changes persist and **audit** records the change (**FR33**)

---

## Epic 7: Feedback loops & golden-set governance

### Story 7.1: User feedback capture on updates

As a **user**,
I want **to flag incorrect relevance/severity with comments**,
So that **quality improves** (**FR26**, **FR27**).

**Acceptance Criteria:**

**Given** an update detail view  
**When** I submit feedback  
**Then** it persists linked to classification and user

---

### Story 7.2: Feedback metrics view for admin

As an **admin**,
I want **override rate and category distributions**,
So that **I can monitor model health** (**FR28**).

**Acceptance Criteria:**

**Given** feedback data  
**When** I open metrics view  
**Then** aggregates display per PRD examples

---

### Story 7.3: Governed threshold and prompt change proposals

As an **admin**,
I want **to propose threshold/prompt changes with explicit apply**,
So that **no silent production changes occur** (**FR29**).

**Acceptance Criteria:**

**Given** a draft change  
**When** I apply via governed action  
**Then** version increments and audit records actor (**NFR13** alignment)

---

### Story 7.4: Golden-set policy and configuration history

As **Regulatory Affairs** (role or workflow),
I want **golden-set label policy approval and auditable history**,
So that **evaluation truth is owned by the business** (**FR44**, **FR45**, **NFR13**).

**Acceptance Criteria:**

**Given** golden-set config change  
**When** saved  
**Then** who/when/why is recorded and visible in history  
**And** cadence fields support quarterly + post–major-change flags

---

## Epic 8: Audit search, replay & operational observability

### Story 8.1: Audit event search API and UI

As an **operator**,
I want **to search by update id, source, time range, user**,
So that **I can investigate incidents** (**FR34**).

**Acceptance Criteria:**

**Given** audit events exist  
**When** I query with filters  
**Then** matching entries return with pagination

---

### Story 8.2: Workflow replay from persisted state

As an **operator**,
I want **non-destructive replay of a run segment**,
So that **I can debug classification issues** (**FR35**).

**Acceptance Criteria:**

**Given** stored checkpointer state for `run_id`  
**When** I request replay  
**Then** graph re-executes or simulates without mutating production data inappropriately  
**And** `run_id` trace is continuous (**NFR8**)

---

### Story 8.3: Cross-service observability dashboard (operator)

As an **operator**,
I want **structured log correlation and key metrics surfaced**,
So that **on-call can trace failures** (**NFR8**, **NFR9**).

**Acceptance Criteria:**

**Given** running system  
**When** I view ops dashboard or logs  
**Then** `run_id` links API → worker → graph steps  
**And** per-source metrics match **NFR9**

---

## Final validation summary

| Check | Result |
|-------|--------|
| All FR1–FR46 mapped to at least one epic/story | **Yes** (FR33 split: Epic 3 pipeline + Epic 8 search/replay) |
| NFRs addressed in acceptance criteria or epics | **Yes** |
| Stories ordered without forward dependencies within epic | **Yes** (each story builds on prior in sequence) |
| Epic independence (Epic N does not require Epic N+1) | **Yes** — later epics consume APIs/events from earlier |
| FR41 guardrail | **Story 5.5** |
| Architecture starter | **Story 1.1** |
| UX / accessibility | **Epic 6** + **NFR11** in AC |

---

**Epics and stories complete.** Use `bmad-check-implementation-readiness` or `bmad-help` for next steps.
