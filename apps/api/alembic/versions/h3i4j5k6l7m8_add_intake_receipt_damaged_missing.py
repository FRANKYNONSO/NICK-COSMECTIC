# _column_exists_guard_applied
"""add damaged_count and missing_count columns to intake_receipts

Revision ID: h3i4j5k6l7m8
Revises: g2b3c4d5e6f7
Create Date: 2026-08-20 07:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "h3i4j5k6l7m8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "damaged_count" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("intake_receipts")]:
        op.add_column(
            "intake_receipts",
            sa.Column("damaged_count", sa.Integer(), nullable=True),
        )
    if "missing_count" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("intake_receipts")]:
        op.add_column(
            "intake_receipts",
            sa.Column("missing_count", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("intake_receipts", "missing_count")
    op.drop_column("intake_receipts", "damaged_count")
