"""branches: drop phone and is_warehouse

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-08-27 00:00:00.000000

Spec: "branches, like I said, branches should not just be name,
address, and phone number. There's no even need for phone number...
they should remove this option of this is a warehouse. There is no
branch that is a warehouse. A branch is a branch, and a branch also
has its own warehouse."

`phone` was only ever read/written by the branches create form
itself (grepped the whole backend -- no other reference). `is_warehouse`
was already effectively dead: nothing in the checkout/warehouse-push
logic ever branches on it (see the "unused for this" comment removed
from api/v1/routers/sales.py in the same change that ships this
migration) -- every branch already gets its own warehouse workflow via
the warehouse endpoints, regardless of this flag.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'NEWID2'
down_revision: Union[str, None] = 'NEWID1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("branches", "phone")
    op.drop_column("branches", "is_warehouse")


def downgrade() -> None:
    op.add_column("branches", sa.Column("is_warehouse", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("branches", sa.Column("phone", sa.String(), nullable=True))
    op.alter_column("branches", "is_warehouse", server_default=None)
