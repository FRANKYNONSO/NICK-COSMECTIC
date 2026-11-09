# Tests for patch 19d: undoing a recorded payment on a website order.
# PATCH19D-UNDO-PAYMENT
# Run ONLY against shelf_test. Every test is rolled back.
import os
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _say  # noqa: E402
from test_website_order_cancel import _orders_url, _patch, _place, _prepare, _remaining  # noqa: E402
from test_website_order_payment import (  # noqa: E402
    _branch_of,
    _make_user,
    _new_branch,
    _order_row,
    _pay,
    _payment_events,
    _standard,
)

from infrastructure.db.models import AuditLog  # noqa: E402

pytestmark = pytest.mark.asyncio

REASON = "Wrong amount typed"


def _undo_url(order_id):
    return _orders_url() + "/" + order_id + "/payment/undo"


async def _undo(client, headers, order_id, payload):
    resp = await client.post(_undo_url(order_id), json=payload, headers=headers)
    _say("undo", payload, resp.status_code, resp.text[:300])
    return resp


async def _undo_events(db, order_id):
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.entity_id == uuid.UUID(order_id),
            AuditLog.action == "website_order_payment_undone",
        )
    )
    return list(result.scalars().all())


async def _paid_order(client, db_session, qty=3):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, qty)
    paid = await _pay(client, headers, order["id"], _standard(order["payable_naira"]))
    assert paid.status_code == 200, paid.text
    return order, headers, batch_ids


async def test_a_undo_puts_the_order_back_to_unpaid(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    resp = await _undo(client, headers, order["id"], {"reason": "  " + REASON + "  "})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_status"] == "unpaid"
    assert body["payment_method"] is None
    assert body["amount_received_naira"] is None
    assert body["paid_at"] is None
    assert body["paid_recorded_by"] is None
    assert body["payment_note"] is None
    assert body["status"] == "new"
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "unpaid"
    assert row.amount_received_kobo is None
    assert row.paid_at is None
    assert await _remaining(db_session, batch_ids[0]) == 7, "undoing a payment must not change stock"
    print("PASS a: undo puts every payment field back and changes nothing else")


async def test_b_payment_can_be_recorded_again_after_undo(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    undone = await _undo(client, headers, order["id"], {"reason": REASON})
    assert undone.status_code == 200, undone.text
    again = await _pay(client, headers, order["id"], {"method": "cash_on_delivery", "amount_naira": 2000.0})
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["payment_status"] == "paid"
    assert body["payment_method"] == "cash_on_delivery"
    assert body["amount_received_naira"] == 2000.0
    print("PASS b: after an undo the payment can be recorded again with new values")


async def test_c_unpaid_order_cannot_be_undone(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    order, phone = await _place(client, headers, product_id, 2)
    resp = await _undo(client, headers, order["id"], {"reason": REASON})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "This order is not marked as paid."
    assert len(await _undo_events(db_session, order["id"])) == 0
    print("PASS c: an unpaid order gives 409 and writes no audit row")


async def test_d_cancelled_order_payment_cannot_be_changed(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    cancel = await _patch(client, headers, order["id"], {"status": "cancelled"})
    assert cancel.status_code == 200, cancel.text
    resp = await _undo(client, headers, order["id"], {"reason": REASON})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "A cancelled order's payment cannot be changed."
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "paid"
    assert len(await _undo_events(db_session, order["id"])) == 0
    print("PASS d: a cancelled order keeps its payment record (409)")


async def test_e_bad_reasons_are_rejected(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    empty = await _undo(client, headers, order["id"], {"reason": ""})
    assert empty.status_code == 422, empty.text
    spaces = await _undo(client, headers, order["id"], {"reason": "    "})
    assert spaces.status_code == 422, spaces.text
    assert spaces.json()["detail"] == "Write why the payment is being undone."
    missing = await _undo(client, headers, order["id"], {})
    assert missing.status_code == 422, missing.text
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "paid"
    assert len(await _undo_events(db_session, order["id"])) == 0
    print("PASS e: an empty, blank or missing reason gives 422 and the payment stays")


async def test_f_cashier_cannot_undo(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    cashier = await _make_user(db_session, "cashier", None)
    resp = await _undo(client, cashier, order["id"], {"reason": REASON})
    assert resp.status_code == 403, resp.text
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "paid"
    print("PASS f: a cashier gets 403 and the payment stays")


async def test_g_manager_of_another_branch_gets_404(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    other_id = await _new_branch(db_session)
    manager = await _make_user(db_session, "branch_manager", other_id)
    resp = await _undo(client, manager, order["id"], {"reason": REASON})
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Order not found."
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "paid"
    print("PASS g: a branch manager with no share in the order gets 404")


async def test_h_manager_with_a_share_can_undo(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    share_branch = await _branch_of(db_session, batch_ids[0])
    manager = await _make_user(db_session, "branch_manager", share_branch)
    resp = await _undo(client, manager, order["id"], {"reason": REASON})
    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_status"] == "unpaid"
    print("PASS h: a branch manager whose branch has a share can undo the payment")


async def test_i_audit_rows_keep_the_history(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    resp = await _undo(client, headers, order["id"], {"reason": REASON})
    assert resp.status_code == 200, resp.text
    assert len(await _payment_events(db_session, order["id"])) == 1
    undone = await _undo_events(db_session, order["id"])
    assert len(undone) == 1
    blob = " ".join(str(v) for v in vars(undone[0]).values())
    assert REASON in blob, blob
    print("PASS i: the recorded row stays and one undone row holds the reason")


async def test_j_order_detail_shows_unpaid_after_undo(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    url = _orders_url() + "/" + order["id"]
    before = await client.get(url, headers=headers)
    assert before.json()["payment_status"] == "paid"
    resp = await _undo(client, headers, order["id"], {"reason": REASON})
    assert resp.status_code == 200, resp.text
    after = await client.get(url, headers=headers)
    assert after.status_code == 200, after.text
    assert after.json()["payment_status"] == "unpaid"
    assert after.json()["amount_received_naira"] is None
    print("PASS j: the order detail shows paid before and unpaid after")


async def test_k_too_long_reason_is_rejected(client, db_session):
    order, headers, batch_ids = await _paid_order(client, db_session)
    resp = await _undo(client, headers, order["id"], {"reason": "x" * 501})
    assert resp.status_code == 422, resp.text
    row = await _order_row(db_session, order["id"])
    assert row.payment_status == "paid"
    print("PASS k: a reason longer than 500 characters gives 422")


async def test_l_unknown_order_gets_404(client, db_session):
    product_id, headers, batch_ids = await _prepare(db_session, [10])
    resp = await _undo(client, headers, str(uuid.uuid4()), {"reason": REASON})
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Order not found."
    print("PASS l: an unknown order gives 404")
