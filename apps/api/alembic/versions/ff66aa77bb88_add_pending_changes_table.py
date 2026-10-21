"""add pending_changes table (section 11 - customer owed change)

Revision ID: ff66aa77bb88
Revises: ee55ff66aa77
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 11: "Customer is owed change"
tracking is a brand-new feature -- no prior mechanism existed for
this. Tied to the Sale/receipt ID (not a bare name string) per the
approach confirmed with the super admin, since two different customers
can share a full name and the receipt already sidesteps that
collision problem.

*** Same caveat as the migrations before this one in the chain: this
repo has had duplicate/branching revision ids in the past, so please
run `alembic heads` against your actual database before applying this
and adjust down_revision below if ee55ff66aa77 isn't really your
current head. ***
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "ff66aa77bb88"
down_revision = "ee55ff66aa77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("customer_name", sa.String(), nullable=False),
        sa.Column("customer_phone", sa.String(), nullable=True),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="outstanding"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('outstanding','settled')", name="ck_pending_change_status"),
    )
    op.alter_column("pending_changes", "status", server_default=None)


def downgrade() -> None:
    op.drop_table("pending_changes")
