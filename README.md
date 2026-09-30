# DevTrack

**A lightweight, self-hosted issue-tracking backend for small engineering teams.**

DevTrack is a simplified Jira/Trello/GitHub Issues clone, built as a production-style
FastAPI backend: JWT-authenticated users, projects, issues with a status workflow,
comments, an activity log, and a cached dashboard summary.

> Add screenshots here after running the app locally.

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture Overview](#architecture-overview)
- [Database Schema](#database-schema)
- [Installation & Local Run](#installation--local-run)
- [Running Migrations](#running-migrations)
- [API Endpoints](#api-endpoints)
- [Running Tests](#running-tests)
- [Future Improvements](#future-improvements)

## Features

- JWT authentication (access + refresh tokens) with bcrypt password hashing
- Role-based access control: `admin` and `developer` roles
- Full CRUD for Projects, scoped to owner/admin permissions
- Full CRUD for Issues with a `todo -> in_progress -> done` status workflow and assignment
- Comments on issues (an issue's audit trail / history)
- Dashboard summary endpoint (total/completed/pending/high-priority issues) cached in Redis
- Graceful degradation if Redis is unreachable (falls back to a live DB query)
- Structured logging to rotating log files (`app.log`, `error.log`, `access.log`)
- Consistent JSON error responses via custom exception classes
- Full Pydantic v2 request/response validation
- Alembic migrations for PostgreSQL schema management
- Real-time notifications over WebSocket (issue assignment, status changes,
  comments), persisted to Postgres so they're also visible via a REST
  endpoint if the client wasn't connected when the event happened
- Asynchronous CSV export of a project's issues via a Celery + Redis task
  queue, so generating the file doesn't block the request that triggered it
- Redis-backed rate limiting on login/registration (per-IP, to slow
  brute-force/spam) and issue/comment creation + exports (per-user, to
  bound abuse); fails open (allows requests) if Redis itself is down --
  see `app/utils/rate_limit.py` for why that trade-off was made deliberately
- Reliable background jobs (Day 4): the export task now actually retries.
  `max_retries=2` had been declared on the task from the start with **no
  `self.retry()` call anywhere in its body**, so the setting was inert --
  any exception marked the job permanently failed on the first attempt, and
  a transient database lock or filesystem blip was indistinguishable from a
  genuine bug. Now: up to 3 attempts with exponential backoff + jitter,
  genuinely permanent failures (missing job row) skip retrying instead of
  burning the budget, and exhausted jobs are recorded in a Redis
  dead-letter list (`app/utils/dead_letter.py`) carrying enough of the
  original request to replay by hand. `attempts` is persisted on the row
  (migration `0004`) and exposed through `GET /export-jobs/{id}`, so retry
  history is visible after the fact rather than only in worker logs.
  - The row is deliberately left non-terminal *between* attempts -- marking
    it failed on the first exception would show a polling client a failure
    that then succeeds.
  - Jobs abandoned by a worker that died mid-task are reaped at API startup
    (`recover_stranded_export_jobs`) and reported as terminally failed with
    an explanation, instead of sitting in `running` forever while a client
    polls a completion that can never arrive. The staleness threshold is
    deliberately far larger than Celery's `task_time_limit`, because a false
    positive marks a legitimately in-flight job as failed.
  - The dead-letter list **fails open**: if Redis is down the push is logged
    at CRITICAL and dropped, and the worker does not crash, because the
    Postgres row (`status=failed` + `error_message`) is already the
    authoritative record. This is the *opposite* bias to the sibling
    CertiFake project's Kafka DLQ, where the envelope is the only surviving
    copy of the event and a failed publish must therefore block the offset
    commit. Same phrase, different correct answer -- the difference is which
    store is authoritative.
- Dockerized (API + worker + PostgreSQL + Redis) via docker-compose
- Deployment config validated in CI (Day 5) — see below
- **Compose stack booted and driven end to end in CI (Day 6)** — see below.
  This is also what found that the stack had no schema-creation step at all,
  so `docker compose up` was starting the API against an empty Postgres.
- GitHub Actions CI: lint + isolated-SQLite unit tests on every push, plus
  three additional jobs that exercise **real** infrastructure rather than
  mocks. Two run against real Postgres/Redis service containers -- an Alembic
  migration round-trip check (catches Postgres-specific bugs SQLite's
  forgiving type system would miss -- this project already hit one: a
  duplicate index name) and a full register->login->export->rate-limit smoke
  test driven over real HTTP against a real API process + real Celery worker.
  The third boots `docker-compose.yml` itself and drives the same flow through
  the actual containers, which is what covers the deployment artifact rather
  than just the application.
- No hardcoded insecure `SECRET_KEY` fallback: the app now fails to start
  (loudly, at import time) if `SECRET_KEY` isn't set, instead of silently
  signing JWTs with a hardcoded default in every environment including
  production

### Deployment config hardening (Day 5)

`docker-compose.yml` had never been validated by anything — no Docker daemon
existed where it was written. Three real defects, plus the checks that now
prevent their recurrence:

- **A startup race that failed *silently*.** `api` and `worker` used the short
  `depends_on: [db, redis]` form, which means `service_started`: wait for the
  container to start, not for Postgres to accept connections. This is **not** a
  crash risk, and it is worth being precise about why — `on_startup()` is
  deliberately DB-tolerant (`create_all` runs only for SQLite, and
  `recover_stranded_export_jobs` is wrapped in a broad `except` that logs and
  continues, so boot is never blocked). The actual consequence is worse than a
  crash because nothing reports it: if Postgres isn't ready, Day 4's
  stranded-job recovery is **skipped**, so export jobs abandoned by a previous
  crashed worker stay unrecovered until the *next* restart. Both services now
  gate on `condition: service_healthy`, backed by real healthchecks.
- **Healthcheck binaries verified, not assumed.** `pg_isready` was confirmed
  present at `/usr/bin/pg_isready` in `postgres:16` (amd64
  `sha256:a85daf0d…`) and `redis-cli` at `/usr/local/bin/redis-cli` in
  `redis:7-alpine` (`sha256:ca0acbb1…`) by downloading and listing each image's
  layer tarballs. This matters more than it sounds: a healthcheck naming a
  binary the image lacks marks that service permanently unhealthy, and once
  dependents gate on `service_healthy` they would never start — turning a
  self-recovering stack into one that is dead on arrival. The `api` healthcheck
  uses **Python**, because `backend/Dockerfile` installs only `gcc` and
  `libpq-dev` on `python:3.12-slim-trixie` — no curl, wget or nc.
- **The worker deliberately has no healthcheck.** Unlike CertiFake's workers
  (which run a metrics HTTP server), this Celery worker exposes no HTTP surface,
  so there is nothing to probe. `celery inspect ping` was considered and
  rejected: it does a broadcast round-trip through the broker, can time out
  under load, and combined with `restart: unless-stopped` a false negative
  would cause a restart loop — a self-inflicted outage nobody could debug from a
  compose file. What *is* covered: process exit restarts the container, and
  Celery retries broker connection on its own.
- Obsolete top-level `version:` removed; `restart: unless-stopped` and a
  healthcheck added to `api`.
- **Two new CI checks, neither needing a Docker daemon:**
  `scripts/check_images.py` resolves every image reference — compose `image:`
  *and* Dockerfile `FROM` — against its registry and fails if any can no longer
  be pulled; `scripts/check_config_consistency.py` asserts the things no
  single-file validator can see (a service setting `DATABASE_URL`/`REDIS_HOST`
  at another service must have the matching `depends_on` edge; nothing may gate
  on `service_healthy` for a service with no healthcheck; no placeholder image
  strings). Both are byte-identical to CertiFake's copies on purpose — three
  divergent forks of a config checker would be the same duplication problem it
  exists to prevent. Dependency inference reads the **hostname out of the env
  value** rather than assuming a service is called `postgres`, which is why the
  same script works here (service named `db`, `REDIS_HOST` rather than
  `REDIS_URL`) and there.
- **Both checkers were mutation-tested**: each was run against deliberately
  reintroduced defects (placeholder image, deleted worker deployment,
  `replicas` on an HPA-managed Deployment, undocumented Secret key, dropped
  `depends_on` edge, obsolete `version:`, Prometheus scraping a nonexistent
  service, `service_healthy` on a service with no healthcheck) and caught all
  eight. This was not a formality — an earlier version silently skipped *every*
  k8s check while still printing PASS, because a file lookup matched filenames
  instead of extensions.

**Now verified in CI (Day 6).** The `compose-smoke-test` job runs
`docker compose up -d --build --wait` on this file and then executes
`scripts/compose_smoke_test.sh` against the running containers. It passed on
commit `f404cd8` (run `36613268375`, 2026-09-29), having observed:

- `migrate` exiting 0, and **all 7 tables plus `alembic_version` present inside
  the `db` container** — migrations ran in the stack, not on the runner;
- the `api` container reaching Docker's own `healthy` state, the first
  empirical confirmation of that healthcheck rather than an inference from the
  image containing `python`;
- register → login → create project → create issue working through
  service-name networking;
- the Celery worker consuming a real task from real Redis and completing the
  export;
- the CSV the **worker** container wrote being readable by the **api**
  container, and `GET /export-jobs/{id}/download` serving its contents.

That last point is the one only separate containers can make, and it is what
gives `ExportJob`'s documented limitation — "`file_path` points at a file on
the local filesystem of whichever worker produced it" — real teeth: it holds
only because both mount `devtrack_exports` at `/app/exports`. Running both
processes on one host, as development did and as `real-infra-smoke-test` still
does, cannot fail that way.

It also found a genuine bug. **This stack previously had no schema-creation
step at all**: the image `CMD` is a bare `uvicorn` with no entrypoint, and
`app/main.py:on_startup()` calls `create_all()` only when `DATABASE_URL`
starts with `sqlite` — while compose configures Postgres. So `docker compose
up` brought the API up against an **empty database**, where every DB-touching
request fails with `relation "users" does not exist`. Nothing caught it
because the two places the stack was ever exercised both create the schema
some other way: pytest runs on SQLite, and `real-infra-smoke-test` runs
`alembic upgrade head` as a manual step on the runner. Fixed by the one-shot
`migrate` service, which `api` and `worker` gate on with
`condition: service_completed_successfully`.

**Still not verified:** this repo has no Kubernetes manifests, so there is no
deployment schema to validate here; and nothing exercises more than one worker
replica, which is exactly the case `ExportJob`'s file-path limitation would
break under.

## Tech Stack

| Layer | Technology |
|---|---|
| Language / Framework | Python 3.12, FastAPI, Uvicorn |
| Validation | Pydantic v2 |
| ORM / Migrations | SQLAlchemy 2.x, Alembic |
| Database | PostgreSQL (SQLite in-memory for tests) |
| Auth | JWT (python-jose), bcrypt (passlib), OAuth2PasswordBearer |
| Caching / Rate limiting | Redis (redis-py) |
| Background jobs | Celery (broker + result backend: the same Redis instance) |
| Real-time | WebSockets (FastAPI's native support) |
| Testing | pytest, pytest-cov, httpx / FastAPI TestClient, fakeredis |
| Containerization | Docker, docker-compose |
| CI/CD | GitHub Actions |
| Logging | Python `logging` with `RotatingFileHandler` |

## Architecture Overview

DevTrack follows a layered architecture to keep concerns separated and testable:

```
Request
  │
  ▼
app/api/*        <- FastAPI routers: parse/validate request, call a service, shape the response
  │
  ▼
app/services/*   <- business logic: permission checks, workflow rules, activity logging
  │
  ▼
app/database/*   <- SQLAlchemy models + session management (the persistence layer)
  │
  ▼
PostgreSQL
```

Supporting modules:

- `app/core/` — configuration (`config.py`), JWT/password utilities (`security.py`),
  logging setup (`logging.py`), and shared FastAPI dependencies (`dependencies.py`,
  e.g. `get_current_user`, `require_admin`).
- `app/schemas/` — Pydantic v2 request/response models, one module per resource.
- `app/utils/` — cross-cutting helpers: `cache.py` (Redis, fails open) and
  `exceptions.py` (custom exception hierarchy mapped to HTTP status codes in `main.py`).
- `app/tests/` — pytest suite exercising the whole stack against an in-memory SQLite DB.

Routers never touch the ORM directly, and services never touch `Request`/`Response`
objects — this keeps business rules reusable and easy to unit test.

## Database Schema

| Table | Key Columns |
|---|---|
| `users` | id, name, email (unique), password_hash, role (`admin`/`developer`), created_at |
| `projects` | id, title, description, owner_id (FK → users), created_at |
| `issues` | id, title, description, priority (`low`/`medium`/`high`), status (`todo`/`in_progress`/`done`), due_date, project_id (FK → projects), assigned_to (FK → users, nullable), created_at |
| `comments` | id, issue_id (FK → issues), user_id (FK → users), comment, created_at |
| `activity_logs` | id, user_id (FK → users), action, timestamp |

Relationships: a `User` owns many `Project`s and can be assigned many `Issue`s; a
`Project` has many `Issue`s; an `Issue` has many `Comment`s; every mutating action is
recorded in `activity_logs`.

## Installation & Local Run

### Option A: Docker (recommended)

```bash
cd backend
cp .env.example .env      # edit values as needed (never commit real secrets)
docker compose up --build
```

This starts three containers: `api` (FastAPI on port 8000), `db` (PostgreSQL 16), and
`redis` (Redis 7). Once the database container is healthy, run migrations:

```bash
docker compose exec api alembic upgrade head
```

The API is now available at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`.

### Option B: Local Python environment

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# For a fully local run without PostgreSQL, you can point DATABASE_URL at SQLite:
#   DATABASE_URL=sqlite:///./devtrack.db
# (the app auto-creates tables for sqlite:// URLs on startup; for PostgreSQL, run
# Alembic migrations instead — see below)

uvicorn app.main:app --reload
```

The API is now available at `http://localhost:8000`.

## Running Migrations

Migrations are managed with Alembic and target PostgreSQL:

```bash
cd backend
alembic upgrade head          # apply all migrations
alembic revision -m "message" # create a new migration
alembic downgrade -1          # roll back the last migration
```

Alembic reads `DATABASE_URL` from the environment via `app/core/config.py`, so make
sure your `.env` (or exported environment variables) point at a reachable PostgreSQL
instance before running migrations.

## API Endpoints

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/auth/register` | Create a new user (default role: developer) | Public |
| POST | `/auth/login` | Log in with email/password, get access + refresh tokens | Public |
| POST | `/auth/refresh` | Exchange a refresh token for a new token pair | Public |
| POST | `/auth/change-password` | Change the current user's password | Required |
| POST | `/auth/logout` | Record a logout event | Required |
| GET | `/users/me` | Get current user's profile | Required |
| PUT | `/users/me` | Update current user's profile | Required |
| DELETE | `/users/{id}` | Delete a user | Admin |
| POST | `/projects` | Create a project | Required |
| GET | `/projects` | List projects (optional `search` query param) | Required |
| GET | `/projects/{id}` | Get a project by id | Required |
| PUT | `/projects/{id}` | Update a project | Owner/Admin |
| DELETE | `/projects/{id}` | Delete a project | Owner/Admin |
| POST | `/issues` | Create an issue | Required |
| GET | `/issues` | List issues (filters: `project_id`, `status`, `priority`, `search`) | Required |
| GET | `/issues/{id}` | Get an issue by id | Required |
| PUT | `/issues/{id}` | Update an issue | Owner/Admin |
| DELETE | `/issues/{id}` | Delete an issue | Owner/Admin |
| POST | `/issues/{id}/assign` | Assign an issue to a user | Owner/Admin |
| PATCH | `/issues/{id}/status` | Change issue status (`todo` → `in_progress` → `done`) | Owner/Admin |
| POST | `/issues/{id}/comments` | Add a comment to an issue | Required |
| GET | `/issues/{id}/comments` | List comments on an issue (issue history) | Required |
| DELETE | `/comments/{id}` | Delete a comment | Author/Admin |
| GET | `/dashboard/summary` | Aggregate stats for the current user (Redis-cached, 60s TTL) | Required |
| GET | `/notifications` | List the current user's notifications (`unread_only` filter) | Required |
| POST | `/notifications/{id}/read` | Mark one notification as read | Owner |
| POST | `/notifications/read-all` | Mark all of the current user's notifications as read | Required |
| WS | `/ws/notifications?token=...` | Live push of new notifications (delivers unread backlog on connect) | Required (token as query param) |
| POST | `/projects/{id}/export` | Queue an async CSV export of a project's issues (Celery) — `202 Accepted` | Required, rate-limited (5/min/user) |
| GET | `/export-jobs/{id}` | Poll export job status (`pending`/`running`/`completed`/`failed`) | Requester/Owner/Admin |
| GET | `/export-jobs/{id}/download` | Download the completed CSV | Requester/Owner/Admin |
| GET | `/health` | Liveness probe | Public |

Full interactive documentation (Swagger UI) is available at `/docs` once the app is
running, and the raw OpenAPI schema at `/openapi.json`.

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest --cov=app --cov-report=term-missing
```

`backend/.coveragerc` omits `app/tests/` from measurement, so the reported
percentage is production code only — **91%** (1303 statements, 119 missed) as of
the Day-7 audit. Without that omission the suite scores its own test files and
reports a flattering 94%; the difference is documented in `RESUME_NOTES.md`.

Tests run against an isolated in-memory SQLite database (no live PostgreSQL/Redis
required) and cover: registration, login (success + wrong password), JWT validation
(missing/invalid/wrong-type tokens), project CRUD, issue CRUD + workflow transitions,
comments, and permission checks (non-owner editing a project, non-admin deleting a
user, non-author deleting a comment).

## Running the background worker (required for CSV export)

The `/projects/{id}/export` endpoint only *queues* a job — a separate
Celery worker process actually generates the CSV. If you're not using
`docker-compose` (which already runs a `worker` service), start one
yourself alongside the API:

```bash
cd backend
source .venv/bin/activate
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

This requires a reachable Redis instance (same `REDIS_HOST`/`REDIS_PORT`
the API uses for caching and rate limiting). Without a running worker, an
export job will sit in `pending` forever — it degrades to "nothing happens
past 202 Accepted," not a crash, but it also never completes, so this is a
genuine operational dependency worth knowing about, not an implementation
detail you can ignore.

## Future Improvements

- File attachments on issues (e.g. screenshots, logs)
- Email notifications (the current notification system is in-app: persisted
  + WebSocket push, not email)
- A per-issue activity timeline (currently activity is logged globally per user,
  not yet surfaced per-issue in the API)
- Moving exported CSVs to shared/object storage (e.g. S3) instead of local
  disk, which is what would be required to run more than one worker
  replica correctly (see the `ExportJob` model's docstring)
- Automatic **re-enqueue** of jobs recovered as stranded. Today
  `recover_stranded_export_jobs()` correctly reports them as terminally
  failed so clients stop waiting, but the user must request the export
  again. Re-running them automatically needs a claim/lease mechanism (a
  worker heartbeat, or Celery `acks_late` plus a broker visibility timeout)
  to avoid double-executing a task whose worker is merely slow rather than
  dead -- a larger change than this pass warranted

## Live Demo

A live instance is deployed on Render:

- **Interactive API docs (Swagger UI):** https://devtrack-api-qhcp.onrender.com/docs
- **ReDoc:** https://devtrack-api-qhcp.onrender.com/redoc
- **Health check:** https://devtrack-api-qhcp.onrender.com/health

Try it: register a user via `POST /auth/register`, log in via `POST /auth/login`
(or use Swagger's built-in "Authorize" button), then create a project and an issue
and watch `GET /dashboard/summary` update in real time.

Note: the free-tier instance may take a few seconds to wake up on the first request
after a period of inactivity.

## License

MIT — see [LICENSE](LICENSE).
