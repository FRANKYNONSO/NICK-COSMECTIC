"""Script tests for Patch 11a: cancelling a website order really cancels it.
Run ONLY against shelf_test. Every test is rolled back.
PATCH11A-WEBSITE-CANCEL
"""
import os
import random
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _fresh, _say, _setup  # noqa: E402

from infrastructure.db.models import (  # noqa: E402
    AuditLog,
    Branch,
    Customer,
    Sale,
    StockBatch,
    StorefrontOrder,
)
from main import app  # noqa: E402

pytestmark = pytest.mark.asyncio

UNIT_KOBO = 1_000_000  # NGN 10,000 per piece, so points are clearly above zero


def _orders_url():
    paths = [p for p in app.openapi()["paths"].keys() if p.endswith("/storefront/orders")]
    assert len(paths) == 1, paths
    return paths[0]


async def _prepare(db, branch_stock):
    """One shelf batch per entry of branch_stock, each in its own branch."""
    branch0, user, product, headers = await _setup(db)
    product_id = product.id
    product.selling_price_kobo = UNIT_KOBO
    product.status = "active"
    branches = [branch0]
    for _ in range(len(branch_stock) - 1):
        extra = Branch(name="P11A Extra Branch " + uuid.uuid4().hex[:8])
        db.add(extra)
        branches.append(extra)
    await db.flush()
    batches = []
    for branch, qty in zip(branches, branch_stock):
        batch = StockBatch(
            product_id=product_id,
            branch_id=branch.id,
            quantity_received=qty,
            quantity_remaining=qty,
            unit_cost_kobo=100,
        )
        db.add(batch)
        batches.append(batch)
    await db.flush()
    batch_ids = [b.id for b in batches]
    await db.commit()
    return product_id, headers, batch_ids


async def _place(client, headers, product_id, qty):
    phone = "081" + "".join(random.choice("0123456789") for _ in range(8))
    body = {
        "reference": "P11A-" + uuid.uuid4().hex,
        "customer": {"full_name": "P11A Customer", "phone": phone},
        "delivery": {"address_line": "12 Test Street", "state": "Taraba"},
        "items": [{"product_id": str(product_id), "quantity": qty}],
    }
    resp = await client.post(_orders_url(), json=body, headers=headers)
    _say("place", resp.status_code, resp.text[:300])
    assert resp.status_code == 201, resp.text
    return resp.json(), phone


async def _patch(client, headers, order_id, payload):
    resp = await client.patch(_orders_url() + "/" + order_id, json=payload, headers=headers)
    _say("patch", payload, resp.status_code, resp.text[:300])
    return resp


async def _remaining(db, batch_id):
    return (await _fresh(db, StockBatch, batch_id)).quantity_remaining


async def _points(db, phone):
    result = await db.execute(
        select(Customer).where(Customer.phone == phone).execution_options(populate_existing=True)
    )
    return result.scalar_one().loyalty_points


async def _sale_statuses(db, order):
    out = []
    for s in order["sales"]:
        row = await _fresh(db, Sale, uuid.UUID(s["sale_id"]))
        out.append(row.status)
    return out


async def _order_status(db, order_id):
    return (await _fresh(db, StorefrontOrder, uuid.UUID(order_id))).status


async def _cancel_events(db, order_id):
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.entity_id == uuid.UUID(order_id), AuditLog.action == "website_order_cancelled"
        )
    )
    return list(result.scalars().all())


