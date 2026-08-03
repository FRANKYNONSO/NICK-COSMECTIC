"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("address", sa.String, nullable=True),
        sa.Column("phone", sa.String, nullable=True),
        sa.Column("is_warehouse", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("full_name", sa.String, nullable=False),
        sa.Column("email", sa.String, unique=True, nullable=True),
        sa.Column("phone", sa.String, unique=True, nullable=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('super_admin','branch_manager','staff')", name="ck_user_role"),
    )

    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, unique=True, nullable=False),
    )

    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=True),
    )

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sku", sa.String, unique=True, nullable=False),
        sa.Column("barcode", sa.String, unique=True, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("unit_of_measure", sa.String, nullable=False, server_default="each"),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','discontinued','draft')", name="ck_product_status"),
    )

    op.create_table(
        "product_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("variant_name", sa.String, nullable=False),
        sa.Column("sku", sa.String, unique=True, nullable=False),
        sa.Column("barcode", sa.String, unique=True, nullable=True),
        sa.Column("cost_price_kobo", sa.BigInteger, nullable=False),
        sa.Column("selling_price_kobo", sa.BigInteger, nullable=False),
        sa.Column("reorder_level", sa.Integer, nullable=False, server_default="5"),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "product_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("url", sa.String, nullable=False),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("contact_phone", sa.String, nullable=True),
        sa.Column("contact_email", sa.String, nullable=True),
        sa.Column("address", sa.String, nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="draft"),
        sa.Column("ordered_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_at", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','ordered','partially_received','received','cancelled')",
            name="ck_po_status",
        ),
    )

    op.create_table(
        "purchase_order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "purchase_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id"),
            nullable=False,
        ),
        sa.Column(
            "variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_variants.id"), nullable=False
        ),
        sa.Column("quantity_ordered", sa.Integer, nullable=False),
        sa.Column("quantity_received", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unit_cost_kobo", sa.BigInteger, nullable=False),
    )

    op.create_table(
        "stock_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_variants.id"), nullable=False
        ),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column(
            "purchase_order_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_order_items.id"),
            nullable=True,
        ),
        sa.Column("batch_number", sa.String, nullable=True),
        sa.Column("quantity_received", sa.Integer, nullable=False),
        sa.Column("quantity_remaining", sa.Integer, nullable=False),
        sa.Column("unit_cost_kobo", sa.BigInteger, nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "stock_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "stock_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_batches.id"), nullable=False
        ),
        sa.Column("adjusted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("quantity_delta", sa.Integer, nullable=False),
        sa.Column("reason", sa.String, nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IN ('damaged','expired','theft','recount','other')", name="ck_adjustment_reason"
        ),
    )

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("full_name", sa.String, nullable=True),
        sa.Column("email", sa.String, unique=True, nullable=True),
        sa.Column("phone", sa.String, unique=True, nullable=True),
        sa.Column("password_hash", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "customer_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("label", sa.String, nullable=True),
        sa.Column("address_line", sa.String, nullable=False),
        sa.Column("city", sa.String, nullable=True),
        sa.Column("state", sa.String, nullable=True),
        sa.Column("country", sa.String, nullable=False, server_default="Nigeria"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "sales",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("served_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="completed"),
        sa.Column("subtotal_kobo", sa.BigInteger, nullable=False),
        sa.Column("discount_kobo", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_kobo", sa.BigInteger, nullable=False),
        sa.Column("payment_method", sa.String, nullable=True),
        sa.Column("local_ref_id", sa.String, unique=True, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("channel IN ('pos','website','mobile')", name="ck_sale_channel"),
        sa.CheckConstraint(
            "status IN ('completed','refunded','partially_refunded','cancelled')", name="ck_sale_status"
        ),
        sa.CheckConstraint(
            "payment_method IN ('cash','card','transfer','online')", name="ck_sale_payment_method"
        ),
    )

    op.create_table(
        "sale_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column(
            "variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_variants.id"), nullable=False
        ),
        sa.Column(
            "stock_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_batches.id"), nullable=True
        ),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price_kobo", sa.BigInteger, nullable=False),
        sa.Column("line_total_kobo", sa.BigInteger, nullable=False),
    )

    op.create_table(
        "refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("processed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("amount_kobo", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("entity_type", sa.String, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("log_metadata", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "sync_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.String, nullable=False),
        sa.Column("entity_type", sa.String, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("operation IN ('insert','update','delete')", name="ck_sync_operation"),
        sa.CheckConstraint("status IN ('pending','applied','conflict','failed')", name="ck_sync_status"),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("sync_log")
    op.drop_table("audit_logs")
    op.drop_table("refunds")
    op.drop_table("sale_items")
    op.drop_table("sales")
    op.drop_table("customer_addresses")
    op.drop_table("customers")
    op.drop_table("stock_adjustments")
    op.drop_table("stock_batches")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("suppliers")
    op.drop_table("product_images")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_table("brands")
    op.drop_table("users")
    op.drop_table("branches")
