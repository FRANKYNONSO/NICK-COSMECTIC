"""Goods receiving tests (fix16).

Run against the restored shelf_test database. Every test is rolled back by the
db_session fixture, and each test creates its own branch and product and only
looks at those, so real rows never affect a result.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from infrastructure.db.models import (
    Branch,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceLineItem,
    StockTransfer,
    User,
)
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/goods-receiving"


async def _branch(db):
    branch = Branch(name=f"GR Branch {uuid.uuid4().hex[:6]}")
    db.add(branch)
    await db.flush()
    return branch


async def _user(db, role, branch_id=None):
    user = User(
        full_name="GR Test User",
        role=role,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=branch_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


def _headers(user):
    token = create_access_token(str(user.id), user.role, str(user.branch_id) if user.branch_id else None)
    return {"Authorization": f"Bearer {token}"}


async def _product(db):
    tag = uuid.uuid4().hex[:8]
    product = Product(sku=f"GRT-{tag}", name=f"GR Test Product {tag}", needs_invoice=False, cost_price_kobo=100)
    db.add(product)
    await db.flush()
    return product


async def _invoice(db, branch, user, product, ordered=100, per_piece=150):
    invoice = PurchaseInvoice(
        branch_id=branch.id,
        supplier_name="GR Test Supplier",
        invoice_date=date.today(),
        created_by=user.id,
    )
    db.add(invoice)
    await db.flush()
    line = PurchaseInvoiceLineItem(
        invoice_id=invoice.id,
        position=0,
        item_name=product.name,
        product_id=product.id,
        quantity=ordered,
        unit_label="pieces",
        rate_kobo=per_piece,
        line_total_kobo=ordered * per_piece,
        total_pieces=ordered,
        cost_price_per_piece_kobo=per_piece,
    )
    db.add(line)
    await db.flush()
    return invoice, line


async def _setup(db, role="super_admin"):
    branch = await _branch(db)
    admin = await _user(db, role, None)
    product = await _product(db)
    await db.commit()
    return branch, admin, product, _headers(admin)


async def _receive(client, headers, branch_id, product_id, received, shelf, post=True, key=None, line_extra=None, **extra):
    line = {"product_id": str(product_id), "quantity_received": received, "quantity_to_shelf": shelf}
    if line_extra:
        line.update(line_extra)
    body = {"branch_id": str(branch_id), "lines": [line], "post_now": post}
    if key:
        body["idempotency_key"] = key
    body.update(extra)
    return await client.post(f"{BASE}/receipts", json=body, headers=headers)


async def _summary(client, headers, branch_id, product_id):
    resp = await client.get(
        f"{BASE}/stock-summary",
        params={"branch_id": str(branch_id), "product_id": str(product_id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1, rows
    return rows[0]


def _figures(summary):
    return (summary["total_stock"], summary["warehouse_stock"], summary["shelf_stock"])


async def _set_limit(client, headers, branch_id, product_id, max_q, alert):
    resp = await client.put(
        f"{BASE}/shelf-settings",
        json={
            "branch_id": str(branch_id),
            "product_id": str(product_id),
            "max_shelf_quantity": max_q,
            "replenish_alert_level": alert,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _transfer_count(db, receipt_id):
    return await db.scalar(
        select(func.count()).select_from(StockTransfer).where(StockTransfer.goods_receipt_id == uuid.UUID(receipt_id))
    )


# ---------------------------------------------------------------- Test A
async def test_a_normal_receiving_100_with_20_on_shelf(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "posted"
    summary = await _summary(client, h, branch.id, product.id)
    assert _figures(summary) == (100, 80, 20)
    rows = (
        await db_session.execute(
            select(StockTransfer.transfer_type, StockTransfer.quantity_pieces).where(
                StockTransfer.goods_receipt_id == uuid.UUID(body["id"])
            )
        )
    ).all()
    assert {(a, b) for a, b in rows} == {("receipt_to_shelf", 20), ("receipt_to_warehouse", 80)}


async def test_draft_receipt_does_not_touch_stock(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20, post=False)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "draft"
    summary = await _summary(client, h, branch.id, product.id)
    assert _figures(summary) == (0, 0, 0)


async def test_shelf_quantity_cannot_exceed_received(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 10, 11, post=False)
    assert resp.status_code == 400
    assert "cannot exceed" in resp.json()["detail"]


# ---------------------------------------------------------------- Test B (receiving path)
async def test_b_shelf_limit_enforced_on_receiving(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    first = await _receive(client, h, branch.id, product.id, 20, 20)
    assert first.status_code == 201, first.text
    summary = await _set_limit(client, h, branch.id, product.id, 24, 5)
    assert summary["available_shelf_capacity"] == 4

    too_many = await _receive(client, h, branch.id, product.id, 5, 5)
    assert too_many.status_code == 400, too_many.text
    assert "only 4 unit(s)" in too_many.json()["detail"]
    assert _figures(await _summary(client, h, branch.id, product.id)) == (20, 0, 20)

    ok = await _receive(client, h, branch.id, product.id, 9, 4)
    assert ok.status_code == 201, ok.text
    after = await _summary(client, h, branch.id, product.id)
    assert _figures(after) == (29, 5, 24)
    assert after["available_shelf_capacity"] == 0

    full = await _receive(client, h, branch.id, product.id, 1, 1)
    assert full.status_code == 400
    assert "limit" in full.json()["detail"]


async def test_no_limit_row_means_no_enforcement(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 50, 50)
    assert resp.status_code == 201, resp.text
    summary = await _summary(client, h, branch.id, product.id)
    assert summary["limits_configured"] is False
    assert summary["shelf_stock"] == 50


# ---------------------------------------------------------------- Test C (derived alert)
async def test_c_replenishment_alert_appears_and_clears(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    await _set_limit(client, h, branch.id, product.id, 24, 5)
    resp = await _receive(client, h, branch.id, product.id, 100, 5)
    assert resp.status_code == 201, resp.text

    low = await _summary(client, h, branch.id, product.id)
    assert _figures(low) == (100, 95, 5)
    assert low["stock_state"] == "low_shelf"
    assert low["alert_label"] == "LOW SHELF STOCK - REPLENISH FROM WAREHOUSE"

    for _ in range(2):  # refreshing must not create duplicates
        alerts = await client.get(
            f"{BASE}/stock-summary", params={"branch_id": str(branch.id), "alerts_only": "true"}, headers=h
        )
        assert alerts.status_code == 200, alerts.text
        mine = [r for r in alerts.json() if r["product_id"] == str(product.id)]
        assert len(mine) == 1

    more = await _receive(client, h, branch.id, product.id, 20, 10)
    assert more.status_code == 201, more.text
    ok = await _summary(client, h, branch.id, product.id)
    assert ok["shelf_stock"] == 15
    assert ok["stock_state"] == "ok"
    assert ok["alert_label"] is None
    alerts = await client.get(
        f"{BASE}/stock-summary", params={"branch_id": str(branch.id), "alerts_only": "true"}, headers=h
    )
    assert all(r["product_id"] != str(product.id) for r in alerts.json())


async def test_shelf_empty_is_not_out_of_stock(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    await _receive(client, h, branch.id, product.id, 10, 0)
    summary = await _summary(client, h, branch.id, product.id)
    assert _figures(summary) == (10, 10, 0)
    assert summary["stock_state"] == "shelf_empty"
    empty = await _summary(client, h, branch.id, (await _product(db_session)).id)
    assert empty["stock_state"] == "out_of_stock"


# ---------------------------------------------------------------- Test E
async def test_e_partial_delivery_only_received_quantity_counts(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    invoice, line_item = await _invoice(db_session, branch, admin, product, ordered=100)
    await db_session.commit()

    resp = await _receive(
        client, h, branch.id, product.id, 80, 20,
        invoice_id=str(invoice.id), line_extra={"invoice_line_item_id": str(line_item.id)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["cost_status"] == "verified"
    assert _figures(await _summary(client, h, branch.id, product.id)) == (80, 60, 20)

    out = await client.get(f"{BASE}/receipts/outstanding", params={"invoice_id": str(invoice.id)}, headers=h)
    assert out.status_code == 200, out.text
    row = [r for r in out.json() if r["invoice_line_item_id"] == str(line_item.id)][0]
    assert row["ordered_pieces"] == 100
    assert row["received_via_receipts"] == 80
    assert row["outstanding_pieces"] == 20

    over = await _receive(
        client, h, branch.id, product.id, 30, 0,
        invoice_id=str(invoice.id), line_extra={"invoice_line_item_id": str(line_item.id)},
    )
    assert over.status_code == 400
    assert "outstanding" in over.json()["detail"]
    assert _figures(await _summary(client, h, branch.id, product.id)) == (80, 60, 20)

    rest = await _receive(
        client, h, branch.id, product.id, 20, 0,
        invoice_id=str(invoice.id), line_extra={"invoice_line_item_id": str(line_item.id)},
    )
    assert rest.status_code == 201, rest.text
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)


# ---------------------------------------------------------------- Test F
async def test_f_invoice_entered_later_does_not_double_count(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    resp = await _receive(client, h, branch.id, product.id, 100, 20, supplier_name="Pending Supplier")
    assert resp.status_code == 201, resp.text
    receipt = resp.json()
    assert receipt["documentation_status"] == "pending"
    assert receipt["cost_status"] == "unverified"
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)
    transfers_before = await _transfer_count(db_session, receipt["id"])
    assert transfers_before == 2

    invoice, line_item = await _invoice(db_session, branch, admin, product, ordered=100)
    await db_session.commit()
    linked = await client.patch(
        f"{BASE}/receipts/{receipt['id']}/link-invoice",
        json={
            "invoice_id": str(invoice.id),
            "line_links": {receipt["lines"][0]["id"]: str(line_item.id)},
            "verify_costs": True,
        },
        headers=h,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["documentation_status"] == "documented"
    assert linked.json()["cost_status"] == "verified"

    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)
    assert await _transfer_count(db_session, receipt["id"]) == transfers_before
    listing = await client.get(f"{BASE}/receipts", params={"branch_id": str(branch.id)}, headers=h)
    assert len(listing.json()) == 1


# ---------------------------------------------------------------- Test G
async def test_g_duplicate_submission_posts_once(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    first = await _receive(client, h, branch.id, product.id, 100, 20, key="dup-key-1")
    second = await _receive(client, h, branch.id, product.id, 100, 20, key="dup-key-1")
    assert first.status_code == 201, first.text
    assert second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)


async def test_g_posting_the_same_draft_twice_posts_once(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    draft = await _receive(client, h, branch.id, product.id, 100, 20, post=False)
    rid = draft.json()["id"]
    one = await client.post(f"{BASE}/receipts/{rid}/post", headers=h)
    two = await client.post(f"{BASE}/receipts/{rid}/post", headers=h)
    assert one.status_code == 200, one.text
    assert one.json()["already_posted"] is False
    assert two.json()["already_posted"] is True
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)


# ---------------------------------------------------------------- Test I
async def test_i_other_branch_is_unaffected_and_isolated(client, db_session):
    branch_a = await _branch(db_session)
    branch_b = await _branch(db_session)
    admin = await _user(db_session, "super_admin")
    manager_a = await _user(db_session, "branch_manager", branch_a.id)
    product = await _product(db_session)
    await db_session.commit()
    ha, hm = _headers(admin), _headers(manager_a)

    resp = await _receive(client, ha, branch_a.id, product.id, 100, 20)
    assert resp.status_code == 201, resp.text
    assert _figures(await _summary(client, ha, branch_a.id, product.id)) == (100, 80, 20)
    assert _figures(await _summary(client, ha, branch_b.id, product.id)) == (0, 0, 0)

    blocked = await _receive(client, hm, branch_b.id, product.id, 5, 0)
    assert blocked.status_code == 403
    peek = await client.get(f"{BASE}/stock-summary", params={"branch_id": str(branch_b.id)}, headers=hm)
    assert peek.status_code == 403
    own = await _receive(client, hm, branch_a.id, product.id, 10, 0)
    assert own.status_code == 201, own.text
    assert _figures(await _summary(client, ha, branch_b.id, product.id)) == (0, 0, 0)


async def test_staff_cannot_receive_goods(client, db_session):
    branch = await _branch(db_session)
    staff = await _user(db_session, "cashier", branch.id)
    product = await _product(db_session)
    await db_session.commit()
    resp = await _receive(client, _headers(staff), branch.id, product.id, 5, 0)
    assert resp.status_code == 403


# ---------------------------------------------------------------- Test L (late entry, receipt side)
async def test_l_late_entry_needs_reconciliation_and_is_not_backdated(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    delivered = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()

    blocked = await _receive(client, h, branch.id, product.id, 100, 20, delivered_at=delivered)
    assert blocked.status_code == 400
    assert "after the fact" in blocked.json()["detail"]
    assert _figures(await _summary(client, h, branch.id, product.id)) == (0, 0, 0)

    ok = await _receive(
        client, h, branch.id, product.id, 100, 20,
        delivered_at=delivered,
        reconciliation_notes="Checked shelf count and sales since the delivery",
        reconciliation_confirmed=True,
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["is_late_entry"] is True
    posted = datetime.fromisoformat(body["posted_at"])
    delivered_back = datetime.fromisoformat(body["delivered_at"])
    assert posted - delivered_back > timedelta(hours=4)
    assert _figures(await _summary(client, h, branch.id, product.id)) == (100, 80, 20)


async def test_delivery_time_in_the_future_is_rejected(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    resp = await _receive(client, h, branch.id, product.id, 10, 0, delivered_at=future)
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"]