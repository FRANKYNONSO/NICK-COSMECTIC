"""add wholesale pricing tier fields to products (section 22)

Revision ID: aa77bb88cc99
Revises: ff66aa77bb88
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 22: wholesale/bulk pricing tiers,
confirmed net-new -- no trace of this anywhere in the prior codebase.
A standing quantity-threshold rule on the product itself (e.g. "10+
pieces -> 4500/unit instead of 5000"), applied automatically at POS.

*** Same caveat as the migrations before this one in the chain: this
repo has had duplicate/branching revision ids in the past, so please
run `alembic heads` against your actual database before applying this
and adjust down_revision below if ff66aa77bb88 isn't really your
current head. ***
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "aa77bb88cc99"
down_revision = "ff66aa77bb88"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("wholesale_price_kobo", sa.BigInteger(), nullable=True))
    op.add_column("products", sa.Column("wholesale_min_quantity", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "wholesale_min_quantity")
    op.drop_column("products", "wholesale_price_kobo")
