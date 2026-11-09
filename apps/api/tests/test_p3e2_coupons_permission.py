# PATCHP3E2-TESTS: website coupon routes use the manage_website_coupons key
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, Role, StorefrontCoupon, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "manage_website_coupons"
OTHER_KEY = "manage_users"
URL = "/api/v1/storefront/coupons"
GEN_BODY = {"discount_amount_naira": 1000, "expires_in_hours": 24, "count": 1, "note": "P3E2 test"}


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3E2 Test User",
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
    return "p3e2_" + uuid.uuid4().hex[:8]


async def _four_statuses(client, headers):
    # Calls generate, list, reveal and void. Returns the 4 status codes in this order.
    gen = await client.post(URL, json=dict(GEN_BODY), headers=headers)
    lst = await client.get(URL, headers=headers)
    rev = await client.post(URL + "/reveal", json={"ids": [str(uuid.uuid4())]}, headers=headers)
    void = await client.post(URL + "/" + str(uuid.uuid4()) + "/void", headers=headers)
    return [gen.status_code, lst.status_code, rev.status_code, void.status_code]


async def _real_code(db_session, coupon_id):
    stmt = select(StorefrontCoupon.code).where(StorefrontCoupon.id == uuid.UUID(coupon_id))
    return (await db_session.execute(stmt)).scalar_one()


async def test_a_custom_role_with_the_key_can_generate_list_reveal_and_void(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3E2 branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()
    headers = _auth_headers(user)

    resp = await client.post(URL, json=dict(GEN_BODY), headers=headers)
    assert resp.status_code == 201, resp.text
    made = resp.json()
    assert len(made) == 1
    coupon_id = made[0]["id"]
    real = await _real_code(db_session, coupon_id)
    assert made[0]["code"] != real, "the code must be hidden before reveal"
    assert made[0]["started_at"] is None

    resp = await client.get(URL, params={"limit": 500}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert coupon_id in [c["id"] for c in resp.json()]

    resp = await client.post(URL + "/reveal", json={"ids": [coupon_id]}, headers=headers)
    assert resp.status_code == 200, resp.text
    revealed = resp.json()
    assert len(revealed) == 1
    assert revealed[0]["code"] == real
    assert revealed[0]["started_at"] is not None

    resp = await client.post(URL + "/" + coupon_id + "/void", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "voided"

    resp = await client.post(URL + "/" + coupon_id + "/void", headers=headers)
    assert resp.status_code == 409, resp.text


async def test_b_role_without_the_key_gets_403_on_all_four_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name)
    branch = await _make_branch(db_session, "P3E2 branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    assert await _four_statuses(client, _auth_headers(user)) == [403, 403, 403, 403]


async def test_c_unrelated_key_alone_gets_403_on_all_four_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OTHER_KEY])
    branch = await _make_branch(db_session, "P3E2 branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    assert await _four_statuses(client, _auth_headers(user)) == [403, 403, 403, 403]


async def test_d_denied_key_gets_403_on_all_four_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3E2 branch")
    user = await _make_user(db_session, role_name, branch.id, denied=[KEY])
    await db_session.commit()

    assert await _four_statuses(client, _auth_headers(user)) == [403, 403, 403, 403]


async def test_e_super_admin_passes_all_four_routes(client, db_session):
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()
    headers = _auth_headers(admin)

    resp = await client.post(URL, json=dict(GEN_BODY), headers=headers)
    assert resp.status_code == 201, resp.text
    coupon_id = resp.json()[0]["id"]

    resp = await client.get(URL, params={"limit": 500}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert coupon_id in [c["id"] for c in resp.json()]

    resp = await client.post(URL + "/reveal", json={"ids": [coupon_id]}, headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post(URL + "/" + coupon_id + "/void", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "voided"


async def test_f_branch_manager_stays_403_on_all_four_routes(client, db_session):
    branch = await _make_branch(db_session, "P3E2 branch")
    manager = await _make_user(db_session, "branch_manager", branch.id)
    await db_session.commit()

    assert await _four_statuses(client, _auth_headers(manager)) == [403, 403, 403, 403]


async def test_g_coupon_check_route_is_still_open_to_process_sale(client, db_session):
    sale_role = _new_role_name()
    await _set_role(db_session, sale_role, add=["process_sale"])
    key_role = _new_role_name()
    await _set_role(db_session, key_role, add=[KEY])
    branch = await _make_branch(db_session, "P3E2 branch")
    sale_user = await _make_user(db_session, sale_role, branch.id)
    key_user = await _make_user(db_session, key_role, branch.id)
    code = "P3E2" + uuid.uuid4().hex[:8].upper()
    db_session.add(
        StorefrontCoupon(
            code=code,
            discount_amount_kobo=500000,
            status="active",
            expires_at=None,
            valid_hours=24,
            issued_by=sale_user.id,
        )
    )
    await db_session.flush()
    await db_session.commit()

    resp = await client.get(URL + "/check", params={"code": code}, headers=_auth_headers(sale_user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["valid"] is True
    assert resp.json()["discount_amount_naira"] == 5000

    resp = await client.get(URL + "/check", params={"code": code}, headers=_auth_headers(key_user))
    assert resp.status_code == 403, resp.text
