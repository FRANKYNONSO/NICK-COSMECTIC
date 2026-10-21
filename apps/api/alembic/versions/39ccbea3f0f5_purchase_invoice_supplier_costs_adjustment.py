"""purchase_invoices: supplier link, partial payment, waybill/handling costs, check & balance

Revision ID: 39ccbea3f0f5
Revises: 0cdc81bb3abe
Create Date: 2026-08-30 02:00:00.000000

Spec sections 14, 16-19: links purchase_invoices to the existing
suppliers registry for autofill; adds amount_paid_kobo (partial
payment/balance), waybill_cost_kobo + handling_cost_kobo (landed
cost), and adjustment_amount_kobo/status/note (Check and Balance for
damaged/returned supplier goods). invoice_number (spec section 15,
"remove receipt number") is intentionally left in place as a column
-- only the create/edit form stops asking for it -- so no history is
lost from a prior migration that added it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "39ccbea3f0f5"
down_revision: Union[str, None] = "0cdc81bb3abe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_invoices",
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=True),
    )
    op.add_column("purchase_invoices", sa.Column("amount_paid_kobo", sa.BigInteger(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("waybill_cost_kobo", sa.BigInteger(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("handling_cost_kobo", sa.BigInteger(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("adjustment_amount_kobo", sa.BigInteger(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("adjustment_status", sa.String(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("adjustment_note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_purchase_invoice_adjustment_status",
        "purchase_invoices",
        "adjustment_status IS NULL OR adjustment_status IN ('pending','balanced','replaced','refunded')",
    )

    # Best-effort backfill: link existing invoices to a supplier row
    # with a matching name where one already exists, so history
    # benefits from the new autofill immediately rather than only
    # going forward.
    op.execute(
        """
        UPDATE purchase_invoices pi
        SET supplier_id = s.id
        FROM suppliers s
        WHERE pi.supplier_id IS NULL
          AND s.is_deleted IS FALSE
          AND lower(trim(s.name)) = lower(trim(pi.supplier_name))
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchase_invoice_adjustment_status", "purchase_invoices", type_="check")
    op.drop_column("purchase_invoices", "adjustment_note")
    op.drop_column("purchase_invoices", "adjustment_status")
    op.drop_column("purchase_invoices", "adjustment_amount_kobo")
    op.drop_column("purchase_invoices", "handling_cost_kobo")
    op.drop_column("purchase_invoices", "waybill_cost_kobo")
    op.drop_column("purchase_invoices", "amount_paid_kobo")
    op.drop_column("purchase_invoices", "supplier_id")
