"""Script test for PATCH7A-COUPON-CREDIT-LINE (server).
Coupon history shows the CHG code of the store credit a coupon was made from.
Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_coupon_credit import _coupon_row, _credit, _issue  # noqa: E402

from api.v1.routers.coupons import coupon_history  # noqa: E402

pytestmark = pytest.mark.asyncio

# coupon_history only reads .role and .branch_id from the user.
ADMIN = SimpleNamespace(role="super_admin", branch_id=None)


async def _issue_and_use(client, db, c, sale, ref):
    resp = await _issue(client, c["headers"], ref, amount=1)
    assert resp.status_code < 300, resp.text
    code = resp.json()["code"]
    row = await _coupon_row(db, code)
    row.status = "redeemed"
    row.redeemed_by = c["user"].id
    row.redeemed_sale_id = sale.id
    row.redeemed_at = datetime.now(timezone.utc)
    await db.commit()
    return code


async def _history_item(db, code):
    rows = await coupon_history(issued_by=None, branch_id=None, limit=200, session=db, current_user=ADMIN)
    found = [r for r in rows if r.code == code]
    assert len(found) == 1, "expected the coupon once in history, got " + str(len(found))
    return found[0]


async def test_a_credit_coupon_shows_chg_code(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    code = await _issue_and_use(client, db_session, c, sale, ch.reference_code)
    item = await _history_item(db_session, code)
    print("[7a] credit coupon", code, "->", item.credit_reference_code, "expected", ch.reference_code)
    assert item.credit_reference_code == ch.reference_code


async def test_b_hand_made_coupon_shows_nothing(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    code = await _issue_and_use(client, db_session, c, sale, None)
    item = await _history_item(db_session, code)
    print("[7a] hand-made coupon", code, "->", item.credit_reference_code)
    assert item.credit_reference_code is None


async def test_c_deleted_credit_shows_nothing_and_no_error(client, db_session):
    c, sale, ch = await _credit(client, db_session)
    code = await _issue_and_use(client, db_session, c, sale, ch.reference_code)
    await db_session.delete(ch)
    await db_session.commit()
    item = await _history_item(db_session, code)
    print("[7a] credit deleted", code, "->", item.credit_reference_code)
    assert item.credit_reference_code is None