async def test_a_cancel_new_order(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    assert await _remaining(db_session, batch_ids[0]) == 7
    points_before = await _points(db_session, phone)
    assert points_before > 0, points_before
    assert await _sale_statuses(db_session, order) == ["completed"]
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert [s["status"] for s in resp.json()["sales"]] == ["cancelled"]
    assert await _sale_statuses(db_session, order) == ["cancelled"]
    assert await _remaining(db_session, batch_ids[0]) == 10, "stock was not returned"
    assert await _points(db_session, phone) == 0, "loyalty points were not taken back"
    assert len(await _cancel_events(db_session, order["id"])) == 1
    print("PASS a: cancelling a New order cancels the sale, returns stock, takes back points, logs once")


async def test_b_cancel_processing_order(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 4)
    resp = await _patch(client, headers, order["id"], {"status": "processing"})
    assert resp.status_code == 200, resp.text
    assert await _remaining(db_session, batch_ids[0]) == 6
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 200, resp.text
    assert await _sale_statuses(db_session, order) == ["cancelled"]
    assert await _remaining(db_session, batch_ids[0]) == 10
    assert await _points(db_session, phone) == 0
    print("PASS b: cancelling a Processing order works the same way")


async def test_c_cancel_two_branch_order(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [5, 3])
    order, phone = await _place(client, headers, product_id, 7)
    assert len(order["sales"]) == 2, len(order["sales"])
    assert await _remaining(db_session, batch_ids[0]) + await _remaining(db_session, batch_ids[1]) == 1
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 200, resp.text
    assert await _sale_statuses(db_session, order) == ["cancelled", "cancelled"]
    assert await _remaining(db_session, batch_ids[0]) == 5
    assert await _remaining(db_session, batch_ids[1]) == 3
    assert await _points(db_session, phone) == 0
    assert len(await _cancel_events(db_session, order["id"])) == 1
    print("PASS c: a two-branch order cancels both sales and restores both batches")


async def test_d_cancel_twice_returns_stock_once(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    first = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert first.status_code == 200, first.text
    assert await _remaining(db_session, batch_ids[0]) == 10
    second = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert second.status_code == 200, second.text
    assert await _remaining(db_session, batch_ids[0]) == 10, "stock was returned twice"
    assert len(await _cancel_events(db_session, order["id"])) == 1
    print("PASS d: a second cancel returns nothing and logs nothing")


async def test_e_delivered_cannot_be_cancelled(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    resp = await _patch(client, headers, order["id"], {"status": "delivered"})
    assert resp.status_code == 200, resp.text
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 409, resp.text
    assert "delivered order cannot be cancelled" in resp.json()["detail"]
    assert await _order_status(db_session, order["id"]) == "delivered"
    assert await _sale_statuses(db_session, order) == ["completed"]
    assert await _remaining(db_session, batch_ids[0]) == 7
    assert await _cancel_events(db_session, order["id"]) == []
    print("PASS e: a delivered order cannot be cancelled and nothing changes")


async def test_f_cancelled_cannot_change_back(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 200, resp.text
    resp = await _patch(client, headers, order["id"], {"status": "processing"})
    assert resp.status_code == 409, resp.text
    assert "cannot be changed back" in resp.json()["detail"]
    assert await _order_status(db_session, order["id"]) == "cancelled"
    assert await _remaining(db_session, batch_ids[0]) == 10
    print("PASS f: a cancelled order cannot be changed back")


async def test_g_refunded_sale_blocks_cancel(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    points_before = await _points(db_session, phone)
    row = await _fresh(db_session, Sale, uuid.UUID(order["sales"][0]["sale_id"]))
    row.status = "refunded"  # simulated: the refund route itself is not run here
    await db_session.flush()
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 409, resp.text
    assert "refund was already made" in resp.json()["detail"]
    assert await _order_status(db_session, order["id"]) == "new"
    assert await _remaining(db_session, batch_ids[0]) == 7
    assert await _points(db_session, phone) == points_before
    assert await _cancel_events(db_session, order["id"]) == []
    print("PASS g: a refunded sale blocks the cancel and nothing changes")


async def test_h_normal_steps_change_nothing_else(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    points_before = await _points(db_session, phone)
    for payload in (
        {"status": "processing"},
        {"status": "waybilled", "waybill_ref": "WB-P11A"},
        {"status": "delivered"},
    ):
        resp = await _patch(client, headers, order["id"], payload)
        assert resp.status_code == 200, resp.text
    assert await _sale_statuses(db_session, order) == ["completed"]
    assert await _remaining(db_session, batch_ids[0]) == 7
    assert await _points(db_session, phone) == points_before
    assert await _cancel_events(db_session, order["id"]) == []
    print("PASS h: normal status steps leave the sale, stock and points alone")
