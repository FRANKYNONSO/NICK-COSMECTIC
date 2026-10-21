import uuid

from pydantic import BaseModel, Field


class BranchCreate(BaseModel):
    # Address-first per spec: "input the address of that particular
    # location... first things first". Name is still required (it's
    # the label used everywhere else in the app -- see the Branch model
    # comment), but the field order here drives the form field order on
    # the New Branch modal. phone/is_warehouse are gone entirely.
    address: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)


class BranchOut(BaseModel):
    id: uuid.UUID
    address: str | None
    name: str

    model_config = {"from_attributes": True}


class BranchShiftSettingsUpdate(BaseModel):
    """Spec section 19 -- admin-configurable shift closing grace
    period, per branch. expected_close_hour left null disables the
    Late Close check entirely for that branch (only Manual/Auto-Closed
    apply); it's opt-in, not a default every branch is forced into.
    """

    expected_close_hour: int | None = Field(default=None, ge=0, le=23)
    expected_close_minute: int | None = Field(default=None, ge=0, le=59)
    grace_minutes: int = Field(default=10, ge=0, le=240)


class BranchShiftSettingsOut(BaseModel):
    branch_id: uuid.UUID
    expected_close_hour: int | None
    expected_close_minute: int | None
    grace_minutes: int
