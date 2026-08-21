"""add damaged_count and missing_count to intake_receipts

Revision ID: e5f6a7b8c9d1
Revises: c3d4e5f6a7b8
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d1'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: damaged_count / missing_count were already added by
    # h3i4j5k6l7m8_add_intake_receipt_damaged_missing.py (duplicate
    # migration from a separate session). Skipping to avoid
    # DuplicateColumnError.
    pass


def downgrade() -> None:
    pass
