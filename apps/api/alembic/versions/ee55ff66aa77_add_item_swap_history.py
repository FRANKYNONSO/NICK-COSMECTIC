"""add item_swap_history to sales (section 9)

Revision ID: ee55ff66aa77
Revises: dd44ee55ff66
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 9: "when a booked item is swapped
for another, the system must record and display the full chain --
original item + price, the swap, and the new item + price/topup/
balance." This is the append-only trail of every item swap on a
booking, since the SaleItem row itself is overwritten in place (same
one-current-state-row-per-line pattern everywhere else in this app).

*** Same caveat as the migrations before this one in the chain: this
repo has had duplicate/branching revision ids in the past, so please
run `alembic heads` against your actual database before applying this
and adjust down_revision below if dd44ee55ff66 isn't really your
current head. ***
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "ee55ff66aa77"
down_revision = "dd44ee55ff66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("item_swap_history", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales", "item_swap_history")
