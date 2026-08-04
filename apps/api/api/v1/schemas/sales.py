import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SaleLineIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(gt=0)
    unit_price_naira: float = Field(ge=0)


class CheckoutRequest(BaseModel):
    branch_id: uuid.UUID
    channel: str = Field(pattern="^(pos|website|mobile)$")
    # Client-generated idempotency key. The desktop POS generates this
    # offline at sale time; retried syncs with the same key never
    # create a duplicate sale. See docs/architecture Section 7.
    local_ref_id: str = Field(min_length=1, max_length=200)
    items: list[SaleLineIn] = Field(min_length=1)
    customer_id: uuid.UUID | None = None
    discount_naira: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|card|transfer|online)$")


class SaleItemOut(BaseModel):
    variant_id: uuid.UUID
    stock_batch_id: uuid.UUID | None
    quantity: int
    unit_price_naira: float
    line_total_naira: float


class SaleOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    customer_id: uuid.UUID | None
    served_by: uuid.UUID | None
    channel: str
    status: str
    subtotal_naira: float
    discount_naira: float
    total_naira: float
    payment_method: str | None
    local_ref_id: str | None
    created_at: datetime
    items: list[SaleItemOut]
