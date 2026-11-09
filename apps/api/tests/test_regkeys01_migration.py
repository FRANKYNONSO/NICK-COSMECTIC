"""PATCHREG2-NEW-KEYS: checks for the 14 new permission keys and migration regkeys01."""
import importlib.util
import pathlib

from nickcosmetics_core.domain.users.permission_registry import REGISTRY_KEYS
from nickcosmetics_core.domain.users.user import ALL_PERMISSIONS

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "regkeys01_new_permission_keys.py"
)

NEW_KEYS = {
    "acknowledge_shift_violations", "confirm_theft_losses", "create_staff",
    "manage_website_coupons", "manage_website_orders", "post_announcements",
    "process_pending_changes", "process_refunds", "record_losses",
    "review_password_resets", "review_staff_applications",
    "view_dashboard_notifications", "view_open_bookings", "view_staff",
}


def _load():
    spec = importlib.util.spec_from_file_location("regkeys01_mod", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_new_keys_are_in_registry_and_all_permissions():
    assert NEW_KEYS <= REGISTRY_KEYS
    assert NEW_KEYS <= ALL_PERMISSIONS
    assert len(REGISTRY_KEYS) == 53


def test_migration_chain():
    mod = _load()
    assert mod.revision == "regkeys01"
    assert mod.down_revision == "cashier_key_01"


def test_migration_grants_only_agreed_keys_to_agreed_roles():
    grants = _load()._NEW_GRANTS
    assert set(grants) == {"super_admin", "branch_manager", "cashier"}
    union = set()
    for names in grants.values():
        assert len(names) == len(set(names))
        union |= set(names)
    assert union == NEW_KEYS
    assert set(grants["super_admin"]) == NEW_KEYS
    assert set(grants["cashier"]) == {"record_losses", "view_open_bookings"}
    assert set(grants["cashier"]) <= set(grants["branch_manager"])
    assert set(grants["branch_manager"]) <= set(grants["super_admin"])


def test_migration_never_gives_big_or_owner_keys():
    grants = _load()._NEW_GRANTS
    for names in grants.values():
        assert "view_all_reports" not in names
        assert "remove_super_admin" not in names
        assert "transfer_ownership" not in names
    for banned in ("acknowledge_shift_violations", "confirm_theft_losses",
                   "post_announcements", "manage_website_coupons"):
        assert banned not in grants["branch_manager"]
        assert banned not in grants["cashier"]
