"""credit unit price/paid columns, view_purchase_invoices permission, cashier display name

Revision ID: z1a2b3c4d5e6
Revises: f2a7cffbcd81
Create Date: 2026-08-28 00:00:00.000000

Three fixes from the August 2026 correction pass:

  1. credit_entries gets unit_price_kobo (the per-unit price the
     total was calculated from, for audit/traceability) and
     stock_deducted, so we can tell, for old rows created before this
     migration, that their stock was never deducted (stock_deducted
     defaults to true for pre-existing rows -- see data migration
     below -- so the app never tries to double-deduct history it
     didn't originally touch). The initial deposit taken at opening
     time is recorded as an ordinary CreditPayment row (same table
     top-ups already use), not a new column -- "amount owed" stays a
     read-time computation of amount_kobo minus every payment, exactly
     as it already was.
  2. "view_purchase_invoices" is added to every existing role's
     permission set (staff/branch_manager/super_admin) so a cashier
     can be granted read-only access to purchase invoice history
     without also getting "manage_suppliers" (which lets you add/
     edit/delete invoices and manage suppliers). Existing behaviour
     is unchanged for branch_manager/super_admin, who already had
     manage_suppliers; this purely adds a narrower, view-only grant
     everyone (including staff) now holds by default -- a super admin
     can still revoke it per-account via denied_permissions the same
     as any other permission.
  3. The "staff" role's display_name changes from "Staff" to
     "Cashiers" -- the role's `name` slug (used everywhere in code:
     users.role, permission checks, FKs) is untouched, only the
     human-facing label changes, per spec: rename the POS-operator
     facing label without touching the underlying role identity.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, None] = "f2a7cffbcd81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


roles_table = sa.table(
    "roles",
    sa.column("name", sa.String),
    sa.column("display_name", sa.String),
    sa.column("permissions", postgresql.JSONB),
)


def upgrade() -> None:
    op.add_column("credit_entries", sa.Column("unit_price_kobo", sa.BigInteger(), nullable=True))
    # True for every row that exists at migration time -- they were
    # opened before inventory-deduction-on-credit existed, so we must
    # not deduct stock for them retroactively. New rows created by the
    # API from now on explicitly set this to True at creation time,
    # once the deduction has actually happened in the same transaction.
    op.add_column(
        "credit_entries",
        sa.Column("stock_deducted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    bind = op.get_bind()
    for role_name in ("staff", "branch_manager", "super_admin"):
        row = bind.execute(
            sa.select(roles_table.c.permissions).where(roles_table.c.name == role_name)
        ).first()
        if row is None:
            continue
        perms = list(row[0] or [])
        if "view_purchase_invoices" not in perms:
            perms.append("view_purchase_invoices")
        bind.execute(
            roles_table.update()
            .where(roles_table.c.name == role_name)
            .values(permissions=perms)
        )

    bind.execute(
        roles_table.update()
        .where(roles_table.c.name == "staff")
        .values(display_name="Cashiers")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        roles_table.update()
        .where(roles_table.c.name == "staff")
        .values(display_name="Staff")
    )
    for role_name in ("staff", "branch_manager", "super_admin"):
        row = bind.execute(
            sa.select(roles_table.c.permissions).where(roles_table.c.name == role_name)
        ).first()
        if row is None:
            continue
        perms = [p for p in (row[0] or []) if p != "view_purchase_invoices"]
        bind.execute(
            roles_table.update()
            .where(roles_table.c.name == role_name)
            .values(permissions=perms)
        )

    op.drop_column("credit_entries", "stock_deducted")
    op.drop_column("credit_entries", "unit_price_kobo")
