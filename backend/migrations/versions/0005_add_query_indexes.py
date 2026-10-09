"""add indexes for the filtered/sorted columns the API actually queries

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08 00:00:00

Day 7 measured every query the API issues against a real Postgres with 50,000
issues, 200,000 comments and 50,000 notifications, then added an index only
where a plan showed a sequential scan on a table that grows:

  issues.project_id, status, priority, created_at   seq scan 5.1 / 7.2 / 6.5 ms at 50k rows
  comments(issue_id, created_at)                    parallel seq scan 11.4 ms at 200k rows
  notifications(user_id, created_at)                existing single-column index, sort removed
  export_jobs(status, created_at)                   the reap_stale_jobs sweep
  projects.owner_id                                 the dashboard's project lookup
  comments.user_id, issues.assigned_to              delete-cascade lookups by FK

Deliberately NOT indexed, with the measurement that decided it:
  * `issues.title ILIKE '%term%'` / `projects.title ILIKE ...` -- a leading wildcard
    cannot use a btree index at all; at 50k rows the scan is 31 ms. A trigram (pg_trgm)
    or full-text index is the real fix; that is a decision for whoever needs the speed,
    not something to add now.
  * `projects.created_at` (sort) -- 200-row table, 0.06 ms. No index.
  * `activity_logs.user_id`/`timestamp` -- write-heavy table with no read path in the
    API; the only consumer is the user-delete cascade, which is an admin action.
    Indexing it would tax every login and issue write for nothing.

Each statement is guarded so the migration is re-runnable against a database where
`create_all` already made some of these (the test suite builds its schema from the
models, not from migrations).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("ix_issues_project_id", "issues", ["project_id"]),
    ("ix_issues_status", "issues", ["status"]),
    ("ix_issues_priority", "issues", ["priority"]),
    ("ix_issues_created_at", "issues", ["created_at"]),
    ("ix_issues_assigned_to", "issues", ["assigned_to"]),
    ("ix_comments_issue_id_created_at", "comments", ["issue_id", "created_at"]),
    ("ix_comments_user_id", "comments", ["user_id"]),
    ("ix_notifications_user_id_created_at", "notifications", ["user_id", "created_at"]),
    ("ix_export_jobs_status_created_at", "export_jobs", ["status", "created_at"]),
    ("ix_projects_owner_id", "projects", ["owner_id"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, unique=False, if_not_exists=True)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
