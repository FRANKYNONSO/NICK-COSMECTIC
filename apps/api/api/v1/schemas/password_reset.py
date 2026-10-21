import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class PasswordResetRequestCreate(BaseModel):
    email: EmailStr


class PasswordResetRequestCreated(BaseModel):
    request_id: uuid.UUID
    secret: str


class PasswordResetStatusCheck(BaseModel):
    request_id: uuid.UUID
    secret: str


class PasswordResetStatusOut(BaseModel):
    status: str


class PasswordResetComplete(BaseModel):
    request_id: uuid.UUID
    secret: str
    new_password: str = Field(min_length=8)


class PasswordResetRequestOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str | None
    role: str
    branch_id: uuid.UUID | None
    status: str
    requested_at: datetime
    decided_at: datetime | None
    approval_expires_at: datetime | None
    used_at: datetime | None = None
    decided_by_name: str | None = None
