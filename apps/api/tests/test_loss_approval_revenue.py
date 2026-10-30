"""Losses and revenue: a loss only touches revenue once it is APPROVED.

  recorded (pending_review) -> revenue unchanged
  approved                  -> revenue drops by the loss amount, exactly once,
                               on the day it was approved
  rejected                  -> revenue unchanged, stock goes back

Revenue is read from /reports/sales-summary, the same endpoint behind the
dashboard's Total revenue and Today's revenue cards. Each test creates its own
branch with no sales, so the grand total is simply minus the approved losses.

Run against shelf_test (see conftest.py):
    pytest apps/api/tests/test_loss_approval_revenue.py -x
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from infrastructure.db.models import StockAdjustment
from tests.test_goods_receiving import _figures, _receive, _setup, _summary

pytestmark = pytest.mark.asyncio

INV = "/api/v1/inventory"
REPORTS = "/api/v1/reports"

UNIT_PRICE_KOBO = 5000          # NGN 50 per piece
LOSS_QTY = 2                    # so one loss is worth NGN 100
LOSS_NAIRA = 100.0


async def _stocked(db_session, client):
    """Branch + super admin + a product with 20 pieces on the shelf and a
    selling price (the loss value is taken from the selling price)."""
    branch, _admin, product, headers = await _setup(db_session)
    resp = await _receive(client, headers, branch.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text
    product.selling_price_kobo = UNIT_PRICE_KOBO
    await db_session.commit()
    return branch, product, headers


async def _record_loss(client, headers, branch, product, qty=LOSS_QTY):
    return await client.post(
        f"{INV}/stock-adjustments",
        json={
            "product_id": str(product.id),
            "branch_id": str(branch.id),
            "location": "shelf",
            "quantity_delta": -qty,
            "reason": "damaged",
            "damage_caused_by": "customer",
            "notes": "dropped at the till",
            "image_url": "https://example.com/evidence.jpg",
        },
        headers=headers,
    )


async def _the_adjustment(db_session, branch):
    rows = (
        (await db_session.execute(select(StockAdjustment).where(StockAdjustment.branch_id == branch.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"expected exactly one loss row, found {len(rows)}"
    return rows[0]


async def _revenue(client, headers, branch, date_from=None, date_to=None):
    params = {"branch_id": str(branch.id)}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    resp = await client.get(f"{REPORTS}/sales-summary", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["grand_total_naira"]


async def _losses_total(client, headers, branch, start_date=None):
    params = {"branch_id": str(branch.id)}
    if start_date:
        params["start_date"] = start_date.isoformat()
    resp = await client.get(f"{INV}/losses/summary", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["total_value_naira"]


# ----------------------------------------------------------------- Test 1
async def test_recording_a_loss_does_not_change_revenue(client, db_session):
    branch, product, h = await _stocked(db_session, client)
    assert await _revenue(client, h, branch) == 0

    resp = await _record_loss(client, h, branch, product)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending_review"

    adj = await _the_adjustment(db_session, branch)
    assert adj.status == "pending_review"
    assert adj.value_kobo == LOSS_QTY * UNIT_PRICE_KOBO  # value is captured, but not yet counted

    # Total revenue and today's revenue are both untouched while it is pending.
    assert await _revenue(client, h, branch) == 0
    assert await _revenue(client, h, branch, date_from=date.today() - timedelta(days=1)) == 0
    assert await _losses_total(client, h, branch) == 0


# ----------------------------------------------------------------- Test 2
async def test_approving_a_loss_deducts_exactly_once(client, db_session):
    branch, product, h = await _stocked(db_session, client)
    assert (await _record_loss(client, h, branch, product)).status_code == 201
    adj = await _the_adjustment(db_session, branch)
    assert await _revenue(client, h, branch) == 0

    resp = await client.post(f"{INV}/stock-adjustments/{adj.id}/approve", headers=h)
    assert resp.status_code == 200, resp.text
    await db_session.refresh(adj)
    assert adj.status == "completed"
    assert adj.reviewed_at is not None

    # Deducted once: minus NGN 100, not minus 200, and today's figure agrees.
    assert await _revenue(client, h, branch) == pytest.approx(-LOSS_NAIRA)
    assert await _revenue(client, h, branch, date_from=date.today() - timedelta(days=1)) == pytest.approx(-LOSS_NAIRA)
    assert await _losses_total(client, h, branch) == pytest.approx(LOSS_NAIRA)

    # Stock stays deducted after approval (20 -> 18 on the shelf).
    assert _figures(await _summary(client, h, branch.id, product.id)) == (98, 80, 18)


# ----------------------------------------------------------------- Test 3
async def test_rejecting_a_loss_leaves_revenue_unchanged(client, db_session):
    branch, product, h = await _stocked(db_session, client)
    assert (await _record_loss(client, h, branch, product)).status_code == 201
    adj = await _the_adjustment(db_session, branch)
    assert _figures(await _summary(client, h, branch.id, product.id)) == (98, 80, 18)

    resp = await client.post(
        f"{INV}/stock-adjustments/{adj.id}/reject",
        params={"reason": "it was not damaged"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    await db_session.refresh(adj)
    assert adj.status == "rejected"

    # Revenue is exactly where it was, and the stock went back on the shelf.
    assert await _revenue(client, h, branch) == 0
    assert await _revenue(client, h, branch, date_from=date.today() - timedelta(days=1)) == 0
    assert await _losses_total(client, h, branch) == 0
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)


# ----------------------------------------------------------------- Test 4
async def test_approved_loss_counts_on_approval_day_not_recorded_day(client, db_session):
    branch, product, h = await _stocked(db_session, client)
    assert (await _record_loss(client, h, branch, product)).status_code == 201
    adj = await _the_adjustment(db_session, branch)

    # Pretend it was recorded 5 days ago, then approve it now.
    adj.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    await db_session.commit()
    resp = await client.post(f"{INV}/stock-adjustments/{adj.id}/approve", headers=h)
    assert resp.status_code == 200, resp.text

    today = date.today()
    # It counts in the window that contains the approval day...
    assert await _revenue(client, h, branch, date_from=today - timedelta(days=1)) == pytest.approx(-LOSS_NAIRA)
    assert await _losses_total(client, h, branch, start_date=today - timedelta(days=1)) == pytest.approx(LOSS_NAIRA)
    # ...and NOT in the older window that contains only the day it was recorded.
    assert await _revenue(client, h, branch, date_from=today - timedelta(days=10), date_to=today - timedelta(days=3)) == 0
