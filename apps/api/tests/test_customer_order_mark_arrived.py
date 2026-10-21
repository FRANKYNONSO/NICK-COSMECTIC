"""Script test for Patch 2b: "Mark arrived" can add the arrived pieces to the shelf.
Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import random
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import (  # noqa: E402
    _fresh,
    _make_booking,
    _orders_base,
    _say,
    _setup,
    _shelf,
)

from api.v1.routers.customer_requests import sync_arrivals  # noqa: E402
from infrastructure.db.models import CustomerRequest, Product, StockBatch  # noqa: E402

pytestmark = pytest.mark.asyncio


async def _order(client, headers, branch, name, product=None, quantity=2, unit="pieces", pieces=None):
    phone = "080" + "".join(random.choice("0123456789") for _ in range(8))
    body = {
        "branch_id": str(branch.id),
        "item_name": name,
        "quantity": quantity,
        "unit_label": unit,
        "customer_name": "MA Test Customer",
        "customer_phone": phone,
        "deposit_naira": 0,
        "add_to_restock_list": False,
    }
    if product is not None:
        body["product_id"] = str(product.id)
    if pieces is not None:
        body["customer_pieces"] = pieces
    resp = await client.post(_orders_base(), json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


def _url(order_id):
    return _orders_base() + "/" + str(order_id) + "/arrived"


async def _set_price(db, product, kobo):
    product.selling_price_kobo = kobo
    await db.commit()


async def test_a_new_item_creates_product_stock_and_books(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name = "MA New Item " + uuid.uuid4().hex[:8]
    order_id = await _order(client, headers, branch, name, quantity=3, pieces=3)
    resp = await client.post(
        _url(order_id),
        json={"add_to_shelf": True, "pieces_arrived": 3, "selling_price_naira": 150},
        headers=headers,
    )
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    found = await db_session.execute(select(Product).where(Product.name == name))
    new_product = found.scalars().first()
    assert new_product is not None
    shelf = await _shelf(db_session, new_product.id, branch.id)
    _say("product", new_product.status, new_product.selling_price_kobo, new_product.needs_invoice, "shelf", shelf)
    assert new_product.status == "active"
    assert new_product.selling_price_kobo == 15000
    assert new_product.needs_invoice is True
    assert shelf == 3
    await sync_arrivals(db_session, branch.id)
    order = await _fresh(db_session, CustomerRequest, order_id)
    _say("order", order.status, "booking", order.booking_id, "note", order.resolution_note)
    assert order.status == "arrived"
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    print("PASS a: new item -> product, stock, flag and a booking")


async def test_b_no_price_is_refused(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    await _set_price(db_session, product, 0)
    order_id = await _order(client, headers, branch, product.name, product=product)
    resp = await client.post(_url(order_id), json={"add_to_shelf": True, "pieces_arrived": 2}, headers=headers)
    shelf = await _shelf(db_session, product.id, branch.id)
    _say("status", resp.status_code, resp.text[:200], "shelf", shelf)
    assert resp.status_code == 422
    assert "selling price" in resp.text.lower()
    assert shelf == 0
    print("PASS b: price 0 and none typed -> 422, no stock added")


async def test_c_existing_price_is_kept(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    order_id = await _order(client, headers, branch, product.name, product=product)
    resp = await client.post(
        _url(order_id),
        json={"add_to_shelf": True, "pieces_arrived": 2, "selling_price_naira": 99},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    fresh = await _fresh(db_session, Product, product.id)
    shelf = await _shelf(db_session, product.id, branch.id)
    _say("price", fresh.selling_price_kobo, "shelf", shelf)
    assert fresh.selling_price_kobo == 20000
    assert shelf == 2
    print("PASS c: an existing price is kept, typed price ignored")


async def test_d_already_counted_adds_no_stock(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    order_id = await _order(client, headers, branch, product.name, product=product)
    resp = await client.post(_url(order_id), json={"product_id": str(product.id), "hold_pieces": 0}, headers=headers)
    assert resp.status_code == 200, resp.text
    shelf = await _shelf(db_session, product.id, branch.id)
    order = await _fresh(db_session, CustomerRequest, order_id)
    _say("order", order.status, "shelf", shelf)
    assert order.status == "arrived"
    assert shelf == 0
    print("PASS d: 'already counted' adds no stock")


async def test_e_order_with_booking_is_refused(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    before = await _shelf(db_session, c["product"].id, c["branch"].id)
    resp = await client.post(
        _url(c["order_id"]), json={"add_to_shelf": True, "pieces_arrived": 2}, headers=c["headers"]
    )
    after = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("status", resp.status_code, resp.text[:200], "shelf", before, after)
    assert resp.status_code == 409
    assert before == after
    print("PASS e: an order that already has a booking -> 409, shelf unchanged")


async def test_f_pieces_default_to_order_total(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    order_id = await _order(client, headers, branch, product.name, product=product, quantity=2, unit="dozens")
    resp = await client.post(_url(order_id), json={"add_to_shelf": True}, headers=headers)
    assert resp.status_code == 200, resp.text
    shelf = await _shelf(db_session, product.id, branch.id)
    _say("shelf", shelf)
    assert shelf == 24
    print("PASS f: pieces arrived default to the order total (24)")


async def test_g_typed_cost_is_saved(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    order_id = await _order(client, headers, branch, product.name, product=product)
    resp = await client.post(
        _url(order_id),
        json={"add_to_shelf": True, "pieces_arrived": 2, "cost_price_naira": 9},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    result = await db_session.execute(
        select(StockBatch).where(StockBatch.product_id == product.id, StockBatch.branch_id == branch.id)
    )
    batches = list(result.scalars().all())
    _say("batches", [(b.quantity_received, b.unit_cost_kobo) for b in batches])
    assert len(batches) == 1
    assert batches[0].unit_cost_kobo == 900
    print("PASS g: typed cost 9 naira saved as 900 kobo on the batch")


async def test_h_product_gets_the_invoice_flag(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    assert product.needs_invoice is False
    order_id = await _order(client, headers, branch, product.name, product=product)
    resp = await client.post(_url(order_id), json={"add_to_shelf": True, "pieces_arrived": 2}, headers=headers)
    assert resp.status_code == 200, resp.text
    fresh = await _fresh(db_session, Product, product.id)
    _say("needs_invoice", fresh.needs_invoice)
    assert fresh.needs_invoice is True
    print("PASS h: product shows 'Add to invoice' after arrival")
