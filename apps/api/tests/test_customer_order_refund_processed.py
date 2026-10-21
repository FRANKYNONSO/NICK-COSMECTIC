"""Script test: marking a pending transfer refund as processed updates the linked order (Patch 3a-2).

Run ONLY against shelf_test (set DATABASE_URL for the run). Every test is
rolled back by the db_session fixture, so no rows are left behind.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from infrastructure.db.models import CustomerRequest, Refund
from test_customer_order_booking import SALES, _fresh, _make_booking, _refunds, _say

pytestmark = pytest.mark.asyncio


async def _cancel_with_transfer(client, c):
    resp = await client.post(
        SALES + "/bookings/" + str(c["booking_id"]) + "/cancel",
        json={
            "reason": "script test cancel",
            "refund_method": "transfer",
            "refund_bank_name": "Test Bank",
            "refund_account_number": "0123456789",
            "refund_account_name": "CO Test Customer",
        },
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text


async def test_h_mark_refund_processed_updates_order(client, db_session):
    c = await _make_booking(client, db_session, 50, "transfer")
    await _cancel_with_transfer(client, c)
    refunds = await _refunds(db_session, c["booking_id"])
    assert len(refunds) == 1 and refunds[0].status == "pending"
    refund_id = refunds[0].id
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    _say("before: order", order.status, "deposit_refunded", order.deposit_refunded, "note", order.resolution_note)
    assert order.resolution_note == "Refund pending (bank transfer)"

    resp = await client.post(
        SALES + "/refunds/" + str(refund_id) + "/mark-processed",
        json={"note": "script test"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    refund = await _fresh(db_session, Refund, refund_id)
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    _say("after: refund", refund.status, "order", order.status, "deposit_refunded", order.deposit_refunded, "note", order.resolution_note)
    assert refund.status == "completed"
    assert order.status == "cancelled"
    assert order.deposit_refunded is True
    assert order.resolution_note is None
    print("PASS h: marking the transfer refund processed -> order shows deposit refunded")


async def test_i_changed_note_is_left_alone(client, db_session):
    c = await _make_booking(client, db_session, 50, "transfer")
    await _cancel_with_transfer(client, c)
    refunds = await _refunds(db_session, c["booking_id"])
    refund_id = refunds[0].id
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    order.resolution_note = "custom note"
    await db_session.commit()

    resp = await client.post(
        SALES + "/refunds/" + str(refund_id) + "/mark-processed",
        json={"note": "script test"},
        headers=c["headers"],
    )
    assert resp.status_code == 200, resp.text
    refund = await _fresh(db_session, Refund, refund_id)
    order = await _fresh(db_session, CustomerRequest, c["order_id"])
    _say("refund", refund.status, "order", order.status, "deposit_refunded", order.deposit_refunded, "note", order.resolution_note)
    assert refund.status == "completed"
    assert not order.deposit_refunded
    assert order.resolution_note == "custom note"
    print("PASS i: an order with a changed note is left alone")
