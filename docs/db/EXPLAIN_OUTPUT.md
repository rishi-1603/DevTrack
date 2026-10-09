# Database and cache evidence — DevTrack (Day 7, 2026-10-08)

Everything here was measured on the development machine against a real PostgreSQL 17.11,
on a scratch database (`day7_dt`) seeded with 50,000 issues, 200,000 comments, 50,000
notifications, 5,000 export jobs and 20,000 activity rows — a size where a missing index
shows up as milliseconds instead of noise. Raw transcripts:

| File | Contents |
|---|---|
| `day7/raw/dt-pg-plans-before.txt` | 13 plans on the schema as it was (no Day-7 indexes) |
| `day7/raw/dt-pg-plans-after.txt` | the same 13 plans after migration 0005 + `ANALYZE` |
| `day7/raw/dt-created-at-index-experiment.txt` | DROP → measure → CREATE → measure for `ix_issues_created_at` |
| `day7/raw/dt-index-selection-experiment.txt` | when the composite indexes are actually chosen |
| `day7/raw/dt-migration-0005.txt` | upgrade → downgrade → re-upgrade on a fresh database |

## 1. Before and after, per query the API actually issues

Times are `EXPLAIN (ANALYZE, BUFFERS)` execution times on the seeded database, with
`ANALYZE` run first so the planner sees real statistics (the first run of this probe did
not, and its numbers were worse for reasons that had nothing to do with indexes).

| Endpoint / query | Before | After | What changed |
|---|---:|---:|---|
| `GET /issues?project_id=42` | 5.07 ms (Sort) | **1.30 ms** (Bitmap Heap Scan) | `ix_issues_project_id` |
| `GET /issues?project_id=42&status=todo` | 3.26 ms (Sort) | **0.62 ms** (BitmapAnd → Bitmap Heap) | the two single-column indexes, combined |
| `GET /issues?status=todo` (unbounded) | **7.16 ms** (Sort) | 11.97 ms (Backward Index Scan + Filter on 50k rows) | the planner now prefers `ix_issues_created_at`; see §2 |
| `GET /issues?priority=high` (unbounded) | **6.46 ms** (Sort) | 8.39 ms (same) | as above, same cause |
| `GET /issues?search=payment` | 31.29 ms (Sort over 50k rows) | 0.41 ms (Limit → Backward Index Scan) | *bounded* shape (see §2); unbounded stays ~24–31 ms either way |
| `GET /issues/{id}/comments` (200k rows) | 11.36 ms (Gather Merge) | **0.056 ms** (Index Scan) | `ix_comments_issue_id_created_at` |
| `GET /notifications` | 1.40 ms | 1.76 ms | unchanged shape (Sort); bounded form is 0.095 ms — §3 |
| `GET /notifications/unread-count` | 0.56 ms | 0.92 ms | same index, measurement noise at these sizes |
| `GET /projects` | 0.064 ms (Seq Scan, 50 rows) | 0.097 ms | nothing to fix at 50 rows |
| dashboard: owned project ids | 0.034 ms (Seq Scan, 50 rows) | 0.035 ms | `ix_projects_owner_id` exists but is not needed here |
| dashboard: issue count for owned projects | 8.95 ms | **0.255 ms** (Index Only Scan) | `ix_issues_project_id` — the count is answered from the index |
| dashboard: completed count | 4.07 ms | **1.28 ms** (Bitmap Heap + Filter) | same index |
| dashboard: high-priority count | 3.85 ms | **1.00 ms** | same index |
| `reap_stale_jobs` sweep (export_jobs) | 0.77 ms | 1.08 ms → **0.513 ms** after reseeding | see §3 — the first "after" number was my own seed's fault |
| dashboard summary, as a whole | 5 statements | 5 statements | the filter/sort indexes changed the *cost*, not the number of round trips |

Two of the eleven shapes got *slower* and they are in the table on purpose (§2).

## 2. `ix_issues_created_at`: measured, kept, and the caveat that comes with it

Every list query ends in `ORDER BY created_at DESC`. The index was tested by dropping it,
re-planning, recreating it and re-planning (`dt-created-at-index-experiment.txt`):

