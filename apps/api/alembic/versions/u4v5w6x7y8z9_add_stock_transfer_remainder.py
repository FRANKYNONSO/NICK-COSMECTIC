"""add remainder_quantity/remainder_unit_label to stock_transfers

Revision ID: u4v5w6x7y8z9
Revises: u4v5w6x7y8z9
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "u4v5w6x7y8z9"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_transfers", sa.Column("remainder_quantity", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("remainder_unit_label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_transfers", "remainder_unit_label")
    op.drop_column("stock_transfers", "remainder_quantity")
