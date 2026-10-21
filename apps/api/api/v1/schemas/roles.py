import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    # Stored as-is in users.role and used in URLs/permission checks, so
    # kept to a machine-friendly slug -- the admin panel free-types a
    # display name and derives this, but validate here too in case a
    # future caller posts directly to the API.
    name: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=80)
    # Deliberately no default permission set (per the dictated spec --
    # "there should not be any default"): a brand-new role starts with
    # an empty list and the super admin explicitly grants from there.
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    display_name: str | None = None
    permissions: list[str] | None = None


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    permissions: list[str]
    is_system: bool
    user_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
