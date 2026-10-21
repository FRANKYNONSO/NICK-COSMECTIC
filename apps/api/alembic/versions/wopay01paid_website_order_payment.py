"""website orders: payment fields

Revision ID: wopay01paid
Revises: cust4packs01
Marker: PATCH19A-ORDER-PAYMENT-COLUMNS
"""
from alembic import op
import sqlalchemy as sa

revision = "wopay01paid"
down_revision = "cust4packs01"
branch_labels = None
depends_on = None

STATUS_CHECK = "payment_status IN ('unpaid','paid')"
METHOD_CHECK = "payment_method IS NULL OR payment_method IN ('bank_transfer','cash_on_delivery')"


def upgrade():
    op.add_column(
        "storefront_orders",
        sa.Column("payment_status", sa.String(), nullable=False, server_default="unpaid"),
    )
    op.add_column("storefront_orders", sa.Column("payment_method", sa.String(), nullable=True))
    op.add_column("storefront_orders", sa.Column("amount_received_kobo", sa.BigInteger(), nullable=True))
    op.add_column("storefront_orders", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("storefront_orders", sa.Column("paid_recorded_by", sa.Uuid(), nullable=True))
    op.add_column("storefront_orders", sa.Column("payment_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_storefront_orders_paid_recorded_by", "storefront_orders", "users", ["paid_recorded_by"], ["id"]
    )
    op.create_check_constraint("ck_storefront_order_payment_status", "storefront_orders", STATUS_CHECK)
    op.create_check_constraint("ck_storefront_order_payment_method", "storefront_orders", METHOD_CHECK)


def downgrade():
    op.drop_constraint("ck_storefront_order_payment_method", "storefront_orders", type_="check")
    op.drop_constraint("ck_storefront_order_payment_status", "storefront_orders", type_="check")
    op.drop_constraint("fk_storefront_orders_paid_recorded_by", "storefront_orders", type_="foreignkey")
    op.drop_column("storefront_orders", "payment_note")
    op.drop_column("storefront_orders", "paid_recorded_by")
    op.drop_column("storefront_orders", "paid_at")
    op.drop_column("storefront_orders", "amount_received_kobo")
    op.drop_column("storefront_orders", "payment_method")
    op.drop_column("storefront_orders", "payment_status")
