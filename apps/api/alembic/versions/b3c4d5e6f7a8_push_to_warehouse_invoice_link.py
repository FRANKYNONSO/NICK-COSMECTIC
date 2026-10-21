"""warehouse: push-to-warehouse transfers must reference a purchase invoice line item

Revision ID: b3c4d5e6f7a8
Revises: d7e8f9a0b1c2
Create Date: 2026-08-30 00:00:00.000000

Spec (feedback transcript, "push to store" section): push-to-warehouse
was deducting straight from the shop floor's live sellable stock
(StockBatch via FIFO), which is wrong -- it's meant to be a pure
documentation move ("28 cartons of product is already on the
warehouse, available, not in use... a way of documentation"),
completely independent of what's currently on the shop floor. It
should also only ever be possible for an item that's actually on a
purchase invoice on file: "if the item is not on the invoice... it
should not be pushed to warehouse."

`invoice_line_item_id` ties each store_to_warehouse StockTransfer back
to the specific invoice line it's documenting delivery of, so the API
can (a) refuse to push an item with no matching invoice line and (b)
cap how much can be pushed against that line to what the invoice
actually says was bought, tracked by summing prior pushes against the
same line. Nullable, and only meaningful for transfer_type =
'store_to_warehouse' -- every other transfer type leaves it null.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_transfers",
        sa.Column(
            "invoice_line_item_id",
            sa.UUID(),
            sa.ForeignKey("purchase_invoice_line_items.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("stock_transfers", "invoice_line_item_id")
