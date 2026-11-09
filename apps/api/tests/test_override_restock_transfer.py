"""Shelf-limit override tests for pull-to-store and transfer-to-branch (fix30)."""
import pytest
from sqlalchemy import func, select

from infrastructure.db.models import AuditLog
from tests.test_goods_receiving import (
    _branch,
    _figures,
    _headers,
    _product,
    _receive,
    _set_limit,
    _summary,
    _user,
)

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"
REASON = "Display promotion, manager approved"


async def _pull_o(client, headers, branch_id, product_id, units, override=True, reason=None):
    params = {"branch_id": str(branch_id)}
    if override:
        params["override_shelf_limit"] = "true"
    if reason:
        params["override_reason"] = reason
    body = {"product_id": str(product_id), "unit_label": "pieces", "unit_count": units, "pieces_per_unit": 1}
    return await client.post(f"{WH}/pull-to-store", params=params, json=body, headers=headers)


async def _xfer_o(client, headers, from_branch, to_branch, product_id, units, override=True, reason=None):
    params = {"branch_id": str(from_branch)}
    if override:
        params["override_shelf_limit"] = "true"
    if reason:
        params["override_reason"] = reason
    body = {
        "product_id": str(product_id),
        "unit_label": "pieces",
        "unit_count": units,
        "pieces_per_unit": 1,
        "to_branch_id": str(to_branch),
    }
    return await client.post(f"{WH}/transfer-to-branch", params=params, json=body, headers=headers)


async def _override_audits(db):
    return await db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "shelf_limit_override")
    )


async def _stocked_branch(db, client):
    """Branch with 100 received (20 on shelf, 80 warehouse), limit 24 / alert 5,
    a super admin, and a branch manager bound to that branch."""
    branch = await _branch(db)
    admin = await _user(db, "super_admin")
    manager = await _user(db, "branch_manager", branch.id)
    product = await _product(db)
    await db.commit()
    ah = _headers(admin)
    assert (await _receive(client, ah, branch.id, product.id, 100, 20)).status_code == 201
    await _set_limit(client, ah, branch.id, product.id, 24, 5)
    return branch, admin, manager, product, ah, _headers(manager)


# ---------------------------------------------------------------- O1
# PATCHT2-STALE-TESTS: fix45 rule - a warehouse restock may go over the shelf limit,
# so a pull needs no override and no reason, and it writes no override audit row.
async def test_o1_pull_may_go_over_the_limit_without_override(client, db_session):
    branch, admin, manager, product, ah, mh = await _stocked_branch(db_session, client)
    assert _figures(await _summary(client, ah, branch.id, product.id)) == (100, 80, 20)

    ok = await _pull_o(client, mh, branch.id, product.id, 10, override=False)
    assert ok.status_code == 201, ok.text
    s = await _summary(client, ah, branch.id, product.id)
    assert _figures(s) == (100, 70, 30)
    assert s["over_limit"] is True
    assert await _override_audits(db_session) == 0


# ---------------------------------------------------------------- O2
async def test_o2_override_cannot_create_stock(client, db_session):
    branch, admin, manager, product, ah, mh = await _stocked_branch(db_session, client)

    resp = await _pull_o(client, mh, branch.id, product.id, 81, reason=REASON)
    assert resp.status_code == 400, resp.text
    assert _figures(await _summary(client, ah, branch.id, product.id)) == (100, 80, 20)
    assert await _override_audits(db_session) == 0


# ---------------------------------------------------------------- O3
async def test_o3_staff_cannot_use_pull_to_store(client, db_session):
    branch, admin, manager, product, ah, mh = await _stocked_branch(db_session, client)
    staff = await _user(db_session, "cashier", branch.id)
    await db_session.commit()

    resp = await _pull_o(client, _headers(staff), branch.id, product.id, 10, reason=REASON)
    assert resp.status_code == 403, resp.text
    assert _figures(await _summary(client, ah, branch.id, product.id)) == (100, 80, 20)


# ---------------------------------------------------------------- O4
async def test_o4_manager_of_another_branch_cannot_override_here(client, db_session):
    branch, admin, manager, product, ah, mh = await _stocked_branch(db_session, client)
    other = await _branch(db_session)
    other_manager = await _user(db_session, "branch_manager", other.id)
    await db_session.commit()

    resp = await _pull_o(client, _headers(other_manager), branch.id, product.id, 10, reason=REASON)
    assert resp.status_code == 403, resp.text
    assert _figures(await _summary(client, ah, branch.id, product.id)) == (100, 80, 20)
    assert await _override_audits(db_session) == 0


# ---------------------------------------------------------------- O5
async def test_o5_transfer_override_only_for_destination_scope(client, db_session):
    a = await _branch(db_session)
    b = await _branch(db_session)
    admin = await _user(db_session, "super_admin")
    manager_a = await _user(db_session, "branch_manager", a.id)
    product = await _product(db_session)
    await db_session.commit()
    ah = _headers(admin)
    assert (await _receive(client, ah, a.id, product.id, 100, 0)).status_code == 201
    assert (await _receive(client, ah, b.id, product.id, 20, 20)).status_code == 201
    await _set_limit(client, ah, b.id, product.id, 24, 5)

    # A manager of the source branch cannot override a limit at another branch's shelf.
    denied = await _xfer_o(client, _headers(manager_a), a.id, b.id, product.id, 10, reason=REASON)
    assert denied.status_code == 403, denied.text
    assert _figures(await _summary(client, ah, a.id, product.id)) == (100, 100, 0)
    assert _figures(await _summary(client, ah, b.id, product.id)) == (20, 0, 20)

    # A super admin needs a reason.
    no_reason = await _xfer_o(client, ah, a.id, b.id, product.id, 10)
    assert no_reason.status_code == 400, no_reason.text
    assert _figures(await _summary(client, ah, a.id, product.id)) == (100, 100, 0)

    ok = await _xfer_o(client, ah, a.id, b.id, product.id, 10, reason=REASON)
    assert ok.status_code == 201, ok.text
    sa = await _summary(client, ah, a.id, product.id)
    sb = await _summary(client, ah, b.id, product.id)
    assert _figures(sa) == (90, 90, 0)
    assert _figures(sb) == (30, 0, 30)
    assert sb["over_limit"] is True
    assert sa["total_stock"] + sb["total_stock"] == 120
    assert await _override_audits(db_session) == 1