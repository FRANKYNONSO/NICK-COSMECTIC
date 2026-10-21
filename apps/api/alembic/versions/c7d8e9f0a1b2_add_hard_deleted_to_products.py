"""add hard_deleted flag to products (permanent delete from inventory list)

Revision ID: c7d8e9f0a1b2
Revises: 1f2e3d4c5b6a
Create Date: 2026-09-13
"""
from alembic import op
import sqlalchemy as sa

revision = "c7d8e9f0a1b2"
down_revision = "1f2e3d4c5b6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("hard_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("products", "hard_deleted")
