"""Seed the role permission matrix into the roles table (union only)

Revision ID: permseed01
Revises: stasst01

PATCHP1B-SEED-PERMISSIONS

Adds the permission names of the role matrix to the four production roles
(super_admin, branch_manager, staff, store_assistant). UNION ONLY: a name a
role already holds is kept, nothing is removed or replaced, and a role that
already holds every name is not touched at all, so running it twice changes
nothing. The lists below are frozen copies; they are not imported from app
code, so later edits to the app matrix never change what this migration does.

No downgrade: it cannot know which names were there before, and removing
names could lock people out, so downgrade does nothing on purpose.
"""
import json

from alembic import op

revision = "permseed01"
down_revision = "stasst01"
branch_labels = None
depends_on = None

_ROLE_PERMISSIONS = {
    "super_admin": [
        "change_own_password",
        "configure_system",
        "download_statements",
        "generate_pos_code",
        "manage_backups",
        "manage_coupons",
        "manage_inventory",
        "manage_products",
        "manage_restock_list",
        "manage_suppliers",
        "manage_sync",
        "manage_units",
        "manage_users",
        "process_sale",
        "reduce_shelf_stock",
        "register_customer",
        "remove_super_admin",
        "restock_shelf",
        "search_products",
        "set_shelf_limit",
        "transfer_ownership",
        "use_warehouse",
        "view_all_reports",
        "view_audit_logs",
        "view_catalog",
        "view_customer_orders",
        "view_financials",
        "view_inventory_history",
        "view_inventory_overview",
        "view_low_stock",
        "view_own_sales",
        "view_purchase_invoices",
        "view_received_goods",
        "view_restock_list",
        "view_revenue_totals",
        "view_stock",
        "view_stock_alerts",
        "view_transactions",
        "view_website_code",
    ],
    "branch_manager": [
        "change_own_password",
        "download_statements",
        "generate_pos_code",
        "manage_coupons",
        "manage_inventory",
        "manage_products",
        "manage_restock_list",
        "manage_suppliers",
        "manage_sync",
        "manage_units",
        "process_sale",
        "reduce_shelf_stock",
        "register_customer",
        "restock_shelf",
        "search_products",
        "set_shelf_limit",
        "use_warehouse",
        "view_all_reports",
        "view_audit_logs",
        "view_catalog",
        "view_customer_orders",
        "view_financials",
        "view_inventory_history",
        "view_inventory_overview",
        "view_low_stock",
        "view_own_sales",
        "view_purchase_invoices",
        "view_received_goods",
        "view_restock_list",
        "view_revenue_totals",
        "view_stock",
        "view_stock_alerts",
        "view_transactions",
    ],
    "staff": [
        "change_own_password",
        "manage_units",
        "process_sale",
        "register_customer",
        "search_products",
        "set_shelf_limit",
        "view_catalog",
        "view_customer_orders",
        "view_inventory_overview",
        "view_low_stock",
        "view_own_sales",
        "view_restock_list",
        "view_stock",
        "view_transactions",
    ],
    "store_assistant": [
        "change_own_password",
        "manage_units",
        "reduce_shelf_stock",
        "restock_shelf",
        "search_products",
        "set_shelf_limit",
        "use_warehouse",
        "view_inventory_overview",
        "view_low_stock",
        "view_received_goods",
        "view_stock",
        "view_stock_alerts",
    ],
}


def upgrade() -> None:
    for role_name, names in _ROLE_PERMISSIONS.items():
        payload = json.dumps(sorted(names))
        op.execute(
            "UPDATE roles SET permissions = ("
            "SELECT COALESCE(jsonb_agg(v ORDER BY v), '[]'::jsonb) FROM ("
            "SELECT jsonb_array_elements_text(roles.permissions) AS v "
            "UNION "
            "SELECT jsonb_array_elements_text('" + payload + "'::jsonb) AS v"
            ") s), updated_at = now() "
            "WHERE name = '" + role_name + "' "
            "AND NOT (permissions @> '" + payload + "'::jsonb)"
        )


def downgrade() -> None:
    pass
