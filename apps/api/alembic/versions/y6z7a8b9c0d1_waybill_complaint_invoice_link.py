"""warehouse: log a waybill complaint against a purchase invoice

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-08-26 00:20:00.000000

Spec: "the log delivery receipt on the warehouse, it should actually
log a waybill complaint... it should be selectable by information of
what is on the invoice page... we have to select the person that made
that supply, referencing the information that is on the invoice...
when lodging this complaint, we should have a menu that asks when
this item came into the store. After selecting, it should come with a
drop-down of the invoice[s] from that period, then select the
supplier... lodge the complaint... upload the image or the video."

The underlying `intake_receipts` table/model already existed (a photo
+ damaged/missing counts, logged against a branch). What was missing
was the tie back to a specific purchase invoice, so the workflow was
"type in a supplier name and counts" rather than "pick the actual
invoice this delivery was on, which supplies the supplier and date
for you." `invoice_id` adds that link; `supplier_name` is no longer
free-typed once an invoice is picked (see the API layer), but stays
on the row so a complaint filed before this migration, or filed
without picking an invoice, still reads sensibly.

Nullable: not every intake receipt on file necessarily has a matching
purchase invoice on record (older rows, or a delivery that arrived
without paperwork the office has entered yet).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'y6z7a8b9c0d1'
down_revision: Union[str, None] = 'x5y6z7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "intake_receipts",
        sa.Column("invoice_id", sa.UUID(), sa.ForeignKey("purchase_invoices.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intake_receipts", "invoice_id")
