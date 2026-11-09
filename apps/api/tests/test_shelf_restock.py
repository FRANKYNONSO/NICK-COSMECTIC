"""Manual restock (warehouse -> shelf) tests (fix20).

Most tests use the rollback fixtures. The two Test K tests use real committed
sessions (two simultaneous requests cannot share one rolled-back session) and
delete the rows they create afterwards. shelf_test is a disposable database.
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.dependencies import get_db_session
from infrastructure.db.models import StockTransfer
from infrastructure.db.session import AsyncSessionLocal
from main import app
from tests.test_goods_receiving import (
    _branch,
    _figures,
    _headers,
    _product,
    _receive,
    _set_limit,
    _setup,
    _summary,
    _user,
)

pytestmark = pytest.mark.asyncio

# PATCHT1-RESTOCK-TESTS: fix45 rule - the shelf limit is only the default amount; restock may go over it.
WH = "/api/v1/warehouse"


async def _pull(client, headers, branch_id, product_id, units, notes=None):
    body = {
        "product_id": str(product_id),
        "unit_label": "pieces",
        "unit_count": units,
        "pieces_per_unit": 1,
    }
    if notes:
        body["notes"] = notes
    return await client.post(
        f"{WH}/pull-to-store", params={"branch_id": str(branch_id)}, json=body, headers=headers
    )


# ---------------------------------------------------------------- Test D
async def test_d_restock_moves_stock_and_keeps_total(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    assert (await _receive(client, h, branch.id, product.id, 100, 20)).status_code == 201
    await _set_limit(client, h, branch.id, product.id, 24, 5)

    resp = await _pull(client, h, branch.id, product.id, 4, notes="restock test")
    assert resp.status_code == 201, resp.text
    assert resp.json()["quantity_pieces"] == 4
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 76, 24)

    rows = (
        (
            await db_session.execute(
                select(StockTransfer).where(
                    StockTransfer.product_id == product.id,
                    StockTransfer.transfer_type == "warehouse_to_store",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.quantity_pieces == 4
    assert row.created_by == admin.id
    assert row.from_branch_id == branch.id and row.to_branch_id == branch.id
    assert row.notes == "restock test"


# ---------------------------------------------------------------- Test B (restock path)
async def test_b_restock_may_exceed_the_shelf_limit(client, db_session):
    # fix45: the limit is only the default amount, so restock is not refused above it.
    branch, admin, product, h = await _setup(db_session)
    assert (await _receive(client, h, branch.id, product.id, 100, 20)).status_code == 201
    await _set_limit(client, h, branch.id, product.id, 24, 5)

    over = await _pull(client, h, branch.id, product.id, 5)
    assert over.status_code == 201, over.text
    assert over.json()["quantity_pieces"] == 5
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 75, 25)

    more = await _pull(client, h, branch.id, product.id, 1)
    assert more.status_code == 201, more.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 74, 26)


async def test_restock_without_limit_row_is_not_blocked(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    assert (await _receive(client, h, branch.id, product.id, 100, 0)).status_code == 201
    resp = await _pull(client, h, branch.id, product.id, 30)
    assert resp.status_code == 201, resp.text
    summary = await _summary(client, h, branch.id, product.id)
    assert _figures(summary) == (100, 70, 30)
    assert summary["limits_configured"] is False


async def test_restock_more_than_warehouse_holds_is_rejected(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    assert (await _receive(client, h, branch.id, product.id, 10, 0)).status_code == 201
    resp = await _pull(client, h, branch.id, product.id, 11)
    assert resp.status_code == 400
    assert "Not enough stock" in resp.json()["detail"]
    assert _figures(await _summary(client, h, branch.id, product.id)) == (10, 10, 0)


async def test_lowered_limit_does_not_block_restock(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    assert (await _receive(client, h, branch.id, product.id, 30, 30)).status_code == 201
    flagged = await _set_limit(client, h, branch.id, product.id, 24, 5)
    assert flagged["over_limit"] is True
    assert (await _receive(client, h, branch.id, product.id, 10, 0)).status_code == 201

    resp = await _pull(client, h, branch.id, product.id, 1)
    assert resp.status_code == 201, resp.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (40, 9, 31)


# ---------------------------------------------------------------- Test C (restock side)
async def test_c_alert_clears_after_restock(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    await _set_limit(client, h, branch.id, product.id, 24, 5)
    assert (await _receive(client, h, branch.id, product.id, 100, 5)).status_code == 201
    low = await _summary(client, h, branch.id, product.id)
    assert low["stock_state"] == "low_shelf"

    resp = await _pull(client, h, branch.id, product.id, 10)
    assert resp.status_code == 201, resp.text
    after = await _summary(client, h, branch.id, product.id)
    assert _figures(after) == (100, 85, 15)
    assert after["stock_state"] == "ok"
    assert after["alert_label"] is None


# ---------------------------------------------------------------- branch isolation
async def test_restock_in_another_branch_is_forbidden(client, db_session):
    branch_a = await _branch(db_session)
    branch_b = await _branch(db_session)
    admin = await _user(db_session, "super_admin")
    manager_a = await _user(db_session, "branch_manager", branch_a.id)
    product = await _product(db_session)
    await db_session.commit()
    ha, hm = _headers(admin), _headers(manager_a)

    assert (await _receive(client, ha, branch_b.id, product.id, 50, 0)).status_code == 201
    blocked = await _pull(client, hm, branch_b.id, product.id, 5)
    assert blocked.status_code == 403
    assert _figures(await _summary(client, ha, branch_b.id, product.id)) == (50, 50, 0)
    assert _figures(await _summary(client, ha, branch_a.id, product.id)) == (0, 0, 0)


# ---------------------------------------------------------------- Test K (real concurrency)
async def _seed_committed():
    async with AsyncSessionLocal() as s:
        branch = await _branch(s)
        admin = await _user(s, "super_admin")
        product = await _product(s)
        await s.commit()
        return branch.id, admin.id, product.id, _headers(admin)


async def _cleanup(branch_id, admin_id, product_id):
    from sqlalchemy import delete
    from infrastructure.db import models as m

    try:
        async with AsyncSessionLocal() as s:
            receipts = select(m.GoodsReceipt.id).where(m.GoodsReceipt.branch_id == branch_id)
            await s.execute(delete(m.GoodsReceiptLine).where(m.GoodsReceiptLine.receipt_id.in_(receipts)))
            await s.execute(delete(m.StockTransfer).where(m.StockTransfer.product_id == product_id))
            await s.execute(delete(m.GoodsReceipt).where(m.GoodsReceipt.branch_id == branch_id))
            await s.execute(delete(m.StockBatch).where(m.StockBatch.product_id == product_id))
            await s.execute(delete(m.WarehouseStock).where(m.WarehouseStock.product_id == product_id))
            await s.execute(delete(m.ProductShelfSetting).where(m.ProductShelfSetting.product_id == product_id))
            await s.execute(delete(m.AuditLog).where(m.AuditLog.user_id == admin_id))
            await s.execute(delete(m.User).where(m.User.id == admin_id))
            await s.execute(delete(m.Product).where(m.Product.id == product_id))
            await s.execute(delete(m.Branch).where(m.Branch.id == branch_id))
            await s.commit()
    except Exception as exc:
        print("CLEANUP INCOMPLETE (shelf_test is disposable): %r" % (exc,))


async def test_k_two_simultaneous_restocks_both_succeed():
    # fix45: restock may exceed the limit, so both succeed and no stock is lost or doubled.
    branch_id, admin_id, product_id, h = await _seed_committed()
    try:
        app.dependency_overrides.pop(get_db_session, None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await _receive(c, h, branch_id, product_id, 100, 20)).status_code == 201
            await _set_limit(c, h, branch_id, product_id, 24, 5)
            results = await asyncio.gather(
                _pull(c, h, branch_id, product_id, 3),
                _pull(c, h, branch_id, product_id, 3),
            )
            codes = sorted(r.status_code for r in results)
            assert codes == [201, 201], [r.text for r in results]
            assert _figures(await _summary(c, h, branch_id, product_id)) == (100, 74, 26)
    finally:
        await _cleanup(branch_id, admin_id, product_id)


async def test_k_receipt_and_restock_at_the_same_time_cannot_exceed_limit():
    branch_id, admin_id, product_id, h = await _seed_committed()
    try:
        app.dependency_overrides.pop(get_db_session, None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await _receive(c, h, branch_id, product_id, 100, 20)).status_code == 201
            await _set_limit(c, h, branch_id, product_id, 24, 5)
            results = await asyncio.gather(
                _receive(c, h, branch_id, product_id, 10, 4),
                _pull(c, h, branch_id, product_id, 4),
            )
            codes = sorted(r.status_code for r in results)
            assert codes == [201, 400], [r.text for r in results]
            summary = await _summary(c, h, branch_id, product_id)
            assert summary["shelf_stock"] == 24
            assert summary["total_stock"] == summary["warehouse_stock"] + summary["shelf_stock"]
    finally:
        await _cleanup(branch_id, admin_id, product_id)