| Query shape | without the index | with the index |
|---|---:|---:|
| `status='todo' ORDER BY created_at DESC` (unbounded) | 6.92 ms (Sort) | 10.66 ms (Backward Index Scan) |
| `priority='high' ORDER BY created_at DESC` (unbounded) | 6.62 ms (Sort) | 7.90 ms (Backward Index Scan) |
| `status='todo' ... LIMIT 50` | 4.67 ms | **0.048 ms** |
| `title ILIKE '%payment%' ... LIMIT 50` | 32.88 ms | **0.27–0.49 ms** |

**Kept, with the trade-off stated rather than hidden.** Where a `LIMIT` bounds the scan the
index is 10–100× faster because the planner reads 50 index entries backwards instead of
sorting every match. Where nothing bounds it, the planner walks the whole index backwards
and filters, which costs about 1–5 ms more than a sort at 50k rows — and it takes over the
plan even then, because it believes the sort is dearer than it is.

The reason this is the right trade for *this* app is what the shapes are: the unbounded
cases only happen because no endpoint paginates — which is itself a defect, now registered:

> **D7-1 (Med): `GET /issues` has no pagination at all.** `list_issues` builds
> `select(...).order_by(created_at.desc())` with no limit, so on the seeded database a
> single request materialises **16,666 rows** for `status=todo` (and 50,000 for no filter).
> The measured fix is a `LIMIT`: same query, 11.97 ms → 0.048 ms at `LIMIT 50`, i.e. the
> endpoint's cost is dominated by how much it sends, not by which index it uses. Left for
> the performance day (Day 9) because it changes the response contract (`IssueList`), and
> that change deserves its own tests and its own README line rather than being smuggled in
> with the indexes.

Indexes not added, each with the number that decided it (also recorded in the migration's
docstring so the reasoning survives in the repo, not only here):

* `ILIKE '%term%'` search — a leading wildcard cannot use a btree at all; 31 ms at 50k rows
  with or without any index here. `pg_trgm`/full-text is the real fix and a separate decision.
* `projects.created_at`, `projects.title` — 50–200 rows, 0.06–0.10 ms. An index on a table
  this size is a cost with no benefit.
* `activity_logs.user_id` / `timestamp` — written on every login and issue change, read by
  nothing in the API (only the admin delete cascade). Indexing it would tax every write.

## 3. Composite indexes are only chosen when the query is bounded

The two composite indexes in 0005 were the interesting case, because a composite index whose
first column is selective is not automatically used:

* `ix_notifications_user_id_created_at` — the shipped query (`WHERE user_id = 7 ORDER BY
  created_at DESC`, no limit) keeps its Sort: 1.10–1.76 ms, the planner walks 1,000 rows and
  sorts them. Add `LIMIT 50` and the same index is chosen immediately: **0.095 ms**, and
  0.059 ms after `ANALYZE`. So the index is earning its keep exactly in the shape the
  endpoint should have (D7-1), and correctly not being forced into the shape it has.
* `ix_export_jobs_status_created_at` — the first "after" measurement showed a Seq Scan
  (1.08 ms) and I nearly recorded that as the index being wrong. It was **my seed**: 50% of
  the seeded export jobs matched `status IN ('pending','running')`, which no index can help
  with, because the sweep then matches half the table. Reseeded to a realistic 95%-terminal
  distribution (`id % 20`) and `ANALYZE`d, the same query becomes a Bitmap Heap Scan at
  **0.513 ms** (index condition `status`, recheck `created_at`), and 0.276 ms without the
  `created_at` filter. The lesson recorded in `dt-index-selection-experiment.txt`: a plan
  that looks wrong on a uniform seed may be a seed artefact — check the data distribution
  before blaming the index.
* `ix_comments_issue_id_created_at` — the clearest win of the day: 11.36 ms → 0.056 ms on
  200k comments, because it answers both the filter and the sort with one Index Scan.

## 4. Migration 0005, round-tripped

`day7/raw/dt-migration-0005.txt`, on a database created from scratch by Alembic:

* `upgrade head` → 23 `ix_*` indexes present, `alembic_version = 0005 (head)`.
* `downgrade base` → all app tables and indexes dropped; only `alembic_version` remains.
* `upgrade head` again → back to `0005 (head)`, same index set.

CI already runs this cycle in the `postgres-migration-check` job (upgrade → downgrade base →
re-upgrade), so the migration's own reversibility is checked on every push; what Day 7 added
is the human-readable transcript above. Ten indexes are created: the four single-column
`issues` indexes, `comments(issue_id, created_at)`, `comments.user_id`,
`notifications(user_id, created_at)`, `export_jobs(status, created_at)`, `projects.owner_id`,
`issues.assigned_to` (the last two back the delete-cascade lookups).

