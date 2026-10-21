"""Hard-delete logic for User accounts, shared between users.py and
staff_applications.py (the Applications tab can delete a rejected/
never-onboarded applicant directly, without going through the Staff
list first).

Deletion is only allowed when the account has never actually done any
real business-transaction work. Some of that would violate a NOT NULL
foreign key if we tried to delete anyway (Shift.opened_by,
Coupon.issued_by); the rest is technically nullable but deleting it
would silently erase who rang up a sale, processed a refund, ordered
stock, etc., which is exactly the kind of thing an admin should have
to consciously decide to lose -- so for consistency ALL of it blocks
deletion the same way, and Deactivate (which keeps every record
intact, per the existing suspend/reactivate behaviour) is the offered
alternative.

References that are just "who reviewed this" or "who's on this log
line" bookkeeping -- not the business record itself -- are cleaned up
instead of blocking: AuditLog.user_id and StaffApplication.reviewed_by
are nullable and get nulled out (log rows are kept, just anonymized),
and the user's own StaffApplication row is deleted along with them.
"""
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import (
    AuditLog,
    Coupon,
    IntakeReceipt,
    PurchaseOrder,
    Refund,
    Sale,
    ScanSession,
    Shift,
    StaffApplication,
    StockAdjustment,
    StockTransfer,
    User,
)

# (model, column, human description used in the "cannot delete" message)
_BLOCKING_REFERENCES = [
    (Shift, "opened_by", "opened a cash-register shift"),
    (Sale, "served_by", "recorded a sale"),
    (Refund, "processed_by", "processed a refund"),
    (PurchaseOrder, "ordered_by", "placed a purchase order"),
    (StockAdjustment, "adjusted_by", "made a stock adjustment"),
    (IntakeReceipt, "created_by", "created an intake receipt"),
    (StockTransfer, "created_by", "created a stock transfer"),
    (ScanSession, "created_by", "run a scan session"),
    (Coupon, "issued_by", "issued a coupon"),
    (Coupon, "redeemed_by", "redeemed a coupon"),
]


async def deletion_blockers(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Returns a list of human-readable reasons this account can't be
    hard-deleted. Empty list means it's safe to delete.
    """
    blockers: list[str] = []
    for model, column, description in _BLOCKING_REFERENCES:
        stmt = select(func.count()).select_from(model).where(getattr(model, column) == user_id)
        count = (await session.execute(stmt)).scalar_one()
        if count:
            blockers.append(description)
    return blockers


async def hard_delete_user(session: AsyncSession, user: User) -> None:
    """Actually deletes the row, after the caller has already run
    deletion_blockers() and confirmed it's empty. Does NOT commit --
    same convention as log_audit_event, so the caller's audit entry
    for the deletion rides in the same transaction.
    """
    await session.execute(update(AuditLog).where(AuditLog.user_id == user.id).values(user_id=None))
    await session.execute(
        update(StaffApplication).where(StaffApplication.reviewed_by == user.id).values(reviewed_by=None)
    )
    await session.execute(delete(StaffApplication).where(StaffApplication.user_id == user.id))
    await session.delete(user)
