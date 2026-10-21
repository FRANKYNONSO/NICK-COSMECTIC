"""POS sale consistency tests (fix23) -- Test H.

Run against the restored shelf_test database. Every test is rolled back by the
db_session fixture and uses its own branch and product.
"""
import uuid

import pytest
from sqlalchemy import func, select

from infrastructure.db.models import (
    Sale,
    SaleItem,
    SalePayment,
    StockBatch,
    StockTransfer,
    WarehouseStock,
)

try:
    from tests.test_goods_receiving import _figures, _receive, _setup, _summary
except ImportError:
    from test_goods_receiving import _figures, _receive, _setup, _summary

pytestmark = pytest.mark.asyncio


def _rid(body):
    rid = body.get("id")
    if rid is None and isinstance(body.get("receipt"), dict):
        rid = body["receipt"].get("id")
    assert rid, body
    return rid


async def _open_shift(client, headers, branch_id):
    resp = await client.post(
        "/api/v1/shifts",
        json={"branch_id": str(branch_id), "opening_cash_naira": 0},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _checkout(client, headers, branch_id, shift_id, product_id, qty, price=10.0, ref=None):
    total = qty * price
    body = {
        "branch_id": str(branch_id),
        "channel": "pos",
        "local_ref_id": ref or f"h-{uuid.uuid4().hex}",
        "items": [{"product_id": str(product_id), "quantity": qty, "unit_price_naira": price}],
        "payments": [{"method": "cash", "amount_naira": total}],
        "shift_id": str(shift_id),
    }
    return await client.post("/api/v1/sales/checkout", json=body, headers=headers)


async def _sale_counts(db, branch_id):
    sales = await db.scalar(select(func.count()).select_from(Sale).where(Sale.branch_id == branch_id))
    items = await db.scalar(
        select(func.count())
        .select_from(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.branch_id == branch_id)
    )
    pays = await db.scalar(
        select(func.count())
        .select_from(SalePayment)
        .join(Sale, Sale.id == SalePayment.sale_id)
        .where(Sale.branch_id == branch_id)
    )
    return sales, items, pays


async def _transfer_count(db, receipt_id):
    return await db.scalar(
        select(func.count()).select_from(StockTransfer).where(StockTransfer.goods_receipt_id == uuid.UUID(receipt_id))
    )


async def _warehouse_pieces(db, branch_id, product_id):
    return await db.scalar(
        select(WarehouseStock.quantity_pieces).where(
            WarehouseStock.branch_id == branch_id, WarehouseStock.product_id == product_id
        )
    )


async def _batch_sums(db, branch_id, product_id):
    row = (
        await db.execute(
            select(func.sum(StockBatch.quantity_received), func.sum(StockBatch.quantity_remaining)).where(
                StockBatch.branch_id == branch_id, StockBatch.product_id == product_id
            )
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


# ---------------------------------------------------------------- Test H1
async def test_h1_pos_sale_reduces_shelf_only_and_records_consistently(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text
    rid = _rid(resp.json())

    # Receiving must not create any sale, item or payment.
    assert await _sale_counts(db_session, branch.id) == (0, 0, 0)
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)
    transfers_before = await _transfer_count(db_session, rid)
    assert transfers_before == 2

    shift_id = await _open_shift(client, h, branch.id)
    sale = await _checkout(client, h, branch.id, shift_id, product.id, 3)
    assert sale.status_code == 201, sale.text
    sale_body = sale.json()
    assert sale_body["status"] == "completed"
    assert sale_body["total_naira"] == 30.0
    assert len(sale_body["items"]) == 1
    assert sale_body["items"][0]["product_id"] == str(product.id)
    assert sale_body["items"][0]["quantity"] == 3

    # Shelf down by exactly 3, warehouse untouched, total = warehouse + shelf.
    summary = await _summary(client, h, branch.id, product.id)
    assert _figures(summary) == (97, 80, 17)
    assert summary["total_stock"] == summary["warehouse_stock"] + summary["shelf_stock"]
    assert await _warehouse_pieces(db_session, branch.id, product.id) == 80
    received, remaining = await _batch_sums(db_session, branch.id, product.id)
    assert (received, remaining) == (20, 17)

    # Exactly one sale, one item, one payment (3000 kobo).
    assert await _sale_counts(db_session, branch.id) == (1, 1, 1)
    paid = await db_session.scalar(
        select(func.sum(SalePayment.amount_kobo))
        .join(Sale, Sale.id == SalePayment.sale_id)
        .where(Sale.branch_id == branch.id)
    )
    assert int(paid) == 3000

    # The sale did not add or change any receipt movement.
    assert await _transfer_count(db_session, rid) == transfers_before

    # The sale used the receipt's batch.
    batch_id = await db_session.scalar(
        select(StockBatch.id).where(StockBatch.branch_id == branch.id, StockBatch.product_id == product.id)
    )
    assert sale_body["items"][0]["stock_batch_id"] == str(batch_id)

    # Transaction history and receipt view agree with the checkout response.
    listing = await client.get("/api/v1/sales", params={"branch_id": str(branch.id)}, headers=h)
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["id"] == sale_body["id"]
    assert rows[0]["total_naira"] == 30.0
    assert rows[0]["items"][0]["quantity"] == 3

    single = await client.get(f"/api/v1/sales/{sale_body['id']}", headers=h)
    assert single.status_code == 200, single.text
    one = single.json()
    assert one["id"] == sale_body["id"]
    assert one["total_naira"] == 30.0
    assert one["paid_naira"] == 30.0
    assert one["balance_naira"] == 0
    assert one["items"][0]["product_id"] == str(product.id)
    assert one["items"][0]["quantity"] == 3


# ---------------------------------------------------------------- Test H2
async def test_h2_retried_checkout_reduces_stock_once(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text
    shift_id = await _open_shift(client, h, branch.id)

    ref = f"h2-{uuid.uuid4().hex}"
    first = await _checkout(client, h, branch.id, shift_id, product.id, 3, ref=ref)
    assert first.status_code == 201, first.text
    second = await _checkout(client, h, branch.id, shift_id, product.id, 3, ref=ref)
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]

    assert _figures(await _summary(client, h, branch.id, product.id)) == (97, 80, 17)
    assert await _sale_counts(db_session, branch.id) == (1, 1, 1)


# ---------------------------------------------------------------- Test H3
async def test_h3_pos_cannot_sell_warehouse_units(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text
    shift_id = await _open_shift(client, h, branch.id)

    sale = await _checkout(client, h, branch.id, shift_id, product.id, 21)
    assert sale.status_code == 400, sale.text

    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)
    assert await _sale_counts(db_session, branch.id) == (0, 0, 0)


# ---------------------------------------------------------------- Test H4
async def test_h4_open_booking_hold_does_not_change_stock_summary(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text

    booking = Sale(
        branch_id=branch.id,
        served_by=admin.id,
        channel="pos",
        status="booking",
        subtotal_kobo=5000,
        total_kobo=5000,
        local_ref_id=f"h4-booking-{uuid.uuid4().hex}",
    )
    db_session.add(booking)
    await db_session.flush()
    db_session.add(
        SaleItem(sale_id=booking.id, product_id=product.id, quantity=5, unit_price_kobo=1000, line_total_kobo=5000)
    )
    await db_session.commit()

    # A hold reserves but does not deduct.
    summary = await _summary(client, h, branch.id, product.id)
    assert _figures(summary) == (100, 80, 20)

    holds = await client.get(
        "/api/v1/sales/bookings/holds",
        params={"branch_id": str(branch.id), "product_id": [str(product.id)]},
        headers=h,
    )
    assert holds.status_code == 200, holds.text
    assert holds.json()[str(product.id)]["reserved_quantity"] == 5

    shift_id = await _open_shift(client, h, branch.id)

    blocked = await _checkout(client, h, branch.id, shift_id, product.id, 16)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "STOCK_ON_OPEN_BOOKING"
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)

    allowed = await _checkout(client, h, branch.id, shift_id, product.id, 15)
    assert allowed.status_code == 201, allowed.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (85, 80, 5)