#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Compose-stack smoke test for DevTrack.
#
#     COMPOSE_CMD="docker compose" scripts/compose_smoke_test.sh
#
# Runs on the CI RUNNER (not inside a container) against the ports the compose
# file publishes. That is a deliberate choice, and the opposite of the sibling
# CertiFake project -- there the test must run inside api-gateway because it
# needs the app image's own Pillow to render an image for OCR to read. Here the
# app image has no HTTP client (backend/Dockerfile installs only gcc and
# libpq-dev on python:3.12-slim-trixie: no curl, wget or nc), the requests are
# plain JSON, and the runner already has curl. Installing a Python HTTP stack
# on the runner to duplicate what curl does would add a dependency for no
# gain -- and driving the published port is the more realistic path anyway,
# because it also proves the port mapping works.
#
# WHAT THIS ADDS OVER THE EXISTING real-infra-smoke-test CI JOB
# ------------------------------------------------------------
# That job already drives this exact HTTP sequence (register -> login ->
# project -> issue -> async export -> poll -> download -> grep) against real
# Postgres and real Redis, and it passes. But it starts uvicorn and celery as
# bare processes ON THE RUNNER with GitHub Actions service containers, and it
# runs `alembic upgrade head` as its own manual step first. So it has never
# tested the deployment artifact: not backend/docker-compose.yml, not
# backend/Dockerfile's CMD, not the service-name networking, not the health
# checks, and not the shared volume between the two app containers.
#
# This script tests exactly those, which is also how it found a real bug: the
# compose stack had NO schema-creation step at all (see the `migrate` service
# in backend/docker-compose.yml for the full story), so `docker compose up`
# brought the API up against an empty Postgres. The steps below assert the
# things that were previously unverified:
#
#   1. the one-shot migrate container ran and exited 0;
#   2. the schema it created is actually present in the db container -- the 7
#      tables from migrations/versions/0001..0004;
#   3. the api container reached Docker's own `healthy` state, which is the
#      first empirical confirmation that its healthcheck works (until now that
#      was only reasoned about by verifying `python` exists in the image);
#   4. the full HTTP flow works through the containers;
#   5. the export CSV written by the WORKER container is readable by the API
#      container, in both containers' own filesystem view. This is the
#      assertion that only compose can make: with two separate containers it
#      passes only because of the shared `devtrack_exports` volume. Running
#      both processes on one machine -- as the other job does, and as
#      development did -- cannot fail this way, which is precisely why the
#      volume's necessity was documented but never demonstrated.
#
# Exit 0 = stack verified. Non-zero = something is broken; the calling CI job
# dumps `docker compose logs` on failure.
# ---------------------------------------------------------------------------
set -euo pipefail

COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"
BASE="${SMOKE_BASE_URL:-http://localhost:8000}"
# Container names are fixed by `container_name:` in the compose file, so these
# can be inspected directly instead of parsing `compose ps` JSON.
MIGRATE_CONTAINER=devtrack_migrate
API_CONTAINER=devtrack_api
API_READY_TRIES="${SMOKE_API_TRIES:-60}"
EXPORT_TRIES="${SMOKE_EXPORT_TRIES:-60}"

# The 7 tables defined in app/database/models.py and created by
# migrations/versions/0001_initial_schema.py .. 0004_add_export_job_attempts.py.
EXPECTED_TABLES="activity_logs comments export_jobs issues notifications projects users"

pass() { printf '  [ OK ]   %s\n' "$1"; }
info() { printf '  [ .. ]   %s\n' "$1"; }
die()  { printf '  [FAIL]   %s\n' "$1" >&2; exit 1; }

# `cd` to the directory holding the compose file, resolved from this script's
# own location so the script works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../backend"

