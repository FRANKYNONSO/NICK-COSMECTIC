"""intake_receipts: add manual_item_name (typed product name for photo-only invoices)

Revision ID: b8c9d0e1f2a3
Revises: f2a3b4c5d6e7
Create Date: 2026-08-31 00:00:01.000000

Lets a waybill complaint show a product name even when it isn't tied
to invoice_line_item_id -- e.g. the invoice was only ever saved as a
photo, so there's no typed line item to pick from.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("intake_receipts", sa.Column("manual_item_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("intake_receipts", "manual_item_name")
