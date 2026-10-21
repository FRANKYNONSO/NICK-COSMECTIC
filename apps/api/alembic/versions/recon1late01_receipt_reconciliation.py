"""goods receipts: late-entry reconciliation status and flags

Revision ID: recon1late01
Revises: cr1custreq01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "recon1late01"
down_revision = "cr1custreq01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "goods_receipts",
        sa.Column("reconciliation_status", sa.String(), nullable=False, server_default="not_required"),
    )
    op.add_column(
        "goods_receipts",
        sa.Column("reconciliation_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_goods_receipt_recon_status",
        "goods_receipts",
        "reconciliation_status IN ('not_required','clear','discrepancy_confirmed')",
    )


def downgrade():
    op.drop_constraint("ck_goods_receipt_recon_status", "goods_receipts", type_="check")
    op.drop_column("goods_receipts", "reconciliation_flags")
    op.drop_column("goods_receipts", "reconciliation_status")