---
title: "Product Brief: Sentinel Prism"
status: complete
created: "2026-04-12T12:00:00Z"
updated: "2026-04-12T12:00:00Z"
inputs:
  - "User session: comprehensive product description (invoked via /bmad-product-brief)"
---

# Product Brief: Sentinel Prism

## Executive Summary

**Sentinel Prism** is an **agentic compliance monitoring platform** for **pharmaceutical regulatory intelligence**. It continuously watches **public** regulatory sources (feeds, guidance pages, announcements, downloadable documents), decides **what matters**, estimates **compliance impact**, turns findings into **prioritized briefings**, and **routes** them to the right people through a **full web UI** and **notification layer**—with **auditability** and **human feedback loops** built in.

The product is intentionally **end-to-end**: multi-agent **orchestration** (graph-oriented, stateful), **ingestion and storage**, **reasoning and classification**, **dashboards and alerts**, and **governance**—not a narrow script or pipeline. **All connectors use public or mock data only** (no live enterprise systems in this phase), but the **architecture is enterprise-grade** so real sources and messaging can drop in later with **configuration and connector changes**, not a redesign.

**Why now:** Regulatory surface area is expanding; teams drown in noise; manual monitoring does not scale. Sentinel Prism targets **actionable, explainable, routed** intelligence **with provenance**—the missing layer between public signals and accountable decisions.

## The Problem

Regulatory affairs and quality teams must track **EMA, FDA, and peer authorities** across **many channels**. Today they mix **manual checks**, **generic alerts**, and **ad hoc spreadsheets**—high **latency**, **inconsistent relevance**, **weak audit trails**, and **alert fatigue**. High-stakes items hide in noise; low-stakes noise erodes trust. **Proving what was known when**—for audits or escalations—is painful when work lives in inboxes and chats.

**Who feels it:** Compliance leaders, regulatory ops, PV and labeling stakeholders, and executives who must **attest to diligence** without a single system of record for **external** regulatory change.

## The Solution

Sentinel Prism operates as a **goal-driven multi-agent system** on a **shared state**: ingest from configured **public** sources, **normalize** into a single schema, **analyze impact** with **rules plus LLM reasoning** (with guardrails), **compose briefings**, **route** via UI and channels (sandbox-friendly Slack/email/in-app), and **capture feedback** to improve **safely** over time. **LangGraph-style** graphs express **nodes** (agents), **edges** (including conditional paths), **loops/retries**, and **central state**—not one-off scripts.

**Outcome:** Users get a **production-style console**: overview stats, **update explorer**, **briefings**, **review queue** for low-confidence/high-risk items, **feedback and evaluation** views, and **source/routing configuration**—fed only by **public/mock** data, behaving like a system ready for **real** enterprise attachment.

## What Makes This Different

- **Truly agentic:** Explicit **roles, goals, tools**, runtime **branching**, **retries**, **escalation to humans**—encoded in the **graph**, not buried in a monolith function.
- **Compliance-grade posture:** **Provenance**, **rationales**, **confidence**, **separation of recommendation vs. human decision**, **no irreversible external actions** (e.g., filings), and **replay-friendly** logs—while staying on **public/mock** inputs.
- **Swap-ready design:** **Source registry**, per-source **schema and reliability**, **mock routing/escalation tables** that drive **real behavior** now and **real enterprise** mappings later.

Honest moat: **execution**—wiring **orchestration, UX, audit, and feedback** into one coherent product—not a single algorithmic trick.

## Who This Serves

| Segment | Need | Success looks like |
|--------|------|---------------------|
| **Primary:** Pharma regulatory / quality / PV leads | Trusted, timely signal on **public** regulatory change | Fewer misses, less noise, **traceable** decisions |
| **Secondary:** Exec oversight | Confidence in **coverage** and **diligence** | Dashboards + audit narrative without manual heroics |
| **Build-phase:** Builders & partners | Clear extension points | **Connectors** and **channels** as plug-in surfaces |

## Success Criteria

- **Coverage & freshness:** Configured sources polled reliably; **dedup** and **failure/retry** behavior visible in metrics.
- **Decision quality:** Relevance/severity/urgency classifications with **scores and rationale**; **human review queue** for ambiguous/high-risk cases.
- **Actionability:** **Briefings** consumed in UI; **routing** success/failure tracked; **critical** items surface faster than batched noise.
- **Trust:** End-to-end **audit trail** per update and run; **replay** for demo/debug.
- **Improvement:** Feedback captured; **governed** updates to prompts/thresholds/rules—**no silent** changes to safety-critical logic.

## Scope

**In (this product vision):** Full stack—**LangGraph**-oriented orchestration, **six first-class agents** (Scout, Normalizer, Impact Analyst, Briefing, Routing, Feedback), **DB-backed** records and logs, **modern web UI**, **sandbox notifications**, **mock enterprise** mappings (teams, escalation, routes), **access control** (simple roles acceptable for this build).

**Explicit constraint:** **Public or mock inputs only** (e.g., EMA RSS, FDA public pages, additional feasible public regulators, local mock tables). **Sandbox** Slack/email/test endpoints allowed and treated as **non-enterprise** for compliance posture.

**Out (for this phase):** Live internal systems, sensitive datasets, **binding** regulatory submissions, and **production** enterprise messaging—**architecturally** prepared, not required to ship in V1.

**Orchestration non-negotiable:** State, branching, retries, escalation expressed in a **graph** (e.g., **LangGraph `StateGraph`**), shared **central state** across agents.

## Technical direction (high level)

- **Python** primary; **LangGraph** (or equivalent graph/state pattern) for multi-agent **workflow**.
- **LLM APIs** for analysis, under **schemas and guardrails**.
- **Database** (or equivalent) for structured updates, logs, state, feedback.
- **Web stack** appropriate for **enterprise-style** internal tools (modern framework + component library).

## Governance, safety, and boundaries

- **Provenance** for every artifact and decision; **model rationale** and **confidence** surfaced.
- **Human authority** on high-impact paths; **no** irreversible **external** regulatory actions from automation.
- **Access control** on UI and configuration even in the **public/mock** deployment.

## Vision

If Sentinel Prism succeeds, it becomes the **always-on regulatory sensing layer** for pharma—starting on **public** signals, expanding to **licensed enterprise** sources and messaging with **minimal** core change—while **agents, audit, and feedback** stay the spine of how the organization **knows**, **decides**, and **improves**.

## Review panel (internal)

**Skeptic:** LLM **misclassification** in regulated context is a headline risk—mitigate with **rules**, **confidence thresholds**, **review queue**, and **evaluation sets**. **Source drift** and **rate limits** will break naive scrapers—**per-source** health metrics are mandatory. **Mock-to-prod** gaps often underestimated—**contract-first** connectors and routing configs reduce rework.

**Opportunity:** **CROs**, smaller biotech, and **consultancies** share the same pain; **API-first** briefing delivery could expand reach; **packaged** source bundles by jurisdiction accelerate time-to-value.

**Regulatory / quality lens:** **Audit narrative** ("what we monitored, what we concluded, who saw it") is as important as **ML accuracy**—invest early in **immutable event logs** and **human override** semantics.

---

*Next step:* Use this brief plus the companion **distillate** (`product-brief-sentinel-prism-distillate.md`) as structured input for **PRD** creation (`bmad-create-prd`).
