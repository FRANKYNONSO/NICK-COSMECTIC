"""Script test for Patch 6c: the pushable list and the link-product route tell the
push screen how much of an invoice line was already received in Receive Goods.
Run ONLY against shelf_test. Every test is rolled back.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _invoice, _receive, _setup  # noqa: E402

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


async def _list_item(client, h, branch, line):
    resp = await client.get(WH + "/pushable-invoice-items", params={"branch_id": str(branch.id)}, headers=h)
    assert resp.status_code == 200, resp.text
    found = [x for x in resp.json() if x["invoice_line_item_id"] == str(line.id)]
    assert len(found) == 1, "line not in the pushable list: " + str(len(found))
    return found[0]


async def _link(client, h, line, product):
    return await client.patch(
        WH + "/invoice-line-items/" + str(line.id) + "/link-product",
        json={"product_id": str(product.id)},
        headers=h,
    )


async def test_a_list_shows_received_and_outstanding(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    assert (await _receive_line(client, h, branch, product, inv, line, 40)).status_code == 201
    item = await _list_item(client, h, branch, line)
    print("EVIDENCE received", item["received_pieces"], "outstanding", item["outstanding_pieces"])
    assert item["received_pieces"] == 40
    assert item["outstanding_pieces"] == 60
    print("PASS a: partly received line shows received 40 and outstanding 60")


async def test_b_no_receipts_gives_nulls(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    item = await _list_item(client, h, branch, line)
    print("EVIDENCE received", item["received_pieces"], "outstanding", item["outstanding_pieces"])
    assert item["received_pieces"] is None
    assert item["outstanding_pieces"] is None
    print("PASS b: a line with no receipts shows null and null")


async def test_c_unknown_total_gives_nulls(client, db_session):
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
    item = await _list_item(client, h, branch, line)
    print("EVIDENCE received", item["received_pieces"], "outstanding", item["outstanding_pieces"])
    assert item["received_pieces"] is None
    assert item["outstanding_pieces"] is None
    print("PASS c: a line with unknown total pieces shows null and null")


async def test_d_received_then_pushed(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    assert (await _receive_line(client, h, branch, product, inv, line, 40)).status_code == 201
    resp = await _push(client, h, branch, product, line, 20)
    print("EVIDENCE push 20 status", resp.status_code, resp.text[:200])
    assert resp.status_code == 201, resp.text
    item = await _list_item(client, h, branch, line)
    print("EVIDENCE received", item["received_pieces"], "outstanding", item["outstanding_pieces"])
    assert item["received_pieces"] == 40
    assert item["outstanding_pieces"] == 40
    print("PASS d: received 40 and pushed 20 leaves outstanding 40")


async def test_e_link_route_partly_received(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    assert (await _receive_line(client, h, branch, product, inv, line, 40)).status_code == 201
    resp = await _link(client, h, line, product)
    print("EVIDENCE link status", resp.status_code, resp.text[:300])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received_pieces"] == 40
    assert body["outstanding_pieces"] == 60
    print("PASS e: link-product route shows received 40 and outstanding 60")


async def test_f_link_route_no_receipts(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    inv, line = await _invoice(db_session, branch, admin, product, ordered=100)
    resp = await _link(client, h, line, product)
    print("EVIDENCE link status", resp.status_code, resp.text[:300])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received_pieces"] is None
    assert body["outstanding_pieces"] is None
    print("PASS f: link-product route for a line with no receipts shows null and null")
