# DevTrack live verification — 2026-10-12

Target: https://devtrack-api-qhcp.onrender.com

- `/health`: 200
- `/docs`: 200
- register two users: 201, 201
- login both: 200, 200
- create project: 201
- create issue / assign: 201 / 200
- status `todo -> in_progress -> done`: 200 / 200
- add/read comment: 201 / 200
- re-read issue from a separate request: 200; state persisted
- project/status/priority/search filters: 200; expected issue returned
- dashboard summary twice: 200 / 200
- non-owner project update: 403
- non-admin user delete: 403
- non-owner issue delete: 403
- missing token / invalid token: 401 / 401
- invalid project payload: 422
- nonexistent issue: 404
- notifications: 200
- post-fix export request: 503 with safe `ServiceUnavailableException`

The live export `503` is expected for this Render service because no reachable
Redis/Celery worker is configured there. CI Compose verifies the successful
export path with real Postgres, Redis, API, and worker containers.
