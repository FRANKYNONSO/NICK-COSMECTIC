from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from nickcosmetics_core.exceptions import InvalidRoleError
from nickcosmetics_core.domain.users.permission_registry import REGISTRY_KEYS as _REGISTRY_KEYS  # PATCHREG2


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    BRANCH_MANAGER = "branch_manager"
    STAFF = "cashier"


# Central permission table. Kept here (not scattered across API routers)
# so "who can do what" is defined in exactly one place. See architecture
# doc Section 12.2 — recommend migrating this to a DB-backed permissions
# table once branches multiply past 1.
_PERMISSIONS: dict[Role, set[str]] = {
    Role.SUPER_ADMIN: {
        "manage_users",
        "manage_products",
        "manage_inventory",
        "manage_suppliers",
        "manage_coupons",
        "view_all_reports",
        "view_low_stock",
        "view_financials",
        "configure_system",
        "view_audit_logs",
        "manage_sync",
        "manage_backups",
        "process_sale",
        "view_own_sales",
        "remove_super_admin",
        "transfer_ownership",
        # Download a filtered account-statement (per-staff or
        # store-wide) of sales/credit history. On by default for
        # super_admin/branch_manager, but -- per spec -- it's a plain
        # permission like any other, so a super_admin can grant it to
        # an individual staff account via the per-account permission
        # override in users.py without needing a code change.
        "download_statements",
    },
    Role.BRANCH_MANAGER: {
        # "manage_users" deliberately excluded here (as of the
        # branch_manager view-only-staff migration): a branch manager
        # can view/list staff (that's gated on role alone in
        # api/v1/routers/users.py, not this permission table) but not
        # edit their credentials, deactivate, or delete them, by
        # default. A super_admin can grant a specific, trusted branch
        # manager that edit capability by creating a custom role that
        # includes "manage_users" (via the Roles page) and assigning
        # that one person to it -- see the migration's docstring.
        "manage_products",
        "manage_inventory",
        "manage_suppliers",
        "manage_coupons",
        "view_all_reports",
        "view_low_stock",
        "view_financials",
        "view_audit_logs",
        "manage_sync",
        "process_sale",
        "view_own_sales",
        "download_statements",
        # deliberately excluded: remove_super_admin, transfer_ownership,
        # configure_system, manage_backups -- branch_manager has full
        # operational parity with super_admin but not these: they're
        # account-security/ownership actions, not day-to-day management.
        # Role escalation (PATCH /users/{id}/role) also stays
        # super_admin-only at the router level in users.py, independent
        # of this table, so a branch_manager can't promote anyone
        # (including themselves) to super_admin.
    },
    Role.STAFF: {
        "process_sale",
        "view_own_sales",
        "search_products",
        "view_stock",
        "view_low_stock",
        "register_customer",
        "change_own_password",
    },
}


# Every permission name that exists anywhere in the table above --
# used by the API layer to reject typos/unknown permission names when
# a super_admin sets per-account restrictions (see
# api/v1/routers/users.py's update_user_permissions).
# PATCHP1-MATRIX
# One shared source of truth for the four production roles, keyed by the
# role NAME stored in the roles table. The API itself still reads the roles
# table on every request (api/dependencies.py); this table is what the tests,
# the web admin fallback and the seeding migration are checked against.
_SUPER_ADMIN_PERMISSIONS: frozenset[str] = frozenset({
    "manage_users", "manage_products", "manage_inventory", "manage_suppliers",
    "manage_coupons", "view_all_reports", "view_low_stock", "view_financials",
    "configure_system", "view_audit_logs", "manage_sync", "manage_backups",
    "process_sale", "view_own_sales", "remove_super_admin", "transfer_ownership",
    "download_statements", "search_products", "view_stock", "register_customer",
    "change_own_password", "view_purchase_invoices", "view_restock_list",
    "manage_restock_list", "restock_shelf", "set_shelf_limit", "reduce_shelf_stock",
    "view_received_goods", "view_catalog", "manage_units", "view_inventory_history",
    "view_customer_orders", "view_revenue_totals", "view_inventory_overview",
    "view_stock_alerts", "generate_pos_code", "view_website_code",
    "view_transactions", "use_warehouse",
})

ROLE_PERMISSION_MATRIX: dict[str, frozenset[str]] = {
    "super_admin": _SUPER_ADMIN_PERMISSIONS,
    "branch_manager": _SUPER_ADMIN_PERMISSIONS - frozenset({
        "manage_users", "configure_system", "manage_backups",
        "remove_super_admin", "transfer_ownership", "view_website_code",
    }),
    "cashier": frozenset({
        "process_sale", "view_own_sales", "search_products", "view_stock",
        "view_low_stock", "register_customer", "change_own_password",
        "view_restock_list", "view_catalog", "manage_units", "set_shelf_limit",
        "view_customer_orders", "view_inventory_overview", "view_transactions",
    }),
    "store_assistant": frozenset({
        "change_own_password", "search_products", "view_stock", "view_low_stock",
        "restock_shelf", "set_shelf_limit", "reduce_shelf_stock",
        "view_received_goods", "manage_units", "view_inventory_overview",
        "view_stock_alerts", "use_warehouse",
    }),
}

ALL_PERMISSIONS: frozenset[str] = (
    frozenset().union(*_PERMISSIONS.values()) | _SUPER_ADMIN_PERMISSIONS | _REGISTRY_KEYS
)  # PATCHP1-MATRIX PATCHREG2


@dataclass
class User:
    id: str
    full_name: str
    role: Role
    branch_id: str | None = None
    email: str | None = None
    phone: str | None = None
    is_active: bool = True
    last_login_at: datetime | None = None
    # Permission names revoked from this specific account by a
    # super_admin, on top of whatever the role table above grants --
    # e.g. a branch_manager who normally has "manage_coupons" but has
    # been individually restricted from it. Never applies to
    # super_admin: that role is intentionally the one tier an override
    # can't touch, so there's no path to a super_admin locking
    # themselves (or every super_admin) out by mistake. Set via
    # PATCH /users/{id}/permissions, super_admin-only -- see
    # api/v1/routers/users.py.
    denied_permissions: frozenset[str] = field(default_factory=frozenset)
    _password_hash: str = field(default="", repr=False)

    def can(self, action: str) -> bool:
        """Check whether this user's role permits `action`, minus any
        per-account restriction a super_admin has placed on them.

        Usage in API layer:
            if not current_user.can("manage_inventory"):
                raise PermissionDeniedError(...)
        """
        if action not in _PERMISSIONS.get(self.role, set()):
            return False
        if self.role != Role.SUPER_ADMIN and action in self.denied_permissions:
            return False
        return True

    def can_view_sales_of(self, other_user_id: str) -> bool:
        """Staff can only ever see their own sales; managers/admins see all."""
        if self.role == Role.STAFF:
            return other_user_id == self.id
        return True

    @staticmethod
    def parse_role(value: str) -> Role:
        try:
            return Role(value)
        except ValueError as exc:
            raise InvalidRoleError(value) from exc