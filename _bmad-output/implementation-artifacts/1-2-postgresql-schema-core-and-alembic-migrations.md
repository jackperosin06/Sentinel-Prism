# Story 1.2: PostgreSQL schema core and Alembic migrations

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **Alembic migrations and a minimal database URL configuration**,
so that **later stories can persist users and domain data**.

## Acceptance Criteria

1. **Given** a valid `DATABASE_URL` (and running PostgreSQL when not using a stub) **when** I run the documented migration command **then** Alembic applies through **`head`** on an **empty** database without error and leaves a consistent `alembic_version` row.
2. **Given** the repository after migrations **when** I inspect Alembic config **then** `script_location` and revision chain are wired to the **`sentinel_prism`** package layout (metadata / models import path documented and stable).
3. **`DATABASE_URL`** is documented in **`.env.example`** with **placeholder** values only (**NFR3** — no real secrets in repo); README or story-adjacent docs state required vars for local Docker Postgres alignment with `docker-compose.yml`.
4. **Scope guard:** **No** local auth, sessions, user tables, RBAC, or domain pipeline tables — those belong to **Story 1.3+** and later epics unless a migration file only adds **non–user** foundational DDL explicitly justified below (prefer **zero** domain tables in this story).

## Tasks / Subtasks

- [x] **Dependencies** (AC: #1–2)
  - [x] Add pinned **`sqlalchemy`**, **`alembic`**, **`asyncpg`** (and any minimal typing helpers) to `requirements.txt`; keep versions consistent with **FastAPI** / Python **3.11+** at install time [Source: `architecture.md` §2 Version pinning].
- [x] **SQLAlchemy baseline** (AC: #2, #4)
  - [x] Implement `src/sentinel_prism/db/models.py`: **`DeclarativeBase`** (SQLAlchemy 2.x style), **`metadata`** object exported for Alembic **`env.py`**; leave **no** ORM models **or** only a trivial internal placeholder **not** used for auth/domain — prefer **empty model set** + comment “Story 1.3+ adds `User`, etc.”
- [x] **Alembic project** (AC: #1–2)
  - [x] Add repo-root **`alembic.ini`** (or documented equivalent) with `script_location = alembic`, `prepend_sys_path = .` (or `src` as appropriate), and version path convention matching existing `alembic/versions/`.
  - [x] Add `alembic/env.py` that: loads **`ALEMBIC_SYNC_URL`** from the environment (sync DSN for Alembic); configures **`target_metadata`** from `sentinel_prism.db.models` (or agreed import); supports **`offline`** and **`online`** modes per Alembic defaults.
  - [x] **Driver note:** `.env.example` uses **`postgresql+asyncpg://`** for the **app**; Alembic’s migration runner is typically **sync** — either use a **sync** URL variant for migrations (`postgresql://…` + `psycopg` / `psycopg2`) **or** use **`run_sync`** with an async engine — **pick one approach**, document both URLs in `.env.example` **or** document a single URL + conversion rule in README to avoid developer confusion.
- [x] **Initial revision** (AC: #1, #4)
  - [x] Create first revision under `alembic/versions/` (replace placeholder `README.md`-only state if needed) so `upgrade head` is **non-empty** or an explicit **no-op** revision; must succeed on **empty** DB.
  - [x] If adding DDL: limit to **foundational** only (e.g. `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"` or `pgcrypto` **only if** justified for **1.3+** UUID PKs — otherwise prefer **empty** upgrade body with a comment).
- [x] **Documentation** (AC: #3)
  - [x] Update **`README.md`**: prerequisites (Postgres), copy `.env.example` → `.env`, `docker compose up -d postgres`, **`alembic upgrade head`** (exact command), and troubleshooting (wrong URL, sync vs async driver).
  - [x] Confirm **`.env.example`** lists `DATABASE_URL` and optional `POSTGRES_*` aligned with `docker-compose.yml`.

### Review Findings

- [x] [Review][Patch] Document URL-encoding for special characters in Postgres DSNs — README troubleshooting (code review 2026-04-14).
- [x] [Review][Patch] Integration test applies `upgrade head` to configured DB — warn in docstring `tests/test_alembic_cli.py` (code review 2026-04-14).

## Dev Notes

### Epic 1 context

- **Epic1 goal:** Repo layout, database, **local auth**, **RBAC**, **auth provider abstraction** — this story delivers **persistence plumbing only** [Source: `_bmad-output/planning-artifacts/epics.md` §Epic 1].
- **Order:** 1.1 skeleton → **1.2 DB/Alembic** → 1.3 auth → 1.4 RBAC → 1.5 provider stub. Do **not** implement auth or graph logic here.

### Technical requirements (must follow)

- **Stack:** **PostgreSQL** + **Alembic** + **FastAPI (async)** per [Source: `architecture.md` §2, §4 Data].
- **Future data:** Architecture expects **JSONB** for flexible metadata and relational core for users, audit, feedback, sources — **do not** implement those schemas now unless explicitly pulled forward with PM/architect agreement; epics scope for **1.2** is **minimal** [Source: `architecture.md` §4 Data; `epics.md` Story 1.2].
- **Boundaries:** **`db/models.py`** is the home for ORM definitions; **repositories** come later; **services must not import graph** (unchanged from architecture) [Source: `architecture.md` §6].
- **NFR3:** Secrets only via env; `.env` gitignored; `.env.example` safe [Source: `epics.md` NFR table; `prd.md` technical direction].

### Architecture compliance checklist

| Topic | Requirement |
| --- | --- |
| Persistence | PostgreSQL system of record; migrations via Alembic [Source: `architecture.md` §2, §4] |
| Project tree | `alembic/`, `src/sentinel_prism/db/models.py` per reference tree [Source: `architecture.md` §6] |
| Version pinning | Implementer pins SQLAlchemy/Alembic/asyncpg in `requirements.txt` at implementation time [Source: `architecture.md` §2] |

### Library / framework requirements

- Use **SQLAlchemy 2.0** declarative style (`Mapped`, `mapped_column`) when adding first real models in later stories; for this story, **Base + metadata** may suffice.
- Pin **compatible** `sqlalchemy`, `alembic`, `asyncpg` (and sync driver if used for Alembic) — verify on **PyPI** at implementation time; do not copy version numbers from this story file as authoritative.

### File structure requirements

| Path | Purpose |
| --- | --- |
| `alembic.ini` | Alembic configuration (new) |
| `alembic/env.py` | Migration runtime, `target_metadata` wiring (new) |
| `alembic/versions/*.py` | Revision scripts (new; may replace `versions/README.md` as sole artifact) |
| `src/sentinel_prism/db/models.py` | `DeclarativeBase` + `metadata` export |
| `requirements.txt` | New DB-related pins |
| `README.md` | Migration and env documentation |
| `.env.example` | Confirm / extend DB vars |

Do **not** move the Python package root; editable install remains `pip install -e .` with `src/` layout per Story 1.1.

### Testing requirements

- **Minimum (manual):** Empty Postgres → `alembic upgrade head` → success; optional `alembic downgrade base` if downgrade defined.
- **Recommended:** Integration test that runs Alembic programmatically against a **test database URL** (CI optional) — only if low friction; otherwise document manual steps clearly.

### UX / product notes

- None for this story (backend infrastructure only).

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 1.2]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — §2 Stack, §4 Data, §6 Project structure]
- [Source: `_bmad-output/planning-artifacts/prd.md` — NFR3, technical direction]

## Previous story intelligence (Story 1.1)

- **Layout established:** `src/sentinel_prism/db/models.py` exists as a one-line placeholder — **replace/expand** per this story; `alembic/versions/` had only `README.md` — **replace** with real revisions as appropriate.
- **Conventions:** Pinned deps in `requirements.txt`; `pyproject.toml` notes pins live in `requirements.txt`; `GET /health` smoke test in `tests/test_health.py`.
- **Docker:** `docker-compose.yml` Postgres service uses `.env` for `POSTGRES_*`; README already describes optional Docker — extend with **migration** steps.
- **Review learnings:** Avoid hardcoded DB passwords; keep `.env.example` non-secret; `postgresql+asyncpg` already exemplified for app URL.

## Git intelligence summary

- Recent work: **Story 1.1** monorepo skeleton (`510e448` area) — FastAPI app, `web/`, `tests/`, placeholder `alembic/versions`, `db/models.py` stub. Extend that structure rather than introducing a second package layout.

## Latest technical information (implementation time)

- Resolve **SQLAlchemy 2.x**, **Alembic**, and **asyncpg** versions from PyPI for Python **3.11/3.12**; confirm Alembic **`env.py`** pattern (async `run_sync` vs sync engine) against current Alembic docs.
- If using **psycopg3**, prefer documented SQLAlchemy dialect strings; avoid mixing unmaintained drivers.

## Project context reference

- No `project-context.md` in repo at story creation time; use Architecture + PRD + this file + Story 1.1 file list.

## Story completion status

- **Status:** done
- **Note:** Code review complete (2026-04-14); patch findings addressed; implementation complete; full pytest green; optional `@pytest.mark.integration` when `ALEMBIC_SYNC_URL` is set.

## Change Log

- **2026-04-14:** Story 1.2 authored — DB/Alembic baseline, scope guards for 1.3+ auth tables.
- **2026-04-14:** Implemented Alembic + SQLAlchemy baseline, `ALEMBIC_SYNC_URL` / `DATABASE_URL` split, tests, README — status → review.
- **2026-04-14:** Code review — README DSN encoding note; integration test docstring; status → done.

---

## Dev Agent Record

### Agent Model Used

Cursor agent (Composer)

### Debug Log References

- None.

### Completion Notes List

- **AC1–2:** `alembic.ini` → `script_location = alembic`; `env.py` prepends `src/` and sets `target_metadata = Base.metadata` from `sentinel_prism.db.models`. Migrations use **`ALEMBIC_SYNC_URL`** (`postgresql+psycopg://`); app async URL remains **`DATABASE_URL`** (`postgresql+asyncpg://`).
- **AC3:** `.env.example` + README “Database migrations” + troubleshooting; `POSTGRES_*` unchanged vs `docker-compose.yml`.
- **AC4:** Baseline revision `e7d4f1a08c2b` has empty `upgrade()`; no domain ORM models.
- **Tests:** `tests/test_db_models.py`, `tests/test_alembic_cli.py` (`heads`, missing URL failure, optional integration); `tests/test_health.py` still passes.
- **Note:** Story task text referenced `DATABASE_URL` in `env.py`; implementation uses **`ALEMBIC_SYNC_URL`** for Alembic (sync) per Dev Notes driver split — documented in README and `.env.example`.
- **Code review (2026-04-14):** README troubleshooting — URL-encode special characters in DSNs; integration test docstring — use disposable DB for `ALEMBIC_SYNC_URL`.

### File List

- `requirements.txt`
- `pyproject.toml`
- `.env.example`
- `README.md`
- `alembic.ini`
- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/e7d4f1a08c2b_baseline_empty_schema.py`
- `src/sentinel_prism/db/models.py`
- `tests/test_db_models.py`
- `tests/test_alembic_cli.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/1-2-postgresql-schema-core-and-alembic-migrations.md`

**Removed**

- `alembic/versions/README.md` (replaced by real revision)
