# DevTrack — Resume & LinkedIn Bullet Points

*Rewritten Day 7. Every number below is measured, not remembered: test count and
coverage come from the CI `test` job's `pytest app/tests/ --cov=app
--cov-report=term-missing` output (run 36614453421 on commit `d6e21d3`), and
the deployment claim was re-checked live on 2026-09-30. The previous version of
this file said "91% coverage across 29 pytest cases"; the measured values at
that time were 94% across 59. Understating is as wrong as overstating — an
interviewer who runs the suite will get 59.*

## Resume bullet points (pick 2-3 based on space)

- Built **DevTrack**, an issue-tracking REST API (FastAPI, PostgreSQL,
  SQLAlchemy, Redis) with JWT auth (access + refresh), role-based access
  control (ADMIN/DEVELOPER enforced in dependencies *and* re-checked at the
  service layer), full CRUD for projects/issues/comments, notifications, and a
  WebSocket realtime channel; 59 pytest cases at 94% statement coverage in CI.

- Implemented asynchronous CSV export as a Celery task over Redis: request
  returns immediately, a worker generates the file, the job row tracks
  attempts and terminal state, and stranded jobs from a dead worker are reaped
  at startup. Verified end-to-end in CI twice — once with the API and worker as
  real processes, once as separate containers sharing a volume.

- Containerized the stack (API + worker + PostgreSQL + Redis + one-shot
  Alembic migrator) and made CI boot it: `docker compose up --wait`, then a
  shell test asserting migrations ran *inside* the stack, the API reaches
  Docker-healthy, and a CSV written by the worker container is readable by the
  API container.

- Found and fixed a deployment bug no test could see: the compose stack had no
  schema-creation step at all, so `docker compose up` started the API against
  an empty Postgres. It had gone unnoticed for the life of the project because
  the two places the stack was ever exercised (SQLite-based pytest, and a CI
  job that migrates on the runner) both create the schema some other way.

## One-liner (LinkedIn / portfolio card)

DevTrack — a Jira/Trello-style issue-tracker backend in FastAPI + PostgreSQL:
JWT auth with roles, async Celery CSV export, WebSocket updates, 59 tests at
94% coverage, Docker Compose stack whose boot is asserted in CI.

## Links to have ready

- Live API docs: https://devtrack-api-qhcp.onrender.com/docs
  *(verified reachable 2026-09-30: `/docs` HTTP 200 after a ~42 s free-tier
  cold start, `/health` HTTP 200 in 0.13 s)*
- GitHub repo: https://github.com/rishi-1603/DevTrack

## Numbers, and where they come from

| Claim | Value | Source |
|---|---|---|
| pytest cases | 59 | CI `test` job, `59 passed in 49.09s` |
| Statement coverage | 94% | same job: `TOTAL 2020 stmts, 120 missed, 94%` |
| Modules at 100% | 37 of 57 | same coverage table |
| Migrations | 4 (+ round-trip) | `migrations/versions/0001..0004`; CI downgrades and re-upgrades them |
| Compose services | 5 | `backend/docker-compose.yml`: api, worker, migrate, db, redis |
| Live deployment | HTTP 200 | checked by hand on 2026-09-30, cited above |

Coverage is measured **only** in the isolated-SQLite unit job. The two
lowest-covered modules — `services/dashboard_service.py` (33%) and
`utils/cache.py` (33%) — are the Redis caching layer, whose real paths are
exercised by the `real-infra-smoke-test` job, which runs against a live Redis
but does not measure coverage. So 94% understates what is actually tested;
say that only if asked, and say it with this explanation.

## Interview prep — questions to be ready for

**Architecture**
- Q: Walk me through the request lifecycle for creating an issue.
  A: Client sends JWT in `Authorization` → `get_current_user` decodes and
  validates it, loads the User → route handler in `api/issues.py` calls
  `issue_service.create_issue()` → service checks the project exists and that
  the caller owns it or is ADMIN, builds the `Issue` ORM object, commits via
  the SQLAlchemy session → Pydantic response schema serializes to JSON.

- Q: Why layered (api/services/database) instead of logic in route handlers?
  A: Handlers stay HTTP-only; business rules are testable without HTTP; the
  ORM/session stays out of the API layer so the database can be swapped or
  faked. The payoff is visible in the tests: services are tested directly.

**Auth & authorization**
- Q: How does JWT auth work here, and why refresh tokens?
  A: Access tokens short-lived (30 min) to limit exposure if leaked; refresh
  tokens long-lived (7 days) mint new access tokens via `/auth/refresh`.
- Q: Where is authorization enforced?
  A: Twice, deliberately. `core/dependencies.py` gates admin-only routes, and
  each service re-checks ownership (`_ensure_can_modify_issue`,
  `_ensure_can_view`) so a bug in one router cannot leak another user's row.
- Q: How are passwords stored?
  A: bcrypt via passlib; login compares hashes, never plaintext.

**Database**
- Q: Why native Postgres enums?
  A: Valid values enforced at the database level. (Real bug here: SQLAlchemy's
  `Enum()` sends the member *name* by default but Postgres wants the *value*;
  fixed with `values_callable=`. That bug is why CI migrates against real
  Postgres instead of trusting SQLite.)
- Q: How do migrations run in containers, where there is no human?
  A: A one-shot `migrate` service runs `alembic upgrade head`; api and worker
  depend on it with `condition: service_completed_successfully`. One-shot
  rather than in both commands because two concurrent Alembic runs against one
  database is a race, and `service_completed_successfully` is also what lets
  `up --wait` treat the migrator's exit 0 as success (docker/compose#10596).

**Caching**
- Q: What happens if Redis goes down?
  A: Every cache call is wrapped with a 1 s connect timeout; on failure it logs
  and falls back to a live DB query. The dashboard degrades, never crashes.

**Testing**
- Q: How do you test without live Postgres/Redis in the unit job?
  A: In-memory SQLite via a fixture — fast and dependency-free, at the cost of
  not testing Postgres-specific behaviour. That cost was paid once for real
  (the enum bug above), which is why `postgres-migration-check` and
  `real-infra-smoke-test` exist as separate jobs against real services.
- Q: What does the compose smoke test prove that the others cannot?
  A: That the deployment artifact works: schema created inside the stack, the
  API reaching Docker-healthy, and — only possible with separate containers —
  that the CSV the worker wrote to its own filesystem is readable by the API
  through the shared `devtrack_exports` volume.

**Deployment**
- Q: How is this deployed?
  A: Render web service auto-deploying from `main`; build runs `pip install` +
  `alembic upgrade head` so migrations apply on every deploy; Postgres is a
  managed instance. Verified reachable 2026-09-30.

## Trade-offs / what you'd improve (be ready to say these unprompted)

- Coverage's blind spot is the Redis layer, explained above; the honest fix is
  measuring coverage in the real-infra job too, which has not been done.
- `ExportJob.file_path` points at the worker's local filesystem. Correct for a
  single-node deployment, wrong for more than one worker replica — it would
  need shared/object storage. The compose smoke test only passes *because*
  both containers mount the same volume; say so rather than letting it sound
  like a design virtue.
- The recorded `file_path` is unnormalized (`/app/app/tasks/../../exports/…`)
  because `export_tasks.py` never calls `normpath`. Harmless — the kernel
  resolves it — but it is exactly the kind of thing a string prefix check
  trips on, which is how the smoke test's first version failed.
- No email notifications or file attachments; scoped out of the MVP
  deliberately. (CSV export *is* implemented — an older version of this file
  claimed otherwise.)
- Whether the live Render instance has a Redis attached is not something I can
  verify from here; the graceful-degradation path is what makes that safe
  either way, and it is covered by a real Redis-outage test in the unit suite.
