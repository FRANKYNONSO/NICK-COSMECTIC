"""add transfer-refund fields and pending-approval status to refunds

Revision ID: 2bed15eb4a86
Revises: f6a7b8c9d0e2
Create Date: 2026-09-02 00:00:00.000000

Update spec ("nick-cosmetics-update-spec-3.md", Sections 7, 8 & 10):
a refund can now be paid out by transfer (not just handed over as
cash), which needs bank/account details captured and a pending
approval step for a Super Admin / Branch Manager to mark processed
within 24 hours. `action` widens from ('cash','exchange') to
('cash','transfer','exchange'); existing rows all keep meaning
exactly what they did before ('cash' stays 'cash', 'exchange' stays
'exchange') so no data migration is needed beyond the new columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2bed15eb4a86"
down_revision: Union[str, None] = "f6a7b8c9d0e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("refunds", sa.Column("bank_name", sa.String(), nullable=True))
    op.add_column("refunds", sa.Column("account_number", sa.String(), nullable=True))
    op.add_column("refunds", sa.Column("account_name", sa.String(), nullable=True))
    op.add_column(
        "refunds",
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
    )
    op.add_column(
        "refunds", sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("refunds", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("refunds", sa.Column("settlement_method", sa.String(), nullable=True))
    op.create_foreign_key(
        "refunds_approved_by_fkey", "refunds", "users", ["approved_by"], ["id"]
    )
    op.drop_constraint("ck_refund_action", "refunds", type_="check")
    op.create_check_constraint(
        "ck_refund_action", "refunds", "action IN ('cash','transfer','exchange') OR action IS NULL"
    )
    op.create_check_constraint(
        "ck_refund_status", "refunds", "status IN ('completed','pending') OR status IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint("ck_refund_status", "refunds", type_="check")
    op.drop_constraint("ck_refund_action", "refunds", type_="check")
    op.drop_constraint("refunds_approved_by_fkey", "refunds", type_="foreignkey")
    op.drop_column("refunds", "settlement_method")
    op.drop_column("refunds", "processed_at")
    op.drop_column("refunds", "approved_by")
    op.drop_column("refunds", "status")
    op.drop_column("refunds", "account_name")
    op.drop_column("refunds", "account_number")
    op.drop_column("refunds", "bank_name")
