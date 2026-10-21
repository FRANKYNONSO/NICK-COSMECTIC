"""purchase_invoices: nullable supplier_name, add status (draft/completed)

Revision ID: 1f2e3d4c5b6a
Revises: ff77aa88bb99
Create Date: 2026-09-13 00:00:00.000000

Add Product -> Invoice Workflow spec (Sept 2026): a purchase invoice
must be creatable as a bare draft -- just an item, no supplier info
yet -- so supplier_name (previously NOT NULL) becomes nullable. status
distinguishes an unfinished draft (still missing supplier/landed-cost
details, shown in the "add to existing draft" picker) from a completed
invoice. Existing rows all default to 'completed' since every invoice
on file before this migration was filed the old way, fully filled in
by hand.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "1f2e3d4c5b6a"
down_revision: Union[str, None] = "ff77aa88bb99"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("purchase_invoices", "supplier_name", nullable=True)
    op.add_column(
        "purchase_invoices",
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
    )
    op.create_check_constraint(
        "ck_purchase_invoice_status",
        "purchase_invoices",
        "status IN ('draft', 'completed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchase_invoice_status", "purchase_invoices", type_="check")
    op.drop_column("purchase_invoices", "status")
    op.execute("UPDATE purchase_invoices SET supplier_name = 'Unknown' WHERE supplier_name IS NULL")
    op.alter_column("purchase_invoices", "supplier_name", nullable=False)
