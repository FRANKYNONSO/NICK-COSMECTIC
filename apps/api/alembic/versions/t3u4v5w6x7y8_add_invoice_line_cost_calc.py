"""add invoice line item cost-per-piece calculator fields

Revision ID: t3u4v5w6x7y8
Revises: 18a41dc326f3
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "t3u4v5w6x7y8"
down_revision = "18a41dc326f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_invoice_line_items", sa.Column("pieces_per_carton", sa.Integer(), nullable=True))
    op.add_column("purchase_invoice_line_items", sa.Column("remainder_quantity", sa.Integer(), nullable=True))
    op.add_column("purchase_invoice_line_items", sa.Column("remainder_unit_label", sa.String(), nullable=True))
    op.add_column("purchase_invoice_line_items", sa.Column("total_pieces", sa.Integer(), nullable=True))
    op.add_column("purchase_invoice_line_items", sa.Column("cost_price_per_piece_kobo", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_invoice_line_items", "cost_price_per_piece_kobo")
    op.drop_column("purchase_invoice_line_items", "total_pieces")
    op.drop_column("purchase_invoice_line_items", "remainder_unit_label")
    op.drop_column("purchase_invoice_line_items", "remainder_quantity")
    op.drop_column("purchase_invoice_line_items", "pieces_per_carton")
