"""add roles table for custom roles

Revision ID: o1p2q3r4s5t6
Revises: n0o1p2q3r4s5
Create Date: 2026-08-24 00:00:00.000000

Replaces the fixed users.role CHECK constraint
(super_admin/branch_manager/staff only) with a proper `roles` table, so
a super admin can define further roles (e.g.
"assistant_branch_manager") with their own editable permission set --
editing a role's permissions here changes what everyone holding that
role can do immediately, since api/dependencies.py's
require_permission() now reads this table directly per request
instead of the old hardcoded permission table in
nickcosmetics_core.domain.users.user.

The three existing roles are seeded as system rows (is_system=True,
can't be deleted/renamed) with the exact permission sets that were
previously hardcoded there, so no existing account's access changes.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'o1p2q3r4s5t6'
down_revision: Union[str, None] = 'n0o1p2q3r4s5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SUPER_ADMIN_PERMISSIONS = [
    "manage_users", "manage_products", "manage_inventory", "manage_suppliers",
    "manage_coupons", "view_all_reports", "view_low_stock", "view_financials",
    "configure_system", "view_audit_logs", "manage_sync", "manage_backups",
    "process_sale", "view_own_sales", "remove_super_admin", "transfer_ownership",
]

BRANCH_MANAGER_PERMISSIONS = [
    "manage_users", "manage_products", "manage_inventory", "manage_suppliers",
    "manage_coupons", "view_all_reports", "view_low_stock", "view_financials",
    "view_audit_logs", "manage_sync", "process_sale", "view_own_sales",
]

STAFF_PERMISSIONS = [
    "process_sale", "view_own_sales", "search_products", "view_stock",
    "view_low_stock", "register_customer", "change_own_password",
]

roles_table = sa.table(
    "roles",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("display_name", sa.String),
    sa.column("permissions", postgresql.JSONB),
    sa.column("is_system", sa.Boolean),
    sa.column("created_by", postgresql.UUID(as_uuid=True)),
)


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("display_name", sa.String, nullable=False),
        sa.Column("permissions", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Plain Python-generated UUIDs (matching the app's existing
    # _uuid_pk() convention elsewhere) rather than a server-side
    # gen_random_uuid() call, since bulk_insert binds these as
    # ordinary parameters, not raw SQL.
    op.bulk_insert(
        roles_table,
        [
            {
                "id": uuid.uuid4(),
                "name": "super_admin",
                "display_name": "Super Admin",
                "permissions": SUPER_ADMIN_PERMISSIONS,
                "is_system": True,
                "created_by": None,
            },
            {
                "id": uuid.uuid4(),
                "name": "branch_manager",
                "display_name": "Branch Manager",
                "permissions": BRANCH_MANAGER_PERMISSIONS,
                "is_system": True,
                "created_by": None,
            },
            {
                "id": uuid.uuid4(),
                "name": "staff",
                "display_name": "Staff",
                "permissions": STAFF_PERMISSIONS,
                "is_system": True,
                "created_by": None,
            },
        ],
    )

    op.drop_constraint("ck_user_role", "users", type_="check")
    op.create_foreign_key(
        "fk_users_role_roles_name", "users", "roles", ["role"], ["name"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_role_roles_name", "users", type_="foreignkey")
    op.create_check_constraint(
        "ck_user_role", "users", "role IN ('super_admin','branch_manager','staff')"
    )
    op.drop_table("roles")
