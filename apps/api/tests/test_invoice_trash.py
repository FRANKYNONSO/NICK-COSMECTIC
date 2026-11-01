"""Invoice Trash tests (TRASH1).

Run ONLY against shelf_test, after the trash1invoice01 migration was applied to it.
Every test is rolled back by the db_session fixture.
"""
import os
import sys
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_goods_receiving import _branch, _headers, _user  # noqa: E402

from infrastructure.db.models import (  # noqa: E402
    AuditLog,
    GoodsReceipt,
    MetricSnapshot,
    PurchaseInvoice,
    PurchaseInvoiceFinalDeletion,
    PurchaseInvoiceLineItem,
)

pytestmark = pytest.mark.asyncio

PI = "/api/v1/purchase-invoices"
TOTAL_PATH = "/api/v1/purchase-invoices/total-invested"
# 100000 + 5000 + 2000 + 1000 kobo = 1080.00 naira landed cost
LANDED_NAIRA = 1080.0


async def _invoice(db, branch, user, supplier="TRASH TEST SUPPLIER", amount=100000):
    inv = PurchaseInvoice(
        branch_id=branch.id,
        supplier_name=supplier,
        status="completed",
        invoice_date=date.today(),
        amount_kobo=amount,
        waybill_cost_kobo=5000,
        transit_cost_kobo=2000,
        handling_cost_kobo=1000,
        created_by=user.id,
    )
    inv.line_items.append(
        PurchaseInvoiceLineItem(
            position=0,
            item_name="TRASH TEST ITEM",
            quantity=2,
            unit_label="pieces",
            rate_kobo=amount // 2,
            line_total_kobo=amount,
        )
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv, attribute_names=["branch", "creator", "line_items"])
    return inv


