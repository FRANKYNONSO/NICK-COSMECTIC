from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from nickcosmetics_core.exceptions import InvalidRoleError


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    BRANCH_MANAGER = "branch_manager"
    STAFF = "staff"


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
        "view_all_reports",
        "view_financials",
        "configure_system",
        "view_audit_logs",
        "manage_sync",
        "manage_backups",
        "process_sale",
        "view_own_sales",
        "remove_super_admin",
        "transfer_ownership",
    },
    Role.BRANCH_MANAGER: {
        "manage_products",
        "manage_inventory",
        "manage_suppliers",
        "view_all_reports",
        "view_financials",
        "view_audit_logs",
        "manage_sync",
        "process_sale",
        "view_own_sales",
        # deliberately excluded: manage_users (super admin removal),
        # remove_super_admin, transfer_ownership, configure_system
    },
    Role.STAFF: {
        "process_sale",
        "view_own_sales",
        "search_products",
        "view_stock",
        "register_customer",
        "change_own_password",
    },
}


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
    _password_hash: str = field(default="", repr=False)

    def can(self, action: str) -> bool:
        """Check whether this user's role permits `action`.

        Usage in API layer:
            if not current_user.can("manage_inventory"):
                raise PermissionDeniedError(...)
        """
        return action in _PERMISSIONS.get(self.role, set())

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
