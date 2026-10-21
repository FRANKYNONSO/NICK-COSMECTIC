import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CustomerAddressCreate(BaseModel):
    address_line: str = Field(min_length=1, max_length=500)
    city: str | None = None
    state: str | None = None
    country: str = "Nigeria"
    label: str | None = None


class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    address: CustomerAddressCreate


class CustomerAddressOut(BaseModel):
    id: uuid.UUID
    address_line: str
    city: str | None
    state: str | None
    country: str
    label: str | None
    is_default: bool


class CustomerOut(BaseModel):
    id: uuid.UUID
    full_name: str | None
    email: str | None
    phone: str | None
    created_at: datetime
    addresses: list[CustomerAddressOut]