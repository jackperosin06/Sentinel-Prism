---
title: "Product Brief Distillate: Sentinel Prism"
type: llm-distillate
source: "product-brief-sentinel-prism.md"
created: "2026-04-12T12:00:00Z"
purpose: "Token-efficient context for downstream PRD creation"
---

# Product Brief Distillate: Sentinel Prism

Dense context from discovery input; use with executive brief for PRD/architecture.

## Product identity- **Name:** Sentinel Prism
- **Category:** Agentic compliance monitoring / pharmaceutical regulatory intelligence platform
- **Build posture:** Complete E2E product (not a demo pipeline); **public + mock data only**; **enterprise-grade** seams for later real systems

## Non-negotiables

- **Orchestration:** Graph-oriented, **stateful** framework (**LangGraph** explicitly requested): agents as **nodes**, workflow as **edges** (incl. **conditional**), **shared central state** (updates, classifications, routing, feedback), **loops/retries/branching in graph** not ad-hoc scripts
- **Agentic:** Explicit roles, goals, toolsets; runtime decisions; retries; fallbacks; branch on context/confidence; **human escalation** when appropriate
- **Data boundary:** All sources **public or mock**—RSS/Atom, public sites, guidance, downloads, **local mock** tables (policy mappings, escalation matrices, labeled examples). **No** live enterprise internal data in this build
- **Notifications:** Real but **sandbox** endpoints OK (personal/test Slack, email, in-app)—treat as mock from compliance stance; **E2E testable**; swap to enterprise = **connector/config** change

## Six agents (minimum) → LangGraph nodes

1. **Scout:** Source registry; poll/trigger; **dedup** new vs seen; retries/backoff; fallbacks (alt endpoints, HTML if feed fails); coarse pharma relevance filter; **state** visible downstream (failures, retries, new items)
2. **Normalizer:** Raw → structured records; fields: title, dates, URL, source, jurisdiction, doc type, body, summary, metadata; **provenance** + collection time; heuristic topic hints (rules + optional LLM); quality/confidence flags; **DB**-suitable schema; writes **normalized list** to shared state
3. **Impact Analyst:** Rules + LLM: in-scope?, severity (critical/high/med/low), impact type (safety, labeling, manufacturing, deadlines, etc.), urgency; **rationale** + **confidence**; ambiguous/high-risk/low-confidence → **human review queue** not guessing; follow-up flags; **conditional edges** to Briefing vs queue vs retry
4. **Briefing:** Group by rules (date, severity, jurisdiction, topic, team); digests + incident briefs + on-demand; sections: *What changed*, *Why it matters*, *Who should care*, *Confidence*, *Suggested actions*; templates + optional LLM; multiple views; outputs for Routing + UI
5. **Routing:** UI + dashboards + Slack/email/in-app; mock routing tables; immediate vs batched by severity; **delivery outcomes** (success/fail, ack, metadata); **alert fatigue** controls (bundle low, rate-limit noisy, emphasize critical in UI)
6. **Feedback:** UI for relabel, comments, FP/FN; record events + context; eval datasets/metrics; feed **governed** improvements (prompts, thresholds, routing; optional SFT **if applicable**); **no silent** mutation of core safety-critical logic—**reviewable** changes

## Data / sources

- **Domain:** Pharmaceutical regulatory monitoring
- **Initial sources (examples):** EMA RSS, FDA public announcements/similar, small set of additional **technically feasible** public regulators
- **Per-source:** Registry + config; schemas + parsing rules; schedules/triggers; **failure + rate-limit** handling; **metrics** per source

## Overall behavior (system)

- Continuous monitor → ingest → structured records w/ provenance → relevance/impact assessment → prioritized briefings → route to stakeholders via UI + notifications → **audit/replay** logs → **human feedback** → controlled improvement

## UI / UX (required surfaces)

- **Home / overview:** Stats (new updates, by severity, awaiting review, top sources, trends); quick view of top items
- **Update explorer:** Table/cards; filters (date, severity, jurisdiction, topic, source, status); detail: raw + normalized + classification + rationale + confidence + history
- **Briefings:** List + open (grouped updates, drill-down)
- **Review queue:** Low-confidence / high-risk; approve/override, notes, follow-ups
- **Feedback & evaluation:** Corrections; metrics (agreement, precision/recall on labeled subsets, override distribution)
- **Configuration & source management:** Sources, poll frequency, filters, thresholds, routing (as safe for public/mock deployment)
- **Quality bar:** Production-style console for regulatory intelligence; references in user input included enterprise agentic articles (Salesforce, Pacewisdom, QAT patterns)—**for positioning**, not requirements

## Orchestration / observability

- **Pattern:** Stateful multi-agent workflow; **persistence** of what was seen/processed/classified/escalated/briefed/delivered
- **Audit trail per update and run:** sources, responses, parsing, classifications, routing, delivery, feedback/overrides
- **Replay** for debug and demo

## Mock enterprise context

- Mock tables: topic→function/team; severity/escalation rules; jurisdiction coverage; notification routes/on-call- Drive **real** routing/prioritization behavior; **data model** must allow swapping mocks for real with **minimal redesign**

## Governance & safety

- Provenance everywhere; rationales + confidence; **recommendation vs human decision** clear on high-impact paths
- **No** irreversible external binding actions (e.g., filings)
- **Access control** on UI/config (simple roles OK)

## Technical stack (stated)

- **Python** primary
- **LangGraph** (or closely equivalent) for multi-agent coordination
- **LLM APIs:** OpenAI or Anthropic class for reasoning/classification
- **Storage:** DB or suitable store for records, logs, state, feedback
- **Web:** Modern framework + component library; enterprise-style internal tool UX

## Open questions (for PRD)

- **MVP cut:** Which source set and jurisdictions for first shippable milestone?
- **Auth model:** IdP vs built-in users for mock deployment?
- **Evaluation:** Golden-set ownership and refresh cadence for classification metrics?
- **Hosting:** SaaS vs on-prem story (affects audit/log retention promises)?

## Scope signals

- **In:** Full six-agent graph, UI, sandbox channels, mock tables, audit, feedback governance
- **Explicit out of data plane (this phase):** Enterprise internal systems, sensitive internal datasets
- **Explicit out of actions:** Automated regulatory filings or binding submissions

## Rejected / deferred (none stated)

- User did not propose alternatives to LangGraph; **defer** = non-graph orchestration (would conflict with stated requirement)
