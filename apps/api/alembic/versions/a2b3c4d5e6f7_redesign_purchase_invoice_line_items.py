"""redesign purchase invoice line items: unit label, catalog link, drop cost price from entry

Revision ID: a2b3c4d5e6f7
Revises: f9a1b2c3d4e5
Create Date: 2026-08-29 13:00:00.000000

Spec section 3/5/6: the Invoice items table changes from
Item | Quantity | Cost Price | Rate | Line Total to
S/N | Quantity | Item | Rate | Total, with:
  - a unit (cartons/rolls/pieces) alongside the numeric quantity
  - an optional link back to the catalog product/variant, for the
    Item autocomplete (section 4)
  - Total always computed server-side as quantity x rate, never a
    free-typed figure
  - Cost Price no longer collected on new lines

Backward compatible: cost_price_kobo stays on the table (nullable) so
invoices filed before this migration keep their historical cost-price
data; rate_kobo/line_total_kobo get a 0 backfill default for any
existing rows that had neither filled in, since both columns become
NOT NULL going forward (a real invoice line always has a rate and a
total from this point on).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'f9a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_invoice_line_items",
        sa.Column("unit_label", sa.String(), nullable=False, server_default="pieces"),
    )
    op.add_column(
        "purchase_invoice_line_items",
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id"),
            nullable=True,
        ),
    )
    # Backfill any pre-existing NULL rate/total to 0 before tightening
    # the columns to NOT NULL.
    op.execute("UPDATE purchase_invoice_line_items SET rate_kobo = 0 WHERE rate_kobo IS NULL")
    op.execute("UPDATE purchase_invoice_line_items SET line_total_kobo = 0 WHERE line_total_kobo IS NULL")
    op.alter_column("purchase_invoice_line_items", "rate_kobo", nullable=False, server_default="0")
    op.alter_column("purchase_invoice_line_items", "line_total_kobo", nullable=False, server_default="0")


def downgrade() -> None:
    op.alter_column("purchase_invoice_line_items", "rate_kobo", nullable=True, server_default=None)
    op.alter_column("purchase_invoice_line_items", "line_total_kobo", nullable=True, server_default=None)
    op.drop_column("purchase_invoice_line_items", "variant_id")
    op.drop_column("purchase_invoice_line_items", "unit_label")
