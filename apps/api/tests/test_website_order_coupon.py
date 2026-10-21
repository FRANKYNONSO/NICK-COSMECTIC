"""Script tests for website order coupons and the service account (brief v20).
They describe what the code already does. Run ONLY against shelf_test.
PATCH13B-WEBSITE-COUPON
"""
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

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
from test_website_order_path import _body, _order_rows, _post  # noqa: E402

from infrastructure.db.models import Role, StorefrontCoupon, User  # noqa: E402
from infrastructure.security.jwt import create_access_token  # noqa: E402
from infrastructure.security.passwords import hash_password  # noqa: E402

pytestmark = pytest.mark.asyncio


async def _issuer_id(db):
    tag = uuid.uuid4().hex[:8]
    user = User(
        full_name="P13B Issuer",
        role="super_admin",
        email="p13b-" + tag + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=None,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    uid = user.id
    await db.commit()
    return uid


async def _make_coupon(db, amount_kobo=500000, status="active", expires_at=None):
    issuer = await _issuer_id(db)
    code = "WEB-T" + uuid.uuid4().hex[:7].upper()
    coupon = StorefrontCoupon(
        code=code,
        discount_amount_kobo=amount_kobo,
        status=status,
        expires_at=expires_at,
        valid_hours=24,
        issued_by=issuer,
    )
    db.add(coupon)
    await db.flush()
    coupon_id = coupon.id
    await db.commit()
    return coupon_id, code


async def test_h_coupon_discounts_the_order_and_is_redeemed(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    coupon_id, code = await _make_coupon(db_session, 500000)
    body = _body(product_id, 3, coupon_code=code)
    resp = await _post(client, headers, body)
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["id"]
    rows = await _order_rows(db_session, body["reference"])
    assert len(rows) == 1
    row = rows[0]
    _say("kobo", row.subtotal_kobo, row.discount_kobo, row.total_kobo)
    assert row.subtotal_kobo == 3 * UNIT_KOBO
    assert row.discount_kobo == 500000
    assert row.total_kobo == 2500000
    coupon = await _fresh(db_session, StorefrontCoupon, coupon_id)
    assert coupon.status == "redeemed"
    assert str(coupon.redeemed_order_id) == order_id
    assert await _remaining(db_session, batch_ids[0]) == 7


async def test_i_second_use_of_same_coupon_is_refused(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    coupon_id, code = await _make_coupon(db_session, 500000)
    first = await _post(client, headers, _body(product_id, 3, coupon_code=code))
    assert first.status_code == 201, first.text
    second_body = _body(product_id, 2, coupon_code=code)
    second = await _post(client, headers, second_body)
    assert second.status_code == 400, second.text
    assert await _remaining(db_session, batch_ids[0]) == 7, "stock changed by the refused order"
    assert await _order_rows(db_session, second_body["reference"]) == []


async def test_j_expired_coupon_is_refused(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    coupon_id, code = await _make_coupon(db_session, 500000, status="active", expires_at=past)
    body = _body(product_id, 2, coupon_code=code)
    resp = await _post(client, headers, body)
    assert resp.status_code == 400, resp.text
    assert await _remaining(db_session, batch_ids[0]) == 10
    assert await _order_rows(db_session, body["reference"]) == []
    coupon = await _fresh(db_session, StorefrontCoupon, coupon_id)
    assert coupon.status == "active"


async def test_k_voided_coupon_is_refused(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    coupon_id, code = await _make_coupon(db_session, 500000, status="voided")
    body = _body(product_id, 2, coupon_code=code)
    resp = await _post(client, headers, body)
    assert resp.status_code == 400, resp.text
    assert await _remaining(db_session, batch_ids[0]) == 10
    assert await _order_rows(db_session, body["reference"]) == []


async def test_l_service_account_without_branch_can_place_order(client, db_session):
    product_id, _admin_headers, batch_ids = await _prepare(db_session, [10])
    role = (await db_session.execute(select(Role).where(Role.name == "storefront_service"))).scalar_one_or_none()
    if role is None:
        role = Role(
            name="storefront_service",
            display_name="Website Service",
            permissions=["process_sale"],
            is_system=False,
            created_by=None,
        )
        db_session.add(role)
        await db_session.flush()
    else:
        assert list(role.permissions) == ["process_sale"], role.permissions
    tag = uuid.uuid4().hex[:8]
    svc = User(
        full_name="P13B Website Storefront",
        role="storefront_service",
        email="p13b-svc-" + tag + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=None,
        is_active=True,
    )
    db_session.add(svc)
    await db_session.flush()
    svc_id, svc_role = svc.id, svc.role
    await db_session.commit()
    token = create_access_token(str(svc_id), svc_role, None)
    headers = {"Authorization": "Bearer " + token}
    body = _body(product_id, 2)
    resp = await _post(client, headers, body)
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert await _sale_statuses(db_session, order) == ["completed"]
    assert await _remaining(db_session, batch_ids[0]) == 8
