"""Shift open/close -- the basic unit of till cash reconciliation.

A shift is opened with a counted starting float and closed with a
counted ending amount. Checkout is gated on having an open shift (see
pos_window.py) so every sale is attributable to a specific till
session, and so a branch manager can reconcile expected vs. counted
cash at close.

Deliberately local-only: shifts are a POS/branch-device concept, not
part of the shared cloud domain, so this doesn't touch
nickcosmetics_core at all.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from local_db.models import OutboxSale, Shift


def get_open_shift(session: Session, branch_id: str, user_id: str) -> Shift | None:
    return session.execute(
        select(Shift).where(
            Shift.branch_id == branch_id,
            Shift.opened_by == user_id,
            Shift.status == "open",
        )
    ).scalar_one_or_none()


def open_shift(
    session: Session,
    branch_id: str,
    user_id: str,
    user_name: str,
    opening_float_naira: float,
) -> Shift:
    """Opens a new shift. Callers should check get_open_shift() first
    -- this doesn't itself guard against opening a second shift for
    the same (branch, user), since the UI is expected to route to
    "resume the open shift" instead of ever calling this twice.
    """
    shift = Shift(
        branch_id=branch_id,
        opened_by=user_id,
        opened_by_name=user_name,
        opening_float_kobo=round(opening_float_naira * 100),
    )
    session.add(shift)
    session.commit()
    return shift


def close_shift(session: Session, shift_id: str, closing_counted_naira: float) -> Shift:
    shift = session.get(Shift, shift_id)
    if shift is None:
        raise ValueError(f"Shift '{shift_id}' not found")

    shift.closing_counted_kobo = round(closing_counted_naira * 100)
    shift.closed_at = datetime.utcnow()
    shift.status = "closed"
    session.commit()
    return shift


def shift_cash_summary(session: Session, shift: Shift) -> dict:
    """Expected cash-in-drawer for a shift: opening float plus every
    cash tender recorded on sales during this shift (voided sales
    excluded). Used on shift close so a manager can compare it
    against what's actually counted in the till.
    """
    from local_db.models import OutboxPayment  # local import avoids a cycle with reports.py

    rows = session.execute(
        select(OutboxPayment)
        .join(OutboxSale, OutboxPayment.outbox_sale_id == OutboxSale.id)
        .where(
            OutboxSale.shift_id == shift.id,
            OutboxSale.voided.is_(False),
            OutboxPayment.method == "cash",
        )
    ).scalars().all()

    cash_sales_kobo = sum(row.amount_kobo for row in rows)
    expected_kobo = shift.opening_float_kobo + cash_sales_kobo

    return {
        "opening_float_naira": shift.opening_float_kobo / 100,
        "cash_sales_naira": cash_sales_kobo / 100,
        "expected_naira": expected_kobo / 100,
    }
