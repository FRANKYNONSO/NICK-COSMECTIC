"""Costing sync tests (fix32): link-invoice corrects receipt batch costs.

Self-contained helpers; run against shelf_test like the other tests.
"""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from infrastructure.db.models import (
    Branch,
    GoodsReceiptLine,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceLineItem,
    StockBatch,
    StockTransfer,
    User,
)
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/goods-receiving"


async def _mk_setup(db):
    branch = Branch(name=f"CS Branch {uuid.uuid4().hex[:6]}")
    db.add(branch)
    await db.flush()
    admin = User(
        full_name="CS Test User",
        role="super_admin",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=None,
        is_active=True,
    )
    db.add(admin)
    tag = uuid.uuid4().hex[:8]
    product = Product(sku=f"CST-{tag}", name=f"CS Test Product {tag}", needs_invoice=False, cost_price_kobo=100)
    db.add(product)
    await db.flush()
    invoice = PurchaseInvoice(
        branch_id=branch.id,
        supplier_name="CS Test Supplier",
        invoice_date=date.today(),
        created_by=admin.id,
    )
    db.add(invoice)
    await db.flush()
    li = PurchaseInvoiceLineItem(
        invoice_id=invoice.id,
        position=0,
        item_name=product.name,
        product_id=product.id,
        quantity=100,
        unit_label="pieces",
        rate_kobo=150,
        line_total_kobo=100 * 150,
        total_pieces=100,
        cost_price_per_piece_kobo=150,
    )
    db.add(li)
    await db.flush()
    ids = [SimpleNamespace(id=obj.id) for obj in (branch, admin, product, invoice, li)]
    token = create_access_token(str(admin.id), admin.role, None)
    await db.commit()
    return ids[0], ids[1], ids[2], ids[3], ids[4], {"Authorization": f"Bearer {token}"}


async def _post_lines(client, headers, branch_id, lines, post_now=True, **extra):
    body = {"branch_id": str(branch_id), "lines": lines, "post_now": post_now}
    body.update(extra)
    return await client.post(f"{BASE}/receipts", json=body, headers=headers)


def _line(product_id, received, shelf, cost=None, **extra):
    d = {"product_id": str(product_id), "quantity_received": received, "quantity_to_shelf": shelf}
    if cost is not None:
        d["unit_cost_kobo"] = cost
    d.update(extra)
    return d


async def _line_ids(db, receipt_id):
    db.expire_all()
    rows = await db.execute(
        select(GoodsReceiptLine)
        .where(GoodsReceiptLine.receipt_id == uuid.UUID(receipt_id))
        .order_by(GoodsReceiptLine.position)
    )
    return [l.id for l in rows.scalars().all()]


async def _batches(db, branch_id, product_id):
    db.expire_all()
    rows = await db.execute(
        select(StockBatch)
        .where(StockBatch.branch_id == branch_id, StockBatch.product_id == product_id)
        .order_by(StockBatch.created_at, StockBatch.id)
    )
    return list(rows.scalars().all())


async def _transfer_rows(db, receipt_id):
    db.expire_all()
    rows = await db.execute(
        select(StockTransfer.transfer_type, StockTransfer.quantity_pieces).where(
            StockTransfer.goods_receipt_id == uuid.UUID(receipt_id)
        )
    )
    return sorted((a, b) for a, b in rows.all())


async def _link(client, headers, receipt_id, body):
    return await client.patch(f"{BASE}/receipts/{receipt_id}/link-invoice", json=body, headers=headers)


