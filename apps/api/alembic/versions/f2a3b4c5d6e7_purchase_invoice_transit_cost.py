"""purchase_invoices: add transit_cost_kobo (the middle park-to-park waybill leg)

Revision ID: f2a3b4c5d6e7
Revises: 39ccbea3f0f5
Create Date: 2026-08-31 00:00:00.000000

Splits the landed-cost breakdown into three legs instead of two:
supplier's shop -> departure park (existing waybill_cost_kobo, name
kept as-is so no data is lost), departure park -> arrival park (new
transit_cost_kobo -- the actual inter-town waybill cost), and arrival
park -> the shop (existing handling_cost_kobo).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "39ccbea3f0f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("purchase_invoices", sa.Column("transit_cost_kobo", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_invoices", "transit_cost_kobo")
