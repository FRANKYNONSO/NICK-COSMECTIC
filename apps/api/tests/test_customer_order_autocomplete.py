"""Script test for Patch 3b: a customer order whose deposit covers the price completes by itself.
Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import SALES, _fresh, _orders_base, _paid, _say, _setup, _shelf  # noqa: E402

from api.v1.routers.customer_requests import sync_arrivals  # noqa: E402
from infrastructure.db.models import (  # noqa: E402
    CustomerRequest,
    PendingChange,
    Sale,
    SalePayment,
    StockBatch,
)

pytestmark = pytest.mark.asyncio

PRICE_KOBO = 20000  # 200 naira, same as the product made by _setup


async def _arrive(client, db, deposit_naira, method, wanted=1, stock=5):
    """New order -> new shelf stock -> sync_arrivals. Returns (context, fresh order)."""
    branch, user, product, headers = await _setup(db)
    phone = "080" + "".join(random.choice("0123456789") for _ in range(8))
    body = {
        "branch_id": str(branch.id),
        "item_name": product.name,
        "product_id": str(product.id),
        "quantity": wanted,
        "unit_label": "pieces",
        "customer_name": "CO Test Customer",
        "customer_phone": phone,
        "deposit_naira": deposit_naira,
        "customer_pieces": wanted,
        "add_to_restock_list": False,
    }
    if deposit_naira > 0:
        body["deposit_method"] = method
    resp = await client.post(_orders_base(), json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    order_id = uuid.UUID(resp.json()["id"])
    db.add(
        StockBatch(
            product_id=product.id,
            branch_id=branch.id,
            quantity_received=stock,
            quantity_remaining=stock,
            unit_cost_kobo=100,
            received_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
    )
    await db.commit()
    await sync_arrivals(db, branch.id)
    order = await _fresh(db, CustomerRequest, order_id)
    ctx = {"branch": branch, "user": user, "product": product, "headers": headers, "order_id": order_id}
    return ctx, order


async def _changes(db, sale_id):
    result = await db.execute(select(PendingChange).where(PendingChange.sale_id == sale_id))
    return list(result.scalars().all())


async def _payment_rows(db, sale_id):
    result = await db.execute(select(SalePayment).where(SalePayment.sale_id == sale_id))
    return list(result.scalars().all())


async def test_o_deposit_equals_price_completes(client, db_session):
    c, order = await _arrive(client, db_session, 200, "cash")
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db_session, Sale, order.booking_id)
    changes = await _changes(db_session, sale.id)
    paid = await _paid(db_session, sale.id)
    shelf = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("sale", sale.status, "method", sale.payment_method, "order", order.status, "paid", paid, "shelf", shelf, "changes", len(changes))
    assert sale.status == "completed"
    assert sale.payment_method == "cash"
    assert order.status == "collected"
    assert order.collected_at is not None
    assert order.resolution_note is None
    assert paid == 20000
    assert shelf == 4
    assert len(changes) == 0
    resp = await client.get(SALES + "/" + str(sale.id), headers=c["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    _say("route: status", data["status"], "paid", data["paid_naira"], "pending_changes", data["pending_changes"])
    assert data["status"] == "completed"
    assert data["pending_changes"] == []
    print("PASS o: deposit equal to the price completes the booking, no pending change")


async def test_p_deposit_above_price_makes_pending_change(client, db_session):
    c, order = await _arrive(client, db_session, 250, "transfer")
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db_session, Sale, order.booking_id)
    changes = await _changes(db_session, sale.id)
    paid = await _paid(db_session, sale.id)
    shelf = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("sale", sale.status, "order", order.status, "paid", paid, "shelf", shelf, "changes", len(changes))
    assert sale.status == "completed"
    assert sale.total_kobo == 20000
    assert order.status == "collected"
    assert paid == 25000  # the payment row keeps the TRUE amount paid
    assert shelf == 4
    assert len(changes) == 1
    ch = changes[0]
    _say("change", ch.amount_kobo, ch.status, ch.settlement_method, ch.reference_code, ch.created_by)
    assert ch.amount_kobo == 5000
    assert ch.status == "outstanding"
    assert ch.settlement_method is None
    assert ch.reference_code and ch.reference_code.startswith("CHG-")
    assert ch.sale_id == sale.id
    assert ch.created_by == c["user"].id
    assert ch.customer_name == "CO Test Customer"
    resp = await client.get(SALES + "/" + str(sale.id), headers=c["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    _say("route: total", data["total_naira"], "paid", data["paid_naira"], "pending_changes", data["pending_changes"])
    assert data["status"] == "completed"
    assert data["total_naira"] == 200.0
    assert data["paid_naira"] == 250.0
    assert len(data["pending_changes"]) == 1
    assert data["pending_changes"][0]["amount_naira"] == 50.0
    assert data["pending_changes"][0]["reference_code"] == ch.reference_code
    assert data["pending_changes"][0]["status"] == "outstanding"
    await sync_arrivals(db_session, c["branch"].id)  # a second run must change nothing
    again = await _changes(db_session, sale.id)
    count = await db_session.scalar(select(func.count()).select_from(Sale).where(Sale.branch_id == c["branch"].id))
    assert len(again) == 1
    assert count == 1
    assert await _shelf(db_session, c["product"].id, c["branch"].id) == 4
    print("PASS p: deposit above the price completes and makes ONE pending change (CHG code)")


async def test_q_deposit_below_price_only_books(client, db_session):
    c, order = await _arrive(client, db_session, 100, "cash")
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db_session, Sale, order.booking_id)
    changes = await _changes(db_session, sale.id)
    shelf = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("sale", sale.status, "order", order.status, "shelf", shelf, "changes", len(changes))
    assert sale.status == "booking"
    assert order.status == "arrived"
    assert shelf == 5
    assert len(changes) == 0
    print("PASS q: deposit below the price still only books")


async def test_r_online_deposit_below_price_books(client, db_session):
    c, order = await _arrive(client, db_session, 100, "online")
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db_session, Sale, order.booking_id)
    rows = await _payment_rows(db_session, sale.id)
    _say("sale", sale.status, "order", order.status, "payment rows", [(r.method, r.amount_kobo) for r in rows])
    assert sale.status == "booking"
    assert order.status == "arrived"
    assert len(rows) == 1 and rows[0].method == "online" and rows[0].amount_kobo == 10000
    print("PASS r: an 'online' deposit books and the payment row keeps the word online")


async def test_s_online_deposit_above_price_completes(client, db_session):
    c, order = await _arrive(client, db_session, 250, "online")
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db_session, Sale, order.booking_id)
    changes = await _changes(db_session, sale.id)
    rows = await _payment_rows(db_session, sale.id)
    _say("sale", sale.status, "order", order.status, "payment rows", [(r.method, r.amount_kobo) for r in rows], "changes", len(changes))
    assert sale.status == "completed"
    assert order.status == "collected"
    assert len(rows) == 1 and rows[0].method == "online" and rows[0].amount_kobo == 25000
    assert len(changes) == 1 and changes[0].amount_kobo == 5000
    print("PASS s: an 'online' deposit above the price completes with a pending change")


async def test_t_partial_arrival_covered_deposit_waits(client, db_session):
    # needs 3, only 2 on the shelf, deposit 400 already covers the 2 pieces
    c, order = await _arrive(client, db_session, 400, "cash", wanted=3, stock=2)
    sales_count = await db_session.scalar(select(func.count()).select_from(Sale).where(Sale.branch_id == c["branch"].id))
    shelf = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("order", order.status, "booking", order.booking_id, "note", order.resolution_note, "sales", sales_count, "shelf", shelf)
    assert order.status == "arrived"
    assert order.booking_id is None
    assert order.resolution_note and order.resolution_note.startswith("Arrived, not booked yet: only 2 of 3")
    assert sales_count == 0
    assert shelf == 2
    print("PASS t: partial arrival with a covering deposit waits and shows the note")


async def test_u_partial_arrival_small_deposit_books_arrived_pieces(client, db_session):
    # needs 3, only 2 on the shelf, deposit 100 does not cover 2 pieces (400)
    c, order = await _arrive(client, db_session, 100, "cash", wanted=3, stock=2)
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db_session, Sale, order.booking_id)
    _say("sale", sale.status, "total_kobo", sale.total_kobo, "order", order.status)
    assert sale.status == "booking"
    assert sale.total_kobo == 40000
    assert order.status == "arrived"
    print("PASS u: partial arrival with a small deposit books the pieces that arrived")