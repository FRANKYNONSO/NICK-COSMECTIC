"""Branch transfer return tests (RET4): account details, audit steps, history route.

Run ONLY against shelf_test. Every test is rolled back by the db_session fixture.
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_transfer_invoice import _made, _two_branches  # noqa: E402
from test_transfer_returns import _act, _ask  # noqa: E402
from test_goods_receiving import _headers, _user  # noqa: E402

from infrastructure.db.models import Sale  # noqa: E402

pytestmark = pytest.mark.asyncio

WH = "/api/v1/warehouse"
ACCT = {"bank_name": "  Test Bank  ", "account_number": "0123456789", "account_name": "Prestige Academy"}


async def _history(client, h, rid):
    r = await client.get(WH + "/transfer-returns/" + rid + "/history", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_m_receiver_starts_with_account(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r = await client.post(
        WH + "/transfer-returns",
        json={"transfer_id": str(transfer.id), "quantity_pieces": 3, "side": "receiver", **ACCT},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["refund_bank_name"] == "Test Bank"
    assert body["refund_account_number"] == "0123456789"
    assert body["refund_account_name"] == "Prestige Academy"
    ok = await _act(client, h, body["id"], "approve", {"payment_method": "transfer"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["refund_account_number"] == "0123456789"
    steps = await _history(client, h, body["id"])
    assert [s["action"] for s in steps] == ["transfer_return_requested", "transfer_return_approved"]
    assert steps[0]["branch_id"] == str(tb.id)
    assert steps[1]["branch_id"] == str(fb.id)
    assert steps[1]["payment_method"] == "transfer"
    assert steps[0]["quantity_pieces"] == 3 and "user_name" in steps[0]
    print("PASS m: receiver starts with account, steps recorded")


async def test_n_sender_cannot_start_a_return(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r = await client.post(
        WH + "/transfer-returns",
        json={"transfer_id": str(transfer.id), "quantity_pieces": 4, "side": "sender", **ACCT},
        headers=h,
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Only the branch that received the goods can ask to return them."
    # nothing was created: the whole transfer can still be returned
    ok = await _ask(client, h, transfer, 10, "receiver")
    assert ok.status_code == 201, ok.text
    print("PASS n: the sending branch can not start a return")


async def test_o_blank_account_becomes_empty(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r1 = await _ask(client, h, transfer, 2, "receiver")
    assert r1.status_code == 201, r1.text
    assert r1.json()["refund_bank_name"] is None and r1.json()["refund_account_number"] is None
    r2 = await client.post(
        WH + "/transfer-returns",
        json={
            "transfer_id": str(transfer.id),
            "quantity_pieces": 2,
            "side": "receiver",
            "bank_name": "   ",
            "account_number": "",
        },
        headers=h,
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["refund_bank_name"] is None and r2.json()["refund_account_number"] is None
    print("PASS o: no account or blank account becomes empty")


async def test_p_decline_and_cancel_steps(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    r1 = await _ask(client, h, transfer, 2, "receiver")
    rid1 = r1.json()["id"]
    assert (await _act(client, h, rid1, "decline")).status_code == 200
    s1 = await _history(client, h, rid1)
    assert [s["action"] for s in s1] == ["transfer_return_requested", "transfer_return_declined"]
    assert s1[0]["branch_id"] == str(tb.id)
    assert s1[1]["branch_id"] == str(fb.id)
    r2 = await _ask(client, h, transfer, 2, "receiver")
    rid2 = r2.json()["id"]
    assert (await _act(client, h, rid2, "cancel")).status_code == 200
    s2 = await _history(client, h, rid2)
    assert [s["action"] for s in s2] == ["transfer_return_requested", "transfer_return_cancelled"]
    assert s2[1]["branch_id"] == str(tb.id)
    print("PASS p: decline and cancel steps")


async def test_q_too_long_name_and_no_extra_sale(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    bad = await client.post(
        WH + "/transfer-returns",
        json={"transfer_id": str(transfer.id), "quantity_pieces": 1, "side": "receiver", "bank_name": "x" * 121},
        headers=h,
    )
    assert bad.status_code == 422, bad.text
    ok = await client.post(
        WH + "/transfer-returns",
        json={"transfer_id": str(transfer.id), "quantity_pieces": 2, "side": "receiver", **ACCT},
        headers=h,
    )
    assert ok.status_code == 201, ok.text
    done = await _act(client, h, ok.json()["id"], "approve", {"payment_method": "cash"})
    assert done.status_code == 200, done.text
    n_sale = await db_session.scalar(select(func.count()).select_from(Sale).where(Sale.channel == "transfer"))
    assert n_sale == 1
    print("PASS q: validation, and no new sale row")


async def test_r_history_unknown_id(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    r = await client.get(WH + "/transfer-returns/" + str(uuid.uuid4()) + "/history", headers=h)
    assert r.status_code == 404, r.text
    print("PASS r: unknown id gives 404")


async def test_s_list_needs_manage_inventory(client, db_session):
    fb, tb, product, h = await _two_branches(client, db_session)
    transfer, _, _ = await _made(client, db_session, h, fb, tb, product, pieces=10)
    made = await client.post(
        WH + "/transfer-returns",
        json={"transfer_id": str(transfer.id), "quantity_pieces": 2, "side": "receiver", **ACCT},
        headers=h,
    )
    assert made.status_code == 201, made.text
    staff = await _user(db_session, "staff", tb.id)
    manager = await _user(db_session, "branch_manager", tb.id)
    await db_session.commit()
    denied = await client.get(WH + "/transfer-returns", headers=_headers(staff))
    assert denied.status_code == 403, denied.text
    ok = await client.get(WH + "/transfer-returns", headers=_headers(manager))
    assert ok.status_code == 200, ok.text
    mine = [x for x in ok.json() if x["id"] == made.json()["id"]]
    assert len(mine) == 1 and mine[0]["refund_account_number"] == "0123456789"
    print("PASS s: the return list needs manage_inventory")
