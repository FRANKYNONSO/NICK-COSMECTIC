"""P2c-1 live tests: cost price is sent as 0 to roles without view_financials.
Run ONLY against shelf_test. Every test is rolled back.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _branch, _headers, _product, _user  # noqa: E402

pytestmark = pytest.mark.asyncio

CAT = "/api/v1/catalog"
CASES = [
    ("super_admin", 1.0),
    ("branch_manager", 1.0),
    ("cashier", 0.0),
    ("store_assistant", 0.0),
    (None, 0.0),
]


async def _setup(db, role):
    branch = await _branch(db)
    product = await _product(db)
    if role is None:
        return product, {}
    user = await _user(db, role, branch.id)
    return product, _headers(user)


def _find(rows, product):
    for r in rows:
        if r.get("product_id") == str(product.id) or r.get("id") == str(product.id):
            return r
    return None


@pytest.mark.parametrize("role,expected", CASES)
async def test_a_list_route_cost(client, db_session, role, expected):
    product, h = await _setup(db_session, role)
    r = await client.get(CAT + "/products", params={"search": product.name}, headers=h)
    assert r.status_code == 200, r.text
    row = _find(r.json(), product)
    assert row is not None
    assert float(row["cost_price_naira"]) == expected


@pytest.mark.parametrize("role,expected", CASES)
async def test_b_single_route_cost(client, db_session, role, expected):
    product, h = await _setup(db_session, role)
    r = await client.get(CAT + "/products/" + str(product.id), headers=h)
    assert r.status_code == 200, r.text
    assert float(r.json()["cost_price_naira"]) == expected


@pytest.mark.parametrize("role,expected", [c for c in CASES if c[0] is not None])
async def test_c_mute_and_unmute_cost(client, db_session, role, expected):
    product, h = await _setup(db_session, role)
    for action in ("mute-out-of-stock-alert", "unmute-out-of-stock-alert"):
        r = await client.post(CAT + "/products/" + str(product.id) + "/" + action, headers=h)
        assert r.status_code == 200, r.text
        assert float(r.json()["cost_price_naira"]) == expected


@pytest.mark.parametrize(
    "role,visible",
    [("super_admin", True), ("branch_manager", True), ("cashier", False), ("store_assistant", False)],
)
async def test_d_hidden_product(client, db_session, role, visible):
    product, h = await _setup(db_session, role)
    product.hidden_from_staff = True
    await db_session.flush()
    one = await client.get(CAT + "/products/" + str(product.id), headers=h)
    assert one.status_code == (200 if visible else 404), one.text
    many = await client.get(CAT + "/products", params={"search": product.name}, headers=h)
    assert many.status_code == 200, many.text
    assert (_find(many.json(), product) is not None) == visible


def test_e_helper_is_in_dependencies():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "api", "dependencies.py"), encoding="utf-8") as f:
        text = f.read()
    assert "PATCHP2C-COST-VISIBLE" in text
    assert "async def can_view_costs" in text
