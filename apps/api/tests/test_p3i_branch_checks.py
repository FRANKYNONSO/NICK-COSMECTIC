# PATCHP3I-TESTS: branch check on refund mark-processed, change mark-processed, confirm-theft and admin-acknowledge
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import (
    Branch,
    PendingChange,
    Product,
    Refund,
    Role,
    Sale,
    Shift,
    ShiftComplianceEvent,
    StockAdjustment,
    StockBatch,
    User,
)
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

REFUND_KEY = "process_refunds"
CHANGE_KEY = "process_pending_changes"
THEFT_KEY = "confirm_theft_losses"
SHIFT_KEY = "acknowledge_shift_violations"
SALES = "/api/v1/sales"
INV = "/api/v1/inventory"
SHIFTS = "/api/v1/shifts"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3I Test User",
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
    return "p3i_" + uuid.uuid4().hex[:8]


async def _actors(db_session, key):
    # A custom role with the key: one user in branch A, one user with no branch. Plus a super_admin with no branch.
    await _set_role(db_session, "super_admin", add=[key])
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[key])
    branch_a = await _make_branch(db_session, "P3I branch A")
    branch_b = await _make_branch(db_session, "P3I branch B")
    in_a = await _make_user(db_session, role_name, branch_a.id)
    no_branch = await _make_user(db_session, role_name, None)
    admin = await _make_user(db_session, "super_admin")
    return {"a": branch_a, "b": branch_b, "in_a": in_a, "no_branch": no_branch, "admin": admin}


async def _make_sale(db_session, branch):
    sale = Sale(branch_id=branch.id, channel="pos", subtotal_kobo=100, total_kobo=100)
    db_session.add(sale)
    await db_session.flush()
    return sale


async def _make_refund(db_session, branch, status="pending"):
    sale = await _make_sale(db_session, branch)
    refund = Refund(sale_id=sale.id, amount_kobo=100, status=status)
    db_session.add(refund)
    await db_session.flush()
    return refund


async def _make_change(db_session, sale_branch, change_branch=None, status="pending"):
    sale = await _make_sale(db_session, sale_branch)
    change = PendingChange(
        sale_id=sale.id,
        branch_id=change_branch.id if change_branch is not None else None,
        customer_name="P3I Customer",
        amount_kobo=100,
        status=status,
    )
    db_session.add(change)
    await db_session.flush()
    return change


async def _make_theft(db_session, branch, status="pending_confirmation"):
    product = Product(sku="P3I-" + uuid.uuid4().hex[:10], name="P3I product")
    db_session.add(product)
    await db_session.flush()
    batch = StockBatch(
        product_id=product.id,
        branch_id=branch.id,
        quantity_received=10,
        quantity_remaining=10,
        unit_cost_kobo=100,
    )
    db_session.add(batch)
    await db_session.flush()
    adjustment = StockAdjustment(
        stock_batch_id=batch.id,
        branch_id=branch.id,
        quantity_delta=-2,
        reason="theft",
        status=status,
    )
    db_session.add(adjustment)
    await db_session.flush()
    return adjustment


async def _make_event(db_session, branch, user):
    shift = Shift(branch_id=branch.id, opened_by=user.id, opening_float_kobo=0)
    db_session.add(shift)
    await db_session.flush()
    event = ShiftComplianceEvent(
        shift_id=shift.id,
        user_id=user.id,
        branch_id=branch.id,
        occurrence_number=1,
        level=1,
    )
    db_session.add(event)
    await db_session.flush()
    return event


def _refund_url(rid):
    return SALES + "/refunds/" + rid + "/mark-processed"


def _change_url(cid):
    return SALES + "/pending-changes/" + cid + "/mark-processed"


def _theft_url(aid):
    return INV + "/stock-adjustments/" + aid + "/confirm-theft"


def _ack_url(eid):
    return SHIFTS + "/compliance/events/" + eid + "/admin-acknowledge"


# ---------- refund mark-processed ----------

