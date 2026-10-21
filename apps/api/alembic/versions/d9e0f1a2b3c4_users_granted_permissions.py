"""users: add granted_permissions for per-staff permission grants

Revision ID: d9e0f1a2b3c4
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 00:00:00.000000

Spec: "let's say, for instance, in these particular rules, I have a
particular staff I trust so much, and this staff, I want him to be
able to do one or two things on the system, just like how all these
bigger roles... can do... It should be customizable that whenever I
come to staff, whenever I click on edit, I should be able to give
that particular staff a particular editable role... but it shouldn't
be that they upgrade his role. I just come and edit it."

`users.denied_permissions` already let an admin take permissions away
from one account below their role's default; this adds the missing
opposite direction -- granting one account extra permissions on top
of their role, without changing the role itself (which would affect
every other holder of that role too).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "granted_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.alter_column("users", "granted_permissions", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "granted_permissions")
