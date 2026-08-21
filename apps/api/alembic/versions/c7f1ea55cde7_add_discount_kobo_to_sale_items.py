# _column_exists_guard_applied
"""add_discount_kobo_to_sale_items

Revision ID: c7f1ea55cde7
Revises: 0001
Create Date: 2026-08-09 12:27:21.421830

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f1ea55cde7'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "discount_kobo" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("sale_items")]:
        op.add_column(
            "sale_items",
            sa.Column("discount_kobo", sa.BigInteger(), nullable=False, server_default="0"),
        )
    op.alter_column("sale_items", "discount_kobo", server_default=None)


def downgrade() -> None:
    op.drop_column("sale_items", "discount_kobo")

