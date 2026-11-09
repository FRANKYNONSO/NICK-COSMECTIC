# PATCHP3D-TESTS: POST /chat/announcements uses the post_announcements key
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, Role, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "post_announcements"
OLD_KEY = "manage_users"
URL = "/api/v1/chat/announcements"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3D Test User",
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
    return "p3d_" + uuid.uuid4().hex[:8]


def _payload():
    return {"body": "P3D test announcement " + uuid.uuid4().hex[:6]}


async def test_a_custom_role_needs_the_key_to_post(client, db_session):
    with_key = _new_role_name()
    without_key = _new_role_name()
    await _set_role(db_session, with_key, add=[KEY])
    await _set_role(db_session, without_key)
    branch = await _make_branch(db_session, "P3D branch")
    allowed = await _make_user(db_session, with_key, branch.id)
    blocked = await _make_user(db_session, without_key, branch.id)
    await db_session.commit()

    payload = _payload()
    resp = await client.post(URL, json=payload, headers=_auth_headers(allowed))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["body"] == payload["body"]
    assert data["sender_id"] == str(allowed.id)
    resp = await client.post(URL, json=_payload(), headers=_auth_headers(blocked))
    assert resp.status_code == 403, resp.text


async def test_b_super_admin_can_post(client, db_session):
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()

    resp = await client.post(URL, json=_payload(), headers=_auth_headers(admin))
    assert resp.status_code == 201, resp.text


async def test_c_branch_manager_cannot_post(client, db_session):
    await _set_role(db_session, "branch_manager", remove=[KEY])
    branch = await _make_branch(db_session, "P3D branch")
    manager = await _make_user(db_session, "branch_manager", branch.id)
    await db_session.commit()

    resp = await client.post(URL, json=_payload(), headers=_auth_headers(manager))
    assert resp.status_code == 403, resp.text


async def test_d_manage_users_alone_cannot_post(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OLD_KEY])
    branch = await _make_branch(db_session, "P3D branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    resp = await client.post(URL, json=_payload(), headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_e_denied_key_blocks_posting(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3D branch")
    user = await _make_user(db_session, role_name, branch.id, denied=[KEY])
    await db_session.commit()

    resp = await client.post(URL, json=_payload(), headers=_auth_headers(user))
    assert resp.status_code == 403, resp.text


async def test_f_reading_announcements_is_still_open(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name)
    branch = await _make_branch(db_session, "P3D branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
