# PATCHP3B-TESTS: P3B refund and pending-change routes use process_refunds / process_pending_changes
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, PendingChange, Refund, Role, Sale, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

REFUND_KEY = "process_refunds"
CHANGE_KEY = "process_pending_changes"
BASE = "/api/v1/sales"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3B Test User",
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


async def _make_sale(db_session, branch):
    sale = Sale(branch_id=branch.id, channel="pos", subtotal_kobo=100, total_kobo=100)
    db_session.add(sale)
    await db_session.flush()
    return sale


async def _add_refunds(db_session, branch, how_many):
    for _ in range(how_many):
        sale = await _make_sale(db_session, branch)
        db_session.add(Refund(sale_id=sale.id, amount_kobo=100, status="pending"))
    await db_session.flush()


async def _add_changes(db_session, branch, how_many):
    for _ in range(how_many):
        sale = await _make_sale(db_session, branch)
        db_session.add(
            PendingChange(
                sale_id=sale.id,
                branch_id=branch.id,
                customer_name="P3B Customer",
                amount_kobo=100,
                status="pending",
            )
        )
    await db_session.flush()


def _new_role_name():
    return "p3b_" + uuid.uuid4().hex[:8]


async def test_a_refund_count_is_branch_scoped(client, db_session):
    await _set_role(db_session, "super_admin", add=[REFUND_KEY])
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[REFUND_KEY])
    branch_a = await _make_branch(db_session, "P3B branch A")
    branch_b = await _make_branch(db_session, "P3B branch B")
    await _add_refunds(db_session, branch_a, 1)
    await _add_refunds(db_session, branch_b, 2)
    custom = await _make_user(db_session, role_name, branch_a.id)
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()

    resp = await client.get(BASE + "/refunds/pending-count", headers=_auth_headers(custom))
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1

    resp = await client.get(BASE + "/refunds/pending-count", headers=_auth_headers(admin))
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] >= 3


async def test_b_change_count_is_branch_scoped(client, db_session):
    await _set_role(db_session, "super_admin", add=[CHANGE_KEY])
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[CHANGE_KEY])
    branch_a = await _make_branch(db_session, "P3B branch A")
    branch_b = await _make_branch(db_session, "P3B branch B")
    await _add_changes(db_session, branch_a, 1)
    await _add_changes(db_session, branch_b, 2)
    custom = await _make_user(db_session, role_name, branch_a.id)
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()

    resp = await client.get(BASE + "/pending-changes/pending-count", headers=_auth_headers(custom))
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1

    resp = await client.get(BASE + "/pending-changes/pending-count", headers=_auth_headers(admin))
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] >= 3


async def test_c_cashier_without_keys_gets_403_on_count_routes(client, db_session):
    await _set_role(db_session, "cashier", remove=[REFUND_KEY, CHANGE_KEY])
    branch = await _make_branch(db_session, "P3B cashier branch")
    cashier = await _make_user(db_session, "cashier", branch.id)
    await db_session.commit()
    headers = _auth_headers(cashier)

    resp = await client.get(BASE + "/refunds/pending-count", headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.get(BASE + "/pending-changes/pending-count", headers=headers)
    assert resp.status_code == 403, resp.text


async def test_d_refund_mark_processed_guard(client, db_session):
    with_key = _new_role_name()
    without_key = _new_role_name()
    await _set_role(db_session, with_key, add=[REFUND_KEY])
    await _set_role(db_session, without_key)
    branch = await _make_branch(db_session, "P3B guard branch")
    allowed = await _make_user(db_session, with_key, branch.id)
    blocked = await _make_user(db_session, without_key, branch.id)
    await db_session.commit()
    url = BASE + "/refunds/" + str(uuid.uuid4()) + "/mark-processed"

    resp = await client.post(url, json={}, headers=_auth_headers(allowed))
    assert resp.status_code == 404, resp.text
    resp = await client.post(url, json={}, headers=_auth_headers(blocked))
    assert resp.status_code == 403, resp.text


async def test_e_change_mark_processed_guard(client, db_session):
    with_key = _new_role_name()
    without_key = _new_role_name()
    await _set_role(db_session, with_key, add=[CHANGE_KEY])
    await _set_role(db_session, without_key)
    branch = await _make_branch(db_session, "P3B guard branch")
    allowed = await _make_user(db_session, with_key, branch.id)
    blocked = await _make_user(db_session, without_key, branch.id)
    await db_session.commit()
    url = BASE + "/pending-changes/" + str(uuid.uuid4()) + "/mark-processed"

    resp = await client.post(url, json={}, headers=_auth_headers(allowed))
    assert resp.status_code == 404, resp.text
    resp = await client.post(url, json={}, headers=_auth_headers(blocked))
    assert resp.status_code == 403, resp.text


async def test_f_user_with_key_removed_gets_403(client, db_session):
    await _set_role(db_session, "branch_manager", add=[REFUND_KEY])
    branch = await _make_branch(db_session, "P3B manager branch")
    manager = await _make_user(db_session, "branch_manager", branch.id, denied=[REFUND_KEY])
    await db_session.commit()
    resp = await client.get(BASE + "/refunds/pending-count", headers=_auth_headers(manager))
    assert resp.status_code == 403, resp.text
