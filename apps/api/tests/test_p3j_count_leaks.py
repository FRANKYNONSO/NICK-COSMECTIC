# PATCHP3J-TESTS: non-super_admin with no branch gets an empty result on the 3 count/list routes in sales.py
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, PendingChange, Refund, Role, Sale, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

REFUND_KEY = "process_refunds"
CHANGE_KEY = "process_pending_changes"
SALE_KEY = "process_sale"
SALES = "/api/v1/sales"
REFUND_COUNT_URL = SALES + "/refunds/pending-count"
CHANGE_COUNT_URL = SALES + "/pending-changes/pending-count"
CHANGE_LIST_URL = SALES + "/pending-changes"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3J Test User",
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
    return "p3j_" + uuid.uuid4().hex[:8]


async def _actors(db_session, key):
    # A custom role with only the key: one user in branch A, one user with no branch.
    # Plus a super_admin with no branch. Headers are built here, before any commit.
    await _set_role(db_session, "super_admin", add=[key])
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[key])
    branch_a = await _make_branch(db_session, "P3J branch A")
    branch_b = await _make_branch(db_session, "P3J branch B")
    in_a = await _make_user(db_session, role_name, branch_a.id)
    no_branch = await _make_user(db_session, role_name, None)
    admin = await _make_user(db_session, "super_admin")
    return {
        "a": branch_a,
        "b": branch_b,
        "h_in_a": _auth_headers(in_a),
        "h_no_branch": _auth_headers(no_branch),
        "h_admin": _auth_headers(admin),
    }


async def _make_sale(db_session, branch):
    sale = Sale(branch_id=branch.id, channel="pos", subtotal_kobo=100, total_kobo=100)
    db_session.add(sale)
    await db_session.flush()
    return sale


async def _make_refund(db_session, branch):
    sale = await _make_sale(db_session, branch)
    refund = Refund(sale_id=sale.id, amount_kobo=100, status="pending")
    db_session.add(refund)
    await db_session.flush()
    return refund


async def _make_change(db_session, branch):
    sale = await _make_sale(db_session, branch)
    change = PendingChange(
        sale_id=sale.id,
        branch_id=branch.id,
        customer_name="P3J Customer",
        amount_kobo=100,
        status="pending",
    )
    db_session.add(change)
    await db_session.flush()
    return change


async def _refund_data(db_session, act):
    # 2 pending refunds in branch A, 1 in branch B.
    for _ in range(2):
        await _make_refund(db_session, act["a"])
    await _make_refund(db_session, act["b"])
    await db_session.commit()


async def _change_data(db_session, act):
    # 2 pending changes in branch A, 1 in branch B. Returns the ids as text.
    a_ids = set()
    for _ in range(2):
        change = await _make_change(db_session, act["a"])
        a_ids.add(str(change.id))
    change_b = await _make_change(db_session, act["b"])
    b_ids = {str(change_b.id)}
    await db_session.commit()
    return a_ids, b_ids


def _param_b(act):
    return {"branch_id": str(act["b"].id)}


# ---------- Route 1: GET /sales/refunds/pending-count (process_refunds) ----------


async def test_refund_count_no_branch_gets_zero(client, db_session):
    act = await _actors(db_session, REFUND_KEY)
    await _refund_data(db_session, act)
    resp = await client.get(REFUND_COUNT_URL, headers=act["h_no_branch"])
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    resp = await client.get(REFUND_COUNT_URL, headers=act["h_no_branch"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


async def test_refund_count_user_in_branch_is_pinned(client, db_session):
    act = await _actors(db_session, REFUND_KEY)
    await _refund_data(db_session, act)
    resp = await client.get(REFUND_COUNT_URL, headers=act["h_in_a"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


async def test_refund_count_super_admin_filters_by_branch_id(client, db_session):
    act = await _actors(db_session, REFUND_KEY)
    await _refund_data(db_session, act)
    resp = await client.get(REFUND_COUNT_URL, headers=act["h_admin"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


async def test_refund_count_super_admin_without_branch_id_sees_all(client, db_session):
    act = await _actors(db_session, REFUND_KEY)
    await _refund_data(db_session, act)
    resp = await client.get(REFUND_COUNT_URL, headers=act["h_admin"])
    assert resp.status_code == 200
    assert resp.json()["count"] >= 3


# ---------- Route 2: GET /sales/pending-changes/pending-count (process_pending_changes) ----------


async def test_change_count_no_branch_gets_zero(client, db_session):
    act = await _actors(db_session, CHANGE_KEY)
    await _change_data(db_session, act)
    resp = await client.get(CHANGE_COUNT_URL, headers=act["h_no_branch"])
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    resp = await client.get(CHANGE_COUNT_URL, headers=act["h_no_branch"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


async def test_change_count_user_in_branch_is_pinned(client, db_session):
    act = await _actors(db_session, CHANGE_KEY)
    await _change_data(db_session, act)
    resp = await client.get(CHANGE_COUNT_URL, headers=act["h_in_a"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


async def test_change_count_super_admin_filters_by_branch_id(client, db_session):
    act = await _actors(db_session, CHANGE_KEY)
    await _change_data(db_session, act)
    resp = await client.get(CHANGE_COUNT_URL, headers=act["h_admin"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


async def test_change_count_super_admin_without_branch_id_sees_all(client, db_session):
    act = await _actors(db_session, CHANGE_KEY)
    await _change_data(db_session, act)
    resp = await client.get(CHANGE_COUNT_URL, headers=act["h_admin"])
    assert resp.status_code == 200
    assert resp.json()["count"] >= 3


# ---------- Route 3: GET /sales/pending-changes (process_sale) ----------


async def test_change_list_no_branch_gets_empty_list(client, db_session):
    act = await _actors(db_session, SALE_KEY)
    await _change_data(db_session, act)
    resp = await client.get(CHANGE_LIST_URL, headers=act["h_no_branch"])
    assert resp.status_code == 200
    assert resp.json() == []
    resp = await client.get(CHANGE_LIST_URL, headers=act["h_no_branch"], params=_param_b(act))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_change_list_user_in_branch_is_pinned(client, db_session):
    act = await _actors(db_session, SALE_KEY)
    a_ids, b_ids = await _change_data(db_session, act)
    resp = await client.get(CHANGE_LIST_URL, headers=act["h_in_a"], params=_param_b(act))
    assert resp.status_code == 200
    ids = {str(row["id"]) for row in resp.json()}
    assert a_ids <= ids
    assert not (b_ids & ids)


async def test_change_list_super_admin_filters_by_branch_id(client, db_session):
    act = await _actors(db_session, SALE_KEY)
    a_ids, b_ids = await _change_data(db_session, act)
    resp = await client.get(CHANGE_LIST_URL, headers=act["h_admin"], params=_param_b(act))
    assert resp.status_code == 200
    ids = {str(row["id"]) for row in resp.json()}
    assert b_ids <= ids
    assert not (a_ids & ids)


async def test_change_list_super_admin_without_branch_id_sees_all(client, db_session):
    act = await _actors(db_session, SALE_KEY)
    a_ids, b_ids = await _change_data(db_session, act)
    resp = await client.get(CHANGE_LIST_URL, headers=act["h_admin"])
    assert resp.status_code == 200
    ids = {str(row["id"]) for row in resp.json()}
    assert a_ids <= ids
    assert b_ids <= ids