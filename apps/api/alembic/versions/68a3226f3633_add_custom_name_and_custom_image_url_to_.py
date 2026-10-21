"""add custom_name and custom_image_url to stock_batches

Revision ID: 68a3226f3633
Revises: rl2freeform01
Create Date: 2026-09-21 20:16:15.519952

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '68a3226f3633'
down_revision: Union[str, None] = 'rl2freeform01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_batches', sa.Column('custom_name', sa.String(), nullable=True))
    op.add_column('stock_batches', sa.Column('custom_image_url', sa.String(), nullable=True))
    op.alter_column('stock_batches', 'product_id',
               existing_type=sa.UUID(),
               nullable=True)


def downgrade() -> None:
    op.alter_column('stock_batches', 'product_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.drop_column('stock_batches', 'custom_image_url')
    op.drop_column('stock_batches', 'custom_name')
