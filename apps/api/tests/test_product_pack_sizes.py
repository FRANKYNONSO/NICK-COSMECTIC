"""Script test for Patch 5a: default pieces per carton, bag and roll on a product.
Run ONLY against shelf_test (migrated to cust4packs01). Every test is rolled back.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _fresh, _say, _setup  # noqa: E402

from infrastructure.db.models import Product  # noqa: E402
from main import app  # noqa: E402

pytestmark = pytest.mark.asyncio


def _base():
    paths = [p for p in app.openapi()["paths"].keys() if p.endswith("/catalog/products")]
    assert len(paths) == 1, paths
    return paths[0]


async def _create(client, headers, **extra):
    name = "PK Test Item " + uuid.uuid4().hex[:8]
    body = {"name": name, "selling_price_naira": 100}
    body.update(extra)
    resp = await client.post(_base(), json=body, headers=headers)
    return name, resp


async def test_a_create_with_defaults(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(
        client, headers,
        default_pieces_per_carton=48, default_pieces_per_bag=24, default_pieces_per_roll=12,
    )
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["default_pieces_per_carton"] == 48
    assert data["default_pieces_per_bag"] == 24
    assert data["default_pieces_per_roll"] == 12
    row = await _fresh(db_session, Product, uuid.UUID(data["id"]))
    assert (row.default_pieces_per_carton, row.default_pieces_per_bag, row.default_pieces_per_roll) == (48, 24, 12)
    print("PASS a: create with three defaults")


async def test_b_create_without_defaults(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(client, headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["default_pieces_per_carton"] is None
    assert data["default_pieces_per_bag"] is None
    assert data["default_pieces_per_roll"] is None
    print("PASS b: create without defaults gives empty values")


async def test_c_edit_sets_one(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(client, headers)
    pid = resp.json()["id"]
    resp = await client.patch(_base() + "/" + pid, json={"default_pieces_per_carton": 30}, headers=headers)
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["default_pieces_per_carton"] == 30
    assert data["default_pieces_per_bag"] is None
    assert data["default_pieces_per_roll"] is None
    row = await _fresh(db_session, Product, uuid.UUID(pid))
    assert row.default_pieces_per_carton == 30 and row.default_pieces_per_bag is None
    print("PASS c: edit sets one default only")


async def test_d_edit_null_clears_one(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(client, headers, default_pieces_per_carton=48, default_pieces_per_bag=24)
    pid = resp.json()["id"]
    resp = await client.patch(_base() + "/" + pid, json={"default_pieces_per_carton": None}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["default_pieces_per_carton"] is None
    assert data["default_pieces_per_bag"] == 24
    row = await _fresh(db_session, Product, uuid.UUID(pid))
    assert row.default_pieces_per_carton is None and row.default_pieces_per_bag == 24
    print("PASS d: null clears one default and keeps the other")


async def test_e_zero_and_negative_refused(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(client, headers, default_pieces_per_carton=0)
    _say("zero on create", resp.status_code)
    assert resp.status_code == 422
    name, resp = await _create(client, headers)
    pid = resp.json()["id"]
    resp = await client.patch(_base() + "/" + pid, json={"default_pieces_per_bag": -5}, headers=headers)
    _say("negative on edit", resp.status_code)
    assert resp.status_code == 422
    row = await _fresh(db_session, Product, uuid.UUID(pid))
    assert row.default_pieces_per_bag is None
    print("PASS e: 0 and negative are refused, nothing saved")


async def test_f_decimal_refused(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(client, headers, default_pieces_per_carton=2.5)
    _say("decimal", resp.status_code)
    assert resp.status_code == 422
    print("PASS f: a decimal is refused")


async def test_g_list_shows_defaults(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    name, resp = await _create(client, headers, default_pieces_per_roll=12)
    assert resp.status_code == 201, resp.text
    resp = await client.get(_base(), params={"search": name}, headers=headers)
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["name"] == name]
    _say("found", len(rows))
    assert len(rows) == 1
    assert rows[0]["default_pieces_per_roll"] == 12
    assert rows[0]["default_pieces_per_carton"] is None
    print("PASS g: the product list shows the defaults")
