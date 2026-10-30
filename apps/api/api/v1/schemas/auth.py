import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    # The field keeps the name "email" so every existing client (web admin,
    # desktop POS, storefront) keeps working. It now accepts an email
    # address, a phone number or a username.
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def _strip_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter your username, email or phone number")
        return value

    @property
    def identifier(self) -> str:
        return self.email


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    role: str
    branch_id: uuid.UUID | None
    is_active: bool

    model_config = {"from_attributes": True}