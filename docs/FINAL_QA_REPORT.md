# DevTrack — Days 8–10 final QA report

**Verification date:** 2026-10-12 (Asia/Calcutta / UTC evidence timestamps)  
**Verified main commit:** `ee0fd120`  
**Repository:** https://github.com/rishi-1603/DevTrack

This report records evidence, not assumptions. A green build alone is not used
to claim the live deployment is complete.

## Result

**Overall: PARTIAL**

The core issue-management workflow, security controls, tests, CI, Docker Compose
stack, migrations, and documentation pass. The public Render API is healthy and
its core workflow passes, but the Render service has no reachable Redis/Celery
worker, so a live CSV export cannot complete. The failure is now handled as a
clear `503` with a terminal failed job instead of an opaque `500`.

## Evidence matrix

| Area | Result | Evidence |
|---|---|---|
| Core workflow | **PASS** | Live: register → login → project → issue → assign → status transitions → comment → persistence → filters → dashboard; see [live verification log](verification-live-2026-10-12.md) |
| Critical automated tests | **PASS** | Local: **178 passed**, 0 xfail; 97% production coverage, 1,395 statements / 43 missed; ruff clean |
| Security/dependencies | **PASS** | `pip-audit`: `No known vulnerabilities found`; tracked-file secret scan found no credential patterns; no tracked real `.env` files; production debug scan clean |
| CI/CD | **PASS** | Main run [38229738047](https://github.com/rishi-1603/DevTrack/actions/runs/38229738047): test, config-validation, real-infra-smoke-test, Postgres migration round-trip, Docker build, and Compose smoke all passed |
| Live API liveness/docs | **PASS** | `https://devtrack-api-qhcp.onrender.com/health` and `/docs` returned HTTP 200 |
| Live RBAC/error paths | **PASS** | Non-owner project/issue operations returned 403; missing/invalid token 401; malformed project 422; nonexistent issue 404 |
| Live async export success | **PARTIAL** | CI Compose proves the successful worker path. Render post-fix check returned controlled 503 because Redis/Celery is not reachable; see `evidence/DevTrack/export-after-fix.txt` |
| README/architecture/screenshots | **PASS** | README describes the actual stack and limitation; `docs/architecture.svg`; live Swagger capture in `docs/screenshots/` |
| Second verification | **PASS for changed behavior / PARTIAL for live export** | PR CI passed after the relationship and broker fixes; post-deployment live export was rechecked and changed from 500 to safe 503 |

## Findings fixed in this pass

### D1-2 / D3-1 — delete cascades and shadowed ORM relationships

The earlier cascade PR added bidirectional `ExportJob.project` and
`ExportJob.requested_by` relationships twice. The later plain relationship
assignments shadowed the new `back_populates` definitions. Mapper inspection
showed `back_populates=None` before the de-duplication and the intended
`export_jobs` / `export_requests` values after it.

The parent-side `delete-orphan` cascade already made the request behavior pass,
so this was a correctness/maintainability defect rather than a claim that the
live delete endpoint was still broken. Two regression tests now cover the
previously untested half: deleting a project with an export job and deleting a
user who requested one. They assert the export row is removed.

### Live reliability finding — broker outage returned opaque 500

The Render API had no reachable Redis/Celery broker. `POST
/projects/{id}/export` committed a job and then allowed the transport exception
to reach the generic 500 handler. The fix records a non-sensitive terminal
failure and raises `ServiceUnavailableException` with HTTP 503. The raw broker
exception is logged for operators, not returned to the client.

A reachable broker plus worker is still required for a successful export. The
CI Compose smoke test is the evidence for that success path.

## Security and production-readiness notes

- JWT access/refresh authentication and role checks are tested.
- WebSocket authentication uses a short-lived single-purpose ticket rather than
  an access token in the query string.
- CORS wildcard/credential combinations and weak production signing keys fail
  safely.
- Redis cache/rate-limit degradation paths are tested.
- Alembic migrations pass a real Postgres downgrade-to-base and re-upgrade
  round trip in CI.
- Docker Compose runs a one-shot migration service and passes a real multi-
  container smoke flow, including worker-written CSV read by the API container.
- Multi-worker shared/object storage remains a documented future requirement;
  local export files are not claimed to support multiple worker replicas.

## Live links

- Docs: https://devtrack-api-qhcp.onrender.com/docs
- Health: https://devtrack-api-qhcp.onrender.com/health
- Architecture: [docs/architecture.svg](architecture.svg)
- Screenshot: [live Swagger UI](screenshots/devtrack-live-api-docs.png)
