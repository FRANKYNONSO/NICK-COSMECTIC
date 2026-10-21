"""Late-receipt reconciliation tests (fix25) -- Test L.

Run against shelf_test after migration recon1late01. Each test is rolled back.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from infrastructure.db.models import AuditLog, StockBatch

try:
    from tests.test_goods_receiving import BASE, _figures, _receive, _setup, _summary
    from tests.test_pos_sale_consistency import _checkout, _open_shift, _rid, _sale_counts
except ImportError:
    from test_goods_receiving import BASE, _figures, _receive, _setup, _summary
    from test_pos_sale_consistency import _checkout, _open_shift, _rid, _sale_counts

pytestmark = pytest.mark.asyncio


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


async def _post(client, headers, rid, **opts):
    return await client.post(f"{BASE}/receipts/{rid}/post", json=opts, headers=headers)


async def _get(client, headers, rid):
    resp = await client.get(f"{BASE}/receipts/{rid}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _late_draft(client, headers, branch, product, received, shelf):
    resp = await _receive(
        client, headers, branch.id, product.id, received, shelf, post=False,
        delivered_at=_ago(5), reconciliation_notes="Checked shelf count and sales since delivery",
    )
    assert resp.status_code == 201, resp.text
    return _rid(resp.json())


async def _blocked_setup(client, headers, branch, product):
    """Normal receipt (30, 10 on shelf), sell 4 at the POS, then a LATE draft
    for the same product that overlaps the stock already recorded."""
    first = await _receive(client, headers, branch.id, product.id, 30, 10)
    assert first.status_code == 201, first.text
    first_body = await _get(client, headers, _rid(first.json()))
    assert first_body["reconciliation_status"] == "not_required"
    shift_id = await _open_shift(client, headers, branch.id)
    sale = await _checkout(client, headers, branch.id, shift_id, product.id, 4)
    assert sale.status_code == 201, sale.text
    assert _figures(await _summary(client, headers, branch.id, product.id)) == (26, 20, 6)
    return await _late_draft(client, headers, branch, product, 50, 10)


# ---------------------------------------------------------------- Test L1
async def test_l1_overlapping_late_receipt_is_blocked_with_report(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    rid = await _blocked_setup(client, h, branch, product)

    resp = await _post(client, h, rid, reconciliation_confirmed=True)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "RECEIPT_RECONCILIATION_DISCREPANCY"
    line = detail["discrepancies"][0]
    assert line["product_id"] == str(product.id)
    assert line["sales_since_delivery"] == 4
    assert line["stock_entered_since_delivery"]["shelf_batches"] == 10
    assert line["stock_entered_since_delivery"]["warehouse_pieces"] == 20
    assert "STOCK_ENTERED_SINCE_DELIVERY" in line["flags"]

    # Nothing was created: stock unchanged, receipt still a draft.
    assert _figures(await _summary(client, h, branch.id, product.id)) == (26, 20, 6)
    assert (await _get(client, h, rid))["status"] == "draft"


# ---------------------------------------------------------------- Test L2
async def test_l2_manager_confirmation_needs_reason_posts_once_and_is_audited(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    rid = await _blocked_setup(client, h, branch, product)

    no_reason = await _post(client, h, rid, reconciliation_confirmed=True, confirm_discrepancy=True)
    assert no_reason.status_code == 400, no_reason.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (26, 20, 6)

    ok = await _post(
        client, h, rid, reconciliation_confirmed=True, confirm_discrepancy=True,
        discrepancy_reason="Counted the shelf; this is a separate second delivery",
    )
    assert ok.status_code == 200, ok.text
    body = await _get(client, h, rid)
    assert body["status"] == "posted"
    assert body["reconciliation_status"] == "discrepancy_confirmed"
    assert body["stock_count_required"] is True
    assert body["reconciliation_flags"]["reason"].startswith("Counted the shelf")
    assert _figures(await _summary(client, h, branch.id, product.id)) == (76, 60, 16)

    audits = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "goods_receipt_discrepancy_confirmed")
    )
    assert audits == 1

    again = await _post(
        client, h, rid, reconciliation_confirmed=True, confirm_discrepancy=True,
        discrepancy_reason="Counted the shelf; this is a separate second delivery",
    )
    assert again.status_code == 200, again.text
    assert again.json()["already_posted"] is True
    assert _figures(await _summary(client, h, branch.id, product.id)) == (76, 60, 16)


# ---------------------------------------------------------------- Test L3
async def test_l3_late_receipt_with_sales_but_no_overlap_posts_once(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    # Older stock already on the shelf from two days ago (before the delivery).
    db_session.add(
        StockBatch(
            product_id=product.id,
            branch_id=branch.id,
            batch_number="old-stock",
            quantity_received=10,
            quantity_remaining=10,
            unit_cost_kobo=100,
            received_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
    )
    await db_session.commit()

    shift_id = await _open_shift(client, h, branch.id)
    sale = await _checkout(client, h, branch.id, shift_id, product.id, 4)
    assert sale.status_code == 201, sale.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (6, 0, 6)

    rid = await _late_draft(client, h, branch, product, 20, 10)
    resp = await _post(client, h, rid, reconciliation_confirmed=True)
    assert resp.status_code == 200, resp.text

    body = await _get(client, h, rid)
    assert body["status"] == "posted"
    assert body["is_late_entry"] is True
    assert body["reconciliation_status"] == "clear"
    assert body["stock_count_required"] is False
    # Added exactly once on top of what was left after the sale; the sale is untouched.
    assert _figures(await _summary(client, h, branch.id, product.id)) == (26, 10, 16)
    assert await _sale_counts(db_session, branch.id) == (1, 1, 1)

    again = await _post(client, h, rid, reconciliation_confirmed=True)
    assert again.status_code == 200, again.text
    assert again.json()["already_posted"] is True
    assert _figures(await _summary(client, h, branch.id, product.id)) == (26, 10, 16)