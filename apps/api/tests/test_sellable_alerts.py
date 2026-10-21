"""Sellable-shelf alerts and manager override tests (fix29)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from infrastructure.db.models import AuditLog, StockBatch

try:
    from tests.test_goods_receiving import BASE, _figures, _product, _receive, _set_limit, _setup, _summary
except ImportError:
    from test_goods_receiving import BASE, _figures, _product, _receive, _set_limit, _setup, _summary

pytestmark = pytest.mark.asyncio

EXPIRED_LABEL = "EXPIRED UNITS ON SHELF - REMOVE"


async def _shelf_batch(db, branch, product, qty, expired):
    expiry = date.today() - timedelta(days=1) if expired else None
    db.add(
        StockBatch(
            product_id=product.id,
            branch_id=branch.id,
            batch_number=f"t-{uuid.uuid4().hex[:6]}",
            quantity_received=qty,
            quantity_remaining=qty,
            unit_cost_kobo=100,
            expiry_date=expiry,
            received_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
    )
    await db.commit()


# ---------------------------------------------------------------- S1
async def test_s1_expired_only_shelf_with_warehouse_stock_is_shelf_empty(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 50, 0)
    assert resp.status_code == 201, resp.text
    await _shelf_batch(db_session, branch, product, 10, expired=True)

    s = await _summary(client, h, branch.id, product.id)
    assert _figures(s) == (60, 50, 10)
    assert s["shelf_sellable_stock"] == 0
    assert s["shelf_expired_stock"] == 10
    assert s["total_sellable_stock"] == 50
    assert s["stock_state"] == "shelf_empty"
    assert s["has_expired_shelf_stock"] is True
    assert s["expired_label"] == EXPIRED_LABEL


# ---------------------------------------------------------------- S2
async def test_s2_expired_units_do_not_hide_a_low_sellable_shelf(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 50, 0)
    assert resp.status_code == 201, resp.text
    await _shelf_batch(db_session, branch, product, 4, expired=True)
    await _shelf_batch(db_session, branch, product, 3, expired=False)

    s = await _summary(client, h, branch.id, product.id)
    # Physical shelf is 7 (above the default alert level 5) but only 3 are sellable.
    assert _figures(s) == (57, 50, 7)
    assert s["shelf_sellable_stock"] == 3
    assert s["shelf_expired_stock"] == 4
    assert s["stock_state"] == "low_shelf"
    assert s["alert_label"] == "LOW SHELF STOCK - REPLENISH FROM WAREHOUSE"
    assert s["expired_label"] == EXPIRED_LABEL


# ---------------------------------------------------------------- S3
async def test_s3_only_expired_stock_anywhere_is_out_of_stock_and_flagged(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    await _shelf_batch(db_session, branch, product, 10, expired=True)

    s = await _summary(client, h, branch.id, product.id)
    assert s["total_stock"] == 10
    assert s["total_sellable_stock"] == 0
    assert s["stock_state"] == "out_of_stock"
    assert s["has_expired_shelf_stock"] is True


# ---------------------------------------------------------------- S4
async def test_s4_alerts_only_lists_expired_product_but_not_a_healthy_one(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    healthy = await _product(db_session)
    await db_session.commit()
    resp = await _receive(client, h, branch.id, product.id, 50, 0)
    assert resp.status_code == 201, resp.text
    await _shelf_batch(db_session, branch, product, 10, expired=True)
    resp = await _receive(client, h, branch.id, healthy.id, 100, 20)
    assert resp.status_code == 201, resp.text

    listing = await client.get(
        f"{BASE}/stock-summary", params={"branch_id": str(branch.id), "alerts_only": "true"}, headers=h
    )
    assert listing.status_code == 200, listing.text
    ids = {row["product_id"] for row in listing.json()}
    assert str(product.id) in ids
    assert str(healthy.id) not in ids


# ---------------------------------------------------------------- E1
async def test_e1_branch_manager_can_override_shelf_limit_with_reason_and_audit(client, db_session):
    branch, manager, product, h = await _setup(db_session, role="branch_manager")
    await _set_limit(client, h, branch.id, product.id, 24, 5)
    first = await _receive(client, h, branch.id, product.id, 100, 20)
    assert first.status_code == 201, first.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)

    blocked = await _receive(client, h, branch.id, product.id, 10, 10)
    assert blocked.status_code == 400, blocked.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)

    no_reason = await _receive(client, h, branch.id, product.id, 10, 10, override_shelf_limit=True)
    assert no_reason.status_code == 400, no_reason.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)

    ok = await _receive(
        client, h, branch.id, product.id, 10, 10,
        override_shelf_limit=True, override_reason="Display promotion, manager approved",
    )
    assert ok.status_code == 201, ok.text
    s = await _summary(client, h, branch.id, product.id)
    assert _figures(s) == (110, 80, 30)
    assert s["over_limit"] is True
    assert s["available_shelf_capacity"] == 0

    audits = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "shelf_limit_override")
    )
    assert audits == 1