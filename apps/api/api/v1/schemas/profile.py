import uuid
from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from api.v1.schemas.validators import validate_digits_only


class MyProfileOut(BaseModel):
    """Merged view of the account -- User fields plus, if this account
    went through the staff-application flow, the personal/ID info
    collected there. Frontend uses `has_application` to decide whether
    to show the "Editable" (preferred_name, photo, phone, email) vs
    "Protected" (everything else, spec Section 15/16) sections at all --
    an admin-created account with no application has neither.
    """

    id: uuid.UUID
    full_name: str
    email: str | None
    pending_email: str | None
    phone: str | None
    role: str
    branch_id: uuid.UUID | None
    employee_id: str | None
    date_joined: date | None
    is_active: bool

    has_application: bool = False
    application_status: str | None = None
    preferred_name: str | None = None
    profile_photo_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    residential_address: str | None = None
    state_of_origin: str | None = None
    lga_of_origin: str | None = None
    hometown: str | None = None
    home_address_of_origin: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    emergency_contact_relationship: str | None = None
    id_type: str | None = None
    id_number: str | None = None
    has_id_document: bool = False


class MyProfileUpdate(BaseModel):
    """Only the fields spec Section 15 lists as staff-editable.
    Everything else on the application (legal name, DOB, address,
    emergency contact, ID info) is protected -- deliberately not
    accepted here at all, so there's no risk of it slipping through
    even if the frontend sent it.
    """

    preferred_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone_number: str | None = None

    @field_validator("phone_number")
    @classmethod
    def _phone_digits(cls, v: str | None) -> str | None:
        return validate_digits_only(v, "Phone number")


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RequestEmailChange(BaseModel):
    new_email: EmailStr


class VerifyEmailChange(BaseModel):
    token: str
