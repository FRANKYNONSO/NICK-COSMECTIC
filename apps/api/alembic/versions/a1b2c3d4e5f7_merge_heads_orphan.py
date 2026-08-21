"""merge_heads

Revision ID: a1b2c3d4e5f7
Revises: 7ef0edc9c363, ef7c31e538e5
Create Date: 2026-08-15 09:00:00.000000

Two migrations were created off the same parent independently
(7ef0edc9c363 and c7f1ea55cde7/ef7c31e538e5), leaving two heads.
This merge has no schema changes of its own -- it just gives
alembic a single head to build on again.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = ('7ef0edc9c363', 'ef7c31e538e5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
