import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.dependencies import get_db_session, require_permission
from api.v1.schemas.shifts import (
    LEVEL_LABELS,
    ComplianceAdminAcknowledge,
    ShiftBehaviorRow,
    ShiftCashSummary,
    ShiftClose,
    ShiftComplianceEventOut,
    ShiftExemptRequest,
    ShiftOpen,
    ShiftOut,
    ShiftPendingClosure,
    StaffComplianceSummary,
)
from infrastructure.db.models import Branch
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SalePayment
from infrastructure.db.models import Shift, ShiftComplianceEvent, User
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/shifts", tags=["shifts"])

# Nigeria doesn't observe DST, so this fixed offset is safe to use as a
# constant rather than reconstructing it per request. Every "what day is
# it" / "is this shift stale" decision in this file goes through this,
# not server-local time or naive UTC, so a shift opened at 11pm Lagos
# time isn't mistaken for "yesterday" just because the server clock (or
# a naive datetime with no offset) reads UTC.
_LAGOS = ZoneInfo("Africa/Lagos")

# Level thresholds for progressive escalation (spec section 9): the
# staff member's Nth non-exempt auto-closed shift maps to this level.
# Capped at 4 -- a 10th violation is still "Level 4", it doesn't
# invent higher levels the spec never asked for.
def _level_for_occurrence(occurrence: int) -> int:
    return min(occurrence, 4)


def _display_status(shift: Shift) -> str:
    if shift.status == "open":
        return "Open"
    if shift.violation_exempt:
        return "Auto-Closed (exempt)"
    if shift.auto_closed:
        return "Auto-Closed" if shift.closing_counted_kobo is None else "Auto-Closed (confirmed)"
    if shift.late_close:
        return "Late Close"
    return "Manually Closed"


def _to_out(shift: Shift, opened_by_name: str | None = None) -> ShiftOut:
    return ShiftOut(
        id=shift.id,
        branch_id=shift.branch_id,
        opened_by=shift.opened_by,
        opened_by_name=opened_by_name,
        opening_cash_naira=Money(shift.opening_float_kobo).naira,
        closing_counted_naira=(
            Money(shift.closing_counted_kobo).naira if shift.closing_counted_kobo is not None else None
        ),
        status=shift.status,
        opened_at=shift.opened_at,
        closed_at=shift.closed_at,
        auto_closed=shift.auto_closed,
        violation_exempt=shift.violation_exempt,
        violation_exempt_reason=shift.violation_exempt_reason,
        display_status=_display_status(shift),
    )


async def _record_compliance_event(session: AsyncSession, shift: Shift) -> None:
    """Called right after a shift is auto-closed (never for a manual
    close, and never twice for the same shift -- shift_id is unique on
    shift_compliance_events). Spec section 4: "do not treat that
    simply as an ordinary shift closure... record it as a Shift
    Compliance Violation."
    """
    existing = await session.execute(
        select(ShiftComplianceEvent).where(ShiftComplianceEvent.shift_id == shift.id)
    )
    if existing.scalar_one_or_none() is not None:
        return  # already recorded -- never double-count

    prior_count = await session.execute(
        select(func.count()).select_from(ShiftComplianceEvent).where(
            ShiftComplianceEvent.user_id == shift.opened_by
        )
    )
    occurrence = prior_count.scalar_one() + 1
    session.add(
        ShiftComplianceEvent(
            shift_id=shift.id,
            user_id=shift.opened_by,
            branch_id=shift.branch_id,
            occurrence_number=occurrence,
            level=_level_for_occurrence(occurrence),
        )
    )


