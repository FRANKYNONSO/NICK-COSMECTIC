"""link credit entries to inventory, add purchase invoices + store settings

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-08-26 00:00:00.000000

Covers three pieces of the same dictated request:
  1. credit_entries gets category_id/product_id/variant_id/quantity so
     "what are they buying" on the Credit List is a real, required
     selection straight out of the catalog (category -> product ->
     variant, same data POS itself sells from) instead of a free-text
     note -- the amount owed can then be auto-priced from the variant
     rather than typed by hand. Columns are nullable so existing
     free-text credit rows opened before this change keep working;
     the API/UI enforces "required" for anything opened from now on.
  2. purchase_invoices -- a super admin/branch manager's record of
     what was bought from a supplier (typed fields, optional photo of
     the paper invoice), filterable by month/year. Deliberately a new
     table rather than reusing `refunds` or `sales` -- this is money
     going OUT to a supplier, not a customer-facing transaction.
  3. store_settings -- a single-row table for the printed-receipt
     branding (store name, contact email, logo image, invoice base
     URL for the QR code) that used to live only in browser
     localStorage (per-device, never synced). Now it's set once by a
     super admin and read by every till/device.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credit_entries", sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=True))
    op.add_column("credit_entries", sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True))
    op.add_column("credit_entries", sa.Column("variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_variants.id"), nullable=True))
    op.add_column("credit_entries", sa.Column("quantity", sa.Integer(), nullable=True))

    op.create_table(
        "purchase_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_purchase_invoices_invoice_date", "purchase_invoices", ["invoice_date"])
    op.create_index("ix_purchase_invoices_branch_id", "purchase_invoices", ["branch_id"])

    op.create_table(
        "store_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("store_settings")
    op.drop_index("ix_purchase_invoices_branch_id", table_name="purchase_invoices")
    op.drop_index("ix_purchase_invoices_invoice_date", table_name="purchase_invoices")
    op.drop_table("purchase_invoices")
    op.drop_column("credit_entries", "quantity")
    op.drop_column("credit_entries", "variant_id")
    op.drop_column("credit_entries", "product_id")
    op.drop_column("credit_entries", "category_id")
