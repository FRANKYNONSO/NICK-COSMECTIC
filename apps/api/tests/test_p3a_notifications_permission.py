# PATCHP3-NOTIF-TESTS: P3A notifications route uses the permission view_dashboard_notifications
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import AuditLog, Branch, Role, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "view_dashboard_notifications"
URL = "/api/v1/notifications/summary"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3A Test User",
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


async def _set_role_key(db_session, name, has_key):
    # Make sure the role row has (or does not have) the key. Rolled back after the test.
    role = (await db_session.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        role = Role(
            name=name,
            display_name=name,
            permissions=[KEY] if has_key else [],
            is_system=False,
            created_by=None,
        )
        db_session.add(role)
    else:
        perms = [p for p in list(role.permissions or []) if p != KEY]
        if has_key:
            perms.append(KEY)
        role.permissions = perms
    await db_session.flush()


def _count(body, item_type):
    return [i["count"] for i in body["items"] if i["type"] == item_type][0]


async def test_a_super_admin_can_read_summary(client, db_session):
    await _set_role_key(db_session, "super_admin", True)
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()
    resp = await client.get(URL, headers=_auth_headers(admin))
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()


async def test_b_cashier_without_key_gets_403(client, db_session):
    await _set_role_key(db_session, "cashier", False)
    branch = await _make_branch(db_session, "P3A cashier branch")
    cashier = await _make_user(db_session, "cashier", branch.id)
    await db_session.commit()
    resp = await client.get(URL, headers=_auth_headers(cashier))
    assert resp.status_code == 403, resp.text


async def test_c_custom_role_with_key_gets_200(client, db_session):
    role_name = "p3a_" + uuid.uuid4().hex[:8]
    await _set_role_key(db_session, role_name, True)
    branch = await _make_branch(db_session, "P3A custom branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()
    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text


async def test_d_custom_role_sees_only_its_own_branch(client, db_session):
    await _set_role_key(db_session, "super_admin", True)
    role_name = "p3a_" + uuid.uuid4().hex[:8]
    await _set_role_key(db_session, role_name, True)
    branch_a = await _make_branch(db_session, "P3A branch A")
    branch_b = await _make_branch(db_session, "P3A branch B")
    for branch, times in ((branch_a, 1), (branch_b, 2)):
        for _ in range(times):
            db_session.add(
                AuditLog(
                    action="branch_price_override_changed",
                    entity_type="product",
                    log_metadata={"branch_id": str(branch.id)},
                )
            )
    await db_session.flush()
    custom = await _make_user(db_session, role_name, branch_a.id)
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()

    resp = await client.get(URL, headers=_auth_headers(custom))
    assert resp.status_code == 200, resp.text
    assert _count(resp.json(), "price_changes") == 1

    resp = await client.get(URL, headers=_auth_headers(admin))
    assert resp.status_code == 200, resp.text
    assert _count(resp.json(), "price_changes") >= 3


async def test_e_user_with_key_removed_gets_403(client, db_session):
    await _set_role_key(db_session, "branch_manager", True)
    branch = await _make_branch(db_session, "P3A manager branch")
    manager = await _make_user(db_session, "branch_manager", branch.id, denied=[KEY])
    await db_session.commit()
    resp = await client.get(URL, headers=_auth_headers(manager))
    assert resp.status_code == 403, resp.text
