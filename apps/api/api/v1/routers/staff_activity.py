"""Staff Activity report (read-only).

One place to see what each staff member has done. Adds NO new table and
changes no existing behaviour: it only reads records the system already keeps
(sales, refunds, stock adjustments/losses, credit entries, shifts, audit_logs)
and merges them into a per-staff summary and a single timeline.

Access: same permission as the other manager-level reports (view_all_reports).
Super admin sees every branch (or one branch when branch_id is given); anyone
else is locked to their own branch.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission
from infrastructure.db.models import (
    AuditLog,
    Branch,
    CreditEntry,
    Refund,
    Sale,
    Shift,
    StockAdjustment,
    User,
)

router = APIRouter(prefix="/staff-activity", tags=["staff-activity"])

_WAT = timezone(timedelta(hours=1))
_MAX_PER_SOURCE = 3000
_MAX_SPAN_DAYS = 366
_ALL_TYPES = {
    "sale", "booking", "transfer", "refund", "loss",
    "credit", "shift_open", "shift_close", "audit",
}


def _naira(kobo):
    return "\u20a6{:,.2f}".format((kobo or 0) / 100)


def _window(date_from, date_to):
    today = datetime.now(_WAT).date()
    if date_from is None and date_to is None:
        d1 = d2 = today
    elif date_from is None:
        d1 = d2 = date_to
    elif date_to is None:
        d1, d2 = date_from, today
    else:
        d1, d2 = date_from, date_to
    if d2 < d1:
        raise HTTPException(status_code=400, detail="The 'To' date is before the 'From' date")
    if (d2 - d1).days > _MAX_SPAN_DAYS:
        raise HTTPException(status_code=400, detail="Please choose a range of one year or less")
    start = datetime.combine(d1, time.min, tzinfo=_WAT)
    end = datetime.combine(d2 + timedelta(days=1), time.min, tzinfo=_WAT)
    return d1, d2, start, end


def _scope(current_user, branch_id):
    if current_user.role != "super_admin":
        if current_user.branch_id is None:
            raise HTTPException(status_code=403, detail="Your account is not assigned to a branch")
        return current_user.branch_id
    return branch_id


def _ev(at, uid, bid, typ, summary, amount=None, ref=None, status=None, auto=False):
    return {
        "at": at, "user_id": uid, "branch_id": bid, "type": typ,
        "summary": summary, "amount_kobo": amount, "ref_id": ref,
        "status": status, "auto": auto,
    }


async def _context(session):
    users = {u.id: u for u in (await session.execute(select(User))).scalars().all()}
    branches = {b.id: b.name for b in (await session.execute(select(Branch))).scalars().all()}
    return users, branches


async def _collect(session, start, end, scope, staff_id, types, users):
    events = []
    truncated = False

    def wants(*names):
        return types is None or any(n in types for n in names)

    # ---- sales, bookings and branch transfers rung up by a staff member
    if wants("sale", "booking", "transfer"):
        q = select(Sale).where(
            Sale.served_by.is_not(None), Sale.created_at >= start, Sale.created_at < end
        )
        if scope:
            q = q.where(Sale.branch_id == scope)
        if staff_id:
            q = q.where(Sale.served_by == staff_id)
        rows = (await session.execute(
            q.order_by(Sale.created_at.desc()).limit(_MAX_PER_SOURCE))).scalars().all()
        truncated = truncated or len(rows) >= _MAX_PER_SOURCE
        for s in rows:
            if s.status == "booking":
                typ, label = "booking", "Booking / deposit"
            elif s.channel == "transfer":
                typ, label = "transfer", "Stock transfer"
            else:
                typ, label = "sale", "{} sale".format((s.channel or "").upper())
            if types is not None and typ not in types:
                continue
            text = "{} - paid by {} - {}".format(label, s.payment_method or "-", s.status)
            events.append(_ev(s.created_at, s.served_by, s.branch_id, typ, text,
                              s.total_kobo, s.id, s.status))

    # ---- refunds and exchanges processed
    if wants("refund"):
        q = (
            select(Refund, Sale.branch_id)
            .join(Sale, Sale.id == Refund.sale_id)
            .where(Refund.processed_by.is_not(None),
                   Refund.created_at >= start, Refund.created_at < end)
        )
        if scope:
            q = q.where(Sale.branch_id == scope)
        if staff_id:
            q = q.where(Refund.processed_by == staff_id)
        rows = (await session.execute(
            q.order_by(Refund.created_at.desc()).limit(_MAX_PER_SOURCE))).all()
        truncated = truncated or len(rows) >= _MAX_PER_SOURCE
        for r, bid in rows:
            text = "{} - {}".format((r.action or "refund").capitalize(), r.status)
            if r.reason:
                text += " - " + r.reason[:120]
            events.append(_ev(r.created_at, r.processed_by, bid, "refund", text,
                              r.amount_kobo, r.id, r.status))

    # ---- losses / stock adjustments
    if wants("loss"):
        q = select(StockAdjustment).where(
            StockAdjustment.adjusted_by.is_not(None),
            StockAdjustment.created_at >= start, StockAdjustment.created_at < end,
        )
        if scope:
            q = q.where(StockAdjustment.branch_id == scope)
        if staff_id:
            q = q.where(StockAdjustment.adjusted_by == staff_id)
        rows = (await session.execute(
            q.order_by(StockAdjustment.created_at.desc()).limit(_MAX_PER_SOURCE))).scalars().all()
        truncated = truncated or len(rows) >= _MAX_PER_SOURCE
        for a in rows:
            text = "{} - qty {} - {}".format(a.reason, a.quantity_delta, a.status)
            if a.notes:
                text += " - " + a.notes[:120]
            events.append(_ev(a.created_at, a.adjusted_by, a.branch_id, "loss", text,
                              a.value_kobo, a.id, a.status))

    # ---- credit entries opened
    if wants("credit"):
        q = select(CreditEntry).where(
            CreditEntry.opened_at >= start, CreditEntry.opened_at < end
        )
        if scope:
            q = q.where(CreditEntry.branch_id == scope)
        if staff_id:
            q = q.where(CreditEntry.opened_by == staff_id)
        rows = (await session.execute(
            q.order_by(CreditEntry.opened_at.desc()).limit(_MAX_PER_SOURCE))).scalars().all()
        truncated = truncated or len(rows) >= _MAX_PER_SOURCE
        for c in rows:
            text = "Credit opened for {} - {}".format(c.customer_full_name, c.status)
            events.append(_ev(c.opened_at, c.opened_by, c.branch_id, "credit", text,
                              c.amount_kobo, c.id, c.status))

    # ---- shifts opened / closed
    if wants("shift_open", "shift_close"):
        q = select(Shift).where(or_(
            and_(Shift.opened_at >= start, Shift.opened_at < end),
            and_(Shift.closed_at.is_not(None), Shift.closed_at >= start, Shift.closed_at < end),
        ))
        if scope:
            q = q.where(Shift.branch_id == scope)
        if staff_id:
            q = q.where(Shift.opened_by == staff_id)
        rows = (await session.execute(
            q.order_by(Shift.opened_at.desc()).limit(_MAX_PER_SOURCE))).scalars().all()
        truncated = truncated or len(rows) >= _MAX_PER_SOURCE
        for s in rows:
            if (types is None or "shift_open" in types) and s.opened_at and start <= s.opened_at < end:
                text = "Opened shift - starting float {}".format(_naira(s.opening_float_kobo))
                events.append(_ev(s.opened_at, s.opened_by, s.branch_id, "shift_open", text,
                                  s.opening_float_kobo, s.id, s.status))
            if (types is None or "shift_close" in types) and s.closed_at and start <= s.closed_at < end:
                if s.auto_closed:
                    text, amount = "Shift closed automatically by the system (not closed by the staff member)", None
                else:
                    text = "Closed shift - counted {}".format(_naira(s.closing_counted_kobo))
                    if s.late_close:
                        text += " - late"
                    amount = s.closing_counted_kobo
                events.append(_ev(s.closed_at, s.opened_by, s.branch_id, "shift_close", text,
                                  amount, s.id, s.status, bool(s.auto_closed)))

    # ---- everything else the system already audits (price changes, goods
    # receiving, coupons, credit payments, profile changes...). Refund events
    # are skipped here because the refunds table above is the real record.
    if wants("audit"):
        q = select(AuditLog).where(
            AuditLog.user_id.is_not(None),
            AuditLog.created_at >= start, AuditLog.created_at < end,
            ~AuditLog.action.ilike("%refund%"),
        )
        if staff_id:
            q = q.where(AuditLog.user_id == staff_id)
        if scope:
            q = q.join(User, User.id == AuditLog.user_id).where(or_(
                User.branch_id == scope,
                AuditLog.log_metadata["branch_id"].astext == str(scope),
            ))
        rows = (await session.execute(
            q.order_by(AuditLog.created_at.desc()).limit(_MAX_PER_SOURCE))).scalars().all()
        truncated = truncated or len(rows) >= _MAX_PER_SOURCE
        for e in rows:
            text = (e.action or "").replace("_", " ").capitalize()
            if e.entity_type:
                text += " ({})".format(e.entity_type.replace("_", " "))
            meta = e.log_metadata if isinstance(e.log_metadata, dict) else {}
            reason = meta.get("reason")
            if isinstance(reason, str) and reason:
                text += " - " + reason[:120]
            u = users.get(e.user_id)
            events.append(_ev(e.created_at, e.user_id, u.branch_id if u else None,
                              "audit", text, None, e.entity_id))

    events = [e for e in events if e["at"] is not None]
    return events, truncated


_SUM_KEYS = [
    "sales_count", "sales_total_kobo", "bookings_count", "refunds_count",
    "refunds_total_kobo", "credits_count", "credits_total_kobo", "losses_count",
    "losses_value_kobo", "shifts_opened", "shifts_auto_closed", "other_actions",
    "total_actions",
]


@router.get("/summary")
async def staff_activity_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
):
    d1, d2, start, end = _window(date_from, date_to)
    scope = _scope(current_user, branch_id)
    users, branches = await _context(session)
    events, truncated = await _collect(session, start, end, scope, None, None, users)

    rows = {}

    def row_for(uid):
        if uid not in rows:
            u = users.get(uid)
            if u is None:
                return None
            r = {
                "user_id": u.id, "name": u.full_name, "role": u.role,
                "employee_id": u.employee_id, "branch_id": u.branch_id,
                "branch_name": branches.get(u.branch_id), "is_active": u.is_active,
                "last_login_at": u.last_login_at, "last_activity_at": None,
            }
            for k in _SUM_KEYS:
                r[k] = 0
            rows[uid] = r
        return rows[uid]

    # list every active staff member in scope, even with no activity
    for uid, u in users.items():
        if u.is_active and (scope is None or u.branch_id == scope):
            row_for(uid)

    for ev in events:
        r = row_for(ev["user_id"])
        if r is None:
            continue
        t = ev["type"]
        amt = ev["amount_kobo"] or 0
        r["total_actions"] += 1
        if r["last_activity_at"] is None or ev["at"] > r["last_activity_at"]:
            r["last_activity_at"] = ev["at"]
        if t == "sale":
            if ev["status"] != "cancelled":
                r["sales_count"] += 1
            if ev["status"] in ("completed", "partially_refunded"):
                r["sales_total_kobo"] += amt
        elif t == "booking":
            r["bookings_count"] += 1
        elif t == "refund":
            r["refunds_count"] += 1
            r["refunds_total_kobo"] += amt
        elif t == "credit":
            r["credits_count"] += 1
            r["credits_total_kobo"] += amt
        elif t == "loss":
            r["losses_count"] += 1
            r["losses_value_kobo"] += amt
        elif t == "shift_open":
            r["shifts_opened"] += 1
        elif t == "shift_close":
            if ev["auto"]:
                r["shifts_auto_closed"] += 1
        elif t == "audit":
            r["other_actions"] += 1

    out = sorted(rows.values(), key=lambda r: (-r["total_actions"], (r["name"] or "").lower()))
    totals = {k: sum(r[k] for r in out) for k in _SUM_KEYS}
    return {
        "date_from": d1, "date_to": d2, "truncated": truncated,
        "totals": totals, "rows": out,
    }


@router.get("")
async def staff_activity_timeline(
    date_from: date | None = None,
    date_to: date | None = None,
    branch_id: uuid.UUID | None = None,
    staff_id: uuid.UUID | None = None,
    action_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
):
    if action_type and action_type not in _ALL_TYPES:
        raise HTTPException(status_code=400, detail="Unknown action type")
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    d1, d2, start, end = _window(date_from, date_to)
    scope = _scope(current_user, branch_id)
    users, branches = await _context(session)
    types = {action_type} if action_type else None
    events, truncated = await _collect(session, start, end, scope, staff_id, types, users)
    events.sort(key=lambda e: e["at"], reverse=True)

    items = []
    for e in events[offset: offset + limit]:
        u = users.get(e["user_id"])
        items.append({
            "at": e["at"], "staff_id": e["user_id"],
            "staff_name": u.full_name if u else "Removed user",
            "branch_id": e["branch_id"], "branch_name": branches.get(e["branch_id"]),
            "type": e["type"], "summary": e["summary"],
            "amount_kobo": e["amount_kobo"], "ref_id": e["ref_id"], "status": e["status"],
        })
    return {"total": len(events), "limit": limit, "offset": offset,
            "truncated": truncated, "items": items}