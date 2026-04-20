# Regulatory outbound surface (FR41)

**FR41:** The system does **not** initiate **binding regulatory submissions** or **external filings** on behalf of users. Outputs are **decision support**, not filings (see PRD).

This document is the **authoritative inventory** of backend-initiated network and submission-style I/O. **None** of the surfaces below are used for binding regulatory filings.

Keep the **Python allowlist** in `src/sentinel_prism/compliance/outbound_allowlist.py` (`ALLOWED_HTTPX_SOURCE_FILES`) **in lockstep** with this file: any new direct `httpx` usage must update **both** places and add a row here.

| Surface | Purpose (non-filing) | Protocols / APIs | Primary paths |
|--------|------------------------|------------------|---------------|
| RSS / HTTP ingestion | Fetch public feeds and HTML for normalization | HTTP GET via `httpx` async client | `src/sentinel_prism/services/connectors/http_client.py`, `http_fetch.py`, `rss_fetch.py`, `html_fallback.py`, `scout_fetch.py`, `poll.py`, `fetch_retry.py` |
| Slack notifications | Internal alerting to workspace webhook | HTTPS POST JSON to Slack **Incoming Webhooks** (operator-controlled URL) | `src/sentinel_prism/services/notifications/adapters/slack.py` |
| Email notifications | Internal alerting via sandbox SMTP | SMTP (`smtplib` — not `httpx`) | `src/sentinel_prism/services/notifications/adapters/smtp.py` |
| Optional web search | Public search snippets for classify enrichment | Tavily HTTP API via **`tavily`** SDK (not direct `httpx` in-repo) | `src/sentinel_prism/graph/tools/tavily_search.py` |
| Optional LLM classification | Structured classification / inference | OpenAI-compatible HTTP via **`langchain-openai`** / OpenAI SDK (not direct `httpx` in-repo) | `src/sentinel_prism/services/llm/classification.py` |
| Persistence | Application and audit data | PostgreSQL wire protocol (SQLAlchemy / asyncpg / psycopg) | `src/sentinel_prism/db/`, Alembic migrations |

## Why Slack POST is not a “filing”

Slack **Incoming Webhooks** accept a JSON payload for **team notifications** only. They do not submit data to regulatory authorities or create binding regulatory records. Operators supply the webhook URL; the product does not map this to any filing workflow.

## Direct `httpx` guard

CI enforces that only the paths listed in `ALLOWED_HTTPX_SOURCE_FILES` may contain direct `httpx` import/call patterns (including `httpx` submodule imports and `Client` / `AsyncClient` / `post` / `request` usage). Other outbound stacks (Tavily, OpenAI) are documented here for auditors but are not covered by that specific guard unless they gain direct `httpx` usage later.

## Machine-checked direct `httpx` allowlist

- `src/sentinel_prism/services/connectors/fetch_retry.py`
- `src/sentinel_prism/services/connectors/http_client.py`
- `src/sentinel_prism/services/connectors/http_fetch.py`
- `src/sentinel_prism/services/connectors/html_fallback.py`
- `src/sentinel_prism/services/connectors/poll.py`
- `src/sentinel_prism/services/connectors/rss_fetch.py`
- `src/sentinel_prism/services/connectors/scout_fetch.py`
- `src/sentinel_prism/services/notifications/adapters/slack.py`
