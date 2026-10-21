"""add purchase_invoice_line_items table

Revision ID: 6706d7e805da
Revises: 30877432d102
Create Date: 2026-08-29 00:00:00.000000

Lets a purchase invoice carry itemized line entries (quantity, item
name, cost price, rate, line total) typed in alongside the existing
optional photo -- spec: "if there was 30 cartons of product on the
invoice, there should also be able to write it down... quantity,
name, cost price, rate, and total".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6706d7e805da'
down_revision: Union[str, None] = '30877432d102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchase_invoice_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_name", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cost_price_kobo", sa.BigInteger(), nullable=True),
        sa.Column("rate_kobo", sa.BigInteger(), nullable=True),
        sa.Column("line_total_kobo", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_purchase_invoice_line_items_invoice_id",
        "purchase_invoice_line_items",
        ["invoice_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_purchase_invoice_line_items_invoice_id", table_name="purchase_invoice_line_items")
    op.drop_table("purchase_invoice_line_items")
