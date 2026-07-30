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
- Dockerized (API + PostgreSQL + Redis) via docker-compose
- GitHub Actions CI running lint + tests on every push/PR

## Tech Stack

| Layer | Technology |
|---|---|
| Language / Framework | Python 3.12, FastAPI, Uvicorn |
| Validation | Pydantic v2 |
| ORM / Migrations | SQLAlchemy 2.x, Alembic |
| Database | PostgreSQL (SQLite in-memory for tests) |
| Auth | JWT (python-jose), bcrypt (passlib), OAuth2PasswordBearer |
| Caching | Redis (redis-py) |
| Testing | pytest, pytest-cov, httpx / FastAPI TestClient |
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

## Future Improvements

- File attachments on issues (e.g. screenshots, logs)
- Email notifications on issue assignment / status change
- CSV export of projects and issues
- A per-issue activity timeline (currently activity is logged globally per user,
  not yet surfaced per-issue in the API)
