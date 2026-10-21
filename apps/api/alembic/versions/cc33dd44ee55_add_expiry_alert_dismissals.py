"""add expiry_alert_dismissals table (section 6 push notifications)

Revision ID: cc33dd44ee55
Revises: bb22cc33dd44
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 6: expiry alerts become a real
push notification (super admin, branch manager, every staff member at
the affected branch) instead of only a dashboard KPI number. This
table just tracks which near-expiry StockBatch a given user has
already dismissed -- the alert set itself is computed fresh on every
request from the existing expiry_report() query, there's no separate
per-recipient notification row to generate or clean up.

*** Same caveat as bb22cc33dd44/aa11bb22cc33: this repo has had
duplicate/branching revision ids in the past, so please run
`alembic heads` against your actual database before applying this and
adjust down_revision below if bb22cc33dd44 isn't really your current
head. ***
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "cc33dd44ee55"
down_revision = "bb22cc33dd44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expiry_alert_dismissals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "stock_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_batches.id"), nullable=False
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "stock_batch_id", name="uq_expiry_dismissal_user_batch"),
    )


def downgrade() -> None:
    op.drop_table("expiry_alert_dismissals")
