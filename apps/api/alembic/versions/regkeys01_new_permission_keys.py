"""Add new permission keys to the roles table (union only)

Revision ID: regkeys01
Revises: cashier_key_01

PATCHREG2-NEW-KEYS

Adds the 14 permission keys that were agreed for actions that had no
permission of their own. UNION ONLY: a name a role already holds is kept,
nothing is removed or replaced, and a role that already holds every name
is not touched, so running it twice changes nothing. Only the roles named
below are touched (store_assistant and storefront_service are not).
The lists are frozen copies, not imported from app code.

No downgrade: removing names could lock people out, so downgrade does
nothing on purpose.
"""
import json

from alembic import op

revision = "regkeys01"
down_revision = "cashier_key_01"
branch_labels = None
depends_on = None

_NEW_GRANTS = {
    "super_admin": [
        "acknowledge_shift_violations",
        "confirm_theft_losses",
        "create_staff",
        "manage_website_coupons",
        "manage_website_orders",
        "post_announcements",
        "process_pending_changes",
        "process_refunds",
        "record_losses",
        "review_password_resets",
        "review_staff_applications",
        "view_dashboard_notifications",
        "view_open_bookings",
        "view_staff",
    ],
    "branch_manager": [
        "create_staff",
        "manage_website_orders",
        "process_pending_changes",
        "process_refunds",
        "record_losses",
        "review_password_resets",
        "review_staff_applications",
        "view_dashboard_notifications",
        "view_open_bookings",
        "view_staff",
    ],
    "cashier": [
        "record_losses",
        "view_open_bookings",
    ],
}


def upgrade() -> None:
    for role_name, names in _NEW_GRANTS.items():
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
