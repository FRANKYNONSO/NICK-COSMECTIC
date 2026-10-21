"""add storefront orders, order-sale links and storefront coupons

Only creates NEW tables; no existing table is altered.

Revision ID: sf1sf2sf3sf4
Revises: bg1bg2bg3bg4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sf1sf2sf3sf4"
down_revision = "bg1bg2bg3bg4"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "storefront_orders",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("reference", sa.String(), nullable=False, unique=True),
        sa.Column("customer_id", _uuid(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("customer_name", sa.String(), nullable=False),
        sa.Column("customer_phone", sa.String(), nullable=False),
        sa.Column("customer_email", sa.String(), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=False),
        sa.Column("delivery_city", sa.String(), nullable=True),
        sa.Column("delivery_lga", sa.String(), nullable=True),
        sa.Column("delivery_state", sa.String(), nullable=False),
        sa.Column("shipping_fee_kobo", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("shipping_note", sa.Text(), nullable=True),
        sa.Column("subtotal_kobo", sa.BigInteger(), nullable=False),
        sa.Column("discount_kobo", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_kobo", sa.BigInteger(), nullable=False),
        sa.Column("coupon_code", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("waybill_ref", sa.String(), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("status_updated_by", _uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('new','processing','waybilled','delivered','cancelled')",
            name="ck_storefront_order_status",
        ),
    )
    op.create_index("ix_storefront_orders_status", "storefront_orders", ["status"])
    op.create_index("ix_storefront_orders_created_at", "storefront_orders", ["created_at"])

    op.create_table(
        "storefront_order_sales",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("order_id", _uuid(), sa.ForeignKey("storefront_orders.id"), nullable=False),
        sa.Column("sale_id", _uuid(), sa.ForeignKey("sales.id"), nullable=False, unique=True),
        sa.Column("branch_id", _uuid(), sa.ForeignKey("branches.id"), nullable=False),
    )
    op.create_index("ix_storefront_order_sales_order_id", "storefront_order_sales", ["order_id"])
    op.create_index("ix_storefront_order_sales_branch_id", "storefront_order_sales", ["branch_id"])

    op.create_table(
        "storefront_coupons",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("discount_amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("issued_by", _uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("redeemed_order_id", _uuid(), sa.ForeignKey("storefront_orders.id"), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('active','redeemed','voided','expired')",
            name="ck_storefront_coupon_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("storefront_coupons")
    op.drop_index("ix_storefront_order_sales_branch_id", table_name="storefront_order_sales")
    op.drop_index("ix_storefront_order_sales_order_id", table_name="storefront_order_sales")
    op.drop_table("storefront_order_sales")
    op.drop_index("ix_storefront_orders_created_at", table_name="storefront_orders")
    op.drop_index("ix_storefront_orders_status", table_name="storefront_orders")
    op.drop_table("storefront_orders")