async def _figures(client, headers, branch_id, product_id):
    resp = await client.get(
        f"{BASE}/stock-summary",
        params={"branch_id": str(branch_id), "product_id": str(product_id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    return (row["total_stock"], row["warehouse_stock"], row["shelf_stock"])


async def test_c1_link_with_verify_corrects_batch_cost(client, db_session):
    branch, admin, product, invoice, li, h = await _mk_setup(db_session)
    resp = await _post_lines(client, h, branch.id, [_line(product.id, 50, 20)])
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    assert resp.json()["cost_status"] == "unverified"
    batches = await _batches(db_session, branch.id, product.id)
    assert len(batches) == 1
    assert batches[0].unit_cost_kobo == 100
    transfers_before = await _transfer_rows(db_session, rid)
    line_ids = await _line_ids(db_session, rid)

    link = await _link(
        client, h, rid,
        {"invoice_id": str(invoice.id), "verify_costs": True, "line_links": {str(line_ids[0]): str(li.id)}},
    )
    assert link.status_code == 200, link.text
    assert link.json()["cost_status"] == "verified"

    batches = await _batches(db_session, branch.id, product.id)
    assert len(batches) == 1
    assert batches[0].unit_cost_kobo == 150
    assert batches[0].quantity_remaining == 20
    assert batches[0].quantity_received == 20
    assert await _figures(client, h, branch.id, product.id) == (50, 30, 20)
    assert await _transfer_rows(db_session, rid) == transfers_before


async def test_c2_link_without_verification_leaves_cost(client, db_session):
    branch, admin, product, invoice, li, h = await _mk_setup(db_session)
    resp = await _post_lines(client, h, branch.id, [_line(product.id, 50, 20)])
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    link = await _link(client, h, rid, {"invoice_id": str(invoice.id)})
    assert link.status_code == 200, link.text
    assert link.json()["cost_status"] == "unverified"
    batches = await _batches(db_session, branch.id, product.id)
    assert [b.unit_cost_kobo for b in batches] == [100]


async def test_c3_draft_verified_cost_survives_posting(client, db_session):
    branch, admin, product, invoice, li, h = await _mk_setup(db_session)
    resp = await _post_lines(
        client, h, branch.id,
        [_line(product.id, 50, 20, invoice_line_item_id=str(li.id))],
        post_now=False,
        invoice_id=str(invoice.id),
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    assert resp.json()["status"] == "draft"
    line_ids = await _line_ids(db_session, rid)
    link = await _link(client, h, rid, {"invoice_id": str(invoice.id), "line_costs": {str(line_ids[0]): 175}})
    assert link.status_code == 200, link.text
    posted = await client.post(f"{BASE}/receipts/{rid}/post", headers=h)
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "posted"
    assert posted.json()["cost_status"] == "verified"
    batches = await _batches(db_session, branch.id, product.id)
    assert [b.unit_cost_kobo for b in batches] == [175]
    assert await _figures(client, h, branch.id, product.id) == (50, 30, 20)


async def test_c4_two_lines_same_product_use_weighted_cost(client, db_session):
    branch, admin, product, invoice, li, h = await _mk_setup(db_session)
    resp = await _post_lines(
        client, h, branch.id,
        [_line(product.id, 10, 10, cost=100), _line(product.id, 30, 30, cost=200)],
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    line_ids = await _line_ids(db_session, rid)
    assert len(line_ids) == 2
    batches = await _batches(db_session, branch.id, product.id)
    assert sorted(b.unit_cost_kobo for b in batches) == [100, 200]

    # only one line verified: the product is skipped, nothing changes
    partial = await _link(
        client, h, rid, {"invoice_id": str(invoice.id), "line_costs": {str(line_ids[0]): 100}}
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["cost_status"] == "unverified"
    batches = await _batches(db_session, branch.id, product.id)
    assert sorted(b.unit_cost_kobo for b in batches) == [100, 200]

    # both verified: (10*100 + 30*200) / 40 = 175 on every receipt batch
    full = await _link(
        client, h, rid,
        {"invoice_id": str(invoice.id), "line_costs": {str(line_ids[0]): 100, str(line_ids[1]): 200}},
    )
    assert full.status_code == 200, full.text
    assert full.json()["cost_status"] == "verified"
    batches = await _batches(db_session, branch.id, product.id)
    assert [b.unit_cost_kobo for b in batches] == [175] * len(batches)
    assert sum(b.quantity_remaining for b in batches) == 40
    assert await _figures(client, h, branch.id, product.id) == (40, 0, 40)
