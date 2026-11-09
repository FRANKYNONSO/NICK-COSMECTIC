"""P2c-2 tests: cost is sent as 0 to roles without view_financials.
Live tests: low-stock report and stock batches. Static (text only) tests:
purchase orders. Run ONLY against shelf_test. Every live test is rolled back.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _branch, _headers, _product, _user  # noqa: E402

from infrastructure.db.models import StockBatch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTERS = os.path.join(HERE, "..", "api", "v1", "routers")
ROLES = [
    ("super_admin", True),
    ("branch_manager", True),
    ("cashier", False),
    ("store_assistant", False),
]


def _read(name):
    with open(os.path.join(ROUTERS, name), encoding="utf-8") as f:
        return f.read()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,sees", ROLES)
async def test_a_low_stock_report_cost(client, db_session, role, sees):
    branch = await _branch(db_session)
    product = await _product(db_session)
    product.reorder_level = 5
    await db_session.flush()
    user = await _user(db_session, role, branch.id)
    r = await client.get("/api/v1/reports/low-stock", headers=_headers(user))
    assert r.status_code == 200, r.text
    rows = [x for x in r.json() if x["product_id"] == str(product.id)]
    assert len(rows) == 1
    assert float(rows[0]["cost_price_naira"]) == (1.0 if sees else 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,sees", ROLES)
async def test_b_stock_batches_cost(client, db_session, role, sees):
    branch = await _branch(db_session)
    product = await _product(db_session)
    db_session.add(
        StockBatch(
            product_id=product.id,
            branch_id=branch.id,
            batch_number="P2C2-" + uuid.uuid4().hex[:6],
            quantity_received=10,
            quantity_remaining=10,
            unit_cost_kobo=250,
        )
    )
    await db_session.flush()
    user = await _user(db_session, role, branch.id)
    r = await client.get(
        "/api/v1/inventory/stock-batches",
        params={"product_id": str(product.id)},
        headers=_headers(user),
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert "unit_cost_naira" in rows[0]
    assert float(rows[0]["unit_cost_naira"]) == (2.5 if sees else 0.0)


def test_c_static_reports_and_inventory():
    rep = _read("reports.py")
    inv = _read("inventory.py")
    assert "PATCHP2C2-COST-VISIBLE" in rep and "if show_cost else 0.0" in rep
    assert "PATCHP2C2-COST-VISIBLE" in inv and "show_cost=show_cost" in inv


def test_d_static_purchase_orders():
    pur = _read("purchasing.py")
    assert pur.count("PATCHP2C2-COST-VISIBLE") == 2
    assert pur.count("show_cost=show_cost") == 2
    assert "unit_cost_naira=Money(i.unit_cost_kobo).naira if show_cost else 0.0" in pur
