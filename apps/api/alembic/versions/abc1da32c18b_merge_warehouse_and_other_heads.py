"""merge warehouse and other heads

Revision ID: abc1da32c18b
Revises: 85a155fc2c1b, b7c8d9e0f1a2
Create Date: 2026-08-21 09:35:47.889792

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'abc1da32c18b'
down_revision: Union[str, None] = ('85a155fc2c1b', 'b7c8d9e0f1a2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
