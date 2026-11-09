# PATCHP3C-TESTS: confirm-theft and admin-acknowledge use their own permission keys
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, Role, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

THEFT_KEY = "confirm_theft_losses"
SHIFT_KEY = "acknowledge_shift_violations"
OLD_KEY = "manage_users"
INV = "/api/v1/inventory"
SHIFTS = "/api/v1/shifts"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3C Test User",
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
    return "p3c_" + uuid.uuid4().hex[:8]


def _theft_url():
    return INV + "/stock-adjustments/" + str(uuid.uuid4()) + "/confirm-theft"


def _ack_url():
    return SHIFTS + "/compliance/events/" + str(uuid.uuid4()) + "/admin-acknowledge"


async def test_a_confirm_theft_needs_its_own_key(client, db_session):
    with_key = _new_role_name()
    without_key = _new_role_name()
    await _set_role(db_session, with_key, add=[THEFT_KEY])
    await _set_role(db_session, without_key)
    branch = await _make_branch(db_session, "P3C branch")
    allowed = await _make_user(db_session, with_key, branch.id)
    blocked = await _make_user(db_session, without_key, branch.id)
    await db_session.commit()

    resp = await client.post(_theft_url(), headers=_auth_headers(allowed))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Theft report not found"
    resp = await client.post(_theft_url(), headers=_auth_headers(blocked))
    assert resp.status_code == 403, resp.text


async def test_b_manage_users_alone_cannot_confirm_theft(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OLD_KEY])
    branch = await _make_branch(db_session, "P3C branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()
    resp = await client.post(_theft_url(), headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_c_admin_acknowledge_needs_its_own_key(client, db_session):
    with_key = _new_role_name()
    without_key = _new_role_name()
    await _set_role(db_session, with_key, add=[SHIFT_KEY])
    await _set_role(db_session, without_key)
    branch = await _make_branch(db_session, "P3C branch")
    allowed = await _make_user(db_session, with_key, branch.id)
    blocked = await _make_user(db_session, without_key, branch.id)
    await db_session.commit()

    resp = await client.post(_ack_url(), json={}, headers=_auth_headers(allowed))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Compliance event not found"
    resp = await client.post(_ack_url(), json={}, headers=_auth_headers(blocked))
    assert resp.status_code == 403, resp.text


async def test_d_manage_users_alone_cannot_acknowledge_shift(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OLD_KEY])
    branch = await _make_branch(db_session, "P3C branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()
    resp = await client.post(_ack_url(), json={}, headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_e_user_with_key_removed_gets_403_on_both_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[THEFT_KEY, SHIFT_KEY])
    branch = await _make_branch(db_session, "P3C branch")
    user = await _make_user(db_session, role_name, branch.id, denied=[THEFT_KEY, SHIFT_KEY])
    await db_session.commit()
    headers = _auth_headers(user)

    resp = await client.post(_theft_url(), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(_ack_url(), json={}, headers=headers)
    assert resp.status_code == 403, resp.text


async def test_f_super_admin_passes_both_guards(client, db_session):
    await _set_role(db_session, "super_admin", add=[THEFT_KEY, SHIFT_KEY])
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()
    headers = _auth_headers(admin)

    resp = await client.post(_theft_url(), headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Theft report not found"
    resp = await client.post(_ack_url(), json={}, headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Compliance event not found"
