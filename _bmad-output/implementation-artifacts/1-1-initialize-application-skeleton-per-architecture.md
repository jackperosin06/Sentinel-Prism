# Story 1.1: Initialize application skeleton per architecture

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **the monorepo structure (FastAPI backend, React web placeholder, Python package layout) documented and runnable**,
so that **subsequent stories have a consistent home for code**.

## Acceptance Criteria

1. **Given** a clean checkout **when** I follow README bootstrap steps **then** the API responds healthy at `GET /health` with a JSON body indicating OK (or equivalent) and HTTP 200.
2. **Given** the same checkout **when** I install frontend deps and run the documented build command **then** the web app builds successfully (production build or `vite build` as documented).
3. **`src/` package layout** matches the Architecture reference tree: Python package **`sentinel_prism`** under `src/sentinel_prism/` with at least placeholders for `api/`, `graph/`, `services/`, `db/`, `workers/` as in [Source: `_bmad-output/planning-artifacts/architecture.md` §6].
4. **Dependencies are pinned** in `requirements.txt` (and/or `pyproject.toml` if the team chooses that single source) and in `web/package.json` with **explicit versions** (no floating `*` ranges for runtime deps).
5. **Repository root** includes `README.md` with bootstrap steps (Python venv, API run command, web install/build) and `.env.example` listing **non-secret** placeholders only (**NFR3** — no secrets in repo) [Source: `_bmad-output/planning-artifacts/epics.md` Story 1.2 preview aligns; architecture §6].

## Tasks / Subtasks