async def _total(client, h, branch_id):
    resp = await client.get(TOTAL_PATH, params={"branch_id": str(branch_id)}, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["total_landed_cost_naira"]


async def _list_ids(client, h, branch_id):
    resp = await client.get(PI, params={"branch_id": str(branch_id)}, headers=h)
    assert resp.status_code == 200, resp.text
    return {row["id"] for row in resp.json()}


async def _trash(client, h, inv, reason="entered twice"):
    return await client.post(f"{PI}/{inv.id}/trash", json={"reason": reason}, headers=h)


async def _count(db, model, *conds):
    return (await db.execute(select(func.count()).select_from(model).where(*conds))).scalar_one()


async def _setup(db):
    branch = await _branch(db)
    admin = await _user(db, "super_admin", None)
    return branch, admin, _headers(admin)


async def test_a_delete_takes_amount_out_and_restore_puts_it_back(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    assert await _total(client, h, branch.id) == LANDED_NAIRA
    assert str(inv.id) in await _list_ids(client, h, branch.id)

    r = await _trash(client, h, inv)
    assert r.status_code == 200, r.text
    assert r.json()["trash_state"] == "trashed"
    assert await _total(client, h, branch.id) == 0
    assert str(inv.id) not in await _list_ids(client, h, branch.id)

    tr = await client.get(PI + "/trash", params={"branch_id": str(branch.id)}, headers=h)
    assert tr.status_code == 200, tr.text
    rows = tr.json()
    assert [x["id"] for x in rows] == [str(inv.id)]
    assert rows[0]["amount_naira"] == LANDED_NAIRA
    assert rows[0]["trashed_by_name"] == admin.full_name
    assert rows[0]["trash_reason"] == "entered twice"
    assert rows[0]["invoice"]["supplier_name"] == "TRASH TEST SUPPLIER"
    assert rows[0]["invoice"]["items"][0]["item_name"] == "TRASH TEST ITEM"

    r = await client.post(f"{PI}/{inv.id}/restore", json={}, headers=h)
    assert r.status_code == 200, r.text
    assert await _total(client, h, branch.id) == LANDED_NAIRA
    assert str(inv.id) in await _list_ids(client, h, branch.id)
    stored = await db_session.get(PurchaseInvoice, inv.id)
    assert stored.trash_state is None and stored.trashed_by is None and stored.trash_reason is None
    assert stored.amount_kobo == 100000 and len(stored.line_items) == 1
    print("PASS a: delete and restore move the total")


async def test_b_reason_is_required(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    for bad in ("", "   "):
        r = await _trash(client, h, inv, reason=bad)
        assert r.status_code == 422, (bad, r.text)
    r = await client.post(f"{PI}/{inv.id}/trash", json={}, headers=h)
    assert r.status_code == 422, r.text
    assert await _total(client, h, branch.id) == LANDED_NAIRA
    print("PASS b: reason required")


async def test_c_second_delete_and_restore_of_active_are_refused(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    r = await client.post(f"{PI}/{inv.id}/restore", json={}, headers=h)
    assert r.status_code == 400 and "not in the Trash" in r.json()["detail"], r.text
    assert (await _trash(client, h, inv)).status_code == 200
    r = await _trash(client, h, inv)
    assert r.status_code == 400 and "already in the Trash" in r.json()["detail"], r.text
    r = await client.post(f"{PI}/{uuid.uuid4()}/trash", json={"reason": "x"}, headers=h)
    assert r.status_code == 404, r.text
    print("PASS c: state guards")


async def test_d_branch_manager_own_branch_only(client, db_session):
    branch, admin, h = await _setup(db_session)
    other = await _branch(db_session)
    mgr = await _user(db_session, "branch_manager", branch.id)
    hm = _headers(mgr)
    mine = await _invoice(db_session, branch, admin)
    theirs = await _invoice(db_session, other, admin)

    r = await _trash(client, hm, theirs)
    assert r.status_code == 403 and "own branch" in r.json()["detail"], r.text
    assert (await _trash(client, hm, mine)).status_code == 200

    rows = (await client.get(PI + "/trash", headers=hm)).json()
    assert [x["id"] for x in rows] == [str(mine.id)]
    r = await client.post(f"{PI}/{theirs.id}/restore", json={}, headers=hm)
    assert r.status_code == 403, r.text
    print("PASS d: manager scoped to own branch")


async def test_e_manager_permanent_delete_only_sends_for_review(client, db_session):
    branch, admin, h = await _setup(db_session)
    mgr = await _user(db_session, "branch_manager", branch.id)
    hm = _headers(mgr)
    inv = await _invoice(db_session, branch, admin)

    # must be in the Trash first
    r = await client.post(f"{PI}/{inv.id}/delete-permanently", json={"reason": "x"}, headers=hm)
    assert r.status_code == 400 and "Delete the invoice first" in r.json()["detail"], r.text

    assert (await _trash(client, hm, inv)).status_code == 200
    r = await client.post(f"{PI}/{inv.id}/delete-permanently", json={"reason": "duplicate"}, headers=hm)
    assert r.status_code == 200, r.text
    assert r.json()["trash_state"] == "pending_final"
    assert await _count(db_session, PurchaseInvoice, PurchaseInvoice.id == inv.id) == 1
    assert await _count(db_session, PurchaseInvoiceFinalDeletion) == 0

    # gone from the manager's Trash, no way for the manager to restore or push it again
    assert (await client.get(PI + "/trash", headers=hm)).json() == []
    r = await client.post(f"{PI}/{inv.id}/restore", json={}, headers=hm)
    assert r.status_code == 403 and "super admin" in r.json()["detail"], r.text
    r = await client.post(f"{PI}/{inv.id}/delete-permanently", json={"reason": "again"}, headers=hm)
    assert r.status_code == 400 and "waiting for a super admin" in r.json()["detail"], r.text
    # and the amount stays out of the totals
    assert await _total(client, h, branch.id) == 0

    # the super admin sees it in the review list; the manager may not open that list
    rev = await client.get(PI + "/trash/review", headers=h)
    assert rev.status_code == 200, rev.text
    row = [x for x in rev.json() if x["id"] == str(inv.id)][0]
    assert row["trash_state"] == "pending_final"
    assert row["review_sent_by_name"] == mgr.full_name and row["review_reason"] == "duplicate"
    assert (await client.get(PI + "/trash/review", headers=hm)).status_code == 403
    assert (await client.get(PI + "/final-deletions", headers=hm)).status_code == 403
    print("PASS e: manager permanent delete goes to review")


async def test_f_super_admin_final_delete_writes_ledger_and_no_audit_row(client, db_session):
    branch, admin, h = await _setup(db_session)
    admin2 = await _user(db_session, "super_admin", None)
    mgr = await _user(db_session, "branch_manager", branch.id)
    hm = _headers(mgr)
    inv = await _invoice(db_session, branch, admin)
    inv_id = inv.id

    assert (await _trash(client, hm, inv, reason="entered twice")).status_code == 200
    r = await client.post(f"{PI}/{inv_id}/delete-permanently", json={"reason": "duplicate"}, headers=hm)
    assert r.status_code == 200, r.text
    audit_before = await _count(db_session, AuditLog, AuditLog.entity_id == inv_id)
    assert audit_before == 2  # trash + send_for_review

    r = await client.post(f"{PI}/{inv_id}/delete-permanently", json={"reason": "confirmed duplicate"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Invoice deleted permanently."

    # the invoice and its lines are gone
    assert await _count(db_session, PurchaseInvoice, PurchaseInvoice.id == inv_id) == 0
    assert await _count(db_session, PurchaseInvoiceLineItem, PurchaseInvoiceLineItem.invoice_id == inv_id) == 0
    # the final step wrote NO audit row
    assert await _count(db_session, AuditLog, AuditLog.entity_id == inv_id) == audit_before
    # but the ledger has the full story, visible to ANOTHER super admin
    led = await client.get(PI + "/final-deletions", headers=_headers(admin2))
    assert led.status_code == 200, led.text
    row = [x for x in led.json() if x["invoice_id"] == str(inv_id)][0]
    assert row["deleted_by_name"] == admin.full_name
    assert row["deleted_reason"] == "confirmed duplicate"
    assert row["trashed_by_name"] == mgr.full_name and row["trash_reason"] == "entered twice"
    assert row["review_sent_by_name"] == mgr.full_name and row["review_reason"] == "duplicate"
    assert row["amount_naira"] == LANDED_NAIRA
    assert row["supplier_name"] == "TRASH TEST SUPPLIER"
    assert len(row["snapshot"]["items"]) == 1 and row["snapshot"]["items"][0]["item_name"] == "TRASH TEST ITEM"

    # the earlier steps stay in the normal record; the final one is not there
    assert (await client.get(PI + "/trash/activity", headers=hm)).status_code == 403  # managers do not see Activity
    act = await client.get(PI + "/trash/activity", headers=h)
    assert act.status_code == 200, act.text
    labels = [e["label"] for e in act.json() if e["invoice_id"] == str(inv_id)]
    assert sorted(labels) == ["Deleted", "Sent for permanent deletion"], labels
    print("PASS f: final delete is ledger-only")


async def test_g_super_admin_can_delete_for_good_straight_from_trash_and_restore_from_review(client, db_session):
    branch, admin, h = await _setup(db_session)
    mgr = await _user(db_session, "branch_manager", branch.id)
    hm = _headers(mgr)
    a = await _invoice(db_session, branch, admin, supplier="SUP A")
    b = await _invoice(db_session, branch, admin, supplier="SUP B")

    assert (await _trash(client, h, a)).status_code == 200
    r = await client.post(f"{PI}/{a.id}/delete-permanently", json={"reason": "wrong"}, headers=h)
    assert r.status_code == 200, r.text
    assert await _count(db_session, PurchaseInvoice, PurchaseInvoice.id == a.id) == 0
    assert await _count(db_session, PurchaseInvoiceFinalDeletion, PurchaseInvoiceFinalDeletion.supplier_name == "SUP A") == 1

    assert (await _trash(client, hm, b)).status_code == 200
    assert (await client.post(f"{PI}/{b.id}/delete-permanently", json={"reason": "x"}, headers=hm)).status_code == 200
    r = await client.post(f"{PI}/{b.id}/restore", json={"reason": "kept it"}, headers=h)
    assert r.status_code == 200, r.text
    assert str(b.id) in await _list_ids(client, h, branch.id)
    assert await _total(client, h, branch.id) == LANDED_NAIRA
    print("PASS g: super admin paths")


async def test_h_used_invoice_cannot_be_deleted(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    db_session.add(GoodsReceipt(branch_id=branch.id, invoice_id=inv.id))
    await db_session.commit()
    r = await _trash(client, h, inv)
    assert r.status_code == 400, r.text
    assert "already used" in r.json()["detail"] and "Receive Goods receipt" in r.json()["detail"]
    assert await _total(client, h, branch.id) == LANDED_NAIRA

    other = await _invoice(db_session, branch, admin, supplier="TRANSFER SUP")
    other.source = "branch_transfer"
    await db_session.commit()
    r = await _trash(client, h, other)
    assert r.status_code == 400 and "branch transfer" in r.json()["detail"], r.text
    print("PASS h: used invoices are blocked")


async def test_i_trashed_invoice_cannot_be_edited(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    assert (await _trash(client, h, inv)).status_code == 200
    r = await client.patch(f"{PI}/{inv.id}", json={"description": "x"}, headers=h)
    assert r.status_code == 400 and "in the Trash" in r.json()["detail"], r.text
    print("PASS i: no edit while in the Trash")


async def test_j_history_is_super_admin_only(client, db_session):
    branch, admin, h = await _setup(db_session)
    other = await _branch(db_session)
    mgr = await _user(db_session, "branch_manager", branch.id)
    hm = _headers(mgr)
    mine = await _invoice(db_session, branch, admin)
    theirs = await _invoice(db_session, other, admin)
    assert (await _trash(client, h, mine)).status_code == 200
    assert (await _trash(client, h, theirs)).status_code == 200

    # A branch manager may not read any invoice's history, even in their own branch.
    assert (await client.get(f"{PI}/{mine.id}/history", headers=hm)).status_code == 403
    assert (await client.get(f"{PI}/{theirs.id}/history", headers=hm)).status_code == 403

    # A super admin still sees the full history of both.
    hist = await client.get(f"{PI}/{mine.id}/history", headers=h)
    assert hist.status_code == 200, hist.text
    assert [e["label"] for e in hist.json()] == ["Deleted"]
    assert hist.json()[0]["user_name"] == admin.full_name and hist.json()[0]["reason"] == "entered twice"
    hist2 = await client.get(f"{PI}/{theirs.id}/history", headers=h)
    assert hist2.status_code == 200 and [e["label"] for e in hist2.json()] == ["Deleted"]

    assert (await client.get(PI + "/trash/activity", headers=hm)).status_code == 403
    all_act = (await client.get(PI + "/trash/activity", headers=h)).json()
    assert {str(mine.id), str(theirs.id)} <= {e["invoice_id"] for e in all_act}
    print("PASS j: history is super admin only")


async def test_k_stored_inventory_investment_follows_delete_and_restore(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    db_session.add(
        MetricSnapshot(
            branch_id=branch.id,
            metric_type="inventory_investment",
            period_start=inv.invoice_date,
            value=LANDED_NAIRA,
        )
    )
    db_session.add(
        MetricSnapshot(branch_id=branch.id, metric_type="revenue", period_start=inv.invoice_date, value=777)
    )
    await db_session.commit()

    async def stored(metric):
        return float(
            (
                await db_session.execute(
                    select(MetricSnapshot.value).where(
                        MetricSnapshot.branch_id == branch.id,
                        MetricSnapshot.metric_type == metric,
                        MetricSnapshot.period_start == inv.invoice_date,
                    )
                )
            ).scalar_one()
        )

    assert (await _trash(client, h, inv)).status_code == 200
    assert await stored("inventory_investment") == 0
    assert await stored("revenue") == 777  # nothing else is touched
    assert (await client.post(f"{PI}/{inv.id}/restore", json={}, headers=h)).status_code == 200
    assert await stored("inventory_investment") == LANDED_NAIRA
    print("PASS k: stored daily figure follows")


async def test_l_trashed_invoice_is_hidden_from_pushable_items(client, db_session):
    branch, admin, h = await _setup(db_session)
    inv = await _invoice(db_session, branch, admin)
    url = "/api/v1/warehouse/pushable-invoice-items"

    def ids(resp):
        return {str(row.get("invoice_id")) for row in resp.json()}

    before = await client.get(url, params={"branch_id": str(branch.id)}, headers=h)
    assert before.status_code == 200, before.text
    assert str(inv.id) in ids(before), before.text
    assert (await _trash(client, h, inv)).status_code == 200
    after = await client.get(url, params={"branch_id": str(branch.id)}, headers=h)
    assert after.status_code == 200, after.text
    assert str(inv.id) not in ids(after)
    print("PASS l: hidden from the warehouse push list")
