"""Script test for Patch 6h: the booking made when a customer order arrives gets the
short note "Order: <item> - <customer>". Run ONLY against shelf_test. Rolled back.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_customer_order_booking import _fresh, _make_booking  # noqa: E402

from infrastructure.db.models import Sale  # noqa: E402

pytestmark = pytest.mark.asyncio


async def test_a_booking_note_is_the_short_text(client, db_session):
    c = await _make_booking(client, db_session, 50, "cash")
    sale = await _fresh(db_session, Sale, c["booking_id"])
    expected = "Order: " + c["product"].name + " - CO Test Customer"
    print("EVIDENCE note", repr(sale.notes))
    assert sale.notes == expected
    assert "Customer order:" not in (sale.notes or "")
    print("PASS a: the booking note is the short text")
