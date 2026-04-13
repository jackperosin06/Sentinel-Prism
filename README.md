# Sentinel Prism

Agentic regulatory intelligence platform (monorepo): **FastAPI** backend under `src/sentinel_prism/`, **React + Vite + TypeScript** web app under `web/`.

## Prerequisites

- **Python** 3.11 or 3.12
- **Node.js** 20 LTS (or current LTS used by your team)

## Backend bootstrap

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Run the API:

```bash
uvicorn sentinel_prism.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health` → `{"status":"ok"}`.

## Tests

```bash
source .venv/bin/activate
python -m pytest
```

## Web app

```bash
cd web
npm ci
npm run build
```

Local dev server (optional): `npm run dev`.

## Environment

Copy `.env.example` to `.env` for local overrides. Do not commit secrets (**NFR3**).

## Optional: Postgres via Docker

For local PostgreSQL (useful before Story 1.2 migrations are run end-to-end):

```bash
cp .env.example .env
# edit .env — set POSTGRES_PASSWORD and DATABASE_URL to match
docker compose up -d postgres
```

`docker compose` reads `.env` in the project root for `POSTGRES_*` variables.

## Layout

See `_bmad-output/planning-artifacts/architecture.md` for the canonical module boundaries (`graph/`, `services/`, `api/`, etc.).
