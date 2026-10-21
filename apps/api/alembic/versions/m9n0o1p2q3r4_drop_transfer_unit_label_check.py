"""drop stock_transfers unit_label check constraint

Revision ID: m9n0o1p2q3r4
Revises: l7m8n9o0p1q2
Create Date: 2026-08-23 00:00:00.000000

The warehouse "Push to store" form's unit-label field is now a dropdown
(carton, cartons, rolls, pieces) with a "quick-add unit label" option
that lets a super admin/branch manager add further store-defined labels
on the fly. The old `ck_transfer_unit` constraint hard-restricted the
column to exactly 'piece' | 'carton' | 'roll', which would reject every
new label added through that quick-add option. Dropping it in favour of
the existing length/non-empty validation already enforced by
StockTransferCreate on the API side.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm9n0o1p2q3r4'
down_revision: Union[str, None] = 'l7m8n9o0p1q2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_transfer_unit', 'stock_transfers', type_='check')


def downgrade() -> None:
    op.create_check_constraint(
        'ck_transfer_unit',
        'stock_transfers',
        "unit_label IN ('piece','carton','roll')",
    )
