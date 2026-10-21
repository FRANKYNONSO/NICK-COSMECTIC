"""add refund_items table for itemized refund receipts

Revision ID: f9a1b2c3d4e5
Revises: e3c29b621792
Create Date: 2026-08-29 12:00:00.000000

The `refunds` table has only ever stored one aggregate amount per
refund event (see the docstring on POST /sales/{sale_id}/refund),
which was fine for a plain-text refund summary but isn't enough to
render a proper itemized refund receipt (spec: "Refunded item(s),
Quantity, Unit, Rate/price, Total/refunded amount"). This adds a
per-line record of exactly what was refunded on each refund event,
snapshotting the product/variant name and unit price at refund time
(the same snapshot pattern SaleItem already uses) so the receipt
stays accurate even if the product is later renamed or repriced.

Nullable/backward compatible: existing refunds processed before this
migration simply have no rows here. The refund detail endpoint falls
back to the audit-log line metadata for those older records rather
than losing them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f9a1b2c3d4e5'
down_revision: Union[str, None] = 'e3c29b621792'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refund_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("refunds.id"), nullable=False),
        sa.Column("sale_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sale_items.id"), nullable=False),
        # Snapshots -- same rationale as SaleItem: keep the receipt
        # accurate even if the product/variant is later renamed,
        # repriced, or deleted.
        sa.Column("product_name", sa.String(), nullable=True),
        sa.Column("unit_label", sa.String(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("line_total_kobo", sa.BigInteger(), nullable=False),
        sa.Column("restocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("spoiled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_refund_items_refund_id", "refund_items", ["refund_id"])


def downgrade() -> None:
    op.drop_index("ix_refund_items_refund_id", table_name="refund_items")
    op.drop_table("refund_items")
