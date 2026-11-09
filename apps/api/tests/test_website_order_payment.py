# Tests for patch 19b: recording a payment on a website order.
# PATCH19B-PAYMENT-FIELDS
# Run ONLY against shelf_test. Every test is rolled back.
import os
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _fresh, _say  # noqa: E402
from test_website_order_cancel import _orders_url, _patch, _place, _prepare, _remaining  # noqa: E402

from infrastructure.db.models import AuditLog, Branch, StockBatch, StorefrontOrder, User  # noqa: E402
from infrastructure.security.jwt import create_access_token  # noqa: E402
from infrastructure.security.passwords import hash_password  # noqa: E402

pytestmark = pytest.mark.asyncio


def _pay_url(order_id):
    return _orders_url() + "/" + order_id + "/payment"


async def _pay(client, headers, order_id, payload):
    resp = await client.post(_pay_url(order_id), json=payload, headers=headers)
    _say("pay", payload, resp.status_code, resp.text[:300])
    return resp


async def _payment_events(db, order_id):
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.entity_id == uuid.UUID(order_id),
            AuditLog.action == "website_order_payment_recorded",
        )
    )
    return list(result.scalars().all())


async def _order_row(db, order_id):
    return await _fresh(db, StorefrontOrder, uuid.UUID(order_id))


async def _branch_of(db, batch_id):
    return (await _fresh(db, StockBatch, batch_id)).branch_id


async def _new_branch(db):
    branch = Branch(name="P19B Other Branch " + uuid.uuid4().hex[:8])
    db.add(branch)
    await db.flush()
    branch_id = branch.id
    await db.commit()
    return branch_id


