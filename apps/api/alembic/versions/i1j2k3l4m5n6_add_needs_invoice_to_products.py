"""products: add needs_invoice flag for the pending-invoice tracker

Revision ID: i1j2k3l4m5n6
Revises: t3u4v5w6x7y8
Create Date: 2026-09-17

Add Product -> Invoice Workflow follow-up (Sept 2026): the invoice
prompt shown after saving a brand-new, non-draft product is fully
skippable, and closing/refreshing away from it used to lose track of
that decision entirely. This flag lets Inventory surface a persistent
"Awaiting invoice" filter so a skipped product is never silently
forgotten, without blocking the save or forcing an invoice to be
auto-created.
"""
from alembic import op
import sqlalchemy as sa

revision = "i1j2k3l4m5n6"
down_revision = "t3u4v5w6x7y8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("needs_invoice", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # server_default only exists to backfill this ALTER TABLE safely;
    # drop it afterward so the ORM-side default=False (matching every
    # other boolean flag on this model) is the single source of truth
    # for new rows going forward, not a DB-level default diverging
    # from it silently.
    op.alter_column("products", "needs_invoice", server_default=None)


def downgrade() -> None:
    op.drop_column("products", "needs_invoice")