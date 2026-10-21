import uuid

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1)
    role: str = Field(description="One of: super_admin, branch_manager, staff")
    email: EmailStr | None = None
    phone: str | None = None
    password: str = Field(min_length=8)
    branch_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    branch_id: uuid.UUID | None = None


class UserRoleUpdate(BaseModel):
    role: str = Field(description="One of: super_admin, branch_manager, staff")


class UserPermissionsUpdate(BaseModel):
    denied_permissions: list[str] = Field(
        default_factory=list,
        description=(
            "Permission names to revoke from this account on top of what "
            "their role normally grants, e.g. ['manage_coupons']. Send an "
            "empty list to clear all restrictions. Ignored for super_admin "
            "accounts -- that role can't be restricted."
        ),
    )
    granted_permissions: list[str] = Field(
        default_factory=list,
        description=(
            "Permission names to grant this account on top of what their "
            "role normally allows, without changing their role, e.g. one "
            "trusted staff member getting ['view_low_stock'] while every "
            "other staff account still can't. Send an empty list to clear "
            "all extra grants. Ignored for super_admin accounts -- there's "
            "nothing left to grant that role."
        ),
    )


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    role: str
    branch_id: uuid.UUID | None
    is_active: bool
    denied_permissions: list[str] = Field(default_factory=list)
    granted_permissions: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}