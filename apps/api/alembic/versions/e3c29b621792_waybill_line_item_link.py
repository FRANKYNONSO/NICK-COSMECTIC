"""link waybill complaints to a specific invoice line item

Revision ID: e3c29b621792
Revises: 846a32c3bb81
Create Date: 2026-08-29 00:20:00.000000

Spec: "you should be able to select that particular item that is from
that particular invoice... this is the product that you gave me 30
cartons. You select that particular one." Nullable -- older complaints
and invoices filed before line items existed won't have one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e3c29b621792'
down_revision: Union[str, None] = '846a32c3bb81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "intake_receipts",
        sa.Column(
            "invoice_line_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_invoice_line_items.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("intake_receipts", "invoice_line_item_id")
