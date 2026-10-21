"""drop sale_losses table (retire SaleLoss / sold-below-price)

Revision ID: bb22cc33dd44
Revises: aa11bb22cc33
Create Date: 2026-09-05 00:00:00.000000

Spec section 1/3 (September 2026 change spec): "Sold Below Original
Price" is removed as a checkout discount path entirely -- all
discounts now flow through super-admin-generated coupon codes only.
The SaleLoss model/table existed solely to back that flow (see the
retired class docstring, kept as a comment in
infrastructure/db/models.py for history), so it's retired along with
it rather than left as dead tracking. Nothing else reads from
sale_losses after this change -- see purchase_invoices.py::
total_invested(), inventory.py's loss-summary/loss-history endpoints,
and sales.py's checkout/get-sale-detail routes.

*** Same caveat as aa11bb22cc33: this repo has had duplicate/branching
revision ids in the past, so please run `alembic heads` against your
actual database before applying this and adjust down_revision below
if aa11bb22cc33 isn't really your current head. ***
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "bb22cc33dd44"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("sale_losses")


def downgrade() -> None:
    op.create_table(
        "sale_losses",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sale_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column(
            "sale_item_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sale_items.id"),
            nullable=False,
        ),
        sa.Column("branch_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("product_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("original_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("sold_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("value_kobo", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
