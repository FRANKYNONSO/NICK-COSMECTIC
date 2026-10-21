"""products: default pieces per carton, bag and roll

Revision ID: cust4packs01
Revises: cust3coupon01
Marker: PATCH5A-PACK-SIZES
"""
from alembic import op
import sqlalchemy as sa

revision = "cust4packs01"
down_revision = "cust3coupon01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("default_pieces_per_carton", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("default_pieces_per_bag", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("default_pieces_per_roll", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("products", "default_pieces_per_roll")
    op.drop_column("products", "default_pieces_per_bag")
    op.drop_column("products", "default_pieces_per_carton")
