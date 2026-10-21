"""Script test for PATCH8A-PRODUCT-RETURNS-CREDIT (server).
Inventory History window: refund lines and goods taken on credit per product.
Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import sys
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _make_booking  # noqa: E402

from api.v1.routers.inventory import product_returns_and_credit  # noqa: E402
from infrastructure.db.models import CreditEntry, Refund, RefundItem, Sale, SaleItem  # noqa: E402

pytestmark = pytest.mark.asyncio

# the route only reads .branch_id from the user (the permission check is not run here)
ADMIN = SimpleNamespace(role="super_admin", branch_id=None)


async def _base(client, db):
    d = await _make_booking(client, db, 50, "cash")
    product_id = d["product"].id
    user_id = d["user"].id
    sale = await db.get(Sale, d["booking_id"])
    assert sale is not None, "booking sale not found"
    sale_id = sale.id
    branch_id = sale.branch_id
    item = SaleItem(sale_id=sale_id, product_id=product_id, quantity=5, unit_price_kobo=100, line_total_kobo=500)
    db.add(item)
    await db.flush()
    return SimpleNamespace(pid=product_id, uid=user_id, sale_id=sale_id, branch_id=branch_id, item_id=item.id)


async def _refund(db, x, qty, reason, restocked, spoiled):
    ref = Refund(sale_id=x.sale_id, processed_by=x.uid, amount_kobo=qty * 100, reason=reason)
    db.add(ref)
    await db.flush()
    db.add(RefundItem(
        refund_id=ref.id, sale_item_id=x.item_id, quantity=qty,
        unit_price_kobo=100, line_total_kobo=qty * 100, restocked=restocked, spoiled=spoiled,
    ))
    await db.commit()


async def _credit(db, x, qty, name, deducted):
    db.add(CreditEntry(
        branch_id=x.branch_id, customer_full_name=name, customer_phone="08000000000",
        product_id=x.pid, quantity=qty, amount_kobo=qty * 100, stock_deducted=deducted,
        status="outstanding", opened_by=x.uid,
    ))
    await db.commit()


async def _rows(db, product_id, user=ADMIN):
    return await product_returns_and_credit(
        product_id=product_id, branch_id=None, limit=200, session=db, current_user=user
    )


async def test_a_refund_put_back_on_shelf(client, db_session):
    x = await _base(client, db_session)
    await _refund(db_session, x, 2, "changed mind", True, False)
    rows = await _rows(db_session, x.pid)
    print("[8a] rows", [(r.kind, r.what, r.quantity, r.detail) for r in rows])
    assert len(rows) == 1
    r = rows[0]
    assert r.kind == "refund"
    assert r.what == "Refund, put back on shelf"
    assert r.quantity == 2
    assert r.detail == "Reason: changed mind"
    assert r.branch_id == x.branch_id
    assert r.branch_name


async def test_b_spoiled_and_not_put_back_lines(client, db_session):
    x = await _base(client, db_session)
    await _refund(db_session, x, 1, "seal broken", False, True)
    await _refund(db_session, x, 3, None, False, False)
    rows = await _rows(db_session, x.pid)
    print("[8b] rows", [(r.what, r.quantity, r.detail) for r in rows])
    assert sorted(r.what for r in rows) == ["Refund, not put back", "Refund, sent to warehouse (spoiled)"]
    plain = [r for r in rows if r.what == "Refund, not put back"][0]
    assert plain.quantity == 3
    assert plain.detail is None


async def test_c_credit_line_only_when_stock_was_taken(client, db_session):
    x = await _base(client, db_session)
    await _credit(db_session, x, 3, "Ada Test", True)
    await _credit(db_session, x, 9, "Not Deducted", False)
    rows = await _rows(db_session, x.pid)
    print("[8c] rows", [(r.kind, r.what, r.quantity, r.detail) for r in rows])
    assert len(rows) == 1
    r = rows[0]
    assert r.kind == "credit"
    assert r.what == "Taken on credit"
    assert r.quantity == 3
    assert r.detail == "Ada Test, outstanding"


async def test_d_branch_scope_and_unknown_product(client, db_session):
    x = await _base(client, db_session)
    await _refund(db_session, x, 2, "changed mind", True, False)
    own = SimpleNamespace(role="staff", branch_id=x.branch_id)
    other = SimpleNamespace(role="staff", branch_id=uuid.uuid4())
    mine = await _rows(db_session, x.pid, own)
    theirs = await _rows(db_session, x.pid, other)
    nothing = await _rows(db_session, uuid.uuid4())
    print("[8d] own", len(mine), "other branch", len(theirs), "unknown product", len(nothing))
    assert len(mine) == 1
    assert theirs == []
    assert nothing == []
