"""purchase invoices: Nigerian-invoice-style fields (receipt no., supplier address/phone, payment method)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-30 00:00:00.000000

The user loves the existing purchase-invoice feature as-is and asked
only for "a little customization... probably like the Nigerian
system" after sending reference photos of physical Nigerian invoice/
receipt booklets. Comparing those photos to the current form, the
fields those paper invoices always carry that we don't yet have
anywhere to type in are: the invoice/receipt number printed on the
booklet itself, the supplier's address and phone (the booklet's
letterhead), and how the purchase was paid for. All four are
optional/nullable -- existing invoices on file simply won't have
them, and filling them in is never required.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("purchase_invoices", sa.Column("invoice_number", sa.String(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("supplier_address", sa.String(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("supplier_phone", sa.String(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("payment_method", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_invoices", "payment_method")
    op.drop_column("purchase_invoices", "supplier_phone")
    op.drop_column("purchase_invoices", "supplier_address")
    op.drop_column("purchase_invoices", "invoice_number")
