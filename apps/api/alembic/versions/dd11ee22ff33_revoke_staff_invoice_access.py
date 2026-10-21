"""revoke staff access to purchase invoices (platform update spec section 5)

Revision ID: dd11ee22ff33
Revises: cc99dd00ee11
Create Date: 2026-09-09 00:00:00.000000

Platform update spec section 5: "Staff should not have access to
Invoices at all. This section should be removed from the staff view
entirely -- invoices should be visible only to the super admin and
branch managers."

Migration z1a2b3c4d5e6 granted "view_purchase_invoices" to every role
including staff, on the theory that a super admin might want to hand a
cashier read-only invoice access. This spec pass reverses that default
for staff specifically -- branch_manager/super_admin keep the
permission unchanged. A super admin can still re-grant it to an
individual staff account via denied_permissions/custom roles exactly
as before; this migration only changes the *default* staff role.

*** Same caveat as prior migrations in this chain: this repo has had
duplicate/branching revision ids in the past, so please run
`alembic heads` against your actual database before applying this and
adjust down_revision below if cc99dd00ee11 isn't really your current
head. ***
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "dd11ee22ff33"
down_revision: Union[str, None] = "cc99dd00ee11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


roles_table = sa.table(
    "roles",
    sa.column("name", sa.String),
    sa.column("permissions", postgresql.JSONB),
)


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.select(roles_table.c.permissions).where(roles_table.c.name == "staff")
    ).first()
    if row is not None:
        perms = [p for p in (row[0] or []) if p != "view_purchase_invoices"]
        bind.execute(
            roles_table.update()
            .where(roles_table.c.name == "staff")
            .values(permissions=perms)
        )


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.select(roles_table.c.permissions).where(roles_table.c.name == "staff")
    ).first()
    if row is not None:
        perms = list(row[0] or [])
        if "view_purchase_invoices" not in perms:
            perms.append("view_purchase_invoices")
        bind.execute(
            roles_table.update()
            .where(roles_table.c.name == "staff")
            .values(permissions=perms)
        )
