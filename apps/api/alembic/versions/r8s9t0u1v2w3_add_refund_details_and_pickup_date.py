"""add refund photo/exchange/settlement fields, sale pickup_date

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-08-24 00:00:00.000000

Covers three pieces of the same request:
  1. Richer refund records -- a photo of the returned item, the
     resolved action (straight cash refund vs. product exchange), and
     -- for an exchange -- which product it was swapped for and which
     side (customer or store) settled a price difference.
  2. A pickup_date on Sale so an open booking/deposit can carry an
     actual date (not just the existing free-text pickup_note), which
     the 1%/day storage-fee calculation is measured against.
  3. `refund_id` on stock_batches, purely additive -- lets a spoiled
     item pulled back in via a refund show up in that product's
     warehouse stock history as its own traceable batch (received then
     immediately written off via a StockAdjustment), rather than
     inventing a whole new spoiled-goods table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'r8s9t0u1v2w3'
down_revision: Union[str, None] = 'q7r8s9t0u1v2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("refunds", sa.Column("image_url", sa.String(), nullable=True))
    op.add_column("refunds", sa.Column("action", sa.String(), nullable=True))
    op.add_column(
        "refunds",
        sa.Column("exchange_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_variants.id"), nullable=True),
    )
    op.add_column("refunds", sa.Column("settlement", sa.String(), nullable=True))
    op.add_column("refunds", sa.Column("settlement_amount_kobo", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_refund_action", "refunds", "action IS NULL OR action IN ('cash','exchange')"
    )
    op.create_check_constraint(
        "ck_refund_settlement",
        "refunds",
        "settlement IS NULL OR settlement IN ('store_paid_customer','customer_topped_up','even')",
    )

    op.add_column("sales", sa.Column("pickup_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sales", "pickup_date")
    op.drop_constraint("ck_refund_settlement", "refunds", type_="check")
    op.drop_constraint("ck_refund_action", "refunds", type_="check")
    op.drop_column("refunds", "settlement_amount_kobo")
    op.drop_column("refunds", "settlement")
    op.drop_column("refunds", "exchange_variant_id")
    op.drop_column("refunds", "action")
    op.drop_column("refunds", "image_url")
