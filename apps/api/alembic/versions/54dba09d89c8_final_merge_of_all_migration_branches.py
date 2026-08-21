"""final merge of all migration branches

Revision ID: 54dba09d89c8
Revises: a1b2c3d4e5f7, abc1da32c18b, e5f6a7b8c9d1, i4j5k6l7m8n9
Create Date: 2026-08-21 09:47:21.518300

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '54dba09d89c8'
down_revision: Union[str, None] = ('a1b2c3d4e5f7', 'abc1da32c18b', 'e5f6a7b8c9d1', 'i4j5k6l7m8n9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
