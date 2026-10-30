"""branch transfer return account details (RET4)

Revision ID: xfer8retacct01
Revises: xfer7ret01

Adds three optional text columns to transfer_returns: the account of the
branch that gives the pieces back (it receives the money), so the sending
branch can pay the refund to it.
downgrade() fails if any row holds account details, so nothing is lost.
"""
from alembic import op
import sqlalchemy as sa

revision = "xfer8retacct01"
down_revision = "xfer7ret01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transfer_returns", sa.Column("refund_bank_name", sa.String(), nullable=True))
    op.add_column("transfer_returns", sa.Column("refund_account_number", sa.String(), nullable=True))
    op.add_column("transfer_returns", sa.Column("refund_account_name", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            "SELECT count(*) FROM transfer_returns WHERE refund_bank_name IS NOT NULL "
            "OR refund_account_number IS NOT NULL OR refund_account_name IS NOT NULL"
        )
    ).scalar()
    if count:
        raise RuntimeError("Cannot downgrade: %s return row(s) hold account details." % count)
    op.drop_column("transfer_returns", "refund_account_name")
    op.drop_column("transfer_returns", "refund_account_number")
    op.drop_column("transfer_returns", "refund_bank_name")
