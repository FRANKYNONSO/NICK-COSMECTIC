"""add cost/payment fields to stock_transfers (section 19)

Revision ID: cc99dd00ee11
Revises: bb88cc99dd00
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 19: a branch-to-branch transfer is
priced at the item's original invoice/supplier cost (never retail),
with a payment method for the receiving branch to settle with the
sending branch. The cost basis itself was already computed
(_fifo_deduct in warehouse.py) but never stored on the transfer record
or exposed to the frontend for the confirmation receipt this section
also requires.

*** Same caveat as the migrations before this one in the chain: this
repo has had duplicate/branching revision ids in the past, so please
run `alembic heads` against your actual database before applying this
and adjust down_revision below if bb88cc99dd00 isn't really your
current head. ***
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "cc99dd00ee11"
down_revision = "bb88cc99dd00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_transfers", sa.Column("total_cost_kobo", sa.BigInteger(), nullable=True))
    op.add_column("stock_transfers", sa.Column("payment_method", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_stock_transfer_payment_method",
        "stock_transfers",
        "payment_method IN ('cash','transfer') OR payment_method IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_stock_transfer_payment_method", "stock_transfers", type_="check")
    op.drop_column("stock_transfers", "payment_method")
    op.drop_column("stock_transfers", "total_cost_kobo")
