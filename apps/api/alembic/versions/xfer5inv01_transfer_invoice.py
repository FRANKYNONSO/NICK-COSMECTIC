"""receiving-branch invoice for branch transfers (XFER5)

Revision ID: xfer5inv01
Revises: xfer1sale01

Adds purchase_invoices.source (empty for every normal invoice,
'branch_transfer' for the invoice a transfer makes for the receiving
branch) and stock_transfers.invoice_id (link to that invoice).
downgrade() fails if transfer invoices exist, because dropping the
source column would turn them into normal supplier invoices.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "xfer5inv01"
down_revision = "xfer1sale01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_invoices", sa.Column("source", sa.String(), nullable=True))
    op.add_column(
        "stock_transfers",
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_stock_transfers_invoice_id",
        "stock_transfers",
        "purchase_invoices",
        ["invoice_id"],
        ["id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT count(*) FROM purchase_invoices WHERE source = 'branch_transfer'")
    ).scalar()
    if count:
        raise RuntimeError(
            "Cannot downgrade: %s branch-transfer invoice(s) exist." % count
        )
    op.drop_constraint("fk_stock_transfers_invoice_id", "stock_transfers", type_="foreignkey")
    op.drop_column("stock_transfers", "invoice_id")
    op.drop_column("purchase_invoices", "source")
