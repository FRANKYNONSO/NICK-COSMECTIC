"""merge heads: credit/pricing/inventory/permissions + user denied permissions

Revision ID: 30877432d102
Revises: z1a2b3c4d5e6, d4e5f6a7b8c9
Create Date: 2026-08-29 00:30:00.000000

This was a pre-existing fork in the migration history, discovered
while adding new migrations on top of `alembic heads` -- two branches
(z1a2b3c4d5e6, touching credit_entries, and d4e5f6a7b8c9, touching
users.denied_permissions) had never been reconciled with a merge
revision, which would have made `alembic upgrade head` fail with
"Multiple head revisions are present" regardless of anything added
here. The two touch entirely disjoint tables/columns, so this merge
is a plain no-op join -- nothing to reconcile.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '30877432d102'
down_revision: Union[str, Sequence[str], None] = ('z1a2b3c4d5e6', 'd4e5f6a7b8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
