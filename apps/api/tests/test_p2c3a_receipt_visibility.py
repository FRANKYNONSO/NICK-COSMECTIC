"""P2c-3a tests. Live tests: who may read receipts and the cost summary.
Static (text only) tests: cost hidden as null, guards present. The live
tests do NOT prove the hidden cost, because they create no receipt.
Run ONLY against shelf_test. Every live test is rolled back.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _branch, _headers, _user  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GR = os.path.join(HERE, "..", "api", "v1", "routers", "goods_receiving.py")
LIST_ROLES = [
    ("super_admin", 200),
    ("branch_manager", 200),
    ("cashier", 403),
    ("store_assistant", 200),
]
ONE_ROLES = [
    ("super_admin", 404),
    ("branch_manager", 404),
    ("cashier", 403),
    ("store_assistant", 404),
]
COST_ROLES = [
    ("super_admin", 200),
    ("branch_manager", 200),
    ("cashier", 403),
    ("store_assistant", 403),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,code", LIST_ROLES)
async def test_a_receipt_list_by_role(client, db_session, role, code):
    branch = await _branch(db_session)
    user = await _user(db_session, role, branch.id)
    r = await client.get("/api/v1/goods-receiving/receipts", headers=_headers(user))
    assert r.status_code == code, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role,code", ONE_ROLES)
async def test_b_one_receipt_by_role(client, db_session, role, code):
    branch = await _branch(db_session)
    user = await _user(db_session, role, branch.id)
    r = await client.get(
        "/api/v1/goods-receiving/receipts/" + str(uuid.uuid4()), headers=_headers(user)
    )
    assert r.status_code == code, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role,code", COST_ROLES)
async def test_c_cost_summary_by_role(client, db_session, role, code):
    branch = await _branch(db_session)
    user = await _user(db_session, role, branch.id)
    r = await client.get("/api/v1/goods-receiving/cost-summary", headers=_headers(user))
    assert r.status_code == code, r.text


def test_d_static_text():
    with open(GR, encoding="utf-8") as f:
        t = f.read()
    assert "PATCHP2C3A-RECEIPTS" in t
    assert 'line["unit_cost_kobo"] = None' in t
    assert t.count('require_any_permission("view_received_goods", "manage_inventory")') == 3
    assert 'Depends(require_permission("view_financials"))' in t
    assert "return out if show_cost else _hide_receipt_costs(out)" in t
