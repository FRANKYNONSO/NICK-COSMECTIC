import uuid

from pydantic import BaseModel, Field


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    phone: str | None = None
    is_warehouse: bool = False


class BranchOut(BaseModel):
    id: uuid.UUID
    name: str
    address: str | None
    phone: str | None
    is_warehouse: bool

    model_config = {"from_attributes": True}
