"""Script test for Patch 10b: a save that sends the price a product already has
must not write a price audit event and must not reset or create branch overrides.
Run ONLY against shelf_test. Every test is rolled back.
PATCH10B-SAME-PRICE-NOOP
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _fresh, _say, _setup  # noqa: E402

from infrastructure.db.models import AuditLog, BranchProductPrice, Product, User  # noqa: E402
from infrastructure.security.jwt import create_access_token  # noqa: E402
from infrastructure.security.passwords import hash_password  # noqa: E402
from main import app  # noqa: E402

pytestmark = pytest.mark.asyncio

PRICE_ACTIONS = ["global_price_changed", "branch_price_override_changed"]


def _url(product_id):
    paths = [p for p in app.openapi()["paths"].keys() if p.endswith("/catalog/products")]
    assert len(paths) == 1, paths
    return paths[0] + "/" + str(product_id)


async def _price_events(db, product_id):
    result = await db.execute(
        select(AuditLog).where(AuditLog.entity_id == product_id, AuditLog.action.in_(PRICE_ACTIONS))
    )
    return list(result.scalars().all())


async def _override(db, branch_id, product_id):
    result = await db.execute(
        select(BranchProductPrice)
        .where(BranchProductPrice.branch_id == branch_id, BranchProductPrice.product_id == product_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _manager(db, branch):
    tag = uuid.uuid4().hex[:8]
    user = User(
        full_name="P10B Manager",
        role="branch_manager",
        email="m" + tag + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=branch.id,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.commit()
    token = create_access_token(str(user.id), user.role, str(branch.id))
    return user, {"Authorization": "Bearer " + token}


async def _add_override(db, branch, user, product, extra_kobo):
    db.add(
        BranchProductPrice(
            branch_id=branch.id,
            product_id=product.id,
            selling_price_kobo=product.selling_price_kobo + extra_kobo,
            set_by=user.id,
        )
    )
    await db.flush()


async def test_a_super_admin_same_price_edit(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    old_naira = round(product.selling_price_kobo / 100, 2)
    await _add_override(db_session, branch, user, product, 500)
    resp = await client.patch(
        _url(product.id), json={"name": "P10B Renamed", "selling_price_naira": old_naira}, headers=headers
    )
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "P10B Renamed"
    events = await _price_events(db_session, product.id)
    assert events == [], "a same-price save wrote %d price event(s)" % len(events)
    assert await _override(db_session, branch.id, product.id) is not None, "branch override was deleted"
    print("PASS a: same-price edit by super admin writes nothing and keeps the override")


async def test_b_super_admin_real_price_change(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    old_kobo = product.selling_price_kobo
    old_naira = round(old_kobo / 100, 2)
    new_naira = old_naira + 50
    await _add_override(db_session, branch, user, product, 500)
    resp = await client.patch(_url(product.id), json={"selling_price_naira": new_naira}, headers=headers)
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    events = await _price_events(db_session, product.id)
    assert len(events) == 1, len(events)
    assert events[0].action == "global_price_changed"
    assert events[0].log_metadata["old_price_naira"] == old_naira
    assert events[0].log_metadata["new_price_naira"] == new_naira
    row = await _fresh(db_session, Product, product.id)
    assert row.selling_price_kobo == old_kobo + 5000, row.selling_price_kobo
    assert await _override(db_session, branch.id, product.id) is None, "override was not cleared"
    print("PASS b: real global price change still logs once and clears branch overrides")


async def test_c_branch_manager_same_price_edit(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    manager, mheaders = await _manager(db_session, branch)
    old_naira = round(product.selling_price_kobo / 100, 2)
    resp = await client.patch(
        _url(product.id), json={"name": "P10B Mgr Renamed", "selling_price_naira": old_naira}, headers=mheaders
    )
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    assert await _override(db_session, branch.id, product.id) is None, "an override row was created"
    events = await _price_events(db_session, product.id)
    assert events == [], "a same-price save wrote %d price event(s)" % len(events)
    print("PASS c: same-price edit by branch manager creates no override and no event")


async def test_d_branch_manager_real_price_change(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    manager, mheaders = await _manager(db_session, branch)
    old_kobo = product.selling_price_kobo
    old_naira = round(old_kobo / 100, 2)
    new_naira = old_naira + 50
    resp = await client.patch(_url(product.id), json={"selling_price_naira": new_naira}, headers=mheaders)
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    row = await _override(db_session, branch.id, product.id)
    assert row is not None, "no override row was created"
    assert row.selling_price_kobo == old_kobo + 5000, row.selling_price_kobo
    events = await _price_events(db_session, product.id)
    assert len(events) == 1, len(events)
    assert events[0].action == "branch_price_override_changed"
    assert events[0].log_metadata["old_price_naira"] == old_naira
    assert events[0].log_metadata["new_price_naira"] == new_naira
    print("PASS d: real branch price change still creates the override and logs once")


async def test_e_name_only_edit(client, db_session):
    branch, user, product, headers = await _setup(db_session)
    await _add_override(db_session, branch, user, product, 500)
    resp = await client.patch(_url(product.id), json={"name": "P10B Name Only"}, headers=headers)
    _say("status", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    assert await _price_events(db_session, product.id) == []
    assert await _override(db_session, branch.id, product.id) is not None
    print("PASS e: name-only edit writes no price event and keeps the override")
