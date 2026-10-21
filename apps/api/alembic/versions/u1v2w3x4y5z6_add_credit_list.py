"""add credit_entries and credit_payments tables

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-08-26 00:00:00.000000

Store-credit ledger: a customer can leave with goods and settle later.
Deliberately no branch/role-scoped visibility beyond ordinary auth --
every worker, plus whichever role is designated "auditor" via Roles,
can see the whole list per spec.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "u1v2w3x4y5z6"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("customer_full_name", sa.String(), nullable=False),
        sa.Column("customer_phone", sa.String(), nullable=False),
        sa.Column("customer_address", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="outstanding"),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("opened_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('outstanding','partially_paid','paid')", name="ck_credit_status"),
    )
    op.create_index("ix_credit_entries_branch_id", "credit_entries", ["branch_id"])
    op.create_index("ix_credit_entries_status", "credit_entries", ["status"])

    op.create_table(
        "credit_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("credit_entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("credit_entries.id"), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(), nullable=False, server_default="cash"),
        sa.Column("received_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_credit_payments_credit_entry_id", "credit_payments", ["credit_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_credit_payments_credit_entry_id", table_name="credit_payments")
    op.drop_table("credit_payments")
    op.drop_index("ix_credit_entries_status", table_name="credit_entries")
    op.drop_index("ix_credit_entries_branch_id", table_name="credit_entries")
    op.drop_table("credit_entries")
