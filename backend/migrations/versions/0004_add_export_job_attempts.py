"""add export_jobs.attempts

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27 00:00:00

Adds the column backing real Celery retry support (Day 4). Before this,
`generate_project_export` declared `max_retries=2` but contained no
`self.retry()` call, so no job was ever actually retried and there was
nothing worth counting.

`server_default="0"` (not just a Python-side default) so existing rows get a
real value during the ALTER rather than NULL-then-backfill -- the column is
NOT NULL and Postgres would otherwise reject adding it to a populated table.
Existing pre-retry rows legitimately read as 0 attempts-recorded; they are
historical and terminal (completed/failed) so nothing re-runs them.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "export_jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("export_jobs", "attempts")
