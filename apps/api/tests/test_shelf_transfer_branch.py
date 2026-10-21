"""Warehouse -> another branch's shelf tests (fix22).

Shared-session tests are rolled back. The two Test K tests use real committed
sessions and delete their rows afterwards. shelf_test is a disposable database.
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
    _summary,
    _user,
)

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"


async def _xfer(client, headers, from_branch, to_branch, product_id, units):
    body = {
        "product_id": str(product_id),
        "unit_label": "pieces",
        "unit_count": units,
        "pieces_per_unit": 1,
        "to_branch_id": str(to_branch),
    }
    return await client.post(
        f"{WH}/transfer-to-branch", params={"branch_id": str(from_branch)}, json=body, headers=headers
    )


async def _two_branches(db):
    a = await _branch(db)
    b = await _branch(db)
    admin = await _user(db, "super_admin")
    product = await _product(db)
    await db.commit()
    return a, b, admin, product, _headers(admin)


async def test_transfer_to_branch_moves_stock_between_branches(client, db_session):
    a, b, admin, product, h = await _two_branches(db_session)
    assert (await _receive(client, h, a.id, product.id, 100, 0)).status_code == 201
    assert (await _receive(client, h, b.id, product.id, 10, 10)).status_code == 201
    await _set_limit(client, h, b.id, product.id, 24, 5)

    resp = await _xfer(client, h, a.id, b.id, product.id, 10)
    assert resp.status_code == 201, resp.text
    assert _figures(await _summary(client, h, a.id, product.id)) == (90, 90, 0)
    assert _figures(await _summary(client, h, b.id, product.id)) == (20, 0, 20)

    rows = (
        (
            await db_session.execute(
                select(StockTransfer).where(
                    StockTransfer.product_id == product.id,
                    StockTransfer.transfer_type == "warehouse_to_branch",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].from_branch_id == a.id and rows[0].to_branch_id == b.id
    assert rows[0].quantity_pieces == 10 and rows[0].created_by == admin.id


async def test_destination_shelf_limit_allows_4_rejects_5(client, db_session):
    a, b, admin, product, h = await _two_branches(db_session)
    assert (await _receive(client, h, a.id, product.id, 100, 0)).status_code == 201
    assert (await _receive(client, h, b.id, product.id, 20, 20)).status_code == 201
    await _set_limit(client, h, b.id, product.id, 24, 5)

    too_many = await _xfer(client, h, a.id, b.id, product.id, 5)
    assert too_many.status_code == 400, too_many.text
    assert "Only 4" in too_many.json()["detail"]
    assert _figures(await _summary(client, h, a.id, product.id)) == (100, 100, 0)
    assert _figures(await _summary(client, h, b.id, product.id)) == (20, 0, 20)

    ok = await _xfer(client, h, a.id, b.id, product.id, 4)
    assert ok.status_code == 201, ok.text
    assert _figures(await _summary(client, h, a.id, product.id)) == (96, 96, 0)
    assert _figures(await _summary(client, h, b.id, product.id)) == (24, 0, 24)


async def test_destination_without_limit_row_is_not_blocked(client, db_session):
    a, b, admin, product, h = await _two_branches(db_session)
    assert (await _receive(client, h, a.id, product.id, 100, 0)).status_code == 201
    resp = await _xfer(client, h, a.id, b.id, product.id, 30)
    assert resp.status_code == 201, resp.text
    assert _figures(await _summary(client, h, b.id, product.id)) == (30, 0, 30)


async def test_transfer_more_than_source_warehouse_holds_is_rejected(client, db_session):
    a, b, admin, product, h = await _two_branches(db_session)
    assert (await _receive(client, h, a.id, product.id, 10, 0)).status_code == 201
    resp = await _xfer(client, h, a.id, b.id, product.id, 11)
    assert resp.status_code == 400
    assert "Not enough stock" in resp.json()["detail"]
    assert _figures(await _summary(client, h, a.id, product.id)) == (10, 10, 0)
    assert _figures(await _summary(client, h, b.id, product.id)) == (0, 0, 0)


# ---------------------------------------------------------------- Test K (real concurrency)
async def _seed_committed():
    async with AsyncSessionLocal() as s:
        a = await _branch(s)
        b = await _branch(s)
        admin = await _user(s, "super_admin")
        product = await _product(s)
        await s.commit()
        return a.id, b.id, admin.id, product.id, _headers(admin)


async def _cleanup(branch_ids, admin_id, product_id):
    from sqlalchemy import delete
    from infrastructure.db import models as m

    try:
        async with AsyncSessionLocal() as s:
            receipts = select(m.GoodsReceipt.id).where(m.GoodsReceipt.branch_id.in_(branch_ids))
            await s.execute(delete(m.GoodsReceiptLine).where(m.GoodsReceiptLine.receipt_id.in_(receipts)))
            await s.execute(delete(m.StockTransfer).where(m.StockTransfer.product_id == product_id))
            await s.execute(delete(m.GoodsReceipt).where(m.GoodsReceipt.branch_id.in_(branch_ids)))
            await s.execute(delete(m.StockBatch).where(m.StockBatch.product_id == product_id))
            await s.execute(delete(m.WarehouseStock).where(m.WarehouseStock.product_id == product_id))
            await s.execute(delete(m.ProductShelfSetting).where(m.ProductShelfSetting.product_id == product_id))
            await s.execute(delete(m.AuditLog).where(m.AuditLog.user_id == admin_id))
            await s.execute(delete(m.User).where(m.User.id == admin_id))
            await s.execute(delete(m.Product).where(m.Product.id == product_id))
            await s.execute(delete(m.Branch).where(m.Branch.id.in_(branch_ids)))
            await s.commit()
    except Exception as exc:
        print("CLEANUP INCOMPLETE (shelf_test is disposable): %r" % (exc,))


async def test_k_transfer_and_receipt_at_destination_cannot_exceed_limit():
    a_id, b_id, admin_id, product_id, h = await _seed_committed()
    try:
        app.dependency_overrides.pop(get_db_session, None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await _receive(c, h, a_id, product_id, 100, 0)).status_code == 201
            assert (await _receive(c, h, b_id, product_id, 20, 20)).status_code == 201
            await _set_limit(c, h, b_id, product_id, 24, 5)
            results = await asyncio.gather(
                _xfer(c, h, a_id, b_id, product_id, 3),
                _receive(c, h, b_id, product_id, 3, 3),
            )
            codes = sorted(r.status_code for r in results)
            assert codes == [201, 400], [r.text for r in results]
            assert (await _summary(c, h, b_id, product_id))["shelf_stock"] == 23
    finally:
        await _cleanup([a_id, b_id], admin_id, product_id)


async def test_k_opposite_transfers_at_the_same_time_do_not_deadlock():
    a_id, b_id, admin_id, product_id, h = await _seed_committed()
    try:
        app.dependency_overrides.pop(get_db_session, None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await _receive(c, h, a_id, product_id, 50, 0)).status_code == 201
            assert (await _receive(c, h, b_id, product_id, 50, 0)).status_code == 201
            results = await asyncio.gather(
                _xfer(c, h, a_id, b_id, product_id, 5),
                _xfer(c, h, b_id, a_id, product_id, 5),
            )
            assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
            assert _figures(await _summary(c, h, a_id, product_id)) == (50, 45, 5)
            assert _figures(await _summary(c, h, b_id, product_id)) == (50, 45, 5)
    finally:
        await _cleanup([a_id, b_id], admin_id, product_id)