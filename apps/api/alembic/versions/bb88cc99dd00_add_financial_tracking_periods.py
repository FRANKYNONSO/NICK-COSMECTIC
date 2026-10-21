"""add financial_tracking_periods table (section 14)

Revision ID: bb88cc99dd00
Revises: aa77bb88cc99
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 14: Total Invested / Total Revenue
become pausable/reset-able, triggered manually by the super admin,
with each reset snapshotting into a history log rather than wiping the
running totals with no record.

The initial row is seeded starting at 2000-01-01 (matching the '2000-
01-01' hardcoded elsewhere in the pre-existing dashboard code as its
own "all time" stand-in) rather than "now", so every existing invoice/
sale keeps counting toward the dashboard's current totals exactly as
it did before this feature existed -- nothing changes for anyone until
a super admin actually triggers the first reset.

*** Same caveat as the migrations before this one in the chain: this
repo has had duplicate/branching revision ids in the past, so please
run `alembic heads` against your actual database before applying this
and adjust down_revision below if aa77bb88cc99 isn't really your
current head. ***
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "bb88cc99dd00"
down_revision = "aa77bb88cc99"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("financial_tracking_periods")