- [x] **Scaffold repo layout** (AC: #3, #5)
  - [x] Create `src/sentinel_prism/` with `__init__.py` and subpackages: `api/` (with `routes/`, `deps.py` placeholders), `graph/` (`state.py`, `graph.py`, `nodes/`, `tools/`, `checkpoints.py` stubs or empty modules), `services/` (`connectors/`, `llm/`, `notifications/`), `db/` (`models.py`, `repositories/`), `workers/`
  - [x] Add `main.py` app factory exposing FastAPI app and mounting `GET /health`
  - [x] Add `alembic/` directory with `versions/` (empty or placeholder README) — migrations implemented in Story 1.2
  - [x] Add `tests/unit/` and `tests/integration/` with minimal smoke (optional but recommended: import app, test `/health`)
- [x] **Backend runtime** (AC: #1, #4)
  - [x] Add `requirements.txt` with pinned **FastAPI**, **Uvicorn** (or equivalent ASGI server), and any minimal typing/runtime deps; follow Architecture guidance to pin **`langgraph`**, **`langchain-core`** when graph code is introduced — for this story, include only what is needed for a runnable API **or** pin minimal set + comment “graph stack pinned for Story 3.x” per team choice
  - [x] Document `uvicorn sentinel_prism.main:app --reload` (adjust import path to match actual module layout)
- [x] **Frontend placeholder** (AC: #2, #4)
  - [x] Create `web/` with **React + Vite + TypeScript** per Architecture §2; default Vite template is acceptable
  - [x] Pin `react`, `react-dom`, `vite`, `@vitejs/plugin-react`, `typescript` versions in `package.json`
- [x] **Documentation** (AC: #5)
  - [x] Root `README.md`: clone → Python3.11+ (or project choice) → venv → `pip install -r requirements.txt` → run API → `cd web && npm ci && npm run build`
  - [x] `.env.example`: e.g. `DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sentinel_prism` (Story 1.2); no real credentials

### Review Findings

- [x] [Review][Patch] Docker Compose used a hardcoded database password — updated `docker-compose.yml` to load `.env` and require `POSTGRES_PASSWORD`; `.env.example` documents `POSTGRES_*` for local Docker.
- [x] [Review][Patch] Empty `pyproject.toml` `[project] dependencies` vs `requirements.txt` could confuse installs — documented in `pyproject.toml` that pins live in `requirements.txt` for this bootstrap.
- [x] [Review][Defer] `npm audit` reports a high-severity issue in transitive frontend dependencies — track under dependency hygiene; not required to resolve in Story 1.1.
- [x] [Review][Defer] `docker-compose.yml` was outside the original story File List — treated as optional local dev tooling; README now references it explicitly.

## Dev Notes

### Epic 1 context

- **Epic 1 goal:** Repo layout, database (later stories), **local auth** with **RBAC**, **auth provider abstraction** for future SSO — **no domain pipeline features** in this story [Source: `_bmad-output/planning-artifacts/epics.md` §Epic 1].
- **Cross-story order:** 1.1 skeleton → 1.2 Alembic/DB URL → 1.3 auth → 1.4 RBAC → 1.5 auth provider stub. Do **not** implement auth, RBAC, or graph logic here.

### Technical requirements (must follow)

- **Orchestration authority:** LangGraph `StateGraph` is the single orchestration pattern for later work; this story only **reserves** `graph/` layout [Source: `architecture.md` §3].
- **Boundaries (future-proofing):** UI consumes **REST/OpenAPI only**; **graph nodes** will call **services**; **services must not import graph definitions** [Source: `architecture.md` §6 Boundaries].
- **API shape (later):** Architecture specifies `POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/resume` — **do not implement** in 1.1; optional to add empty `api/routes/runs.py` stub with comment.
- **NFR3:** Secrets only via env or secret store; `.env` gitignored; `.env.example` safe [Source: `prd.md` / epics NFR table].

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Stack | FastAPI (async), React+Vite+TS, PostgreSQL+Alembic **planned** [Source: `architecture.md` §2] |
| Package name | `sentinel_prism` under `src/sentinel_prism/` [Source: `architecture.md` §5–6] |
| Graph module | `graph/state.py`, `graph.py`, `nodes/`, `tools/`, `checkpoints.py` [Source: `architecture.md` §5] |
| Workers | `workers/` for scheduled jobs triggering graph [Source: `architecture.md` §6 tree] |

### Library / framework requirements

- Pin versions at **implementation time** by checking **PyPI** (FastAPI, Starlette, Uvicorn, LangGraph when added). Architecture explicitly says **do not hardcode versions in the architecture doc** — the story’s `requirements.txt` **is** the authority once merged [Source: `architecture.md` §2 Version pinning].
- Prefer **`python -m pip install -r requirements.txt`** in README for reproducibility.

### File structure requirements

Target tree (allow minor filename tweaks if README documents them):

```text
sentinel-prism/
├── README.md
├── requirements.txt  # and/or pyproject.toml
├── .env.example
├── alembic/
│   └── versions/
├── src/
│   └── sentinel_prism/
│       ├── main.py
│       ├── api/
│       ├── graph/
│       ├── services/
│       ├── db/
│       └── workers/
├── web/
│   ├── package.json
│   └── src/
└── tests/
```

### Testing requirements

- **Minimum:** Manual verification per AC (curl `/health`, `npm run build`).
- **Recommended:** One pytest that uses `TestClient` against the FastAPI app for `/health` (fast regression guard for later stories).

### UX / product notes (placeholder only)

- Console UX, WCAG **NFR11**, and dashboard patterns apply to **Epic 6**; this story’s web app is a **buildable shell** only [Source: `ux-design-specification.md` Executive Summary; `epics.md` Story 6.x].

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 1.1]
- [Source: `_bmad-output/planning-artifacts/architecture.md` §2 Technology stack, §5 Graph module layout, §6 Project structure & boundaries]
- [Source: `_bmad-output/planning-artifacts/prd.md` — Technical Direction, greenfield context]

## Latest technical information (implementation time)

- **Version pins:** Resolve FastAPI, Starlette, and Uvicorn compatible sets from PyPI at scaffold time; FastAPI pre-1.0 follows semver notes in FastAPI docs — avoid manual Starlette pins unless required by a known issue.
- **Python:** Use a single supported Python version (e.g. 3.11 or 3.12) in README; align with team standard.
- **Node:** Document LTS version for Vite 6+ if applicable.

## Project context reference

- No `project-context.md` found in repo at story creation time; rely on Architecture + PRD + this file.

## Story completion status

- **Status:** done
- **Note:** Code review completed (2026-04-13); patch findings addressed; ACs remain satisfied.

## Change Log

- **2026-04-13:** Story 1.1 — Monorepo skeleton (`src/sentinel_prism`, `web/`, `tests/`, `alembic/versions` placeholder), FastAPI health route, pytest smoke, Vite React TS shell, `requirements.txt` + `package-lock.json`, README bootstrap.
- **2026-04-13:** Code review — hardened `docker-compose.yml` + `.env.example` Postgres vars; documented optional Docker Postgres in README; clarified `pyproject.toml` vs `requirements.txt`.

---

## Dev Agent Record

### Agent Model Used

Cursor agent (Composer)

### Debug Log References

- None required.

### Completion Notes List

- Editable install via `pyproject.toml` + `pip install -e .` so `uvicorn sentinel_prism.main:app` resolves the package from `src/`.
- `GET /health` returns `{"status":"ok"}`; covered by `tests/test_health.py` with `TestClient`.
- `web/`: React 19 + Vite 6 + TypeScript 5.7 with pinned versions; `npm ci` and `npm run build` succeed; `package-lock.json` committed for reproducible installs.
- `api/routes/runs.py` stub documents future run APIs only (not mounted).
- `.gitignore` extended for Python/JS build artifacts; `npm audit` reported one high-severity advisory in transitive deps — not addressed in this story (review separately if needed).

### File List

- `.env.example`
- `.gitignore`
- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `alembic/versions/README.md`
- `src/sentinel_prism/__init__.py`
- `src/sentinel_prism/main.py`
- `src/sentinel_prism/api/__init__.py`
- `src/sentinel_prism/api/deps.py`
- `src/sentinel_prism/api/routes/__init__.py`
- `src/sentinel_prism/api/routes/health.py`
- `src/sentinel_prism/api/routes/runs.py`
- `src/sentinel_prism/graph/__init__.py`
- `src/sentinel_prism/graph/state.py`
- `src/sentinel_prism/graph/graph.py`
- `src/sentinel_prism/graph/checkpoints.py`
- `src/sentinel_prism/graph/nodes/__init__.py`
- `src/sentinel_prism/graph/tools/__init__.py`
- `src/sentinel_prism/services/__init__.py`
- `src/sentinel_prism/services/connectors/__init__.py`
- `src/sentinel_prism/services/llm/__init__.py`
- `src/sentinel_prism/services/notifications/__init__.py`
- `src/sentinel_prism/db/__init__.py`
- `src/sentinel_prism/db/models.py`
- `src/sentinel_prism/db/repositories/__init__.py`
- `src/sentinel_prism/workers/__init__.py`
- `tests/__init__.py`
- `tests/test_health.py`
- `tests/unit/__init__.py`
- `tests/integration/__init__.py`
- `web/package.json`
- `web/package-lock.json`
- `web/index.html`
- `web/vite.config.ts`
- `web/tsconfig.json`
- `web/tsconfig.app.json`
- `web/tsconfig.node.json`
- `web/src/main.tsx`
- `web/src/App.tsx`
- `web/src/index.css`
- `web/src/vite-env.d.ts`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (story status progression)
- `_bmad-output/implementation-artifacts/1-1-initialize-application-skeleton-per-architecture.md` (this file — permitted sections only)