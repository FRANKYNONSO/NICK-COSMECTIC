"""merge all heads

Revision ID: f2a7cffbcd81
Revises: y6z7a8b9c0d1, NEWID2, d9e0f1a2b3c4
Create Date: 2026-08-28 04:58:21.340417

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a7cffbcd81'
down_revision: Union[str, None] = ('y6z7a8b9c0d1', 'NEWID2', 'd9e0f1a2b3c4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
