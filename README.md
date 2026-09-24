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
- Dockerized (API + worker + PostgreSQL + Redis) via docker-compose
- GitHub Actions CI: lint + isolated-SQLite unit tests on every push, plus
  two additional jobs that run against **real** Postgres/Redis service
  containers -- an Alembic migration round-trip check (catches
  Postgres-specific bugs SQLite's forgiving type system would miss --
  this project already hit one: a duplicate index name) and a full
  register->login->export->rate-limit smoke test driven over real HTTP
  against a real API process + real Celery worker
- No hardcoded insecure `SECRET_KEY` fallback: the app now fails to start
  (loudly, at import time) if `SECRET_KEY` isn't set, instead of silently
  signing JWTs with a hardcoded default in every environment including
  production

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
- Celery task retry/backoff and a dead-letter queue for permanently-failed
  export jobs (today a failed task marks the job `failed` and stops; there
  is no automatic retry)
- Moving exported CSVs to shared/object storage (e.g. S3) instead of local
  disk, which is what would be required to run more than one worker
  replica correctly (see the `ExportJob` model's docstring)

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
