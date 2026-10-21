"""Script test for Patch 5c: pushable invoice items carry the product pack defaults.
Run ONLY against shelf_test (migrated to cust4packs01). Every test is rolled back.
"""
import os
import sys
import uuid
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _say, _setup  # noqa: E402

from infrastructure.db.models import PurchaseInvoice, PurchaseInvoiceLineItem  # noqa: E402
from main import app  # noqa: E402

pytestmark = pytest.mark.asyncio


def _path():
    paths = [p for p in app.openapi()["paths"].keys() if p.endswith("/warehouse/pushable-invoice-items")]
    assert len(paths) == 1, paths
    return paths[0]


async def _line(db, user, branch, name, product_id, pieces_per_carton=None):
    inv = PurchaseInvoice(
        branch_id=branch.id,
        supplier_name="PK Supplier " + uuid.uuid4().hex[:6],
        status="completed",
        invoice_date=date.today(),
        created_by=user.id,
    )
    db.add(inv)
    await db.flush()
    li = PurchaseInvoiceLineItem(
        invoice_id=inv.id,
        position=0,
        item_name=name,
        product_id=product_id,
        quantity=2,
        unit_label="cartons",
        pieces_per_carton=pieces_per_carton,
    )
    db.add(li)
    await db.flush()
    await db.commit()
    return li


async def _get_item(client, headers, branch, li):
    resp = await client.get(_path(), params={"branch_id": str(branch.id)}, headers=headers)
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    found = [x for x in resp.json() if x["invoice_line_item_id"] == str(li.id)]
    assert len(found) == 1, "line item not in the pushable list"
    return found[0]


async def test_a_linked_product_with_defaults(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    product.default_pieces_per_carton = 48
    product.default_pieces_per_bag = 24
    product.default_pieces_per_roll = 12
    await db_session.commit()
    li = await _line(db_session, user, branch, product.name, product.id, pieces_per_carton=24)
    item = await _get_item(client, headers, branch, li)
    assert item["default_pieces_per_carton"] == 48
    assert item["default_pieces_per_bag"] == 24
    assert item["default_pieces_per_roll"] == 12
    assert item["pieces_per_carton"] == 24
    print("PASS a: defaults and the line's own number both come through")


async def test_b_linked_product_without_defaults(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    li = await _line(db_session, user, branch, product.name, product.id)
    item = await _get_item(client, headers, branch, li)
    assert item["default_pieces_per_carton"] is None
    assert item["default_pieces_per_bag"] is None
    assert item["default_pieces_per_roll"] is None
    print("PASS b: a product with no defaults gives null defaults")


async def test_c_line_with_no_product(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    li = await _line(db_session, user, branch, "No Such Product " + uuid.uuid4().hex[:8], None)
    item = await _get_item(client, headers, branch, li)
    assert item["is_unmatched"] is True
    assert item["product_id"] is None
    assert item["default_pieces_per_carton"] is None
    assert item["default_pieces_per_bag"] is None
    assert item["default_pieces_per_roll"] is None
    print("PASS c: an unmatched line gives null defaults")


async def test_d_matched_by_name_only(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    product.default_pieces_per_carton = 36
    product.default_pieces_per_roll = 10
    await db_session.commit()
    li = await _line(db_session, user, branch, product.name, None)
    item = await _get_item(client, headers, branch, li)
    assert item["is_unmatched"] is False
    assert item["product_id"] == str(product.id)
    assert item["default_pieces_per_carton"] == 36
    assert item["default_pieces_per_bag"] is None
    assert item["default_pieces_per_roll"] == 10
    print("PASS d: a line matched by name only still gets the defaults")
