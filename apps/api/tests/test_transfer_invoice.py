"""Branch-transfer invoice tests (XFER5).

Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _branch, _headers, _product, _receive, _summary, _user  # noqa: E402

from infrastructure.db.models import PurchaseInvoice, PurchaseInvoiceLineItem, StockTransfer  # noqa: E402

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"


# Paths read from main.py (prefix /api/v1) and the router (prefix /purchase-invoices).
PATCH_PATH = "/api/v1/purchase-invoices/{invoice_id}"
TOTAL_PATH = "/api/v1/purchase-invoices/total-invested"


def _fig(summary):
    return (summary["total_stock"], summary["warehouse_stock"], summary["shelf_stock"])


async def _two_branches(client, db):
    from_branch = await _branch(db)
    to_branch = await _branch(db)
    admin = await _user(db, "super_admin", None)
    product = await _product(db)
    product.selling_price_kobo = 2000
    await db.commit()
    h = _headers(admin)
    r = await _receive(client, h, from_branch.id, product.id, 50, 50)
    assert r.status_code == 201, r.text
    return from_branch, to_branch, product, h


async def _transfer(client, h, from_branch, to_branch, product, pieces, price_naira=15.0, pay="transfer"):
    return await client.post(
        WH + "/transfers",
        json={
            "product_id": str(product.id),
            "from_branch_id": str(from_branch.id),
            "to_branch_id": str(to_branch.id),
            "unit_label": "pieces",
            "unit_count": pieces,
            "pieces_per_unit": 1,
            "payment_method": pay,
            "unit_price_naira": price_naira,
            "take_from": "shelf",
        },
        headers=h,
    )


async def _made(client, db, h, from_branch, to_branch, product, pieces=10, pay="transfer"):
    resp = await _transfer(client, h, from_branch, to_branch, product, pieces, pay=pay)
    assert resp.status_code == 201, resp.text
    transfer = await db.get(StockTransfer, uuid.UUID(resp.json()["id"]))
    assert transfer.invoice_id is not None
    invoice = await db.get(PurchaseInvoice, transfer.invoice_id)
    line = (
        await db.execute(
            select(PurchaseInvoiceLineItem).where(PurchaseInvoiceLineItem.invoice_id == invoice.id)
        )
    ).scalars().one()
    return transfer, invoice, line


async def _total(client, h, branch_id=None):
    params = {"branch_id": str(branch_id)} if branch_id else {}
    resp = await client.get(TOTAL_PATH, params=params, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["total_landed_cost_naira"]


async def test_a_transfer_makes_an_invoice_for_the_receiving_branch(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, invoice, line = await _made(client, db_session, h, fb, tb, product)
    assert invoice.source == "branch_transfer"
    assert invoice.branch_id == tb.id
    assert invoice.status == "completed"
    assert invoice.amount_kobo == 15000
    assert invoice.amount_paid_kobo == 15000
    assert invoice.invoice_number == "TRF-" + str(transfer.sale_id)[:8].upper()
    assert invoice.supplier_name == f"{fb.name} (branch transfer)"
    assert invoice.payment_method == "bank_transfer"
    assert invoice.description == f"Branch transfer from {fb.name} to {tb.name}. Arrives as shelf 10."
    assert line.item_name == product.name
    assert line.quantity == 10 and line.unit_label == "pieces"
    assert line.rate_kobo == 1500 and line.line_total_kobo == 15000
    assert line.product_id is None
    assert line.total_pieces is None and line.cost_price_per_piece_kobo is None
    print("PASS a: invoice", invoice.invoice_number)


async def test_a2_cash_transfer_keeps_cash_as_payment(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    _, invoice, _ = await _made(client, db_session, h, fb, tb, product, pay="cash")
    assert invoice.payment_method == "cash"
    print("PASS a2: cash kept")


async def test_b_invoice_cannot_be_received_or_pushed(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    _, invoice, line = await _made(client, db_session, h, fb, tb, product)
    before = _fig(await _summary(client, h, tb.id, product.id))
    resp = await _receive(client, h, tb.id, product.id, 5, 0, invoice_id=str(invoice.id))
    print("EVIDENCE receive status", resp.status_code, resp.text[:200])
    assert resp.status_code == 400, resp.text
    assert "branch transfer" in resp.json()["detail"]
    push = await client.post(
        WH + "/push-to-warehouse",
        params={"branch_id": str(tb.id)},
        json={"product_id": str(product.id), "invoice_line_item_id": str(line.id), "pieces_count": 1},
        headers=h,
    )
    print("EVIDENCE push status", push.status_code, push.text[:200])
    assert push.status_code == 400, push.text
    assert "branch transfer" in push.json()["detail"]
    listed = await client.get(WH + "/pushable-invoice-items", params={"branch_id": str(tb.id)}, headers=h)
    assert listed.status_code == 200, listed.text
    assert str(line.id) not in [x["invoice_line_item_id"] for x in listed.json()]
    assert _fig(await _summary(client, h, tb.id, product.id)) == before
    print("PASS b: receive and push refused, not in the push list, stock unchanged")


async def test_c_receipt_linked_to_the_invoice_line_is_refused(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    _, invoice, line = await _made(client, db_session, h, fb, tb, product)
    before = _fig(await _summary(client, h, tb.id, product.id))
    resp = await _receive(
        client, h, tb.id, product.id, 5, 0, line_extra={"invoice_line_item_id": str(line.id)}
    )
    print("EVIDENCE line receive status", resp.status_code, resp.text[:300])
    assert "branch transfer" in resp.text.lower()
    assert _fig(await _summary(client, h, tb.id, product.id)) == before
    print("PASS c: line-level receive refused, stock unchanged")


async def test_d_invoice_cannot_be_edited(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    _, invoice, _ = await _made(client, db_session, h, fb, tb, product)
    resp = await client.patch(
        PATCH_PATH.replace("{invoice_id}", str(invoice.id)),
        json={"description": "changed"},
        headers=h,
    )
    print("EVIDENCE patch status", resp.status_code, resp.text[:200])
    assert resp.status_code == 400, resp.text
    assert "read-only" in resp.json()["detail"]
    print("PASS d: edit refused")


async def test_e_totals(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    all_before = await _total(client, h)
    await _made(client, db_session, h, fb, tb, product)
    all_after = await _total(client, h)
    assert all_after == pytest.approx(all_before)
    assert await _total(client, h, tb.id) == pytest.approx(150.0)
    print("PASS e: all-branches total unchanged, receiving branch total 150.0")


async def test_f_refused_transfer_leaves_no_invoice(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    resp = await _transfer(client, h, fb, tb, product, 999)
    assert resp.status_code == 400, resp.text
    count = await db_session.scalar(
        select(func.count()).select_from(PurchaseInvoice).where(PurchaseInvoice.branch_id == tb.id)
    )
    assert count == 0
    print("PASS f: refused transfer, no invoice")
