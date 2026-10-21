"""customer requests (deposit pre-orders tied to the restock list)

Revision ID: cr1custreq01
Revises: pwreset5req01
Create Date: 2026-10-10

A customer asks the shop to source an item and pays a deposit. The row
survives the Restock List entry being deleted on PDF download
(restock_list_entry_id is ON DELETE SET NULL). See CustomerRequest in
models.py for the lifecycle.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "cr1custreq01"
down_revision = ("pwreset5req01", "whstk2uniq01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column(
            "restock_list_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("restock_list_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("item_name", sa.String(), nullable=False),
        sa.Column("item_image_url", sa.String(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_label", sa.String(), nullable=False, server_default="pieces"),
        sa.Column("customer_name", sa.String(), nullable=False),
        sa.Column("customer_phone", sa.String(), nullable=False),
        sa.Column("customer_address", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("deposit_kobo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("hold_pieces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deposit_refunded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolution_note", sa.String(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','arrived','collected','cancelled')",
            name="ck_customer_request_status",
        ),
    )
    op.create_index("ix_customer_requests_branch_status", "customer_requests", ["branch_id", "status"])
    op.create_index("ix_customer_requests_product_id", "customer_requests", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_requests_product_id", table_name="customer_requests")
    op.drop_index("ix_customer_requests_branch_status", table_name="customer_requests")
    op.drop_table("customer_requests")
