"""P1 tests: shared permission matrix, my-permissions endpoint, locked
system roles, store_assistant creation rule.
Run ONLY against shelf_test. Every test is rolled back by db_session.
"""
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Role as RoleModel
from nickcosmetics_core.domain.users.user import ALL_PERMISSIONS, ROLE_PERMISSION_MATRIX
from tests.test_goods_receiving import _branch, _headers, _user

FOUR = ("super_admin", "branch_manager", "cashier", "store_assistant")
ME_PERMS = "/api/v1/auth/me/permissions"

EXPECT = [
    ("super_admin", "view_website_code", True),
    ("branch_manager", "view_website_code", False),
    ("cashier", "view_website_code", False),
    ("store_assistant", "view_website_code", False),
    ("branch_manager", "generate_pos_code", True),
    ("cashier", "generate_pos_code", False),
    ("store_assistant", "generate_pos_code", False),
    ("branch_manager", "view_revenue_totals", True),
    ("cashier", "view_revenue_totals", False),
    ("store_assistant", "view_revenue_totals", False),
    ("cashier", "view_financials", False),
    ("cashier", "view_own_sales", True),
    ("store_assistant", "view_own_sales", False),
    ("cashier", "process_sale", True),
    ("store_assistant", "process_sale", False),
    ("cashier", "view_transactions", True),
    ("store_assistant", "view_transactions", False),
    ("cashier", "use_warehouse", False),
    ("store_assistant", "use_warehouse", True),
    ("branch_manager", "use_warehouse", True),
    ("branch_manager", "view_purchase_invoices", True),
    ("cashier", "view_purchase_invoices", False),
    ("store_assistant", "view_purchase_invoices", False),
    ("branch_manager", "view_all_reports", True),
    ("cashier", "view_all_reports", False),
    ("store_assistant", "view_all_reports", False),
    ("cashier", "set_shelf_limit", True),
    ("store_assistant", "set_shelf_limit", True),
    ("cashier", "reduce_shelf_stock", False),
    ("store_assistant", "reduce_shelf_stock", True),
    ("cashier", "restock_shelf", False),
    ("store_assistant", "restock_shelf", True),
    ("cashier", "view_received_goods", False),
    ("store_assistant", "view_received_goods", True),
    ("cashier", "view_catalog", True),
    ("store_assistant", "view_catalog", False),
    ("cashier", "view_restock_list", True),
    ("store_assistant", "view_restock_list", False),
    ("cashier", "manage_restock_list", False),
    ("cashier", "view_customer_orders", True),
    ("store_assistant", "view_customer_orders", False),
    ("cashier", "manage_units", True),
    ("store_assistant", "manage_units", True),
    ("cashier", "view_inventory_history", False),
    ("store_assistant", "view_inventory_history", False),
    ("cashier", "manage_inventory", False),
    ("store_assistant", "manage_inventory", False),
    ("cashier", "manage_products", False),
    ("store_assistant", "manage_products", False),
    ("cashier", "view_stock_alerts", False),
    ("store_assistant", "view_stock_alerts", True),
    ("cashier", "view_inventory_overview", True),
    ("store_assistant", "view_inventory_overview", True),
    ("cashier", "view_low_stock", True),
    ("store_assistant", "view_low_stock", True),
]


def test_matrix_has_exactly_the_four_roles():
    assert set(ROLE_PERMISSION_MATRIX) == set(FOUR)


def test_super_admin_is_a_superset_of_every_role():
    sa = ROLE_PERMISSION_MATRIX["super_admin"]
    for role in FOUR:
        assert ROLE_PERMISSION_MATRIX[role] <= sa, role


def test_every_matrix_name_is_a_known_permission():
    for role, perms in ROLE_PERMISSION_MATRIX.items():
        assert perms <= ALL_PERMISSIONS, (role, sorted(perms - ALL_PERMISSIONS))


def test_previously_missing_names_are_now_known():
    for name in ("view_purchase_invoices", "view_restock_list", "manage_restock_list", "restock_shelf"):
        assert name in ALL_PERMISSIONS, name


@pytest.mark.parametrize("role,perm,expected", EXPECT)
def test_matrix_rule(role, perm, expected):
    assert (perm in ROLE_PERMISSION_MATRIX[role]) is expected, (role, perm)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", FOUR)
