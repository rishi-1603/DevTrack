# DevTrack — Resume & LinkedIn Bullet Points

## Resume bullet points (pick 2-3 based on space)

- Built **DevTrack**, a production-style issue-tracking REST API (FastAPI, PostgreSQL,
  SQLAlchemy, Redis) with JWT authentication, role-based access control, and full CRUD
  for projects/issues/comments; deployed live on Render with CI/CD via GitHub Actions.

- Designed and implemented a layered backend architecture (API → services → database)
  with Alembic-managed schema migrations, achieving 91% test coverage across 29 pytest
  cases covering auth, permissions, and workflow transitions.

- Implemented JWT-based auth (access + refresh tokens, bcrypt password hashing) and a
  Redis-backed caching layer with graceful degradation, ensuring the API stays available
  even when the cache is unreachable.

- Containerized the application with Docker Compose (API + PostgreSQL + Redis) and
  automated linting, testing, and coverage reporting on every push using GitHub Actions.

## One-liner (LinkedIn / portfolio card)

DevTrack — A Jira/Trello-style issue tracker backend built with FastAPI, PostgreSQL,
and JWT auth, fully tested (91% coverage) and deployed live.

## Links to have ready

- Live API docs: https://devtrack-api-qhcp.onrender.com/docs
- GitHub repo: https://github.com/rishi-1603/DevTrack

## Interview prep — questions to be ready for

**Architecture**
- Q: Walk me through the request lifecycle for creating an issue.
  A: Client sends JWT in Authorization header → `get_current_user` dependency decodes
  and validates the token, loads the User → route handler in `api/issues.py` calls
  `issue_service.create_issue()` → service validates the project exists and the caller
  has permission, builds an `Issue` ORM object, commits via SQLAlchemy session → returns
  a Pydantic response schema, which FastAPI serializes to JSON.

- Q: Why a layered architecture (api/services/database) instead of putting logic
  directly in route handlers?
  A: Keeps route handlers thin (HTTP concerns only), makes business logic testable
  independent of HTTP, and keeps the ORM/session details out of the API layer so the
  database can be swapped or mocked more easily.

**Auth**
- Q: How does JWT auth work here, and why refresh tokens?
  A: Access tokens are short-lived (30 min) to limit exposure if leaked; refresh tokens
  are long-lived (7 days) and only used to mint new access tokens via `/auth/refresh`,
  so the client doesn't need to re-enter credentials constantly.

- Q: How are passwords stored?
  A: Never in plaintext — hashed with bcrypt (via passlib) before insert; login compares
  the hash, not the raw password.

**Database**
- Q: Why enums stored as native Postgres enum types instead of plain strings?
  A: Enforces valid values at the database level, not just in the application layer —
  catches bugs early and documents allowed states directly in the schema.
  (Note: hit a real bug here — SQLAlchemy's `Enum()` sends the member *name* by default,
  but Postgres only accepts the member *value*; fixed with `values_callable=`.)

**Caching**
- Q: What happens if Redis goes down?
  A: Every cache call is wrapped in a try/except with a 1-second connect timeout; on
  failure it logs a warning and falls back to a live DB query — the dashboard endpoint
  never crashes or blocks on a dead cache.

**Testing**
- Q: How do you test without a live Postgres/Redis in CI?
  A: Tests run against an in-memory SQLite database via a pytest fixture, so the suite
  is fast and has no external dependencies; this trades off not testing
  Postgres-specific behavior (like native enum types) in CI — which is actually how a
  real bug slipped through and had to be caught during manual deployment testing.

**Deployment**
- Q: How is this deployed?
  A: Render web service, auto-deploying from the `main` branch on GitHub; build step
  runs `pip install` + `alembic upgrade head` so migrations apply automatically on every
  deploy; PostgreSQL is a managed Render instance.

**Trade-offs / what you'd improve**
- No Redis in the live deployment (Render's free tier doesn't include one) — the app
  is designed to degrade gracefully, so this is a deliberate trade-off, not a bug.
- No email notifications, file attachments, or CSV export yet — scoped out of MVP
  deliberately to ship a complete, tested core first.
- Single free-tier Postgres instance is currently shared (via a separate logical
  database) with another project, purely a personal sandbox cost constraint — production
  would use a dedicated instance.
