"""merge wishlist branch with staff applications

Revision ID: e863a5a15855
Revises: a1b2c3d4e5f6, e5f6a7b8c9d0
Create Date: 2026-08-19 09:34:05.246456

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e863a5a15855'
down_revision: Union[str, None] = ('a1b2c3d4e5f6', 'e5f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
