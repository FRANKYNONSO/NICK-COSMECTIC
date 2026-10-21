"""add sales.notes and booking status

Revision ID: n0o1p2q3r4s5
Revises: m9n0o1p2q3r4
Create Date: 2026-08-23 00:05:00.000000

Supports the new booking (part-payment / layaway) feature: a booking
is stored as a Sale row with status='booking' -- stock is deducted up
front via the same FIFO path an ordinary checkout uses, so the item is
genuinely reserved -- plus a free-text `notes` column holding the
customer's flexible pickup window (e.g. "later this week"), since
they aren't held to an exact date/time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n0o1p2q3r4s5'
down_revision: Union[str, None] = 'm9n0o1p2q3r4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sales', sa.Column('notes', sa.Text(), nullable=True))
    op.drop_constraint('ck_sale_status', 'sales', type_='check')
    op.create_check_constraint(
        'ck_sale_status',
        'sales',
        "status IN ('completed','refunded','partially_refunded','cancelled','booking')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_sale_status', 'sales', type_='check')
    op.create_check_constraint(
        'ck_sale_status',
        'sales',
        "status IN ('completed','refunded','partially_refunded','cancelled')",
    )
    op.drop_column('sales', 'notes')
