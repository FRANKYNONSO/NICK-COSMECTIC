# _column_exists_guard_applied
"""add_loyalty_points_to_customers

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-15 09:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "loyalty_points" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("customers")]:
        op.add_column(
            "customers",
            sa.Column("loyalty_points", sa.Integer(), nullable=False, server_default="0"),
        )
    op.alter_column("customers", "loyalty_points", server_default=None)


def downgrade() -> None:
    op.drop_column("customers", "loyalty_points")
