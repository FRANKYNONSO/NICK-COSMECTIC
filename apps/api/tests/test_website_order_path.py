"""Script tests for the website order path (brief v20, option B).
They describe what the code already does. Run ONLY against shelf_test.
PATCH13-WEBSITE-ORDER-PATH
"""
import os
import random
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _fresh, _say  # noqa: E402
from test_website_order_cancel import (  # noqa: E402
    UNIT_KOBO,
    _orders_url,
    _prepare,
    _remaining,
    _sale_statuses,
)

from infrastructure.db.models import Product, StorefrontOrder  # noqa: E402

pytestmark = pytest.mark.asyncio


def _body(product_id, qty, reference=None, **extra):
    phone = "081" + "".join(random.choice("0123456789") for _ in range(8))
    body = {
        "reference": reference or "P13-" + uuid.uuid4().hex,
        "customer": {"full_name": "P13 Customer", "phone": phone},
        "delivery": {"address_line": "12 Test Street", "state": "Taraba"},
        "items": [{"product_id": str(product_id), "quantity": qty}],
    }
    body.update(extra)
    return body


async def _post(client, headers, body):
    resp = await client.post(_orders_url(), json=body, headers=headers)
    _say("post", resp.status_code, resp.text[:300])
    return resp


async def _order_rows(db, reference):
    result = await db.execute(
        select(StorefrontOrder)
        .where(StorefrontOrder.reference == reference)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def test_a_same_reference_returns_same_order(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    body = _body(product_id, 3)
    first = await _post(client, headers, body)
    assert first.status_code == 201, first.text
    second = await _post(client, headers, body)
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(first.json()["sales"]) == 1
    assert len(second.json()["sales"]) == 1
    assert await _remaining(db_session, batch_ids[0]) == 7, "stock was taken twice"
    assert len(await _order_rows(db_session, body["reference"])) == 1


async def test_b_short_stock_gives_409_and_changes_nothing(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [5])
    body = _body(product_id, 6)
    resp = await _post(client, headers, body)
    assert resp.status_code == 409, resp.text
    assert "Only 5" in resp.text, resp.text
    assert await _remaining(db_session, batch_ids[0]) == 5
    assert await _order_rows(db_session, body["reference"]) == []


async def test_c_order_is_split_across_branches(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [3, 4])
    body = _body(product_id, 7)
    resp = await _post(client, headers, body)
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert len(order["sales"]) == 2, order["sales"]
    assert await _sale_statuses(db_session, order) == ["completed", "completed"]
    assert await _remaining(db_session, batch_ids[0]) == 0
    assert await _remaining(db_session, batch_ids[1]) == 0


async def test_d_inactive_product_gives_409(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    product = await _fresh(db_session, Product, product_id)
    product.status = "draft"
    await db_session.commit()
    body = _body(product_id, 1)
    resp = await _post(client, headers, body)
    assert resp.status_code == 409, resp.text
    assert "no longer available" in resp.text, resp.text
    assert await _remaining(db_session, batch_ids[0]) == 10
    assert await _order_rows(db_session, body["reference"]) == []


async def test_e_unknown_coupon_gives_400(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    body = _body(product_id, 2, coupon_code="NOSUCHCODE")
    resp = await _post(client, headers, body)
    assert resp.status_code == 400, resp.text
    assert "invalid" in resp.text.lower(), resp.text
    assert await _remaining(db_session, batch_ids[0]) == 10
    assert await _order_rows(db_session, body["reference"]) == []


async def test_f_total_excludes_shipping(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    body = _body(product_id, 3, shipping_fee_naira=2000, shipping_note="P13 note")
    resp = await _post(client, headers, body)
    assert resp.status_code == 201, resp.text
    rows = await _order_rows(db_session, body["reference"])
    assert len(rows) == 1
    row = rows[0]
    _say("kobo", row.subtotal_kobo, row.discount_kobo, row.total_kobo, row.shipping_fee_kobo)
    assert row.subtotal_kobo == 3 * UNIT_KOBO
    assert row.discount_kobo == 0
    assert row.total_kobo == 3 * UNIT_KOBO
    assert row.shipping_fee_kobo == 200000


async def test_g_empty_cart_gives_422(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    body = _body(product_id, 1)
    body["items"] = []
    resp = await _post(client, headers, body)
    assert resp.status_code == 422, resp.text
    assert await _remaining(db_session, batch_ids[0]) == 10
    assert await _order_rows(db_session, body["reference"]) == []
