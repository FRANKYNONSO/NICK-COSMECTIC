"""Permission registry (PATCHREG1-REGISTRY).

ONE list of every permission: key, friendly label, page group, tab.
The Roles page and the per-staff picker are meant to read this list.
To add a feature: add one line here, put require_permission("key") on
the route, and use Perms.can("key") in the web page.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionInfo:
    key: str
    label: str
    group: str
    tab: str = ""
    owner_only: bool = False


# The page groups, in A-Z order.
PAGE_GROUPS: tuple[str, ...] = (
    "Branches",
    "Chat and Announcements",
    "Coupons",
    "Credit",
    "Customer Orders",
    "Dashboard",
    "Inventory",
    "Invoices",
    "POS",
    "Reports",
    "Restock List",
    "Settings",
    "Shifts",
    "Statements",
    "Suppliers",
    "Team",
    "Transactions",
    "Warehouse",
)

PERMISSION_REGISTRY: tuple[PermissionInfo, ...] = (
    PermissionInfo("change_own_password", "Change own password", "Team", "Own account"),
    PermissionInfo("configure_system", "Configure system settings", "Settings"),
    PermissionInfo("download_statements", "Download account statements", "Statements"),
    PermissionInfo("generate_pos_code", "Generate POS code", "POS"),
    PermissionInfo("manage_backups", "Manage backups", "Settings"),
    PermissionInfo("manage_coupons", "Issue & void coupons", "Coupons"),
    PermissionInfo("manage_inventory", "Manage inventory & warehouse", "Inventory", "Stock"),
    PermissionInfo("manage_products", "Manage products & categories", "Inventory", "Products"),
    PermissionInfo("manage_restock_list", "Manage restock list", "Restock List"),
    PermissionInfo("manage_suppliers", "Manage suppliers & purchasing", "Suppliers"),
    PermissionInfo("manage_sync", "Manage device sync", "Settings"),
    PermissionInfo("manage_units", "Manage packaging units", "Inventory", "Products"),
    PermissionInfo("manage_users", "Manage staff accounts", "Team", "Staff"),
    PermissionInfo("process_sale", "Process sales", "POS"),
    PermissionInfo("reduce_shelf_stock", "Reduce shelf stock", "Inventory", "Stock"),
    PermissionInfo("register_customer", "Register customers", "POS"),
    PermissionInfo("remove_super_admin", "Remove super admin", "Team", "Staff", owner_only=True),
    PermissionInfo("restock_shelf", "Restock shelf", "Inventory", "Stock"),
    PermissionInfo("search_products", "Search products", "POS"),
    PermissionInfo("set_shelf_limit", "Set shelf limit", "Inventory", "Stock"),
    PermissionInfo("transfer_ownership", "Transfer ownership", "Team", "Staff", owner_only=True),
    PermissionInfo("use_warehouse", "Use warehouse", "Warehouse"),
    PermissionInfo("view_all_reports", "View store-wide reports", "Reports"),
    PermissionInfo("view_audit_logs", "View audit logs", "Reports"),
    PermissionInfo("view_catalog", "View product catalog", "Inventory", "Products"),
    PermissionInfo("view_customer_orders", "View customer orders", "Customer Orders"),
    PermissionInfo("view_financials", "View financial totals", "Reports"),
    PermissionInfo("view_inventory_history", "View inventory history", "Inventory", "Stock"),
    PermissionInfo("view_inventory_overview", "View inventory overview", "Inventory", "Stock"),
    PermissionInfo("view_low_stock", "View low stock", "Inventory", "Alerts"),
    PermissionInfo("view_own_sales", "View own sales", "Transactions", "Sales"),
    PermissionInfo("view_purchase_invoices", "View purchase invoices", "Invoices"),
    PermissionInfo("view_received_goods", "View received goods", "Inventory", "Stock"),
    PermissionInfo("view_restock_list", "View restock list", "Restock List"),
    PermissionInfo("view_revenue_totals", "View revenue totals", "Dashboard"),
    PermissionInfo("view_stock", "View stock", "Inventory", "Stock"),
    PermissionInfo("view_stock_alerts", "View stock alerts", "Inventory", "Alerts"),
    PermissionInfo("view_transactions", "View transactions", "Transactions", "Sales"),
    PermissionInfo("view_website_code", "View website code", "Settings"),
    PermissionInfo("acknowledge_shift_violations", "Acknowledge shift violations", "Shifts"),
    PermissionInfo("confirm_theft_losses", "Confirm theft losses", "Transactions", "Losses"),
    PermissionInfo("create_staff", "Create staff accounts", "Team", "Staff"),
    PermissionInfo("manage_website_coupons", "Manage website coupons", "Coupons"),
    PermissionInfo("manage_website_orders", "Manage website orders", "Transactions", "Website Orders"),
    PermissionInfo("post_announcements", "Post announcements", "Chat and Announcements"),
    PermissionInfo("process_pending_changes", "Mark pending changes as processed", "Transactions", "Pending Change"),
    PermissionInfo("process_refunds", "Mark refunds as processed", "Transactions", "Refunds"),
    PermissionInfo("record_losses", "Record losses", "Transactions", "Losses"),
    PermissionInfo("review_password_resets", "Review password reset requests", "Team", "Password Requests"),
    PermissionInfo("review_staff_applications", "Review staff applications", "Team", "Applications"),
    PermissionInfo("view_dashboard_notifications", "View dashboard notifications", "Dashboard"),
    PermissionInfo("view_open_bookings", "View open bookings", "Transactions", "Open Booking"),
    PermissionInfo("view_staff", "View staff list", "Team", "Staff"),
)

REGISTRY_KEYS: frozenset[str] = frozenset(p.key for p in PERMISSION_REGISTRY)


def grouped_registry(include_owner_only: bool = False) -> list[dict]:
    """Registry as groups > tabs > permissions, every level in A-Z order.
    Groups with no permissions are left out. Owner-only keys are left out
    unless asked for."""
    result: list[dict] = []
    for group in PAGE_GROUPS:
        items = [
            p for p in PERMISSION_REGISTRY
            if p.group == group and (include_owner_only or not p.owner_only)
        ]
        if not items:
            continue
        tabs = sorted({p.tab for p in items}, key=lambda t: (t != "", t.lower()))
        result.append({
            "group": group,
            "tabs": [
                {
                    "tab": t,
                    "permissions": [
                        {"key": p.key, "label": p.label}
                        for p in sorted((x for x in items if x.tab == t), key=lambda x: x.label.lower())
                    ],
                }
                for t in tabs
            ],
        })
    return result
