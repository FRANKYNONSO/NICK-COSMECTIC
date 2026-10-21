"""branch_manager: view staff by default, not edit

Revision ID: w4x5y6z7a8b9
Revises: 40f6994290a1
Create Date: 2026-08-26 00:00:00.000000

Spec: "the branch manager should be able to let manager access staff
page... but he should not be able to edit their credentials. He
should only be able to see them and probably view their details...
a particular staff should be able to have access to some things
based on personal trust."

Viewing the staff roster (GET /users, GET /users/{id}) was already,
and remains, open to any branch_manager -- that's gated on role alone
in api/v1/routers/users.py, not this permission. What changes here is
that the *editing* endpoints (PATCH /users/{id}, role, permissions,
deactivate/activate, delete, reset-password) now check the
"manage_users" permission specifically, and branch_manager no longer
carries it by default.

A super_admin who trusts a particular branch manager can still grant
them full edit rights -- not by touching this system role (which
would flip it for every branch manager at once), but by creating a
custom role via the existing Roles page (e.g. "Senior Branch
Manager") that includes "manage_users", and assigning that one
person to it. That per-person customization already works today
through the roles table this migration edits; nothing new needs to
be built for it.

Also folds in an unrelated fix discovered while touching this table:
"download_statements" was added to the Python-side permission
constants (see nickcosmetics_core.domain.users.user) when the
statements/CSV-export feature was built, but no migration ever added
it to the actual `roles` DB rows that api/dependencies.py's
require_permission() reads at request time -- so that feature has
been unreachable for every account (403) since it shipped, despite
looking correctly wired in code. Seeded here for both super_admin and
branch_manager, matching the intent documented in statements.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'w4x5y6z7a8b9'
down_revision: Union[str, None] = '40f6994290a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

roles_table = sa.table(
    "roles",
    sa.column("name", sa.String),
    sa.column("permissions", postgresql.JSONB),
)

_BRANCH_MANAGER_PERMISSIONS_WITH_EDIT = [
    "manage_users", "manage_products", "manage_inventory", "manage_suppliers",
    "manage_coupons", "view_all_reports", "view_low_stock", "view_financials",
    "view_audit_logs", "manage_sync", "process_sale", "view_own_sales",
    "download_statements",
]

_BRANCH_MANAGER_PERMISSIONS_VIEW_ONLY_STAFF = [
    p for p in _BRANCH_MANAGER_PERMISSIONS_WITH_EDIT if p != "manage_users"
]

_SUPER_ADMIN_PERMISSIONS_WITH_STATEMENTS = [
    "manage_users", "manage_products", "manage_inventory", "manage_suppliers",
    "manage_coupons", "view_all_reports", "view_low_stock", "view_financials",
    "configure_system", "view_audit_logs", "manage_sync", "manage_backups",
    "process_sale", "view_own_sales", "remove_super_admin", "transfer_ownership",
    "download_statements",
]

_SUPER_ADMIN_PERMISSIONS_WITHOUT_STATEMENTS = [
    p for p in _SUPER_ADMIN_PERMISSIONS_WITH_STATEMENTS if p != "download_statements"
]


def upgrade() -> None:
    op.execute(
        roles_table.update()
        .where(roles_table.c.name == "branch_manager")
        .values(permissions=_BRANCH_MANAGER_PERMISSIONS_VIEW_ONLY_STAFF)
    )
    op.execute(
        roles_table.update()
        .where(roles_table.c.name == "super_admin")
        .values(permissions=_SUPER_ADMIN_PERMISSIONS_WITH_STATEMENTS)
    )


def downgrade() -> None:
    op.execute(
        roles_table.update()
        .where(roles_table.c.name == "branch_manager")
        .values(permissions=_BRANCH_MANAGER_PERMISSIONS_WITH_EDIT)
    )
    op.execute(
        roles_table.update()
        .where(roles_table.c.name == "super_admin")
        .values(permissions=_SUPER_ADMIN_PERMISSIONS_WITHOUT_STATEMENTS)
    )
