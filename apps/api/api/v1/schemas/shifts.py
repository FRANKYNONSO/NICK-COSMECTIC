import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ShiftOpen(BaseModel):
    branch_id: uuid.UUID
    # Renamed from "opening float" to "Opening Cash" (user-facing term
    # only -- the underlying DB column stays opening_float_kobo so we
    # don't need a data migration for this). Represents the cash
    # available in the drawer when the staff member starts their shift.
    opening_cash_naira: float = Field(ge=0)


class ShiftClose(BaseModel):
    closing_counted_naira: float = Field(ge=0)


class ShiftOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    opened_by: uuid.UUID
    opened_by_name: str | None = None
    opening_cash_naira: float
    closing_counted_naira: float | None
    status: str
    opened_at: datetime
    closed_at: datetime | None
    # True when the system, not the staff member, closed this shift at
    # end of day. The frontend uses this to tell "you resumed a shift
    # you closed yourself yesterday" apart from "the system closed this
    # for you and you still owe a counted-cash confirmation".
    auto_closed: bool = False
    # When True, an admin has reviewed this auto-closed shift and
    # determined it was NOT the staff member's fault (server downtime,
    # sync failure, an admin closing it on their behalf, etc.) -- see
    # spec section 18 ("Staff Auto-Closure" vs "System/Administrative
    # Auto-Closure"). An exempt auto-close never counts as a shift
    # compliance violation.
    violation_exempt: bool = False
    violation_exempt_reason: str | None = None
    # Convenience label for the shift-history table -- mirrors the
    # "Manually Closed / Auto-Closed / Late Close / Open" states from
    # spec section 3, computed the same way everywhere it's shown
    # rather than re-deriving auto_closed/status logic in the frontend.
    display_status: str = "Open"

    model_config = {"from_attributes": True}


class ShiftPendingClosure(BaseModel):
    """What POS shows on load, before letting a cashier open today's
    shift, when yesterday's (or an earlier day's) shift was closed
    automatically at midnight and still needs a real counted-cash
    figure from the staff member. None of this blocks *viewing* the
    till -- only opening a new shift and, transitively, ringing up
    sales (which require an open shift already).
    """

    shift: ShiftOut
    opened_at_date: str  # e.g. "2026-08-25" (Africa/Lagos) -- the date being confirmed


class ShiftCashSummary(BaseModel):
    opening_cash_naira: float
    cash_sales_naira: float
    expected_naira: float


# ------------------------------------------------------------- Shift compliance
# Staff accountability layer built ON TOP of the existing shift records
# above -- see spec doc "Staff Shift Tracking / Shift Management" extension.
# Nothing here replaces or duplicates a Shift; a ShiftComplianceEvent is
# always 1:1 with the specific auto-closed shift that triggered it.

LEVEL_LABELS = {
    1: "Reminder",
    2: "Formal Warning",
    3: "Management Review",
    4: "Final Warning / Disciplinary Review",
}


class ComplianceAdminAcknowledge(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ShiftComplianceEventOut(BaseModel):
    id: uuid.UUID
    shift_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    branch_id: uuid.UUID
    branch_name: str | None = None
    occurrence_number: int
    level: int
    level_label: str
    created_at: datetime
    acknowledged_at: datetime | None
    # Item 15: separate admin-side "I've informed this staff member"
    # follow-up, distinct from the staff member's own acknowledged_at
    # self-ack above.
    admin_acknowledged_at: datetime | None = None
    admin_acknowledged_by_name: str | None = None
    admin_note: str | None = None
    # Denormalized from the linked shift, so the admin dashboard and the
    # staff notification center don't need a second round-trip just to
    # show what happened.
    shift_opened_at: datetime
    opening_cash_naira: float
    closing_counted_naira: float | None

    model_config = {"from_attributes": True}


class ShiftComplianceAcknowledge(BaseModel):
    pass  # no body needed -- the acknowledging user and timestamp come from the request itself


class ShiftExemptRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class StaffComplianceSummary(BaseModel):
    """Spec section 5/12: 'Shift Compliance' counters + section 13
    status, computed live from shift + compliance-event history rather
    than hand-maintained.
    """

    user_id: uuid.UUID
    user_name: str
    branch_id: uuid.UUID | None
    branch_name: str | None
    total_shifts: int
    manually_closed: int
    auto_closed: int
    late_closures: int
    warnings_issued: int  # events at level >= 2
    pending_acknowledgements: int
    management_reviews: int  # events at level >= 3
    status: str  # Good Standing / Needs Attention / Warning / Management Review


class ShiftBehaviorRow(BaseModel):
    """One row of the staff profile's 'Shift Behavior' table -- spec
    section 15.
    """

    date: str
    branch_name: str | None
    opened_at: datetime
    closed_at: datetime | None
    opening_cash_naira: float
    closing_counted_naira: float | None
    display_status: str
    violation: bool