async def test_refund_a_own_branch_passes(client, db_session):
    p = await _actors(db_session, REFUND_KEY)
    rid = str((await _make_refund(db_session, p["a"])).id)
    await db_session.commit()
    resp = await client.post(_refund_url(rid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


async def test_refund_b_other_branch_gets_404(client, db_session):
    p = await _actors(db_session, REFUND_KEY)
    rid = str((await _make_refund(db_session, p["b"])).id)
    await db_session.commit()
    resp = await client.post(_refund_url(rid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Refund not found"


async def test_refund_c_no_branch_gets_404(client, db_session):
    p = await _actors(db_session, REFUND_KEY)
    rid = str((await _make_refund(db_session, p["a"])).id)
    await db_session.commit()
    resp = await client.post(_refund_url(rid), json={}, headers=_auth_headers(p["no_branch"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Refund not found"


async def test_refund_d_super_admin_handles_any_branch(client, db_session):
    p = await _actors(db_session, REFUND_KEY)
    rid = str((await _make_refund(db_session, p["b"])).id)
    await db_session.commit()
    resp = await client.post(_refund_url(rid), json={}, headers=_auth_headers(p["admin"]))
    assert resp.status_code == 200, resp.text


async def test_refund_e_status_does_not_leak_across_branches(client, db_session):
    p = await _actors(db_session, REFUND_KEY)
    other_done = str((await _make_refund(db_session, p["b"], status="completed")).id)
    own_done = str((await _make_refund(db_session, p["a"], status="completed")).id)
    await db_session.commit()
    headers = _auth_headers(p["in_a"])
    resp = await client.post(_refund_url(other_done), json={}, headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Refund not found"
    resp = await client.post(_refund_url(own_done), json={}, headers=headers)
    assert resp.status_code == 400, resp.text


# ---------- pending change mark-processed ----------

async def test_change_a_own_branch_passes(client, db_session):
    p = await _actors(db_session, CHANGE_KEY)
    cid = str((await _make_change(db_session, p["a"], p["a"])).id)
    await db_session.commit()
    resp = await client.post(_change_url(cid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "settled"


async def test_change_b_other_branch_gets_404(client, db_session):
    p = await _actors(db_session, CHANGE_KEY)
    cid = str((await _make_change(db_session, p["b"], p["b"])).id)
    await db_session.commit()
    resp = await client.post(_change_url(cid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Pending change not found"


async def test_change_c_empty_change_branch_uses_sale_branch(client, db_session):
    p = await _actors(db_session, CHANGE_KEY)
    cid = str((await _make_change(db_session, p["a"], None)).id)
    await db_session.commit()
    resp = await client.post(_change_url(cid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 200, resp.text

    other = str((await _make_change(db_session, p["b"], None)).id)
    await db_session.commit()
    resp = await client.post(_change_url(other), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Pending change not found"


async def test_change_d_no_branch_gets_404(client, db_session):
    p = await _actors(db_session, CHANGE_KEY)
    cid = str((await _make_change(db_session, p["a"], p["a"])).id)
    await db_session.commit()
    resp = await client.post(_change_url(cid), json={}, headers=_auth_headers(p["no_branch"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Pending change not found"


async def test_change_e_super_admin_handles_any_branch(client, db_session):
    p = await _actors(db_session, CHANGE_KEY)
    cid = str((await _make_change(db_session, p["b"], p["b"])).id)
    await db_session.commit()
    resp = await client.post(_change_url(cid), json={}, headers=_auth_headers(p["admin"]))
    assert resp.status_code == 200, resp.text


async def test_change_f_status_does_not_leak_across_branches(client, db_session):
    p = await _actors(db_session, CHANGE_KEY)
    other_done = str((await _make_change(db_session, p["b"], p["b"], status="settled")).id)
    own_done = str((await _make_change(db_session, p["a"], p["a"], status="settled")).id)
    await db_session.commit()
    headers = _auth_headers(p["in_a"])
    resp = await client.post(_change_url(other_done), json={}, headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Pending change not found"
    resp = await client.post(_change_url(own_done), json={}, headers=headers)
    assert resp.status_code == 400, resp.text


# ---------- confirm theft ----------

async def test_theft_a_own_branch_passes(client, db_session):
    p = await _actors(db_session, THEFT_KEY)
    aid = str((await _make_theft(db_session, p["a"])).id)
    await db_session.commit()
    resp = await client.post(_theft_url(aid), headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


async def test_theft_b_other_branch_gets_404(client, db_session):
    p = await _actors(db_session, THEFT_KEY)
    aid = str((await _make_theft(db_session, p["b"])).id)
    await db_session.commit()
    resp = await client.post(_theft_url(aid), headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Theft report not found"


async def test_theft_c_no_branch_gets_404(client, db_session):
    p = await _actors(db_session, THEFT_KEY)
    aid = str((await _make_theft(db_session, p["a"])).id)
    await db_session.commit()
    resp = await client.post(_theft_url(aid), headers=_auth_headers(p["no_branch"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Theft report not found"


async def test_theft_d_super_admin_handles_any_branch(client, db_session):
    p = await _actors(db_session, THEFT_KEY)
    aid = str((await _make_theft(db_session, p["b"])).id)
    await db_session.commit()
    resp = await client.post(_theft_url(aid), headers=_auth_headers(p["admin"]))
    assert resp.status_code == 200, resp.text


async def test_theft_e_status_does_not_leak_across_branches(client, db_session):
    p = await _actors(db_session, THEFT_KEY)
    other_done = str((await _make_theft(db_session, p["b"], status="completed")).id)
    own_done = str((await _make_theft(db_session, p["a"], status="completed")).id)
    await db_session.commit()
    headers = _auth_headers(p["in_a"])
    resp = await client.post(_theft_url(other_done), headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Theft report not found"
    resp = await client.post(_theft_url(own_done), headers=headers)
    assert resp.status_code == 400, resp.text


# ---------- admin acknowledge ----------

async def test_ack_a_own_branch_passes(client, db_session):
    p = await _actors(db_session, SHIFT_KEY)
    eid = str((await _make_event(db_session, p["a"], p["in_a"])).id)
    await db_session.commit()
    resp = await client.post(_ack_url(eid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["admin_acknowledged_at"] is not None


async def test_ack_b_other_branch_gets_404(client, db_session):
    p = await _actors(db_session, SHIFT_KEY)
    eid = str((await _make_event(db_session, p["b"], p["in_a"])).id)
    await db_session.commit()
    resp = await client.post(_ack_url(eid), json={}, headers=_auth_headers(p["in_a"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Compliance event not found"


async def test_ack_c_no_branch_gets_404(client, db_session):
    p = await _actors(db_session, SHIFT_KEY)
    eid = str((await _make_event(db_session, p["a"], p["in_a"])).id)
    await db_session.commit()
    resp = await client.post(_ack_url(eid), json={}, headers=_auth_headers(p["no_branch"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Compliance event not found"


async def test_ack_d_super_admin_handles_any_branch(client, db_session):
    p = await _actors(db_session, SHIFT_KEY)
    eid = str((await _make_event(db_session, p["b"], p["in_a"])).id)
    await db_session.commit()
    resp = await client.post(_ack_url(eid), json={}, headers=_auth_headers(p["admin"]))
    assert resp.status_code == 200, resp.text