async def _auto_close_stale_shifts(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Closes, on the staff member's behalf, any shift of theirs still
    `open` whose Lagos calendar day has already passed -- i.e. it's
    past 11:59pm on the day it was opened. Runs lazily (there's no
    background scheduler in this stack) at the top of every endpoint
    that needs an accurate picture of "is a shift open right now":
    POST /shifts, GET /shifts/current, GET /shifts/pending-closure.
    That's frequent enough in practice (POS hits /current on every
    load) that a shift is very rarely more than a few minutes late to
    flip, without needing an actual cron job.

    `closing_counted_kobo` is deliberately left NULL -- the system has
    no idea what was actually in the drawer at midnight, only the
    staff member does, and they confirm it later via PATCH .../close.

    Every shift this sweep closes also gets a shift-compliance
    violation recorded against the staff member (spec section 4) --
    this is, by construction, the ONLY path that sets auto_closed=True,
    so there's no risk of a legitimate admin-initiated closure (which
    goes through the ordinary close_shift path below and is never
    flagged auto_closed) being mistaken for a staff violation.
    """
    now_lagos = datetime.now(_LAGOS)
    today_lagos = now_lagos.date()

    result = await session.execute(
        select(Shift).where(Shift.opened_by == user_id, Shift.status == "open")
    )
    for shift in result.scalars().all():
        opened_lagos_date = shift.opened_at.astimezone(_LAGOS).date()
        if opened_lagos_date < today_lagos:
            # Stamp the closure at 23:59:59 Lagos time on the day it was
            # opened, not "now" -- this shift belongs to that earlier
            # day's records (shift reports, reconciliation), not to
            # whatever later moment the sweep happened to run.
            end_of_that_day_lagos = datetime.combine(
                opened_lagos_date, datetime.max.time().replace(microsecond=0)
            ).replace(tzinfo=_LAGOS)
            shift.status = "closed"
            shift.auto_closed = True
            shift.closing_counted_kobo = None
            shift.closed_at = end_of_that_day_lagos.astimezone(timezone.utc)
            await _record_compliance_event(session, shift)
    await session.commit()


async def _find_pending_closure(session: AsyncSession, user_id: uuid.UUID) -> Shift | None:
    """A shift the system auto-closed that the staff member hasn't yet
    confirmed the real counted cash for. Opening a new shift, and
    therefore ringing up any sale, is blocked while one of these exists.
    """
    result = await session.execute(
        select(Shift).where(
            Shift.opened_by == user_id,
            Shift.status == "closed",
            Shift.auto_closed.is_(True),
            Shift.closing_counted_kobo.is_(None),
        )
    )
    return result.scalars().first()


@router.post("", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
async def open_shift(
    body: ShiftOpen,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> ShiftOut:
    """Opens a till session for the current user at the given branch.
    Every role that can process a sale can open a shift -- it's the
    minimum needed to run a till, same as desktop.
    """
    await _auto_close_stale_shifts(session, current_user.id)

    pending = await _find_pending_closure(session, current_user.id)
    if pending is not None:
        opened_date = pending.opened_at.astimezone(_LAGOS).date().isoformat()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"You didn't close your shift from {opened_date} -- it was closed "
                "automatically at midnight. Confirm the counted cash for that shift "
                "before you can open today's."
            ),
        )

    existing = await session.execute(
        select(Shift).where(
            Shift.opened_by == current_user.id,
            Shift.status == "open",
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="You already have an open shift. Close it before opening another.",
        )

    shift = Shift(
        branch_id=body.branch_id,
        opened_by=current_user.id,
        opening_float_kobo=round(body.opening_cash_naira * 100),
        status="open",
    )
    session.add(shift)
    await session.commit()
    await session.refresh(shift)
    return _to_out(shift, current_user.full_name)


@router.get("/current", response_model=ShiftOut | None)
async def get_current_shift(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> ShiftOut | None:
    """The signed-in user's own open shift, if any -- POS checks this
    on load to know whether to show "resume shift" or "open shift".
    """
    await _auto_close_stale_shifts(session, current_user.id)

    result = await session.execute(
        select(Shift).where(
            Shift.opened_by == current_user.id,
            Shift.status == "open",
        )
    )
    shift = result.scalar_one_or_none()
    return _to_out(shift, current_user.full_name) if shift else None


@router.get("/pending-closure", response_model=ShiftPendingClosure | None)
async def get_pending_closure(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> ShiftPendingClosure | None:
    """POS calls this the moment the till screen loads (spec: "once
    they just click on the POS icon, the close shift should come up,
    telling them they have to close their yesterday shift") so the
    forced-confirmation prompt appears immediately, before the cashier
    even attempts to open a shift and hits the 409 from POST /shifts.
    """
    await _auto_close_stale_shifts(session, current_user.id)
    pending = await _find_pending_closure(session, current_user.id)
    if pending is None:
        return None
    return ShiftPendingClosure(
        shift=_to_out(pending, current_user.full_name),
        opened_at_date=pending.opened_at.astimezone(_LAGOS).date().isoformat(),
    )


@router.get("", response_model=list[ShiftOut])
async def list_shifts(
    branch_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    # Seeing every shift across the branch (not just your own) is a
    # manager/admin concern -- same tier as store-wide reports.
    _: User = Depends(require_permission("view_all_reports")),
) -> list[ShiftOut]:
    stmt = select(Shift, User.full_name).join(User, User.id == Shift.opened_by)
    if branch_id:
        stmt = stmt.where(Shift.branch_id == branch_id)
    if status_filter:
        stmt = stmt.where(Shift.status == status_filter)
    stmt = stmt.order_by(Shift.opened_at.desc())
    rows = await session.execute(stmt)
    return [_to_out(shift, name) for shift, name in rows.all()]


@router.get("/reports/unclosed", response_model=list[ShiftOut])
async def unclosed_shifts_report(
    branch_id: uuid.UUID | None = None,
    since_days: int = 30,
    session: AsyncSession = Depends(get_db_session),
    # Same tier as list_shifts -- this is the "staff who keep forgetting
    # to close their shifts" report for admins/branch managers (spec:
    # "there should also be reports for... those staffs that are not
    # closing their sales... so that they can actually call them to
    # order").
    _: User = Depends(require_permission("view_all_reports")),
) -> list[ShiftOut]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    stmt = (
        select(Shift, User.full_name)
        .join(User, User.id == Shift.opened_by)
        .where(Shift.auto_closed.is_(True), Shift.opened_at >= cutoff)
    )
    if branch_id:
        stmt = stmt.where(Shift.branch_id == branch_id)
    stmt = stmt.order_by(Shift.opened_at.desc())
    rows = await session.execute(stmt)
    return [_to_out(shift, name) for shift, name in rows.all()]


@router.patch("/{shift_id}/close", response_model=ShiftOut)
async def close_shift(
    shift_id: uuid.UUID,
    body: ShiftClose,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> ShiftOut:
    shift = await session.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Shift not found")

    # Two legitimate cases reach this endpoint: a genuinely open shift
    # being closed normally, or an auto-closed shift whose counted cash
    # was never actually confirmed (status is already "closed" here,
    # but closing_counted_kobo is still NULL). Anything else -- already
    # closed *and* confirmed -- is a real "already closed" error.
    is_pending_confirmation = shift.status == "closed" and shift.auto_closed and shift.closing_counted_kobo is None
    if shift.status != "open" and not is_pending_confirmation:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Shift is already closed")

    # A cashier can only close their own shift; a manager/admin closing
    # someone else's shift (e.g. they forgot to before leaving) still
    # needs to happen, so that's allowed via view_all_reports rather
    # than opened_by -- checked below.
    if shift.opened_by != current_user.id and not (
        current_user.role in ("branch_manager", "super_admin")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only close your own shift")

    shift.closing_counted_kobo = round(body.closing_counted_naira * 100)
    if not is_pending_confirmation:
        now_utc = datetime.now(timezone.utc)
        shift.closed_at = now_utc
        shift.status = "closed"

        # Late Close check (spec section 19) -- only meaningful for a
        # genuine manual close (never for the confirm-a-past-auto-close
        # path above, and never if the branch hasn't configured an
        # expected closing time). Compared against the shift's own
        # opening day in Lagos time, same as the auto-close sweep.
        branch = await session.get(Branch, shift.branch_id)
        if branch is not None and branch.shift_expected_close_hour is not None:
            opened_lagos_date = shift.opened_at.astimezone(_LAGOS).date()
            expected_close_lagos = datetime.combine(
                opened_lagos_date,
                datetime.min.time().replace(
                    hour=branch.shift_expected_close_hour,
                    minute=branch.shift_expected_close_minute or 0,
                ),
            ).replace(tzinfo=_LAGOS)
            grace_deadline = expected_close_lagos + timedelta(minutes=branch.shift_grace_minutes or 0)
            if now_utc.astimezone(_LAGOS) > grace_deadline:
                shift.late_close = True
    await session.commit()
    await session.refresh(shift)
    return _to_out(shift)



@router.get("/{shift_id}/cash-summary", response_model=ShiftCashSummary)
async def shift_cash_summary(
    shift_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> ShiftCashSummary:
    """Expected cash in the drawer: opening float plus every cash
    sale/tender recorded during this shift. Mirrors desktop's local
    shift_cash_summary(), used the same way -- compare this against
    what's actually counted at close.

    Handles both cases: a split-tender sale (rows in sale_payments)
    contributes only its actual cash portion; a plain single-tender
    sale (no sale_payments rows, just Sale.payment_method) falls back
    to counting its full total when that method is "cash". A sale
    can't be in both buckets -- the checkout endpoint always writes
    sale_payments rows when body.payments is used, so the "no rows"
    branch only ever matches genuinely single-tender sales.
    """
    shift = await session.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Shift not found")
    if shift.opened_by != current_user.id and not (
        current_user.role in ("branch_manager", "super_admin")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only view your own shift")

    split_cash_rows = await session.execute(
        select(SalePayment.amount_kobo)
        .join(ORMSale, SalePayment.sale_id == ORMSale.id)
        .where(
            ORMSale.shift_id == shift.id,
            ORMSale.status != "cancelled",
            SalePayment.method == "cash",
        )
    )
    split_cash_kobo = sum(split_cash_rows.scalars().all())

    single_tender_rows = await session.execute(
        select(ORMSale.total_kobo)
        .outerjoin(SalePayment, SalePayment.sale_id == ORMSale.id)
        .where(
            ORMSale.shift_id == shift.id,
            ORMSale.status != "cancelled",
            ORMSale.payment_method == "cash",
            SalePayment.id.is_(None),
        )
    )
    single_tender_cash_kobo = sum(single_tender_rows.scalars().all())

    cash_sales_kobo = split_cash_kobo + single_tender_cash_kobo
    opening = shift.opening_float_kobo
    expected = opening + cash_sales_kobo

    return ShiftCashSummary(
        opening_cash_naira=Money(opening).naira,
        cash_sales_naira=Money(cash_sales_kobo).naira,
        expected_naira=Money(expected).naira,
    )


# ------------------------------------------------------------- Shift compliance
# Staff accountability layer on top of the shift-tracking endpoints above.
# See ShiftComplianceEvent / _record_compliance_event for how events get
# created; nothing here is ever hand-entered by an admin.

async def _staff_permission_or_self(shift_user_id: uuid.UUID, current_user: User) -> None:
    """A staff member may only ever see/acknowledge their OWN compliance
    events; a branch_manager/super_admin can see anyone's.
    """
    if current_user.id == shift_user_id:
        return
    if current_user.role in ("branch_manager", "super_admin"):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not allowed")


def _event_to_out(
    event: ShiftComplianceEvent,
    shift: Shift,
    user_name: str | None,
    branch_name: str | None,
    admin_acknowledged_by_name: str | None = None,
) -> ShiftComplianceEventOut:
    return ShiftComplianceEventOut(
        id=event.id,
        shift_id=event.shift_id,
        user_id=event.user_id,
        user_name=user_name,
        branch_id=event.branch_id,
        branch_name=branch_name,
        occurrence_number=event.occurrence_number,
        level=event.level,
        level_label=LEVEL_LABELS.get(event.level, f"Level {event.level}"),
        created_at=event.created_at,
        acknowledged_at=event.acknowledged_at,
        admin_acknowledged_at=event.admin_acknowledged_at,
        admin_acknowledged_by_name=admin_acknowledged_by_name,
        admin_note=event.admin_note,
        shift_opened_at=shift.opened_at,
        opening_cash_naira=Money(shift.opening_float_kobo).naira,
        closing_counted_naira=(
            Money(shift.closing_counted_kobo).naira if shift.closing_counted_kobo is not None else None
        ),
    )


@router.get("/compliance/my-pending", response_model=list[ShiftComplianceEventOut])
async def my_pending_compliance_events(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> list[ShiftComplianceEventOut]:
    """Backs the staff dashboard's notification center (spec section
    10) -- every one of the current user's compliance events that
    hasn't been acknowledged yet. Stays non-empty (and therefore
    visible) until they tap Acknowledge; there's no way to dismiss it
    otherwise (spec: "Do not allow the staff member to simply ignore
    it indefinitely").
    """
    result = await session.execute(
        select(ShiftComplianceEvent, Shift, Branch.name)
        .join(Shift, ShiftComplianceEvent.shift_id == Shift.id)
        .join(Branch, ShiftComplianceEvent.branch_id == Branch.id)
        .where(
            ShiftComplianceEvent.user_id == current_user.id,
            ShiftComplianceEvent.acknowledged_at.is_(None),
        )
        .order_by(ShiftComplianceEvent.created_at.asc())
    )
    return [
        _event_to_out(event, shift, current_user.full_name, branch_name)
        for event, shift, branch_name in result.all()
    ]


@router.post("/compliance/events/{event_id}/acknowledge", response_model=ShiftComplianceEventOut)
async def acknowledge_compliance_event(
    event_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> ShiftComplianceEventOut:
    """"I Understand / Acknowledge" (level 1) / "Acknowledge Warning"
    (level 2+) -- spec sections 6/7/8. Only the staff member the event
    belongs to can acknowledge it; a manager viewing someone else's
    record cannot acknowledge on their behalf, since the point is the
    staff member confirming they've seen it.
    """
    event = await session.get(ShiftComplianceEvent, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Compliance event not found")
    if event.user_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only acknowledge your own warnings")
    if event.acknowledged_at is None:
        event.acknowledged_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(event)
    shift = await session.get(Shift, event.shift_id)
    branch = await session.get(Branch, event.branch_id)
    return _event_to_out(event, shift, current_user.full_name, branch.name if branch else None)


@router.post("/compliance/events/{event_id}/admin-acknowledge", response_model=ShiftComplianceEventOut)
async def admin_acknowledge_compliance_event(
    event_id: uuid.UUID,
    body: ComplianceAdminAcknowledge,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("acknowledge_shift_violations")),  # PATCHP3C
) -> ShiftComplianceEventOut:
    """Update Request item 15: a manager/admin marking that they've
    informed the staff member about this auto-close incident (with an
    optional corrective note, e.g. "won't happen again"). Deliberately
    separate from POST .../acknowledge above -- that one is the staff
    member's own "I saw this" self-ack and stays untouched; this is
    the admin-facing "I've followed up on this" record the Shift
    report needs to show as acknowledged vs. outstanding.
    """
    event = await session.get(ShiftComplianceEvent, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Compliance event not found")
    if current_user.role != "super_admin":  # PATCHP3I-BRANCH
        if current_user.branch_id is None or event.branch_id != current_user.branch_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Compliance event not found")
    event.admin_acknowledged_at = datetime.now(timezone.utc)
    event.admin_acknowledged_by = current_user.id
    if body.note is not None:
        event.admin_note = body.note.strip() or None
    await session.commit()
    await session.refresh(event)
    shift = await session.get(Shift, event.shift_id)
    branch = await session.get(Branch, event.branch_id)
    user = await session.get(User, event.user_id)
    return _event_to_out(
        event,
        shift,
        user.full_name if user else None,
        branch.name if branch else None,
        admin_acknowledged_by_name=current_user.full_name,
    )


@router.get("/compliance/events", response_model=list[ShiftComplianceEventOut])
async def list_compliance_events(
    user_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    status_filter: str | None = None,  # "pending" | "acknowledged"
    since_days: int = 365,
    session: AsyncSession = Depends(get_db_session),
    # Admin-only listing/filtering view -- spec section 14.
    _: User = Depends(require_permission("view_all_reports")),
) -> list[ShiftComplianceEventOut]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    AdminUser = aliased(User)
    stmt = (
        select(ShiftComplianceEvent, Shift, User.full_name, Branch.name, AdminUser.full_name)
        .join(Shift, ShiftComplianceEvent.shift_id == Shift.id)
        .join(User, ShiftComplianceEvent.user_id == User.id)
        .join(Branch, ShiftComplianceEvent.branch_id == Branch.id)
        .outerjoin(AdminUser, ShiftComplianceEvent.admin_acknowledged_by == AdminUser.id)
        .where(ShiftComplianceEvent.created_at >= cutoff)
    )
    if user_id:
        stmt = stmt.where(ShiftComplianceEvent.user_id == user_id)
    if branch_id:
        stmt = stmt.where(ShiftComplianceEvent.branch_id == branch_id)
    if status_filter == "pending":
        stmt = stmt.where(ShiftComplianceEvent.acknowledged_at.is_(None))
    elif status_filter == "acknowledged":
        stmt = stmt.where(ShiftComplianceEvent.acknowledged_at.is_not(None))
    stmt = stmt.order_by(ShiftComplianceEvent.created_at.desc())
    rows = await session.execute(stmt)
    return [
        _event_to_out(event, shift, name, branch_name, admin_acknowledged_by_name=admin_name)
        for event, shift, name, branch_name, admin_name in rows.all()
    ]


def _status_for_summary(warnings_issued: int, management_reviews: int) -> str:
    # Spec section 13.
    if management_reviews > 0:
        return "Management Review"
    if warnings_issued > 0:
        return "Warning"
    return "Good Standing"


@router.get("/compliance/summary", response_model=list[StaffComplianceSummary])
async def compliance_summary(
    branch_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    # Spec section 12 -- admin/branch-manager compliance dashboard.
    _: User = Depends(require_permission("view_all_reports")),
) -> list[StaffComplianceSummary]:
    """Per-staff-member compliance rollup, computed entirely from
    Shift + ShiftComplianceEvent history (spec section 5: "The system
    should be able to calculate this from historical shift records
    rather than requiring the administrator to manually enter it").
    """
    shift_stmt = select(Shift, User.full_name, Branch.name).join(User, Shift.opened_by == User.id).join(
        Branch, Shift.branch_id == Branch.id, isouter=True
    )
    if branch_id:
        shift_stmt = shift_stmt.where(Shift.branch_id == branch_id)
    if user_id:
        shift_stmt = shift_stmt.where(Shift.opened_by == user_id)
    shift_rows = (await session.execute(shift_stmt)).all()

    event_stmt = select(ShiftComplianceEvent)
    if branch_id:
        event_stmt = event_stmt.where(ShiftComplianceEvent.branch_id == branch_id)
    if user_id:
        event_stmt = event_stmt.where(ShiftComplianceEvent.user_id == user_id)
    events = (await session.execute(event_stmt)).scalars().all()
    events_by_user: dict[uuid.UUID, list[ShiftComplianceEvent]] = {}
    for e in events:
        events_by_user.setdefault(e.user_id, []).append(e)

    per_user: dict[uuid.UUID, dict] = {}
    for shift, name, branch_name in shift_rows:
        bucket = per_user.setdefault(
            shift.opened_by,
            {"name": name, "branch_id": shift.branch_id, "branch_name": branch_name,
             "total": 0, "manual": 0, "auto": 0, "late": 0},
        )
        bucket["total"] += 1
        if shift.status == "closed":
            if shift.auto_closed and not shift.violation_exempt:
                bucket["auto"] += 1
            elif shift.auto_closed and shift.violation_exempt:
                pass  # exempt closures don't count toward either bucket
            elif shift.late_close:
                bucket["late"] += 1
                bucket["manual"] += 1  # a late close is still a manual close
            else:
                bucket["manual"] += 1

    out: list[StaffComplianceSummary] = []
    for uid, bucket in per_user.items():
        user_events = events_by_user.get(uid, [])
        warnings_issued = sum(1 for e in user_events if e.level >= 2)
        management_reviews = sum(1 for e in user_events if e.level >= 3)
        pending = sum(1 for e in user_events if e.acknowledged_at is None)
        out.append(
            StaffComplianceSummary(
                user_id=uid,
                user_name=bucket["name"],
                branch_id=bucket["branch_id"],
                branch_name=bucket["branch_name"],
                total_shifts=bucket["total"],
                manually_closed=bucket["manual"],
                auto_closed=bucket["auto"],
                late_closures=bucket["late"],
                warnings_issued=warnings_issued,
                pending_acknowledgements=pending,
                management_reviews=management_reviews,
                status=_status_for_summary(warnings_issued, management_reviews),
            )
        )
    out.sort(key=lambda s: s.user_name or "")
    return out


@router.get("/compliance/staff/{user_id}/behavior", response_model=list[ShiftBehaviorRow])
async def staff_shift_behavior(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("view_all_reports")),
) -> list[ShiftBehaviorRow]:
    """Backs the "Shift Behavior" section of a staff member's admin
    profile page -- spec section 15.
    """
    result = await session.execute(
        select(Shift, Branch.name)
        .join(Branch, Shift.branch_id == Branch.id, isouter=True)
        .where(Shift.opened_by == user_id)
        .order_by(Shift.opened_at.desc())
        .limit(200)
    )
    rows = result.all()
    events = (
        await session.execute(
            select(ShiftComplianceEvent.shift_id).where(ShiftComplianceEvent.user_id == user_id)
        )
    ).scalars().all()
    violation_shift_ids = set(events)

    out = []
    for shift, branch_name in rows:
        out.append(
            ShiftBehaviorRow(
                date=shift.opened_at.astimezone(_LAGOS).date().isoformat(),
                branch_name=branch_name,
                opened_at=shift.opened_at,
                closed_at=shift.closed_at,
                opening_cash_naira=Money(shift.opening_float_kobo).naira,
                closing_counted_naira=(
                    Money(shift.closing_counted_kobo).naira if shift.closing_counted_kobo is not None else None
                ),
                display_status=_display_status(shift),
                violation=shift.id in violation_shift_ids,
            )
        )
    return out


@router.patch("/{shift_id}/exempt", response_model=ShiftOut)
async def exempt_shift_violation(
    shift_id: uuid.UUID,
    body: ShiftExemptRequest,
    session: AsyncSession = Depends(get_db_session),
    # Admin-only -- spec section 18: only management should be able to
    # decide a given auto-close wasn't the staff member's fault.
    current_user: User = Depends(require_permission("view_all_reports")),
) -> ShiftOut:
    """Marks an auto-closed shift as a System/Administrative
    Auto-Closure rather than a Staff Auto-Closure (spec section 18 --
    server downtime, sync failure, an admin closing it on the staff
    member's behalf, etc.) and removes the compliance violation it
    generated, if any. Does NOT renumber other events' occurrence
    numbers -- those already reflect what was true for the staff
    member at the time each one happened.
    """
    shift = await session.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Shift not found")
    if not shift.auto_closed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only an auto-closed shift can be exempted")

    shift.violation_exempt = True
    shift.violation_exempt_reason = body.reason

    existing = await session.execute(
        select(ShiftComplianceEvent).where(ShiftComplianceEvent.shift_id == shift.id)
    )
    event = existing.scalar_one_or_none()
    if event is not None:
        await session.delete(event)

    await session.commit()
    await session.refresh(shift)
    return _to_out(shift)
