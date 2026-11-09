"""Script test: customer order form rules on the server (Patch 2c).

Run ONLY against shelf_test (set DATABASE_URL for the run). Every test is
rolled back by the db_session fixture, so no rows are left behind.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from api.v1.routers.customer_requests import UNIT_LABELS
from test_customer_order_booking import _orders_base, _say, _setup

pytestmark = pytest.mark.asyncio


def _other_unit():
    labels = list(UNIT_LABELS)
    if "cartons" in labels:
        return "cartons"
    others = [u for u in labels if u not in ("pieces", "dozens")]
    assert others, "no unit other than pieces/dozens in UNIT_LABELS: " + str(labels)
    return others[0]


async def _post(client, db, **extra):
    branch, user, product, headers = await _setup(db)
    phone = "080" + "".join(random.choice("0123456789") for _ in range(8))
    body = {
        "branch_id": str(branch.id),
        "item_name": product.name,
        "product_id": str(product.id),
        "customer_name": "CO Rule Customer",
        "customer_phone": phone,
        "deposit_naira": 0,
        "add_to_restock_list": False,
    }
    body.update(extra)
    return await client.post(_orders_base(), json=body, headers=headers)


async def test_j_other_unit_without_total_is_refused(client, db_session):
    unit = _other_unit()
    resp = await _post(client, db_session, quantity=2, unit_label=unit)
    _say("unit", unit, "status", resp.status_code, "body", resp.text[:200])
    assert resp.status_code == 422
    assert "how many pieces the customer needs" in resp.text.lower()  # PATCHT2-STALE-TESTS
    print("PASS j: a unit that is not pieces/dozens needs the pieces the customer needs")


async def test_k_pieces_need_defaults_to_total(client, db_session):
    resp = await _post(client, db_session, quantity=3, unit_label="pieces")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    _say("order_pieces", data["order_pieces"], "customer_pieces", data["customer_pieces"])
    assert data["order_pieces"] == 3
    assert data["customer_pieces"] == 3
    print("PASS k: customer needs defaults to the total")


async def test_l_need_above_total_is_refused(client, db_session):
    resp = await _post(client, db_session, quantity=3, unit_label="pieces", customer_pieces=5)
    _say("status", resp.status_code, "body", resp.text[:200])
    assert resp.status_code == 422
    assert "more pieces than the whole order" in resp.text
    print("PASS l: customer needs above the total is refused")


async def test_m_dozens_fill_both_numbers(client, db_session):
    resp = await _post(client, db_session, quantity=2, unit_label="dozens")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    _say("order_pieces", data["order_pieces"], "customer_pieces", data["customer_pieces"])
    assert data["order_pieces"] == 24
    assert data["customer_pieces"] == 24
    print("PASS m: dozens give total 24 and customer needs 24")


async def test_n_other_unit_with_total_defaults_need(client, db_session):
    unit = _other_unit()
    resp = await _post(client, db_session, quantity=2, unit_label=unit, order_pieces=48)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    _say("unit", unit, "order_pieces", data["order_pieces"], "customer_pieces", data["customer_pieces"])
    assert data["order_pieces"] == 48
    assert data["customer_pieces"] == 48
    print("PASS n: total typed by hand, customer needs follows it")
