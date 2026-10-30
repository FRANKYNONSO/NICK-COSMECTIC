"""Branch transfer return tests, part 2 (RET2b).

Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import sys

import pytest
from sqlalchemy import select, update

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _branch, _headers, _set_limit, _user  # noqa: E402
from test_transfer_invoice import _made, _two_branches  # noqa: E402
from test_transfer_returns import _act, _ask, _fresh  # noqa: E402

from infrastructure.db.models import Sale, SaleItem, StockBatch, WarehouseStock  # noqa: E402

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"


async def _manager(db, branch):
    user = await _user(db, "branch_manager", branch.id)
    await db.commit()
    return _headers(user)


async def test_h_manager_roles(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    third = await _branch(db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    mf = await _manager(db_session, fb)
    mt = await _manager(db_session, tb)
    m3 = await _manager(db_session, third)
    # a manager of a third branch can not even ask
    r3 = await _ask(client, m3, transfer, 1, "receiver")
    assert r3.status_code == 403, r3.text
    # sender manager can not start a return: only the branch that received the goods can
    r0 = await _ask(client, mf, transfer, 1, "sender")
    assert r0.status_code == 403, r0.text
    assert "received the goods" in r0.json()["detail"]
    # receiver manager asks; only the receiving branch may cancel it, only the sender may decline or approve
    r1 = await _ask(client, mt, transfer, 2, "receiver")
    assert r1.status_code == 201 and r1.json()["status"] == "accepted", r1.text
    assert (await _act(client, mf, r1.json()["id"], "cancel")).status_code == 403
    assert (await _act(client, mt, r1.json()["id"], "decline")).status_code == 403
    # receiver manager can not approve the refund, sender manager can
    bad = await _act(client, mt, r1.json()["id"], "approve", {"payment_method": "cash"})
    assert bad.status_code == 403, bad.text
    ok = await _act(client, mf, r1.json()["id"], "approve", {"payment_method": "cash"})
    assert ok.status_code == 200 and ok.json()["status"] == "completed", ok.text
    # receiver manager starts a return, only sender side can approve
    r2 = await _ask(client, mt, transfer, 3, "receiver")
    assert r2.status_code == 201 and r2.json()["status"] == "accepted", r2.text
    assert (await _act(client, mt, r2.json()["id"], "approve", {"payment_method": "cash"})).status_code == 403
    assert (await _act(client, mf, r2.json()["id"], "approve", {"payment_method": "transfer"})).status_code == 200
    print("PASS h: manager roles")


async def test_i_sender_shelf_full_goes_to_warehouse(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    # sender shelf now holds 40; save its default as 40 so the shelf is full
    await _set_limit(client, h, fb.id, product.id, 40, 5)
    r = await _ask(client, h, transfer, 4, "receiver")
    assert r.status_code == 201, r.text
    ok = await _act(client, h, r.json()["id"], "approve", {"payment_method": "cash"})
    assert ok.status_code == 200, ok.text
    print("EVIDENCE landed", ok.json()["landed_shelf_pieces"], ok.json()["landed_warehouse_pieces"])
    assert ok.json()["landed_shelf_pieces"] == 0
    assert ok.json()["landed_warehouse_pieces"] == 4
    print("PASS i: full shelf, pieces go to the warehouse")


async def test_j_giver_warehouse_covers_short_shelf(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    # receiver: empty the shelf, put the 10 pieces in its warehouse instead
    await db_session.execute(
        update(StockBatch)
        .where(StockBatch.branch_id == tb.id, StockBatch.product_id == product.id)
        .values(quantity_remaining=0)
    )
    await db_session.execute(
        update(WarehouseStock)
        .where(WarehouseStock.branch_id == tb.id, WarehouseStock.product_id == product.id)
        .values(quantity_pieces=10)
    )
    await db_session.commit()
    r = await _ask(client, h, transfer, 3, "receiver")
    assert r.status_code == 201, r.text
    ok = await _act(client, h, r.json()["id"], "approve", {"payment_method": "cash"})
    assert ok.status_code == 200, ok.text
    print("EVIDENCE taken", ok.json()["taken_shelf_pieces"], ok.json()["taken_warehouse_pieces"])
    assert ok.json()["taken_shelf_pieces"] == 0
    assert ok.json()["taken_warehouse_pieces"] == 3
    wh = await db_session.scalar(
        select(WarehouseStock.quantity_pieces).where(
            WarehouseStock.branch_id == tb.id, WarehouseStock.product_id == product.id
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.branch_id == tb.id, WarehouseStock.product_id == product.id
            )
        )
    ).scalar_one()
    await db_session.refresh(row)
    assert row.quantity_pieces == 7, (wh, row.quantity_pieces)
    print("PASS j: giver warehouse used")


async def test_k_refund_sale_refuses_a_transfer_sale(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    item = (
        await db_session.execute(select(SaleItem).where(SaleItem.sale_id == transfer.sale_id))
    ).scalars().first()
    resp = await client.post(
        "/api/v1/sales/" + str(transfer.sale_id) + "/refund",
        json={
            "lines": [{"sale_item_id": str(item.id), "quantity": 1}],
            "reason": "test",
            "image_url": "x",
        },
        headers=h,
    )
    print("EVIDENCE refund status", resp.status_code, resp.text[:200])
    assert resp.status_code == 400, resp.text
    assert "branch transfer" in resp.json()["detail"]
    sale = await _fresh(db_session, Sale, transfer.sale_id)
    assert sale.total_kobo == 15000 and sale.status == "completed"
    print("PASS k: refund_sale refuses a transfer sale")


async def test_l_staff_is_refused(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    staff = await _user(db_session, "staff", fb.id)
    await db_session.commit()
    resp = await _ask(client, _headers(staff), transfer, 1, "sender")
    assert resp.status_code == 403, resp.text
    print("PASS l: staff refused")
