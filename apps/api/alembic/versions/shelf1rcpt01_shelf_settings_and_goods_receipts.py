"""shelf settings + goods receipts + receipt transfer types

Revision ID: shelf1rcpt01
Revises: pwreset5req01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "shelf1rcpt01"
down_revision = "pwreset5req01"
branch_labels = None
depends_on = None

_OLD = (
    "transfer_type IN ('branch_to_branch','store_to_warehouse',"
    "'warehouse_to_store','warehouse_to_branch')"
)
_NEW = (
    "transfer_type IN ('branch_to_branch','store_to_warehouse',"
    "'warehouse_to_store','warehouse_to_branch',"
    "'receipt_to_warehouse','receipt_to_shelf')"
)


def _drop_type_check():
    op.execute("ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS ck_stock_transfer_type")
    op.execute("ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS ck_stock_transfers_ck_stock_transfer_type")


def upgrade() -> None:
    uuid_t = postgresql.UUID(as_uuid=True)

    op.create_table(
        "product_shelf_settings",
        sa.Column("id", uuid_t, primary_key=True),
        sa.Column("branch_id", uuid_t, sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("product_id", uuid_t, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("max_shelf_quantity", sa.Integer(), nullable=False, server_default=sa.text("24")),
        sa.Column("replenish_alert_level", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("updated_by", uuid_t, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("branch_id", "product_id", name="uq_product_shelf_settings_branch_product"),
        sa.CheckConstraint("max_shelf_quantity >= 0", name="ck_shelf_settings_max_nonneg"),
        sa.CheckConstraint("replenish_alert_level >= 0", name="ck_shelf_settings_alert_nonneg"),
    )

    op.create_table(
        "goods_receipts",
        sa.Column("id", uuid_t, primary_key=True),
        sa.Column("branch_id", uuid_t, sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("invoice_id", uuid_t, sa.ForeignKey("purchase_invoices.id"), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("invoice_reference", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("documentation_status", sa.String(), nullable=False, server_default="documented"),
        sa.Column("cost_status", sa.String(), nullable=False, server_default="unverified"),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_late_entry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reconciliation_notes", sa.Text(), nullable=True),
        sa.Column("recorded_by", uuid_t, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("branch_id", "idempotency_key", name="uq_goods_receipts_branch_idem"),
        sa.CheckConstraint("status IN ('draft','posted','voided')", name="ck_goods_receipt_status"),
        sa.CheckConstraint("documentation_status IN ('documented','pending')", name="ck_goods_receipt_doc_status"),
        sa.CheckConstraint("cost_status IN ('verified','unverified')", name="ck_goods_receipt_cost_status"),
    )
    op.create_index("ix_goods_receipts_branch_status", "goods_receipts", ["branch_id", "status"])

    op.create_table(
        "goods_receipt_lines",
        sa.Column("id", uuid_t, primary_key=True),
        sa.Column("receipt_id", uuid_t, sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("product_id", uuid_t, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("invoice_line_item_id", uuid_t, sa.ForeignKey("purchase_invoice_line_items.id"), nullable=True),
        sa.Column("quantity_invoiced", sa.Integer(), nullable=True),
        sa.Column("quantity_received", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity_to_shelf", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity_to_warehouse", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unit_cost_kobo", sa.BigInteger(), nullable=True),
        sa.Column("cost_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("quantity_received >= 0", name="ck_receipt_line_received_nonneg"),
        sa.CheckConstraint("quantity_to_shelf >= 0", name="ck_receipt_line_shelf_nonneg"),
        sa.CheckConstraint("quantity_to_warehouse >= 0", name="ck_receipt_line_wh_nonneg"),
        sa.CheckConstraint(
            "quantity_to_shelf + quantity_to_warehouse = quantity_received",
            name="ck_receipt_line_split_sums",
        ),
    )
    op.create_index("ix_goods_receipt_lines_receipt", "goods_receipt_lines", ["receipt_id"])

    op.add_column(
        "stock_transfers",
        sa.Column("goods_receipt_id", uuid_t, sa.ForeignKey("goods_receipts.id"), nullable=True),
    )
    _drop_type_check()
    op.execute(f"ALTER TABLE stock_transfers ADD CONSTRAINT ck_stock_transfer_type CHECK ({_NEW})")


def downgrade() -> None:
    bind = op.get_bind()
    n = bind.execute(
        sa.text(
            "SELECT count(*) FROM stock_transfers "
            "WHERE transfer_type IN ('receipt_to_warehouse','receipt_to_shelf')"
        )
    ).scalar()
    if n:
        raise RuntimeError(
            f"Refusing to downgrade: {n} stock_transfers rows were written by goods receipts. "
            "Void/reverse those receipts first; ledger rows are never deleted."
        )
    _drop_type_check()
    op.execute(f"ALTER TABLE stock_transfers ADD CONSTRAINT ck_stock_transfer_type CHECK ({_OLD})")
    op.drop_column("stock_transfers", "goods_receipt_id")
    op.drop_index("ix_goods_receipt_lines_receipt", table_name="goods_receipt_lines")
    op.drop_table("goods_receipt_lines")
    op.drop_index("ix_goods_receipts_branch_status", table_name="goods_receipts")
    op.drop_table("goods_receipts")
    op.drop_table("product_shelf_settings")