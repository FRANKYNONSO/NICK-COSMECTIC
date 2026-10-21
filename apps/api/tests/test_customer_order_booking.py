"""Script test: customer order -> booking (Patch 2a) and booking -> order (Patch 3a).

Run ONLY against shelf_test (set DATABASE_URL for the run). Every test is
rolled back by the db_session fixture, so no rows are left behind.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from api.v1.routers.customer_requests import sync_arrivals
from infrastructure.db.models import (
    Branch,
    CustomerRequest,
    Product,
    Refund,
    Sale,
    SalePayment,
    StockBatch,
    User,
)
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password
from main import app

pytestmark = pytest.mark.asyncio

SALES = "/api/v1/sales"
PRICE_KOBO = 20000  # 200 naira


def _say(*parts):
    print("EVIDENCE", *parts)


def _orders_base():
    paths = list(app.openapi()["paths"].keys())
    found = sorted(
        (p for p in paths if "customer-request" in p and "{" not in p and "product-search" not in p),
        key=len,
    )
    assert found, "Cannot find the customer orders route. Some paths seen: " + str(paths[:15])
    return found[0]


async def _fresh(db, model, row_id):
    result = await db.execute(
        select(model).where(model.id == row_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _paid(db, sale_id):
    total = await db.scalar(
        select(func.coalesce(func.sum(SalePayment.amount_kobo), 0)).where(SalePayment.sale_id == sale_id)
    )
    return int(total or 0)


async def _shelf(db, product_id, branch_id):
    total = await db.scalar(
        select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
            StockBatch.product_id == product_id, StockBatch.branch_id == branch_id
        )
    )
    return int(total or 0)


async def _refunds(db, sale_id):
    result = await db.execute(select(Refund).where(Refund.sale_id == sale_id))
    return list(result.scalars().all())


async def _setup(db):
    tag = uuid.uuid4().hex[:8]
    branch = Branch(name="CO Test Branch " + tag)
    db.add(branch)
    await db.flush()
    user = User(
        full_name="CO Test Staff",
        role="super_admin",
        email=tag + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=None,
        is_active=True,
    )
    db.add(user)
    product = Product(
        sku="COT-" + tag,
        name="CO Test Item " + tag,
        needs_invoice=False,
        cost_price_kobo=100,
        selling_price_kobo=PRICE_KOBO,
    )
    db.add(product)
    await db.flush()
    await db.commit()
    token = create_access_token(str(user.id), user.role, None)
    return branch, user, product, {"Authorization": "Bearer " + token}


async def _make_booking(client, db, deposit_naira, method):
    """New order -> new shelf stock -> sync_arrivals -> returns the open booking."""
    branch, user, product, headers = await _setup(db)
    phone = "080" + "".join(random.choice("0123456789") for _ in range(8))
    body = {
        "branch_id": str(branch.id),
        "item_name": product.name,
        "product_id": str(product.id),
        "quantity": 1,
        "unit_label": "pieces",
        "customer_name": "CO Test Customer",
        "customer_phone": phone,
        "deposit_naira": deposit_naira,
        "customer_pieces": 1,
        "add_to_restock_list": False,
    }
    if deposit_naira > 0:
        body["deposit_method"] = method
    resp = await client.post(_orders_base(), json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    order_id = uuid.UUID(resp.json()["id"])
    assert resp.json()["status"] == "pending", resp.text

    # Stock that arrives AFTER the order (the arrival rule needs this).
    db.add(
        StockBatch(
            product_id=product.id,
            branch_id=branch.id,
            quantity_received=5,
            quantity_remaining=5,
            unit_cost_kobo=100,
            received_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
    )
    await db.commit()

    await sync_arrivals(db, branch.id)
    order = await _fresh(db, CustomerRequest, order_id)
    assert order.status == "arrived", "order status is " + str(order.status) + ", note: " + str(order.resolution_note)
    assert order.booking_id is not None, "no booking was made, note: " + str(order.resolution_note)
    _say("order", order_id, "booking", order.booking_id, "branch", branch.id, "product", product.id)
    return {
        "branch": branch,
        "user": user,
        "product": product,
        "headers": headers,
        "order_id": order_id,
        "booking_id": order.booking_id,
    }


async def test_a_arrival_creates_booking(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    sale = await _fresh(db_session, Sale, order.booking_id)
    paid = await _paid(db_session, sale.id)
    _say("sale status", sale.status, "total_kobo", sale.total_kobo, "paid_kobo", paid, "served_by", sale.served_by)
    assert sale.status == "booking"
    assert sale.total_kobo == PRICE_KOBO
    assert paid == 5000
    assert sale.served_by == c["user"].id
    assert order.hold_pieces == 0
    assert await _shelf(db_session, c["product"].id, c["branch"].id) == 5  # a booking does not take stock

    await sync_arrivals(db_session, c["branch"].id)  # second run must not make a second booking
    order2 = await _fresh(db_session, CustomerRequest, c["order_id"])
    count = await db_session.scalar(select(func.count()).select_from(Sale).where(Sale.branch_id == c["branch"].id))
    _say("sales in branch after second sync", count)
    assert order2.booking_id == order.booking_id
    assert count == 1
    print("PASS a: arrival makes exactly one booking")


async def test_b_complete_booking_collects_order(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/add-payment",
        json={"amount_naira": 150.0, "payment_method": "cash"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    sale = await _fresh(db_session, Sale, c["booking_id"])
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    shelf = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("sale", sale.status, "payment_method", sale.payment_method, "order", order.status, "collected_at", order.collected_at, "shelf", shelf)
    assert sale.status == "completed"
    assert order.status == "collected"
    assert order.collected_at is not None
    assert not order.deposit_refunded
    assert order.resolution_note is None
    assert order.hold_pieces == 0
    assert shelf == 4
    print("PASS b: completing the booking makes the order Collected")


async def test_c_cancel_cash_booking(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/cancel",
        json={"reason": "script test cancel", "refund_method": "cash"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    sale = await _fresh(db_session, Sale, c["booking_id"])
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    refunds = await _refunds(db_session, c["booking_id"])
    _say("sale", sale.status, "order", order.status, "deposit_refunded", order.deposit_refunded, "note", order.resolution_note)
    _say("refunds", [(r.action, r.status, r.amount_kobo) for r in refunds])
    assert sale.status == "cancelled"
    assert order.status == "cancelled"
    assert order.cancelled_at is not None
    assert order.deposit_refunded is True
    assert order.resolution_note is None
    assert len(refunds) == 1
    assert refunds[0].action == "cash" and refunds[0].status == "completed" and refunds[0].amount_kobo == 5000
    assert await _shelf(db_session, c["product"].id, c["branch"].id) == 5
    print("PASS c: cash cancel -> order Cancelled, deposit_refunded true, refund completed")


async def test_d_cancel_transfer_booking(client, db_session):
    c = await _make_booking(client, db_session, 50, "transfer")
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/cancel",
        json={
            "reason": "script test cancel",
            "refund_method": "transfer",
            "refund_bank_name": "Test Bank",
            "refund_account_number": "0123456789",
            "refund_account_name": "CO Test Customer",
        },
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    sale = await _fresh(db_session, Sale, c["booking_id"])
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    refunds = await _refunds(db_session, c["booking_id"])
    _say("sale", sale.status, "order", order.status, "deposit_refunded", order.deposit_refunded, "note", order.resolution_note)
    _say("refunds", [(r.action, r.status, r.amount_kobo) for r in refunds])
    assert sale.status == "cancelled"
    assert order.status == "cancelled"
    assert order.cancelled_at is not None
    assert not order.deposit_refunded
    assert order.resolution_note == "Refund pending (bank transfer)"
    assert len(refunds) == 1
    assert refunds[0].action == "transfer" and refunds[0].status == "pending" and refunds[0].amount_kobo == 5000
    print("PASS d: transfer cancel -> order Cancelled, note set, refund pending")


async def test_e_order_not_arrived_is_left_alone(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    order.status = "collected"  # something else already closed the order
    await db_session.commit()
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/cancel",
        json={"reason": "script test cancel", "refund_method": "cash"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    sale = await _fresh(db_session, Sale, c["booking_id"])
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    _say("sale", sale.status, "order", order.status, "cancelled_at", order.cancelled_at)
    assert sale.status == "cancelled"
    assert order.status == "collected"
    assert order.cancelled_at is None
    assert not order.deposit_refunded
    print("PASS e: an order that is not 'arrived' is not changed")


async def test_f_booking_without_order_still_completes(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    order.booking_id = None  # the booking is now a plain booking
    await db_session.commit()
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/add-payment",
        json={"amount_naira": 150.0, "payment_method": "cash"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    sale = await _fresh(db_session, Sale, c["booking_id"])
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    _say("sale", sale.status, "order", order.status)
    assert sale.status == "completed"
    assert order.status == "arrived"
    print("PASS f: a booking with no linked order completes without error")


async def test_g_zero_deposit_order_books_and_completes(client, db_session):
    c = await _make_booking(client, db_session, 0, None)
    sale = await _fresh(db_session, Sale, c["booking_id"])
    paid = await _paid(db_session, sale.id)
    _say("sale", sale.status, "total_kobo", sale.total_kobo, "paid_kobo", paid)
    assert sale.status == "booking"
    assert sale.total_kobo == PRICE_KOBO
    assert paid == 0
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/add-payment",
        json={"amount_naira": 200.0, "payment_method": "cash"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    sale = await _fresh(db_session, Sale, c["booking_id"])
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    shelf = await _shelf(db_session, c["product"].id, c["branch"].id)
    _say("sale", sale.status, "order", order.status, "shelf", shelf)
    assert sale.status == "completed"
    assert order.status == "collected"
    assert shelf == 4
    print("PASS g: deposit 0 still books and completes")
