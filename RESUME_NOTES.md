# DevTrack — Resume & LinkedIn Bullet Points

*Rewritten Day 7, corrected again the same day. Every number below is measured,
not remembered: test count and coverage come from the CI `test` job's `pytest
app/tests/ --cov=app --cov-report=term-missing` output, and
the deployment claim was re-checked live on 2026-09-30. The previous version of
this file said "91% coverage across 29 pytest cases"; the measured values at
that time were 94% across 59 — and the 94% was itself measured wrong, counting
the test files as covered code. Understating is as wrong as overstating. After
the Day-7 security remediation the measured values are **110 cases at 92%
production-code coverage**, and an interviewer who runs the suite gets 110.*

## Resume bullet points (pick 2-3 based on space)

- Built **DevTrack**, an issue-tracking REST API (FastAPI, PostgreSQL,
  SQLAlchemy, Redis) with JWT auth (access + refresh), role-based access
  control (ADMIN/DEVELOPER enforced in dependencies *and* re-checked at the
  service layer), full CRUD for projects/issues/comments, notifications, and a
  WebSocket realtime channel authenticated by short-lived single-purpose
  tickets; 110 pytest cases at 92% production-code coverage in CI, with a
  dependency audit that is clean and blocking.

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
JWT auth with roles, async Celery CSV export, WebSocket updates, 110 tests at
92% production-code coverage, Docker Compose stack whose boot is asserted in CI.

## Links to have ready

- Live API docs: https://devtrack-api-qhcp.onrender.com/docs
  *(verified reachable 2026-09-30: `/docs` HTTP 200 after a ~42 s free-tier
  cold start, `/health` HTTP 200 in 0.13 s)*
- GitHub repo: https://github.com/rishi-1603/DevTrack

## Numbers, and where they come from

| Claim | Value | Source |
|---|---|---|
| pytest cases | 110 | CI `test` job (59 before the Day-7 remediation added CORS, WS-ticket and key-strength tests) |
| Statement coverage (production code) | 92% | `--cov=app` with `.coveragerc` omitting `app/tests/`: 1344 stmts, 104 missed |
| Dependency audit | clean, and blocking | `pip-audit` on the pinned set: `No known vulnerabilities found`; was 37 across 5 packages on a green build, because the CI step had both `\|\| true` and `continue-on-error` |
| JWT library | PyJWT 2.15.1 | `app/core/security.py`; python-jose removed — unmaintained, PYSEC-2025-185 unfixed |
| WebSocket credential | 60 s ticket, `type: "ws"` | `POST /auth/ws-ticket`; rejected by every REST dependency, and access/refresh tokens are rejected by the socket |
| Signing-key floor | 32 bytes in production | `app/core/config.py` model validator; warns outside production so a dev key cannot take the live service down |
| Migrations | 4 (+ round-trip) | `migrations/versions/0001..0004`; CI downgrades and re-upgrades them |
| Compose services | 5 | `backend/docker-compose.yml`: api, worker, migrate, db, redis |
| Live deployment | HTTP 200 | checked by hand on 2026-09-30, cited above |

Two methodology notes, because a coverage percentage without them is a trap:

1. **The 94% this file quoted earlier in Day 7 was measured including the test
   files themselves** — `app/tests/` lives inside the package being measured,
   so the suite was scoring its own tests. A `.coveragerc` added the same day
   omits them; the production-only figure was **91%** (1303 statements, 119
   missed), and is **92%** (1344/104) after the Day-7 remediation added the
   CORS and config-validation modules, which are fully covered. Amusingly, 91%
   is also what the *original* pre-audit resume claimed — right number, wrong
   provenance, since nothing measured it then either.
2. Coverage is measured **only** in the isolated-SQLite unit job. The two
   lowest-covered modules — `services/dashboard_service.py` (33%) and
   `utils/cache.py` (33%) — are the Redis caching layer, whose real paths are
   exercised by the `real-infra-smoke-test` job against a live Redis without
   measuring coverage. So 92% understates what is actually tested; say that
   only if asked, and say it with this explanation.

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

**Security**
- Q: What did the security review find here?
  A: Five things. The CI dependency scan was non-blocking twice over
  (`pip-audit --desc || true` plus `continue-on-error`), so a green build was
  reporting 37 known vulnerabilities. The JWT library was python-jose 3.3.0 —
  unmaintained, one advisory with no published fix, plus `ecdsa` dragged in with
  an unfixed one of its own. The WebSocket took the user's full-scope access
  token in a query string, which everything in front of the app logs by default.
  `CORS_ORIGINS` defaulted to `*` with `allow_credentials=True` hardcoded, a
  pairing under which Starlette echoes the caller's Origin instead of sending
  `*`. And `SECRET_KEY` had no minimum length, so HS256 could be running on a key
  short enough to brute-force offline from one captured token. All five are
  fixed; the scan is clean with zero waivers, and only then did it become a gate.
- Q: Which advisories were you actually vulnerable to?
  A: Say this precisely, because the honest answer is "fewer than the count
  suggests". The Starlette ones concern `StaticFiles`/`FileResponse`,
  `request.url.hostname`, bare `HTTPEndpoint` and urlencoded form limits — this
  app uses none of them. python-multipart's concern multipart file parsing, and
  no endpoint here accepts a file upload; the package is required only because
  `/auth/login` declares `OAuth2PasswordRequestForm`, which posts urlencoded
  data. The `pytest` advisory was a dev dependency, never on the request path.
  Bumped anyway — "not reachable today" is not a reason to keep a vulnerable
  pin — but a CVE count is not a risk assessment, and claiming otherwise in an
  interview is the tell.
- Q: Why is the WebSocket ticket better than just using the access token?
  A: It bounds what a leaked credential is worth. Browsers cannot set headers on
  a WS handshake, so *something* has to go in the query string; the choice is
  between a 30-minute full-scope API token and a 60-second ticket that every REST
  dependency rejects because its `type` is `"ws"`. A ticket scraped out of a
  proxy log can open one notification socket for a minute. There are tests in
  both directions: the socket rejects access, refresh, expired, mis-signed and
  orphan tickets, and `/users/me` rejects a ticket while still accepting the
  same user's access token.
- Q: Why does the key-length rule only fail in production?
  A: Because this project is deployed, auto-deploying from `main`, and an
  unconditional rule could take a live service down over a development key —
  trading a real control for an outage. Production is where the key protects
  real tokens, so production is where it is enforced; elsewhere it warns loudly.
  That is the same shape as the CORS rule, and the asymmetry is documented in
  the validator rather than left to be discovered.

**Deployment**
- Q: How is this deployed?
  A: Render web service auto-deploying from `main`; build runs `pip install` +
  `alembic upgrade head` so migrations apply on every deploy; Postgres is a
  managed instance. Verified reachable 2026-09-30.

## Trade-offs / what you'd improve (be ready to say these unprompted)

- Coverage's blind spot is the Redis layer, explained above; the honest fix is
  measuring coverage in the real-infra job too, which has not been done.
- **The WebSocket endpoint still has no client consumer.** It is tested
  server-side (13 tests on the ticket semantics, plus backlog delivery and live
  push) and this repo is backend-only, so nothing in it ever opens the socket.
  The Day-7 remediation made the credential safe to put in a query string; it did
  not make the feature used. Build a minimal client or drop the endpoint.
- **A latent flake in the rate-limit tests was found and fixed.** The limiter
  scopes counters to a wall-clock window, so a test straddling a 60-second
  boundary split its count and passed or failed depending on elapsed time. It
  surfaced when the Day-7 tests changed the suite's timing. The clock is now
  pinned in those tests and the rollover is asserted deliberately. Worth
  mentioning unprompted: it is an example of a test that was green for the wrong
  reason.
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
