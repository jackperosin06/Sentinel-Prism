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

### Database migrations (PostgreSQL)

1. Copy `.env.example` → `.env` and set **`POSTGRES_PASSWORD`**, **`DATABASE_URL`**, and **`ALEMBIC_SYNC_URL`** (same credentials; async vs sync driver — see `.env.example` comments).
2. Start Postgres (local install or `docker compose up -d postgres`).
3. From the repo root (with venv activated):

```bash
alembic upgrade head
```

This applies revisions through **`head`** on an empty database (Story 1.2 baseline is a no-op DDL revision; it still creates `alembic_version`).

**Troubleshooting**

- **`RuntimeError: ALEMBIC_SYNC_URL is not set`** — export it or load `.env` in your shell before running Alembic.
- **`postgresql+asyncpg://` in Alembic** — use **`ALEMBIC_SYNC_URL=postgresql+psycopg://...`** for the CLI; keep **`DATABASE_URL`** with `+asyncpg` for the app.
- **Passwords with `@`, `:`, `/`, or spaces** — URL-encode the user/password components (per RFC 3986) in both DSNs or migrations will fail to parse.
- **Auth failed** — ensure user/password in the URLs match `POSTGRES_*` when using Docker Compose.

Run the API:

```bash
uvicorn sentinel_prism.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health` → `{"status":"ok"}`.

### Authentication (Story 1.3)

Set **`JWT_SECRET`**, **`JWT_ALGORITHM`** (default `HS256`), and **`JWT_EXPIRE_MINUTES`** in `.env` (see `.env.example`). Use a long random secret locally; never commit real secrets (**NFR3**).

**Password rules** (register): minimum **12** characters, at least one **lowercase** letter, one **uppercase** letter, and one **digit**. Weak passwords are rejected with **422** and validation details (also described on the OpenAPI `RegisterRequest` schema).

**Endpoints** (OpenAPI: `/docs`):

- `POST /auth/register` — JSON `{"email","password"}` → `201` with `id` and `email` (password is never returned).
- `POST /auth/login` — JSON `{"email","password"}` → `200` with `access_token`, `token_type`, and `user_id`.
- `GET /auth/me` — header `Authorization: Bearer <access_token>` → current user; missing or invalid token → **401**.

Tokens include standard **`exp`**; no extra clock-skew tolerance is required for local MVP demos.

### Auth provider (Story 1.5)

**`AUTH_PROVIDER`** selects how **`POST /auth/login`** verifies email/password (see `.env.example`):

- **`local`** (default) — existing users table + Argon2 (same behavior as before this story).
- **`stub`** — verification always fails (**401** on login). Intended for **tests** or as a placeholder when wiring a future IdP; **do not** use in production for real users.

JWT issuance and **`GET /auth/me`** / RBAC are unchanged: they only care that the Bearer token’s **`sub`** is a valid user id.

### RBAC (Story 1.4)

Registered users get role **`viewer`** by default (least privilege). Roles: **`admin`**, **`analyst`**, **`viewer`** (see PRD permission model).

**Promote a user locally** (after `alembic upgrade head`), using `psql` or any SQL client against your DB:

```sql
UPDATE users SET role = 'admin' WHERE email = 'you@example.com';
```

**Demo routes** (OpenAPI tag `rbac-demo` — exemplar only, not product API):

- `GET /rbac-demo/admin-only` — **403** unless role is `admin`.
- `GET /rbac-demo/analyst-or-above` — **403** for `viewer`; **200** for `analyst` or `admin`.
- `GET /rbac-demo/authenticated` — **200** for any authenticated role; **401** without a valid Bearer token.

## Tests

```bash
source .venv/bin/activate
python -m pytest
```

**Integration tests** (`@pytest.mark.integration`) need PostgreSQL: set **`DATABASE_URL`** (`postgresql+asyncpg://...`) and **`ALEMBIC_SYNC_URL`** (`postgresql+psycopg://...`), run `alembic upgrade head`, then e.g. `python -m pytest -m integration`. Without a database, those tests are skipped.

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
