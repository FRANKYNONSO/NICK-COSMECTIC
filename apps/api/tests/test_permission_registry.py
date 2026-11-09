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


def test_page_groups_are_a_to_z():
    assert list(PAGE_GROUPS) == sorted(PAGE_GROUPS, key=str.lower)


def test_owner_only_keys_are_only_the_agreed_ones():
    owner_only = {p.key for p in PERMISSION_REGISTRY if p.owner_only}
    assert owner_only <= _AGREED_OWNER_ONLY


def test_grouped_registry_is_a_to_z_and_hides_owner_only():
    groups = grouped_registry()
    names = [g["group"] for g in groups]
    assert names == sorted(names, key=str.lower)
    shown = set()
    for g in groups:
        for t in g["tabs"]:
            labels = [x["label"] for x in t["permissions"]]
            assert labels == sorted(labels, key=str.lower)
            shown.update(x["key"] for x in t["permissions"])
    assert "remove_super_admin" not in shown
    assert "transfer_ownership" not in shown
    assert len(shown) == len(REGISTRY_KEYS) - 2
