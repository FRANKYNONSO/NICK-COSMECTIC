"""Script test for the shelf-limit save route (PUT /goods-receiving/shelf-settings).
Run ONLY against shelf_test. Every test is rolled back.
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import BASE, _branch, _headers, _product, _set_limit, _user  # noqa: E402

from infrastructure.db.models import AuditLog  # noqa: E402

pytestmark = pytest.mark.asyncio

URL = BASE + "/shelf-settings"


async def _put(client, headers, branch_id, product_id, max_q, alert):
    return await client.put(
        URL,
        json={
            "branch_id": str(branch_id),
            "product_id": str(product_id),
            "max_shelf_quantity": max_q,
            "replenish_alert_level": alert,
        },
        headers=headers,
    )


async def _get(client, headers, branch_id, product_id=None):
    params = {"branch_id": str(branch_id)}
    if product_id is not None:
        params["product_id"] = str(product_id)
    return await client.get(URL, params=params, headers=headers)


async def _audit(db, product_id):
    result = await db.execute(
        select(AuditLog).where(AuditLog.action == "shelf_limit_changed", AuditLog.entity_id == product_id)
    )
    return result.scalars().all()


async def _admin_setup(db):
    branch = await _branch(db)
    admin = await _user(db, "super_admin")
    product = await _product(db)
    return branch, admin, product, _headers(admin)


async def test_a_first_save_is_audited_with_no_old_value(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    data = await _set_limit(client, h, branch.id, product.id, 24, 5)
    assert data["limits_configured"] is True
    assert data["max_shelf_quantity"] == 24 and data["replenish_alert_level"] == 5
    rows = await _audit(db_session, product.id)
    assert len(rows) == 1
    meta = rows[0].log_metadata
    assert meta["old"] is None
    assert meta["new"] == {"max": 24, "alert": 5}
    assert meta["branch_id"] == str(branch.id)
    assert rows[0].user_id == admin.id
    print("PASS a: first save is audited with no old value")


async def test_b_second_save_records_old_and_new(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    await _set_limit(client, h, branch.id, product.id, 24, 5)
    await _set_limit(client, h, branch.id, product.id, 30, 6)
    rows = await _audit(db_session, product.id)
    assert len(rows) == 2
    by_new_max = {r.log_metadata["new"]["max"]: r.log_metadata for r in rows}
    assert by_new_max[24]["old"] is None
    assert by_new_max[30]["old"] == {"max": 24, "alert": 5}
    assert by_new_max[30]["new"] == {"max": 30, "alert": 6}
    resp = await _get(client, h, branch.id, product.id)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    assert items[0]["max_shelf_quantity"] == 30 and items[0]["replenish_alert_level"] == 6
    print("PASS b: second save records old and new, the list shows one row")


async def test_c_alert_above_max_is_refused(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    resp = await _put(client, h, branch.id, product.id, 24, 25)
    print("EVIDENCE status", resp.status_code, resp.text[:200])
    assert resp.status_code == 400, resp.text
    assert "cannot be higher" in resp.json()["detail"]
    assert (await _get(client, h, branch.id, product.id)).json() == []
    assert await _audit(db_session, product.id) == []
    print("PASS c: alert above the maximum is refused, nothing saved, no audit")


async def test_d_staff_cannot_save_a_limit(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    staff = await _user(db_session, "cashier", branch.id)
    resp = await _put(client, _headers(staff), branch.id, product.id, 24, 5)
    print("EVIDENCE status", resp.status_code, resp.text[:200])
    assert resp.status_code == 403, resp.text
    assert "not permitted" in resp.json()["detail"]
    assert (await _get(client, h, branch.id, product.id)).json() == []
    assert await _audit(db_session, product.id) == []
    print("PASS d: staff get 403, nothing saved")


async def test_e_manager_of_another_branch_is_refused(client, db_session):
    branch_a = await _branch(db_session)
    branch_b = await _branch(db_session)
    admin = await _user(db_session, "super_admin")
    manager = await _user(db_session, "branch_manager", branch_a.id)
    product = await _product(db_session)
    resp = await _put(client, _headers(manager), branch_b.id, product.id, 24, 5)
    print("EVIDENCE status", resp.status_code, resp.text[:200])
    assert resp.status_code == 403, resp.text
    assert "own branch" in resp.json()["detail"]
    assert (await _get(client, _headers(admin), branch_b.id, product.id)).json() == []
    assert await _audit(db_session, product.id) == []
    print("PASS e: a manager of another branch gets 403, nothing saved")


async def test_f_bad_numbers_are_refused(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    r1 = await _put(client, h, branch.id, product.id, -1, 0)
    r2 = await _put(client, h, branch.id, product.id, 24, -1)
    r3 = await _put(client, h, branch.id, product.id, 1000001, 5)
    print("EVIDENCE statuses", r1.status_code, r2.status_code, r3.status_code)
    assert r1.status_code == 422 and r2.status_code == 422 and r3.status_code == 422
    assert (await _get(client, h, branch.id, product.id)).json() == []
    assert await _audit(db_session, product.id) == []
    print("PASS f: negative and too large numbers are refused, nothing saved")


async def test_g_unknown_product_and_branch_give_404(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    r1 = await _put(client, h, branch.id, uuid.uuid4(), 24, 5)
    r2 = await _put(client, h, uuid.uuid4(), product.id, 24, 5)
    print("EVIDENCE statuses", r1.status_code, r1.text[:100], r2.status_code, r2.text[:100])
    assert r1.status_code == 404 and "Product not found" in r1.json()["detail"]
    assert r2.status_code == 404 and "Branch not found" in r2.json()["detail"]
    print("PASS g: unknown product and unknown branch give 404")


async def test_h_staff_can_read_own_branch_only(client, db_session):
    branch, admin, product, h = await _admin_setup(db_session)
    other_branch = await _branch(db_session)
    await _set_limit(client, h, branch.id, product.id, 24, 5)
    staff = await _user(db_session, "cashier", branch.id)
    resp = await _get(client, _headers(staff), branch.id)
    assert resp.status_code == 200, resp.text
    items = [x for x in resp.json() if x["product_id"] == str(product.id)]
    assert len(items) == 1
    assert items[0]["max_shelf_quantity"] == 24 and items[0]["replenish_alert_level"] == 5
    resp = await _get(client, _headers(staff), other_branch.id)
    print("EVIDENCE other branch", resp.status_code, resp.text[:150])
    assert resp.status_code == 403, resp.text
    print("PASS h: staff can read their own branch and get 403 for another")
