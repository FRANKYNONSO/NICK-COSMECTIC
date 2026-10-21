"""Script test for Patch 3c (server): store credit -> discount coupon.
Run ONLY against shelf_test (migration cust3coupon01 must be applied).
Every test is rolled back by the db_session fixture.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import SALES, _fresh, _say  # noqa: E402
from test_customer_order_autocomplete import _arrive, _changes  # noqa: E402

import infrastructure.db.models as models  # noqa: E402
from infrastructure.db.models import Branch, Coupon, PendingChange, Sale, User  # noqa: E402
from infrastructure.security.jwt import create_access_token  # noqa: E402
from infrastructure.security.passwords import hash_password  # noqa: E402

pytestmark = pytest.mark.asyncio

COUPONS = "/api/v1/coupons"


async def _credit(client, db):
    """Real flow: deposit 250 on a 200 item -> completed booking + 50 naira credit."""
    c, order = await _arrive(client, db, 250, "transfer")
    assert order.booking_id is not None, "no booking, note: " + str(order.resolution_note)
    sale = await _fresh(db, Sale, order.booking_id)
    changes = await _changes(db, sale.id)
    assert len(changes) == 1, "expected one credit, got " + str(len(changes))
    return c, sale, changes[0]


async def _issue(client, headers, ref, amount=1):
    body = {"discount_amount_naira": amount}
    if ref is not None:
        body["reference_code"] = ref
    return await client.post(COUPONS + "/issue", json=body, headers=headers)


async def _coupon_row(db, code):
    result = await db.execute(select(Coupon).where(Coupon.code == code).execution_options(populate_existing=True))
    return result.scalar_one()


async def _give_discount(db, sale, kobo):
    sale.discount_kobo = kobo
    await db.commit()


async def _redeem(client, headers, code, sale_id):
    return await client.post(
        COUPONS + "/redeem",
        json={"code": code, "sale_id": str(sale_id), "sale_total_naira": 200},
        headers=headers,
    )


async def _expire(db, code):
    row = await _coupon_row(db, code)
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()


async def test_a_issue_with_reference_uses_full_credit(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    resp = await _issue(client, c["headers"], ch.reference_code, amount=1)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    row = await _coupon_row(db_session, data["code"])
    life = row.expires_at - row.created_at
    _say("coupon amount", data["discount_amount_naira"], "linked", row.pending_change_id == ch.id, "life", life)
    assert data["discount_amount_naira"] == 50.0  # client sent 1, server used the full credit
    assert row.discount_amount_kobo == 5000
    assert row.pending_change_id == ch.id
    assert abs(life - timedelta(minutes=30)) < timedelta(seconds=60)
    assert (await _fresh(db_session, PendingChange, ch.id)).status == "outstanding"
    print("PASS a: reference code gives the FULL credit, linked, 30 minutes")


async def test_b_unknown_or_settled_code_is_404(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    resp = await _issue(client, c["headers"], "CHG-ZZZZZZ")
    _say("unknown code status", resp.status_code, resp.text)
    assert resp.status_code == 404
    ch.status = "settled"
    ch.settlement_method = "cash"
    await db_session.commit()
    resp = await _issue(client, c["headers"], ch.reference_code)
    _say("settled code status", resp.status_code, resp.text)
    assert resp.status_code == 404
    print("PASS b: unknown and settled codes -> 404")


async def test_c_other_branch_is_refused(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    tag = uuid.uuid4().hex[:8]
    other = Branch(name="CO Other Branch " + tag)
    db_session.add(other)
    await db_session.flush()
    mgr = User(
        full_name="CO Pinned Manager",
        role="branch_manager",
        email="pin" + tag + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=other.id,
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.flush()
    await db_session.commit()
    headers = {"Authorization": "Bearer " + create_access_token(str(mgr.id), mgr.role, str(other.id))}
    resp = await _issue(client, headers, ch.reference_code)
    _say("other branch status", resp.status_code, resp.text)
    assert resp.status_code == 403
    print("PASS c: a branch-pinned user cannot use another branch's credit (403)")


async def test_d_second_issue_blocked_until_first_expires(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    r1 = await _issue(client, c["headers"], ch.reference_code)
    assert r1.status_code == 201, r1.text
    r2 = await _issue(client, c["headers"], ch.reference_code)
    _say("second issue", r2.status_code, r2.text)
    assert r2.status_code == 409
    await _expire(db_session, r1.json()["code"])
    r3 = await _issue(client, c["headers"], ch.reference_code)
    _say("third issue after expiry", r3.status_code)
    assert r3.status_code == 201, r3.text
    assert r3.json()["code"] != r1.json()["code"]
    print("PASS d: second code -> 409; after expiry a new one is allowed")


async def test_e_settle_blocked_while_code_is_live(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    r1 = await _issue(client, c["headers"], ch.reference_code)
    assert r1.status_code == 201, r1.text
    url = SALES + "/pending-changes/" + str(ch.id) + "/settle"
    body = {"amount_naira": 50, "settlement_method": "cash"}
    resp = await client.post(url, json=body, headers=c["headers"])
    _say("settle while live", resp.status_code, resp.text)
    assert resp.status_code == 400
    assert "discount code" in resp.text
    assert (await _fresh(db_session, PendingChange, ch.id)).status == "outstanding"
    await _expire(db_session, r1.json()["code"])
    resp = await client.post(url, json=body, headers=c["headers"])
    _say("settle after expiry", resp.status_code, resp.text)
    assert resp.status_code == 200, resp.text
    assert (await _fresh(db_session, PendingChange, ch.id)).status == "settled"
    print("PASS e: cash settle is refused while the code is live, works after it expires")


async def test_f_redeem_settles_the_credit(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    r1 = await _issue(client, c["headers"], ch.reference_code)
    assert r1.status_code == 201, r1.text
    code = r1.json()["code"]
    await _give_discount(db_session, sale, 5000)
    resp = await _redeem(client, c["headers"], code, sale.id)
    _say("redeem", resp.status_code, resp.text[:200])
    assert resp.status_code == 200, resp.text
    ch2 = await _fresh(db_session, PendingChange, ch.id)
    cp = await _coupon_row(db_session, code)
    _say("credit", ch2.status, ch2.settlement_method, ch2.settled_by, ch2.settled_at, "coupon", cp.status)
    assert ch2.status == "settled"
    assert ch2.settlement_method == "coupon"
    assert ch2.settled_by == c["user"].id
    assert ch2.settled_at is not None
    assert cp.status == "redeemed"
    assert cp.redeemed_sale_id == sale.id
    # audit row
    audit_model = None
    for name in dir(models):
        obj = getattr(models, name)
        if name.lower().startswith("audit") and hasattr(obj, "action"):
            audit_model = obj
            break
    if audit_model is None:
        print("audit NOT CHECKED: no audit model found by name")
    else:
        rows = (await db_session.execute(
            select(audit_model).where(audit_model.action == "pending_change_settled_by_coupon")
        )).scalars().all()
        _say("audit rows", len(rows))
        assert len(rows) >= 1
    # receipt data shows the coupon method (item j)
    resp = await client.get(SALES + "/" + str(sale.id), headers=c["headers"])
    assert resp.status_code == 200, resp.text
    pcs = resp.json()["pending_changes"]
    _say("route pending_changes", pcs)
    assert len(pcs) == 1
    assert pcs[0]["status"] == "settled"
    assert pcs[0]["settlement_method"] == "coupon"
    print("PASS f: redeem settles the credit (method coupon, settled_by, audit) and the receipt data shows it")


async def test_g_redeem_twice_changes_nothing_twice(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    code = (await _issue(client, c["headers"], ch.reference_code)).json()["code"]
    await _give_discount(db_session, sale, 5000)
    r1 = await _redeem(client, c["headers"], code, sale.id)
    assert r1.status_code == 200, r1.text
    first = await _fresh(db_session, PendingChange, ch.id)
    settled_at = first.settled_at
    r2 = await _redeem(client, c["headers"], code, sale.id)
    _say("second redeem", r2.status_code, r2.text)
    assert r2.status_code == 409
    again = await _fresh(db_session, PendingChange, ch.id)
    assert again.settled_at == settled_at
    assert again.status == "settled"
    print("PASS g: second redeem -> 409, credit not changed twice")


async def test_h_credit_settled_meanwhile_blocks_redeem(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    code = (await _issue(client, c["headers"], ch.reference_code)).json()["code"]
    ch.status = "settled"
    ch.settlement_method = "cash"
    await db_session.commit()
    await _give_discount(db_session, sale, 5000)
    resp = await _redeem(client, c["headers"], code, sale.id)
    _say("redeem after settle", resp.status_code, resp.text)
    assert resp.status_code == 409
    assert (await _coupon_row(db_session, code)).status == "active"
    assert (await _fresh(db_session, PendingChange, ch.id)).settlement_method == "cash"
    print("PASS h: credit settled meanwhile -> 409, coupon stays active")


async def test_i_plain_coupon_is_unchanged(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    resp = await _issue(client, c["headers"], None, amount=10)
    assert resp.status_code == 201, resp.text
    code = resp.json()["code"]
    row = await _coupon_row(db_session, code)
    assert row.pending_change_id is None
    assert row.discount_amount_kobo == 1000
    await _give_discount(db_session, sale, 1000)
    r2 = await _redeem(client, c["headers"], code, sale.id)
    _say("plain redeem", r2.status_code)
    assert r2.status_code == 200, r2.text
    assert (await _coupon_row(db_session, code)).status == "redeemed"
    assert (await _fresh(db_session, PendingChange, ch.id)).status == "outstanding"
    print("PASS i: a plain coupon works as before and leaves the credit alone")