# _column_exists_guard_applied
"""add denied_permissions override column to users

Revision ID: a1b2c3d4e5f6
Revises: 85a155fc2c1b
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = '85a155fc2c1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "denied_permissions" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column(
            "users",
            sa.Column(
                "denied_permissions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="[]",
            ),
        )


def downgrade() -> None:
    op.drop_column("users", "denied_permissions")