echo "=== 1. one-shot migrate container completed ==="
# `up --wait` already treats a non-zero exit here as fatal (api/worker depend
# on it with service_completed_successfully), so this is a belt-and-braces
# assertion that also produces a readable message in the log.
MIGRATE_EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$MIGRATE_CONTAINER" 2>/dev/null || echo missing)"
[ "$MIGRATE_EXIT" = "0" ] \
  || die "migrate container exited $MIGRATE_EXIT (want 0). Schema was never created; every DB request will 500. See: $COMPOSE_CMD logs migrate"
pass "migrate exited 0"

echo "=== 2. schema actually exists in the db container ==="
TABLES="$($COMPOSE_CMD exec -T db psql -U devtrack -d devtrack -Atc \
  "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1" | tr '\n' ' ' | xargs)"
info "tables found: ${TABLES:-<none>}"
for t in $EXPECTED_TABLES; do
  case " $TABLES " in
    *" $t "*) ;;
    *) die "table '$t' missing after migration. Found: ${TABLES:-<none>}" ;;
  esac
done
pass "all 7 expected tables present (migrations ran inside the stack, not on the runner)"

echo "=== 3. api container reached Docker 'healthy' ==="
API_HEALTH="$(docker inspect -f '{{.State.Health.Status}}' "$API_CONTAINER" 2>/dev/null || echo missing)"
[ "$API_HEALTH" = "healthy" ] \
  || die "api container health is '$API_HEALTH' (want healthy). Its healthcheck probes http://127.0.0.1:8000/health with python/urllib."
pass "api healthcheck reports healthy"

echo "=== 4. API is reachable on the published port ==="
for i in $(seq 1 "$API_READY_TRIES"); do
  if curl -sf "$BASE/health" > /dev/null 2>&1; then
    pass "GET $BASE/health -> 200 (attempt $i)"
    break
  fi
  [ "$i" = "$API_READY_TRIES" ] && die "API never answered on $BASE after $API_READY_TRIES tries"
  sleep 1
done

echo "=== 5. full request flow through the containers ==="
EMAIL="compose-smoke-$(date +%s)@example.com"
PASSWORD="password123"

curl -sf -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Compose Smoke\",\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" > /dev/null \
  || die "POST /auth/register failed"
pass "registered $EMAIL"

# Form-encoded, matching the OAuth2PasswordRequestForm the login route expects
# (same shape the real-infra-smoke-test job already uses successfully).
TOKEN="$(curl -sf -X POST "$BASE/auth/login" \
  -d "username=$EMAIL&password=$PASSWORD" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")"
[ -n "$TOKEN" ] || die "login returned no access_token"
pass "logged in, JWT issued"

PROJECT_ID="$(curl -sf -X POST "$BASE/projects" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"Compose Smoke Project","description":"created by compose-smoke-test.sh"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")"
[ -n "$PROJECT_ID" ] || die "POST /projects returned no id"
pass "created project $PROJECT_ID (api -> db by service name)"

curl -sf -X POST "$BASE/issues" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"title\":\"Compose smoke issue\",\"description\":\"x\",\"project_id\":$PROJECT_ID,\"priority\":\"medium\"}" \
  > /dev/null || die "POST /issues failed"
pass "created issue in that project"

echo "=== 6. async export: api -> Redis broker -> celery worker -> shared volume -> api ==="
JOB_ID="$(curl -sf -X POST "$BASE/projects/$PROJECT_ID/export" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")"
[ -n "$JOB_ID" ] || die "POST /projects/$PROJECT_ID/export returned no job id"
info "export job $JOB_ID queued"

STATUS="pending"
for i in $(seq 1 "$EXPORT_TRIES"); do
  STATUS="$(curl -sf "$BASE/export-jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")"
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && die "export job failed in the worker. See: $COMPOSE_CMD logs worker"
  sleep 1
done
[ "$STATUS" = "completed" ] \
  || die "export job never completed (status=$STATUS after ${EXPORT_TRIES}s). The worker is not consuming from Redis, or crashed."
pass "worker consumed the task and completed the export"

