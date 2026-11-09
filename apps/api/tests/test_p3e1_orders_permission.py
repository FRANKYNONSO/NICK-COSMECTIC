# PATCHP3E1-TESTS: website orders use the manage_website_orders key and stay inside the user's branch
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, Role, Sale, StorefrontOrder, StorefrontOrderSale, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "manage_website_orders"
OTHER_KEY = "manage_users"
URL = "/api/v1/storefront/orders"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3E1 Test User",
        role=role,
        email=uuid.uuid4().hex[:8] + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=branch_id,
        is_active=True,
        denied_permissions=denied or [],
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_headers(user):
    token = create_access_token(str(user.id), user.role, str(user.branch_id) if user.branch_id else None)
    return {"Authorization": "Bearer " + token}


async def _set_role(db_session, name, add=(), remove=()):
    # Make sure the role row has the keys in add and not the keys in remove. Rolled back after the test.
    role = (await db_session.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        role = Role(name=name, display_name=name, permissions=list(add), is_system=False, created_by=None)
        db_session.add(role)
    else:
        perms = [p for p in list(role.permissions or []) if p not in remove and p not in add]
        perms.extend(add)
        role.permissions = perms
    await db_session.flush()


def _new_role_name():
    return "p3e1_" + uuid.uuid4().hex[:8]


async def _make_order(db_session, branch_id):
    # One order, one sale, one link row. No stock and no items are needed.
    sale = Sale(branch_id=branch_id, channel="website", subtotal_kobo=1000, total_kobo=1000)
    db_session.add(sale)
    await db_session.flush()
    order = StorefrontOrder(
        reference="P3E1-" + uuid.uuid4().hex,
        customer_name="P3E1 Customer",
        customer_phone="0810000000",
        delivery_address="1 Test Street",
        delivery_state="Taraba",
        subtotal_kobo=1000,
        total_kobo=1000,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(StorefrontOrderSale(order_id=order.id, sale_id=sale.id, branch_id=branch_id))
    await db_session.flush()
    return order


async def test_a_custom_role_sees_only_orders_of_its_own_branch(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3E1 branch A")
    branch_b = await _make_branch(db_session, "P3E1 branch B")
    order_a = await _make_order(db_session, branch_a.id)
    order_b = await _make_order(db_session, branch_b.id)
    user = await _make_user(db_session, role_name, branch_a.id)
    await db_session.commit()

    resp = await client.get(URL, params={"limit": 300}, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = [o["id"] for o in data]
    assert str(order_a.id) in ids
    assert str(order_b.id) not in ids
    for order in data:
        assert str(branch_a.id) in [s["branch_id"] for s in order["sales"]]


async def test_b_custom_role_with_no_branch_sees_no_orders(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3E1 branch")
    await _make_order(db_session, branch.id)
    user = await _make_user(db_session, role_name, None)
    await db_session.commit()

    resp = await client.get(URL, params={"limit": 300}, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_c_role_without_the_key_gets_403(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name)
    branch = await _make_branch(db_session, "P3E1 branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_d_unrelated_key_alone_gets_403(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OTHER_KEY])
    branch = await _make_branch(db_session, "P3E1 branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_e_denied_key_gets_403(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3E1 branch")
    user = await _make_user(db_session, role_name, branch.id, denied=[KEY])
    await db_session.commit()

    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_f_super_admin_sees_all_and_the_branch_filter_works(client, db_session):
    branch_a = await _make_branch(db_session, "P3E1 branch A")
    branch_b = await _make_branch(db_session, "P3E1 branch B")
    order_a = await _make_order(db_session, branch_a.id)
    order_b = await _make_order(db_session, branch_b.id)
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()

    resp = await client.get(URL, params={"limit": 300}, headers=_auth_headers(admin))
    assert resp.status_code == 200, resp.text
    ids = [o["id"] for o in resp.json()]
    assert str(order_a.id) in ids
    assert str(order_b.id) in ids

    resp = await client.get(URL, params={"limit": 300, "branch_id": str(branch_a.id)}, headers=_auth_headers(admin))
    assert resp.status_code == 200, resp.text
    ids = [o["id"] for o in resp.json()]
    assert str(order_a.id) in ids
    assert str(order_b.id) not in ids


async def test_g_single_order_of_another_branch_is_404(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3E1 branch A")
    branch_b = await _make_branch(db_session, "P3E1 branch B")
    order_a = await _make_order(db_session, branch_a.id)
    order_b = await _make_order(db_session, branch_b.id)
    user = await _make_user(db_session, role_name, branch_a.id)
    await db_session.commit()

    resp = await client.get(URL + "/" + str(order_a.id), headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(order_a.id)

    resp = await client.get(URL + "/" + str(order_b.id), headers=_auth_headers(user))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Order not found."
