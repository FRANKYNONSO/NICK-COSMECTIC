"""PATCHREG1-REGISTRY: checks for the permission registry."""
import re

from nickcosmetics_core.domain.users.permission_registry import (
    PAGE_GROUPS,
    PERMISSION_REGISTRY,
    REGISTRY_KEYS,
    grouped_registry,
)
from nickcosmetics_core.domain.users.user import ALL_PERMISSIONS

_AGREED_OWNER_ONLY = {
    "remove_super_admin", "transfer_ownership", "change_user_role",
    "edit_user_permissions", "manage_roles",
}


def test_no_duplicate_keys():
    keys = [p.key for p in PERMISSION_REGISTRY]
    assert len(keys) == len(set(keys))


def test_registry_matches_current_all_permissions():
    assert REGISTRY_KEYS == ALL_PERMISSIONS


def test_every_entry_has_label_and_known_group():
    for p in PERMISSION_REGISTRY:
        assert p.label.strip(), p.key
        assert p.group in PAGE_GROUPS, p.key


def test_keys_look_right():
    for p in PERMISSION_REGISTRY:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", p.key), p.key


# PATCHG1-TESTS: page groups are the sidebar sections, in sidebar order.
def test_page_groups_follow_sidebar_order():
    assert list(PAGE_GROUPS) == [
        "Dashboard", "POS", "Inventory", "Transactions", "Invoice",
        "Warehouse", "Reports", "Team", "Settings", "Chat and Announcements",
    ]


def test_owner_only_keys_are_only_the_agreed_ones():
    owner_only = {p.key for p in PERMISSION_REGISTRY if p.owner_only}
    assert owner_only <= _AGREED_OWNER_ONLY


def test_grouped_registry_follows_sidebar_order_and_hides_owner_only():
    groups = grouped_registry()
    names = [g["group"] for g in groups]
    assert names == [g for g in PAGE_GROUPS if g in names]
    shown = set()
    for g in groups:
        for t in g["tabs"]:
            labels = [x["label"] for x in t["permissions"]]
            assert labels == sorted(labels, key=str.lower)
            shown.update(x["key"] for x in t["permissions"])
    assert "remove_super_admin" not in shown
    assert "transfer_ownership" not in shown
    assert len(shown) == len(REGISTRY_KEYS) - 2


def test_moved_permissions_sit_under_their_sidebar_section():
    where = {p.key: (p.group, p.tab) for p in PERMISSION_REGISTRY}
    assert where["manage_coupons"] == ("POS", "Coupons")
    assert where["manage_website_coupons"] == ("POS", "Coupons")
    assert where["manage_restock_list"] == ("Inventory", "Restock List")
    assert where["view_restock_list"] == ("Inventory", "Restock List")
    assert where["view_customer_orders"] == ("Inventory", "Customer Orders")
    assert where["manage_suppliers"] == ("Invoice", "Suppliers")
    assert where["view_purchase_invoices"] == ("Invoice", "")
    assert where["download_statements"] == ("Reports", "Statements")
    assert where["acknowledge_shift_violations"] == ("Team", "Shifts")
