import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from api.v1.schemas.validators import validate_digits_only

# Self-service registration deliberately excludes super_admin -- that
# tier is only ever granted by an existing admin (see accept_application
# in staff_applications.py), never self-selected. Which roles ARE
# self-applicable is looked up live from the roles table at request
# time now (register() in the router), not hardcoded here, so a role a
# super_admin adds later shows up on the registration form
# automatically -- see api/v1/routers/staff_applications.py.


class StaffRegisterRequest(BaseModel):
    """Submitted as multipart form fields alongside the profile_photo
    and id_document file uploads -- see POST /staff-applications/register
    in api/v1/routers/staff_applications.py. Kept as its own schema
    (used with Form(...) per-field, not as a JSON body) because FastAPI
    can't bind a nested JSON model alongside file uploads in one
    multipart request.
    """

    # Credentials (spec Section 4)
    email: EmailStr
    preferred_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password needs at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password needs at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password needs at least one number")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def _dob_not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return v

    # What they're applying for -- admin has final say at accept time.
    # No longer a fixed Literal["cashier","branch_manager"]: a super_admin
    # can define custom roles (see roles.py), and any self-applicable
    # one (every role except super_admin) should be selectable here
    # without a code change. Validated dynamically against the roles
    # table in the register() endpoint instead, where the DB session
    # is available.
    role_applied_for: str = Field(default="cashier", min_length=1, max_length=100)

    # Which branch they're applying to work at -- picked by the
    # applicant themselves now, not assigned later by whoever reviews
    # the application. Validated against the branches table in the
    # register() endpoint, same reasoning as role_applied_for above.
    branch_id: uuid.UUID

    # Basic information (spec Section 3)
    full_legal_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date | None = None
    gender: Literal["male", "female"] | None = None
    phone_number: str | None = None
    residential_address: str | None = None
    state_of_origin: str | None = None
    lga_of_origin: str | None = None
    hometown: str | None = None
    home_address_of_origin: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    emergency_contact_relationship: str | None = None

    # Identification information (spec Section 3)
    id_type: Literal["national_id", "passport", "drivers_license"] | None = None
    id_number: str | None = None

    @field_validator("phone_number")
    @classmethod
    def _phone_digits(cls, v: str | None) -> str | None:
        return validate_digits_only(v, "Phone number")

    @field_validator("emergency_contact_phone")
    @classmethod
    def _emergency_phone_digits(cls, v: str | None) -> str | None:
        return validate_digits_only(v, "Emergency contact phone")


class PublicRoleOption(BaseModel):
    """Minimal, non-sensitive role option for the public registration
    form -- deliberately excludes `permissions` (RoleOut has that, but
    it's not something an anonymous applicant should ever see).
    """

    name: str
    display_name: str


class PublicBranchOption(BaseModel):
    """Minimal, non-sensitive branch option for the public registration
    form.
    """

    id: uuid.UUID
    name: str
    address: str | None = None


class StaffApplicationReject(BaseModel):
    reason: str | None = None


class StaffApplicationAccept(BaseModel):
    # Defaults to the branch the applicant selected at registration
    # (see StaffApplication.branch_id) if omitted -- the router falls
    # back to it. Still overridable here in case the reviewer places
    # them somewhere else instead.
    branch_id: uuid.UUID | None = None
    role: str = Field(
        default="cashier",
        description="One of the existing roles: super_admin, branch_manager, staff",
    )


class StaffApplicationOut(BaseModel):
    """Full application detail -- what an admin sees when reviewing,
    and what the applicant sees on their own limited status page
    (minus reviewer-only fields, trimmed by the router for that case).
    """

    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    role_applied_for: str
    branch_id: uuid.UUID | None

    full_legal_name: str
    preferred_name: str
    profile_photo_url: str | None
    date_of_birth: date | None
    gender: str | None
    phone_number: str | None
    email: str
    residential_address: str | None
    state_of_origin: str | None
    lga_of_origin: str | None
    hometown: str | None
    home_address_of_origin: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    emergency_contact_relationship: str | None

    id_type: str | None
    id_number: str | None
    has_id_document: bool = False

    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    submitted_at: datetime
    correction_note: str | None = None
    correction_fields: list[str] = []

    model_config = {"from_attributes": True}


class StaffApplicationSummary(BaseModel):
    """Trimmed row for list views (Staff Applications table)."""

    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    role_applied_for: str
    branch_id: uuid.UUID | None
    full_legal_name: str
    preferred_name: str
    email: str
    submitted_at: datetime

    model_config = {"from_attributes": True}


class StaffApplicationStatusOut(BaseModel):
    """What a logged-in applicant sees about their own application --
    deliberately excludes reviewer identity/internal fields.
    """

    status: str
    submitted_at: datetime
    rejection_reason: str | None = None

    model_config = {"from_attributes": True}
