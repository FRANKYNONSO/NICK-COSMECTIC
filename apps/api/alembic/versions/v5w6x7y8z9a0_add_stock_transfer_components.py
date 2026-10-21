"""add stock_transfers component columns for push-to-warehouse

Revision ID: v5w6x7y8z9a0
Revises: u4v5w6x7y8z9
"""
from alembic import op
import sqlalchemy as sa

revision = "v5w6x7y8z9a0"
down_revision = "u4v5w6x7y8z9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stock_transfers", sa.Column("cartons_count", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("cartons_pieces_per_unit", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("cartons_half", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("stock_transfers", sa.Column("dozens_count", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("dozens_pieces_per_unit", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("dozens_half", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("stock_transfers", sa.Column("pieces_count", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("stock_transfers", "pieces_count")
    op.drop_column("stock_transfers", "dozens_half")
    op.drop_column("stock_transfers", "dozens_pieces_per_unit")
    op.drop_column("stock_transfers", "dozens_count")
    op.drop_column("stock_transfers", "cartons_half")
    op.drop_column("stock_transfers", "cartons_pieces_per_unit")
    op.drop_column("stock_transfers", "cartons_count")
