# _column_exists_guard_applied
"""add role_applied_for and origin fields to staff_applications

Revision ID: f1a2b3c4d5e6
Revises: e863a5a15855
Create Date: 2026-08-19 09:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e863a5a15855"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "role_applied_for" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("staff_applications")]:
        op.add_column(
            "staff_applications",
            sa.Column("role_applied_for", sa.String(), nullable=False, server_default="staff"),
        )
    if "state_of_origin" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("staff_applications")]:
        op.add_column("staff_applications", sa.Column("state_of_origin", sa.String(), nullable=True))
    if "lga_of_origin" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("staff_applications")]:
        op.add_column("staff_applications", sa.Column("lga_of_origin", sa.String(), nullable=True))
    if "hometown" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("staff_applications")]:
        op.add_column("staff_applications", sa.Column("hometown", sa.String(), nullable=True))
    if "home_address_of_origin" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("staff_applications")]:
        op.add_column("staff_applications", sa.Column("home_address_of_origin", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("staff_applications", "home_address_of_origin")
    op.drop_column("staff_applications", "hometown")
    op.drop_column("staff_applications", "lga_of_origin")
    op.drop_column("staff_applications", "state_of_origin")
    op.drop_column("staff_applications", "role_applied_for")