## 5. Redis: every key, its TTL, and what invalidates it

| Key | Written by | TTL | Invalidated by |
|---|---|---|---|
| `dashboard:summary:{user_id}` | `dashboard_service.get_dashboard_summary` | 60 s (`DASHBOARD_CACHE_TTL_SECONDS`) | `invalidate_dashboard_cache(user_id)` — called on issue create/update/delete, status change, project create/delete, comment create/delete. Tests: create / status-change / project-create invalidate; assignment deliberately does not (it is not in the summary) |
| `devtrack:dead_letter:export_jobs` | `dead_letter.push_dead_letter` | 7 days, sliding (refreshed by each push) | `LTRIM` to 500 entries on every push + the sliding TTL + `clear_dead_letters()` for an operator who has replayed the list |
| `ratelimit:{scope}:{window}` | `rate_limit.is_allowed` | the window (60 s etc.), set on each hit | expiry only — correct for a counter; the key is window-scoped (`{window}` = `time // window_seconds`) so a stale key can outlive its window by at most one window and next window uses a different key |

`delete_cache` existed in `utils/cache.py` and was imported by nothing before Day 7: the
dashboard cache was written with a TTL and never invalidated, so every user saw stale counts
for up to a minute after any change. The interesting part of the fix is not the call sites,
it is the choice to invalidate *exactly* the affected owner's key (the summary is per-owner)
rather than flushing the cache, and to document the three things that deliberately do not
invalidate it — the summary contains only project/issue counts, so notifications, comments
and assignment changes cannot make it stale.

Fail-open behaviour is now tested rather than asserted in a docstring
(`app/tests/test_cache_degradation.py`, 14 tests): with a client whose every call raises,
cache reads are misses, writes are no-ops, `dead_letter_count()` returns −1 (not 0 — "down"
must not look like "nothing failed"), `push_dead_letter` returns False, and
`GET /dashboard/summary` still answers with database numbers.

## 6. N+1: what was found and what now holds it

`app/tests/test_db_query_efficiency.py` (22 tests) counts the SQL statements a real
request issues, by hooking SQLAlchemy's `before_cursor_execute`:

* `GET /issues` — the assignee N+1 was real: 25 issues in one response took **27 statements**
  vs 7 for 5 (the numbers the failing test printed before the fix). `selectinload(Issue.assignee)`
  makes it one extra statement for the page: now the 25-row request issues the same number
  as the 5-row request.
* `GET /issues/{id}/comments` and `GET /projects` — same test shape, same guarantee.
* The identity-map trap is documented in the test file: a suite that reuses one user object
  across rows measures nothing, because the second lazy load of the same object is answered
  from the session's identity map. The test seeds **25 distinct users** so an N+1 cannot hide.
* The tests fail against the pre-fix code (`day7` transcript in the Day-7 findings), which is
  the only way a query-count test is worth anything.

## 6a. Session lifecycle and pooling

* One session per request, closed in the dependency's `finally`. `app/tests/test_session_lifecycle.py`
  makes that countable rather than assumed: with the `finally` removed, the test that fires a
  request which raises unhandled fails (checked both ways as a negative control); with it in
  place, every created session is closed even on the 500 path.
* Rollback on error: a rejected create leaves no row (`test_a_failed_request_leaves_no_partial_state`),
  and a real database error mid-request does not affect later requests. The second property
  has two mechanisms behind it — the dependency's close and SQLAlchemy's pool reset on return —
  and the test says so rather than pretending one of them is the reason.
* Pool sizing: this project uses SQLAlchemy defaults (`QueuePool`, 5 connections + 10 overflow)
  for Postgres; nothing overrides it. Stated rather than tuned: at this scale the API would
  need 15 concurrent requests talking to Postgres at once before the pool mattered, and the
  measured database work per request is single-digit milliseconds (§1).

## 7. Suite and numbers quoted from this state

* `pytest app/tests --cov=app` → **173 passed, 2 xfailed, 97% total** (1374 statements, 44 missed), ruff clean.
* Coverage is measured over production code only — `.coveragerc` omits `app/tests/`, because
  the test modules live inside the package they measure (the long-quoted 94% was measured
  with the tests counting as covered code).
* The two xfails are registered defects D1-2 and D3-1, not Day-7 work.
