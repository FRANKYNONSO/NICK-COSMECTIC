"""Branch transfer return tests (RET2).

Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import sys

import pytest
from sqlalchemy import func, select, update

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _summary  # noqa: E402
from test_transfer_invoice import _fig, _made, _total, _two_branches  # noqa: E402

from infrastructure.db.models import PurchaseInvoice, Sale, StockBatch  # noqa: E402

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"


async def _ask(client, h, transfer, pieces, side):
    return await client.post(
        WH + "/transfer-returns",
        json={"transfer_id": str(transfer.id), "quantity_pieces": pieces, "side": side},
        headers=h,
    )


async def _act(client, h, rid, action, body=None):
    return await client.post(WH + "/transfer-returns/" + rid + "/" + action, json=body or {}, headers=h)


async def _fresh(db, model, pk):
    obj = await db.get(model, pk)
    await db.refresh(obj)
    return obj


async def test_a_receiver_asks_sender_approves_and_figures_move(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    fb0 = _fig(await _summary(client, h, fb.id, product.id))
    tb0 = _fig(await _summary(client, h, tb.id, product.id))
    transfer, invoice, line = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r = await _ask(client, h, transfer, 4, "receiver")
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "accepted"
    ok = await _act(client, h, rid, "approve", {"payment_method": "cash"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "completed" and ok.json()["payment_method"] == "cash"
    fb1 = _fig(await _summary(client, h, fb.id, product.id))
    tb1 = _fig(await _summary(client, h, tb.id, product.id))
    assert fb1[0] == fb0[0] - 6
    assert tb1[0] == tb0[0] + 6
    sale = await _fresh(db_session, Sale, transfer.sale_id)
    assert sale.total_kobo == 9000 and sale.status == "completed"
    inv = await _fresh(db_session, PurchaseInvoice, invoice.id)
    assert inv.amount_kobo == 9000 and inv.amount_paid_kobo == 9000
    assert await _total(client, h, tb.id) == 90.0
    n_inv = await db_session.scalar(
        select(func.count()).select_from(PurchaseInvoice).where(PurchaseInvoice.branch_id == tb.id)
    )
    n_sale = await db_session.scalar(select(func.count()).select_from(Sale).where(Sale.channel == "transfer"))
    assert n_inv == 1 and n_sale == 1
    print("PASS a: request, accept, approve")


async def test_b_receiver_starts_sender_approves(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, invoice, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r = await _ask(client, h, transfer, 3, "receiver")
    assert r.status_code == 201 and r.json()["status"] == "accepted", r.text
    ok = await _act(client, h, r.json()["id"], "approve", {"payment_method": "transfer"})
    assert ok.status_code == 200 and ok.json()["payment_method"] == "transfer", ok.text
    inv = await _fresh(db_session, PurchaseInvoice, invoice.id)
    assert inv.amount_kobo == 10500
    print("PASS b: receiver starts")


async def test_c_cannot_ask_for_more_than_sent(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    assert (await _ask(client, h, transfer, 11, "receiver")).status_code == 400
    assert (await _ask(client, h, transfer, 6, "receiver")).status_code == 201
    assert (await _ask(client, h, transfer, 5, "receiver")).status_code == 400
    assert (await _ask(client, h, transfer, 4, "receiver")).status_code == 201
    print("PASS c: open requests count")


async def test_d_full_return_refunds_sale_and_empties_invoice(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, invoice, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r = await _ask(client, h, transfer, 10, "receiver")
    ok = await _act(client, h, r.json()["id"], "approve", {"payment_method": "cash"})
    assert ok.status_code == 200, ok.text
    sale = await _fresh(db_session, Sale, transfer.sale_id)
    assert sale.status == "refunded"
    inv = await _fresh(db_session, PurchaseInvoice, invoice.id)
    assert inv.amount_kobo == 0
    assert await _total(client, h, tb.id) == 0.0
    assert (await _ask(client, h, transfer, 1, "receiver")).status_code == 400
    print("PASS d: full return")


async def test_e_giver_no_longer_holds_the_pieces(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, invoice, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    await db_session.execute(
        update(StockBatch)
        .where(StockBatch.branch_id == tb.id, StockBatch.product_id == product.id)
        .values(quantity_remaining=0)
    )
    await db_session.commit()
    r = await _ask(client, h, transfer, 3, "receiver")
    assert r.status_code == 201, r.text
    bad = await _act(client, h, r.json()["id"], "approve", {"payment_method": "cash"})
    print("EVIDENCE approve status", bad.status_code, bad.text[:200])
    assert bad.status_code == 400, bad.text
    sale = await _fresh(db_session, Sale, transfer.sale_id)
    assert sale.total_kobo == 15000
    print("PASS e: giver short")


async def test_f_decline_and_cancel(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r1 = await _ask(client, h, transfer, 2, "receiver")
    d = await _act(client, h, r1.json()["id"], "decline")
    assert d.status_code == 200 and d.json()["status"] == "declined", d.text
    assert (await _act(client, h, r1.json()["id"], "approve", {"payment_method": "cash"})).status_code == 400
    r2 = await _ask(client, h, transfer, 2, "receiver")
    c = await _act(client, h, r2.json()["id"], "cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled", c.text
    print("PASS f: decline and cancel")


async def test_g_history_list_shows_returned_pieces(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r = await _ask(client, h, transfer, 4, "receiver")
    await _act(client, h, r.json()["id"], "approve", {"payment_method": "cash"})
    await _ask(client, h, transfer, 2, "receiver")
    lst = await client.get(WH + "/transfers", params={"branch_id": str(fb.id)}, headers=h)
    assert lst.status_code == 200, lst.text
    row = [x for x in lst.json() if x["id"] == str(transfer.id)][0]
    assert row["returned_pieces"] == 4 and row["open_return_pieces"] == 2
    assert row["invoice_id"] == str(transfer.invoice_id)
    print("PASS g: history figures")
