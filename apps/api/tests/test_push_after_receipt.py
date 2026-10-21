"""Script test for Part C item 5: goods received in Receive Goods must also be
subtracted on the warehouse push side (no double counting on one invoice line).
Run ONLY against shelf_test. Every test is rolled back.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _invoice, _product, _receive, _setup, _summary  # noqa: E402

from infrastructure.db.models import PurchaseInvoice, PurchaseInvoiceLineItem  # noqa: E402

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"


async def _receive_line(client, h, branch, product, invoice, line, pieces):
    return await _receive(
        client, h, branch.id, product.id, pieces, 0,
        line_extra={"invoice_line_item_id": str(line.id)},
        invoice_id=str(invoice.id),
    )


async def _push(client, h, branch, product, line, pieces):
    return await client.post(
        WH + "/push-to-warehouse",
        params={"branch_id": str(branch.id)},
        json={
            "product_id": str(product.id),
            "invoice_line_item_id": str(line.id),
            "pieces_count": pieces,
        },
        headers=h,
    )


async def _warehouse(client, h, branch, product):
    return (await _summary(client, h, branch.id, product.id))["warehouse_stock"]


async def _pushable_ids(client, h, branch):
    resp = await client.get(WH + "/pushable-invoice-items", params={"branch_id": str(branch.id)}, headers=h)
    assert resp.status_code == 200, resp.text
    return [x["invoice_line_item_id"] for x in resp.json()]


async def test_a_push_after_full_receipt_is_refused(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    r = await _receive_line(client, h, branch, product, inv, line, 100)
    assert r.status_code == 201, r.text
    assert await _warehouse(client, h, branch, product) == 100
    resp = await _push(client, h, branch, product, line, 100)
    print("EVIDENCE push status", resp.status_code, resp.text[:300])
    assert resp.status_code == 400, resp.text
    assert "still outstanding" in resp.json()["detail"]
    assert await _warehouse(client, h, branch, product) == 100
    print("PASS a: push after a full receipt is refused, warehouse stays 100")


async def test_b_push_only_what_is_left_after_a_partial_receipt(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    r = await _receive_line(client, h, branch, product, inv, line, 40)
    assert r.status_code == 201, r.text
    resp = await _push(client, h, branch, product, line, 60)
    print("EVIDENCE push 60 status", resp.status_code, resp.text[:200])
    assert resp.status_code == 201, resp.text
    assert await _warehouse(client, h, branch, product) == 100
    resp = await _push(client, h, branch, product, line, 1)
    print("EVIDENCE push 1 status", resp.status_code, resp.text[:300])
    assert resp.status_code == 400, resp.text
    assert "still outstanding" in resp.json()["detail"]
    assert await _warehouse(client, h, branch, product) == 100
    print("PASS b: only the 60 left can be pushed, one more piece is refused")


async def test_c_line_without_receipts_pushes_as_before(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    resp = await _push(client, h, branch, product, line, 100)
    print("EVIDENCE push status", resp.status_code, resp.text[:200])
    assert resp.status_code == 201, resp.text
    assert await _warehouse(client, h, branch, product) == 100
    print("PASS c: a line with no receipts can still be pushed in full")


async def test_d_receive_after_push_is_refused(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    resp = await _push(client, h, branch, product, line, 100)
    assert resp.status_code == 201, resp.text
    r = await _receive_line(client, h, branch, product, inv, line, 100)
    print("EVIDENCE receive status", r.status_code, r.text[:300])
    assert r.status_code == 400, r.text
    assert "still outstanding" in r.json()["detail"]
    assert await _warehouse(client, h, branch, product) == 100
    print("PASS d: receiving after a full push is refused")


async def test_e_push_list_hides_a_fully_received_line(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    product2 = await _product(db_session)
    await db_session.commit()
    inv1, line1 = await _invoice(db_session, branch, admin, product, ordered=100)
    inv2, line2 = await _invoice(db_session, branch, admin, product2, ordered=100)
    assert (await _receive_line(client, h, branch, product, inv1, line1, 100)).status_code == 201
    assert (await _receive_line(client, h, branch, product2, inv2, line2, 40)).status_code == 201
    ids = await _pushable_ids(client, h, branch)
    print("EVIDENCE line1 in list", str(line1.id) in ids, "line2 in list", str(line2.id) in ids)
    assert str(line1.id) not in ids
    assert str(line2.id) in ids
    print("PASS e: fully received line is hidden, partly received line stays")


async def test_f_unknown_total_is_not_guarded_known_limit(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv = PurchaseInvoice(
        branch_id=branch.id, supplier_name="PK Supplier", invoice_date=date.today(), created_by=admin.id
    )
    db_session.add(inv)
    await db_session.flush()
    line = PurchaseInvoiceLineItem(
        invoice_id=inv.id, position=0, item_name=product.name, product_id=product.id,
        quantity=100, unit_label="pieces", rate_kobo=150, line_total_kobo=15000,
    )
    db_session.add(line)
    await db_session.flush()
    assert line.total_pieces is None
    assert (await _receive_line(client, h, branch, product, inv, line, 100)).status_code == 201
    resp = await _push(client, h, branch, product, line, 100)
    print("EVIDENCE push status", resp.status_code, resp.text[:200])
    assert resp.status_code == 201, resp.text
    assert await _warehouse(client, h, branch, product) == 200
    print("PASS f: a line with unknown total pieces is not guarded (known limit)")
