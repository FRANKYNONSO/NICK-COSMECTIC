"""Cost summary tests (fix35): unverified receipt costs are surfaced, read-only."""
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
    User,
)
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/goods-receiving"


async def _mk_setup(db):
    branch = Branch(name=f"CSum Branch {uuid.uuid4().hex[:6]}")
    db.add(branch)
    await db.flush()
    admin = User(
        full_name="CSum Test User",
        role="super_admin",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=None,
        is_active=True,
    )
    db.add(admin)
    tag = uuid.uuid4().hex[:8]
    product = Product(sku=f"CSM-{tag}", name=f"CSum Test Product {tag}", needs_invoice=False, cost_price_kobo=100)
    db.add(product)
    await db.flush()
    invoice = PurchaseInvoice(
        branch_id=branch.id,
        supplier_name="CSum Test Supplier",
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


async def _extra_branch(db):
    branch = Branch(name=f"CSum Branch {uuid.uuid4().hex[:6]}")
    db.add(branch)
    await db.flush()
    bid = branch.id
    await db.commit()
    return bid


async def _post(client, headers, branch_id, product_id, received, shelf):
    body = {
        "branch_id": str(branch_id),
        "lines": [{"product_id": str(product_id), "quantity_received": received, "quantity_to_shelf": shelf}],
        "post_now": True,
    }
    return await client.post(f"{BASE}/receipts", json=body, headers=headers)


async def _summary(client, headers, branch_id):
    resp = await client.get(f"{BASE}/cost-summary", params={"branch_id": str(branch_id)}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_s1_unverified_receipt_listed_then_cleared_by_link(client, db_session):
    branch, admin, product, invoice, li, h = await _mk_setup(db_session)
    resp = await _post(client, h, branch.id, product.id, 50, 20)
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]

    data = await _summary(client, h, branch.id)
    assert data["unverified_receipt_count"] == 1
    r = data["receipts"][0]
    assert r["receipt_id"] == rid
    assert r["documentation_status"] == "pending"
    assert r["received_pieces"] == 50
    assert r["received_value_kobo"] == 5000
    assert r["shelf_remaining_pieces"] == 20
    assert r["shelf_remaining_value_kobo"] == 2000
    assert data["totals"]["received_value_kobo"] == 5000
    assert data["pending_invoice_received_value_kobo"] == 5000

    db_session.expire_all()
    rows = await db_session.execute(select(GoodsReceiptLine).where(GoodsReceiptLine.receipt_id == uuid.UUID(rid)))
    line_id = rows.scalars().all()[0].id
    link = await client.patch(
        f"{BASE}/receipts/{rid}/link-invoice",
        json={"invoice_id": str(invoice.id), "verify_costs": True, "line_links": {str(line_id): str(li.id)}},
        headers=h,
    )
    assert link.status_code == 200, link.text
    assert link.json()["cost_status"] == "verified"

    after = await _summary(client, h, branch.id)
    assert after["unverified_receipt_count"] == 0
    assert after["receipts"] == []
    assert after["totals"]["received_value_kobo"] == 0
    assert after["pending_invoice_received_value_kobo"] == 0


async def test_s2_branches_are_kept_separate(client, db_session):
    branch_a, admin, product, invoice, li, h = await _mk_setup(db_session)
    branch_b_id = await _extra_branch(db_session)
    ra = await _post(client, h, branch_a.id, product.id, 10, 4)
    rb = await _post(client, h, branch_b_id, product.id, 30, 6)
    assert ra.status_code == 201, ra.text
    assert rb.status_code == 201, rb.text

    data_a = await _summary(client, h, branch_a.id)
    data_b = await _summary(client, h, branch_b_id)
    assert [x["receipt_id"] for x in data_a["receipts"]] == [ra.json()["id"]]
    assert [x["receipt_id"] for x in data_b["receipts"]] == [rb.json()["id"]]
    assert data_a["totals"]["received_value_kobo"] == 1000
    assert data_b["totals"]["received_value_kobo"] == 3000
