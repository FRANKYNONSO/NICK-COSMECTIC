"""performance records: metric_snapshots table; drop financial_tracking_periods

Revision ID: ff77aa88bb99
Revises: ee22ff33gg44
Create Date: 2026-09-12 00:00:00.000000

Dashboard redesign implementation prompt, step 1 + step 4:

  - Step 1 removes the "reset tracking" concept entirely -- resetting
    zeroed out permanent history, which is exactly the problem this
    migration's replacement (metric_snapshots, below) is designed not
    to have. financial_tracking_periods only ever existed to back that
    reset feature (see bb88cc99dd00_add_financial_tracking_periods.py)
    and nothing else reads it once the reset UI/endpoints are gone
    (see api/v1/routers/reports.py), so it's dropped outright rather
    than left as an orphaned table.

  - Step 4 adds metric_snapshots: one row per (branch_id, metric_type,
    day). Only day-granularity rows are ever stored -- weekly/monthly/
    yearly/all-time are computed with SUM(value) GROUP BY
    date_trunc(...) over this table at read time, so there's no
    separate weekly/monthly/yearly row that could ever drift out of
    sync with the daily rows underneath it. branch_id is nullable to
    allow a future "all branches" rollup row without changing the
    schema, though the current daily job (see main.py) only ever
    writes one row per real branch.

*** Same caveat as every migration before this one in the chain: this
repo has had duplicate/branching revision ids in the past, so please
run `alembic heads` against your actual database before applying this
and adjust down_revision below if ee22ff33gg44 isn't really your
current head. ***
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ff77aa88bb99"
down_revision: Union[str, None] = "ee22ff33gg44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("metric_type", sa.String(), nullable=False),
        # Always a day boundary -- this table only ever stores DAY
        # rows (see docstring above and the daily rollup job in
        # main.py). Named period_start rather than "date" so it reads
        # consistently next to the weekly/monthly/yearly views that
        # GROUP BY date_trunc(...) over it at read time.
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "metric_type IN ('revenue','inventory_investment','sales','losses')",
            name="ck_metric_snapshot_type",
        ),
    )
    # branch_id is part of the uniqueness key (not excluded when NULL)
    # so a future "all branches" rollup row and each individual
    # branch's own row for the same day/metric can coexist without
    # colliding -- Postgres treats NULL as distinct from any other
    # NULL for uniqueness purposes, which is exactly what's wanted
    # here since the daily job (main.py) only ever writes real
    # per-branch rows today.
    op.create_unique_constraint(
        "uq_metric_snapshot_branch_type_period",
        "metric_snapshots",
        ["branch_id", "metric_type", "period_start"],
    )
    op.create_index(
        "ix_metric_snapshot_type_period", "metric_snapshots", ["metric_type", "period_start"]
    )

    op.drop_table("financial_tracking_periods")


def downgrade() -> None:
    op.create_table(
        "financial_tracking_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_revenue_kobo", sa.BigInteger(), nullable=True),
        sa.Column("total_invested_kobo", sa.BigInteger(), nullable=True),
        sa.Column("reset_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reset_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO financial_tracking_periods (id, started_at, ended_at, created_at) "
            "VALUES (:id, '2000-01-01T00:00:00+00:00', NULL, now())"
        ),
        {"id": str(uuid.uuid4())},
    )

    op.drop_index("ix_metric_snapshot_type_period", table_name="metric_snapshots")
    op.drop_constraint("uq_metric_snapshot_branch_type_period", "metric_snapshots", type_="unique")
    op.drop_table("metric_snapshots")
