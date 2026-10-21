"""add half-partial and quantity-pieces override to stock_transfers

Revision ID: q1r2s3t4u5v6
Revises: i1j2k3l4m5n6
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "q1r2s3t4u5v6"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # "5 cartons and a half" on a warehouse push -- an extra half of
    # whatever unit_label/pieces_per_unit already says for this row,
    # on top of unit_count. Mirrors the invoice's half_carton/half_dozen
    # remainder, but the warehouse form has no fixed-conversion units
    # (pieces_per_unit is always typed by hand here, for every unit
    # label), so a single boolean is enough -- there's no second unit
    # to choose between.
    op.add_column("stock_transfers", sa.Column("is_half_partial", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Lets the total pieces actually pushed be typed directly instead
    # of always trusting unit_count * pieces_per_unit -- "should be
    # optional and imputable, so that we should be able to trace the
    # quantity of item on the warehouse without getting to open it."
    # Null means "use the calculated figure" (the existing behavior,
    # unchanged for every row where nobody overrides it).
    op.add_column("stock_transfers", sa.Column("quantity_pieces_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_transfers", "quantity_pieces_override")
    op.drop_column("stock_transfers", "is_half_partial")