async def test_my_permissions_matches_role_row(client, db_session, role):
    branch = await _branch(db_session)
    user = await _user(db_session, role, branch.id)
    row = (
        await db_session.execute(select(RoleModel.permissions).where(RoleModel.name == role))
    ).scalar_one()
    resp = await client.get(ME_PERMS, headers=_headers(user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == role
    assert sorted(body["permissions"]) == sorted(set(row))


@pytest.mark.asyncio
async def test_my_permissions_applies_grants_and_denials(client, db_session):
    user = await _user(db_session, "cashier", None)
    user.granted_permissions = ["manage_coupons"]
    user.denied_permissions = ["view_low_stock"]
    await db_session.flush()
    resp = await client.get(ME_PERMS, headers=_headers(user))
    assert resp.status_code == 200, resp.text
    perms = set(resp.json()["permissions"])
    assert "manage_coupons" in perms
    assert "view_low_stock" not in perms


@pytest.mark.asyncio
async def test_my_permissions_ignores_denials_for_super_admin(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    row = (
        await db_session.execute(select(RoleModel.permissions).where(RoleModel.name == "super_admin"))
    ).scalar_one()
    admin.denied_permissions = ["process_sale"]
    await db_session.flush()
    resp = await client.get(ME_PERMS, headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["permissions"]) == set(row)


@pytest.mark.asyncio
async def test_my_permissions_requires_login(client):
    resp = await client.get(ME_PERMS)
    assert resp.status_code in (401, 403), resp.status_code


# PATCHR1-ROLES: system roles are now editable (display name for all four,
# permissions for all except super_admin). Delete stays blocked (test below).
R1_EDITABLE = ("branch_manager", "cashier", "store_assistant")


async def _r1_role_row(db_session, name):
    row = (
        await db_session.execute(
            select(RoleModel.display_name, RoleModel.permissions).where(RoleModel.name == name)
        )
    ).one()
    return row[0], list(row[1])


async def _r1_restore_role(db_session, name, display_name, permissions):
    from sqlalchemy import update as sa_update

    await db_session.execute(
        sa_update(RoleModel)
        .where(RoleModel.name == name)
        .values(display_name=display_name, permissions=permissions)
    )
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", R1_EDITABLE)
async def test_r1_system_role_name_and_permissions_editable(client, db_session, name):
    admin = await _user(db_session, "super_admin", None)
    old_name, old_perms = await _r1_role_row(db_session, name)
    try:
        resp = await client.patch(
            "/api/v1/roles/" + name,
            json={"display_name": "Renamed " + name, "permissions": ["view_stock"]},
            headers=_headers(admin),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == name
        assert body["display_name"] == "Renamed " + name
        assert body["permissions"] == ["view_stock"]
        new_name, new_perms = await _r1_role_row(db_session, name)
        assert new_name == "Renamed " + name
        assert new_perms == ["view_stock"]
    finally:
        await _r1_restore_role(db_session, name, old_name, old_perms)


@pytest.mark.asyncio
async def test_r1_super_admin_name_editable_permissions_fixed(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    old_name, old_perms = await _r1_role_row(db_session, "super_admin")
    try:
        resp = await client.patch(
            "/api/v1/roles/super_admin",
            json={"permissions": ["view_stock"]},
            headers=_headers(admin),
        )
        assert resp.status_code == 403, resp.text
        resp = await client.patch(
            "/api/v1/roles/super_admin",
            json={"display_name": "Should Not Apply", "permissions": ["view_stock"]},
            headers=_headers(admin),
        )
        assert resp.status_code == 403, resp.text
        same_name, same_perms = await _r1_role_row(db_session, "super_admin")
        assert same_name == old_name
        assert sorted(same_perms) == sorted(old_perms)

        resp = await client.patch(
            "/api/v1/roles/super_admin",
            json={"display_name": "Owner"},
            headers=_headers(admin),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "super_admin"
        assert resp.json()["display_name"] == "Owner"
        new_name, new_perms = await _r1_role_row(db_session, "super_admin")
        assert new_name == "Owner"
        assert sorted(new_perms) == sorted(old_perms)
    finally:
        await _r1_restore_role(db_session, "super_admin", old_name, old_perms)


@pytest.mark.asyncio
async def test_r1_blank_display_name_is_refused(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    old_name, old_perms = await _r1_role_row(db_session, "cashier")
    try:
        resp = await client.patch(
            "/api/v1/roles/cashier", json={"display_name": "   "}, headers=_headers(admin)
        )
        assert resp.status_code == 422, resp.text
        same_name, same_perms = await _r1_role_row(db_session, "cashier")
        assert same_name == old_name
        assert sorted(same_perms) == sorted(old_perms)
    finally:
        await _r1_restore_role(db_session, "cashier", old_name, old_perms)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,code",
    [("super_admin", 400), ("branch_manager", 403), ("cashier", 403), ("store_assistant", 403)],
)
async def test_system_roles_cannot_be_deleted(client, db_session, name, code):
    admin = await _user(db_session, "super_admin", None)
    resp = await client.delete("/api/v1/roles/" + name, headers=_headers(admin))
    assert resp.status_code == code, resp.text


@pytest.mark.asyncio
async def test_custom_roles_can_still_be_edited(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    name = "tmp_" + uuid.uuid4().hex[:8]
    created = await client.post(
        "/api/v1/roles",
        json={"name": name, "display_name": "Tmp", "permissions": ["view_stock"]},
        headers=_headers(admin),
    )
    assert created.status_code == 201, created.text
    edited = await client.patch(
        "/api/v1/roles/" + name,
        json={"permissions": ["view_stock", "set_shelf_limit"]},
        headers=_headers(admin),
    )
    assert edited.status_code == 200, edited.text


@pytest.mark.asyncio
async def test_branch_manager_cannot_create_store_assistant(client, db_session):
    branch = await _branch(db_session)
    manager = await _user(db_session, "branch_manager", branch.id)
    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Not Allowed",
            "role": "store_assistant",
            "email": uuid.uuid4().hex[:8] + "@example.com",
            "password": "SecurePass123",
        },
        headers=_headers(manager),
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_super_admin_can_create_store_assistant(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Shelf Assistant",
            "role": "store_assistant",
            "email": uuid.uuid4().hex[:8] + "@example.com",
            "password": "SecurePass123",
        },
        headers=_headers(admin),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "store_assistant"