echo "=== 7. the CSV the WORKER wrote is visible to the API container ==="
# This is the assertion that only separate containers can make, and it is the
# one that gives ExportJob's documented "known limitation" real teeth:
# app/database/models.py notes that file_path "points at a file on the local
# filesystem of whichever worker produced it". With api and worker as two
# containers, that path is only resolvable by the API because both mount the
# shared `devtrack_exports` volume at /app/exports. app/tasks/export_tasks.py
# resolves EXPORT_DIR there (DEVTRACK_EXPORT_DIR is not set in the compose
# file). Running both processes on one host -- as development did, and as the
# real-infra-smoke-test job does -- cannot fail this way, which is exactly why
# the volume's necessity was documented but never demonstrated until now.
#
# The path is read from Postgres rather than reconstructed from the
# export_<job_id>.csv naming convention, so this asserts what the worker
# actually recorded instead of what this script assumes it recorded.
# (file_path is deliberately NOT in the ExportJobRead response schema -- an
# internal filesystem path should not be leaked to API clients -- so the DB is
# the only place to read it from.)
RECORDED_PATH="$($COMPOSE_CMD exec -T db psql -U devtrack -d devtrack -Atc \
  "SELECT file_path FROM export_jobs WHERE id=$JOB_ID")"
[ -n "$RECORDED_PATH" ] && [ "$RECORDED_PATH" != "" ] \
  || die "export_jobs.file_path is empty for job $JOB_ID -- the worker completed the job without recording where it wrote the CSV"
info "worker recorded file_path: $RECORDED_PATH"

# The recorded value is UNNORMALIZED, and this was found by running the test
# rather than by reading the code: app/tasks/export_tasks.py builds EXPORT_DIR
# with os.path.join(os.path.dirname(__file__), "..", "..", "exports") and never
# calls normpath, so what lands in the database is literally
# `/app/app/tasks/../../exports/export_<id>.csv`.
#
# That is not a bug -- the kernel resolves `..` so the file opens fine, and the
# download endpoint serves it correctly -- but it does mean a naive string
# prefix check against "/app/exports/" is wrong, which is exactly the mistake
# the first run of this script made. Comparing resolved paths instead.
RESOLVED_PATH="$(python3 -c "import os,sys; print(os.path.normpath(sys.argv[1]))" "$RECORDED_PATH")"
info "resolved: $RESOLVED_PATH"
case "$RESOLVED_PATH" in
  /app/exports/*) ;;
  *) die "worker's export resolved to '$RESOLVED_PATH', which is NOT under /app/exports -- the shared devtrack_exports volume would not cover it and the api container could not serve the download" ;;
esac

for svc in worker api; do
  # Checked with the path exactly as the worker recorded it, not the normalized
  # one: the point is that the API can open the very string the worker stored.
  $COMPOSE_CMD exec -T "$svc" test -s "$RECORDED_PATH" \
    || die "$svc container cannot see $RECORDED_PATH -- the shared devtrack_exports volume is not working"
  pass "$svc container sees $RECORDED_PATH"
done

echo "=== 8. download through the API returns that file's contents ==="
curl -sf "$BASE/export-jobs/$JOB_ID/download" -H "Authorization: Bearer $TOKEN" -o /tmp/devtrack-compose-export.csv \
  || die "GET /export-jobs/$JOB_ID/download failed (api could not read the worker's file)"
[ -s /tmp/devtrack-compose-export.csv ] || die "downloaded export is empty"
grep -q "Compose smoke issue" /tmp/devtrack-compose-export.csv \
  || die "exported CSV does not contain the issue created above; contents: $(head -c 300 /tmp/devtrack-compose-export.csv)"
pass "CSV downloaded via the api container and contains the issue created in step 5"

echo
echo "PASS: DevTrack compose stack verified end to end"
echo "      (migrate one-shot -> schema -> healthy api -> auth -> project/issue ->"
echo "       Redis -> celery worker -> shared volume -> api download)"