async def _make_user(db, role, branch_id):
    tag = uuid.uuid4().hex[:8]
    user = User(
        full_name="P19B " + role,
        role=role,
        email="p19b" + tag + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=branch_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    user_id = str(user.id)
    await db.commit()
    token = create_access_token(user_id, role, str(branch_id) if branch_id else None)
    return {"Authorization": "Bearer " + token}


def _standard(payable):
    return {"method": "bank_transfer", "amount_naira": payable, "note": "  Paid at GTB  "}


async def test_a_new_order_is_unpaid(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    assert order["payment_status"] == "unpaid", order
    assert order["payment_method"] is None
    assert order["amount_received_naira"] is None
    assert order["paid_at"] is None
    assert order["paid_recorded_by"] is None
    assert order["payment_note"] is None
    assert order["payable_naira"] > 0
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    print("PASS a: a new website order starts as unpaid")


async def test_b_record_payment_works(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    payable = order["payable_naira"]
    resp = await _pay(client, headers, order["id"], _standard(payable))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_status"] == "paid"
    assert body["payment_method"] == "bank_transfer"
    assert body["amount_received_naira"] == payable
    assert body["paid_at"] is not None
    assert body["paid_recorded_by"] is not None
    assert body["payment_note"] == "Paid at GTB"
    assert body["status"] == "new"
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "paid"
    assert row.payment_method == "bank_transfer"
    assert row.amount_received_kobo == round(payable * 100)
    assert row.paid_at is not None
    assert row.status == "new"
    assert await _remaining(db_session, batch_ids[0]) == 7, "a payment must not change stock"
    print("PASS b: recording a payment stores everything and changes nothing else")


async def test_c_other_amount_cash_and_one_audit_row(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    resp = await _pay(client, headers, order["id"], {"method": "cash_on_delivery", "amount_naira": 1234.5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_method"] == "cash_on_delivery"
    assert body["amount_received_naira"] == 1234.5
    assert body["payment_note"] is None
    row = await _order_row(db_session, order["id"])
    assert row.amount_received_kobo == 123450
    assert len(await _payment_events(db_session, order["id"])) == 1
    print("PASS c: a different amount and cash on delivery work, one audit row is written")


async def test_d_second_payment_is_refused(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    payable = order["payable_naira"]
    first = await _pay(client, headers, order["id"], _standard(payable))
    assert first.status_code == 200, first.text
    second = await _pay(client, headers, order["id"], {"method": "cash_on_delivery", "amount_naira": 1.0})
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "This order is already marked as paid."
    row = await _order_row(db_session, order["id"])
    assert row.payment_method == "bank_transfer"
    assert row.amount_received_kobo == round(payable * 100)
    assert len(await _payment_events(db_session, order["id"])) == 1
    print("PASS d: a second payment is refused with 409 and changes nothing")


async def test_e_cancelled_order_cannot_be_paid(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    cancel = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert cancel.status_code == 200, cancel.text
    resp = await _pay(client, headers, order["id"], _standard(order["payable_naira"]))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "A cancelled order cannot be marked as paid."
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    assert len(await _payment_events(db_session, order["id"])) == 0
    print("PASS e: a cancelled order cannot be marked as paid")


async def test_f_bad_amounts_are_rejected(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    for amount in (0, -5, 0.001, "abc"):
        resp = await _pay(client, headers, order["id"], {"method": "bank_transfer", "amount_naira": amount})
        assert resp.status_code == 422, (amount, resp.status_code, resp.text)
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    assert len(await _payment_events(db_session, order["id"])) == 0
    print("PASS f: zero, negative, tiny and non-number amounts are rejected with 422")


async def test_g_bad_method_is_rejected(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    payable = order["payable_naira"]
    bad = await _pay(client, headers, order["id"], {"method": "card", "amount_naira": payable})
    assert bad.status_code == 422, bad.text
    missing = await _pay(client, headers, order["id"], {"amount_naira": payable})
    assert missing.status_code == 422, missing.text
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    print("PASS g: an unknown or missing payment method is rejected with 422")


async def test_h_paid_order_can_still_be_cancelled(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 3)
    paid = await _pay(client, headers, order["id"], _standard(order["payable_naira"]))
    assert paid.status_code == 200, paid.text
    assert await _remaining(db_session, batch_ids[0]) == 7
    resp = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert await _remaining(db_session, batch_ids[0]) == 10, "stock was not returned"
    print("PASS h: a paid order still cancels as before (stock returns)")


async def test_i_manager_of_another_branch_gets_404(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    other_id = await _new_branch(db_session)
    manager = await _make_user(db_session, "branch_manager", other_id)
    resp = await _pay(client, manager, order["id"], _standard(order["payable_naira"]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Order not found."
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    assert len(await _payment_events(db_session, order["id"])) == 0
    print("PASS i: a branch manager with no share in the order cannot record a payment")


async def test_j_manager_with_a_share_can_record(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    share_branch = await _branch_of(db_session, batch_ids[0])
    manager = await _make_user(db_session, "branch_manager", share_branch)
    resp = await _pay(client, manager, order["id"], _standard(order["payable_naira"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_status"] == "paid"
    assert len(await _payment_events(db_session, order["id"])) == 1
    print("PASS j: a branch manager whose branch has a share can record the payment")


async def test_k_order_detail_shows_payment(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    url = _orders_url() + "/" + order["id"]
    before = await client.get(url, headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["payment_status"] == "unpaid"
    payable = order["payable_naira"]
    paid = await _pay(client, headers, order["id"], _standard(payable))
    assert paid.status_code == 200, paid.text
    after = await client.get(url, headers=headers)
    assert after.status_code == 200, after.text
    assert after.json()["payment_status"] == "paid"
    assert after.json()["payment_method"] == "bank_transfer"
    assert after.json()["amount_received_naira"] == payable
    print("PASS k: the order detail shows unpaid before and paid after")


async def test_l_old_patch_route_leaves_payment_alone(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    payable = order["payable_naira"]
    paid = await _pay(client, headers, order["id"], _standard(payable))
    assert paid.status_code == 200, paid.text
    resp = await _patch(
        client, headers, order["id"], {"status": "processing", "admin_note": "Call the customer", "waybill_ref": "WB-1"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "processing"
    assert body["admin_note"] == "Call the customer"
    assert body["waybill_ref"] == "WB-1"
    assert body["payment_status"] == "paid"
    assert body["amount_received_naira"] == payable
    print("PASS l: the old PATCH route works as before and does not touch the payment")


async def test_m_cashier_cannot_record_a_payment(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    cashier = await _make_user(db_session, "cashier", None)
    resp = await _pay(client, cashier, order["id"], _standard(order["payable_naira"]))
    assert resp.status_code == 403, resp.text
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    print("PASS m: a cashier gets 403 and nothing is recorded")


async def test_n_model_matches_the_migration():
    table = StorefrontOrder.__table__
    for name in (
        "payment_status",
        "payment_method",
        "amount_received_kobo",
        "paid_at",
        "paid_recorded_by",
        "payment_note",
    ):
        assert name in table.columns, name
    names = {c.name for c in table.constraints}
    assert "ck_storefront_order_status" in names
    assert "ck_storefront_order_payment_status" in names
    assert "ck_storefront_order_payment_method" in names
    assert table.columns["payment_status"].nullable is False
    print("PASS n: the model has the 6 columns and both constraints")
