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
    local_ref_id: str = Field(min_length=1, max_length=200)
    items: list[SaleLineIn] = Field(min_length=1)
    customer_id: uuid.UUID | None = None
    discount_naira: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|card|transfer|online)$")


class SaleItemOut(BaseModel):
    id: uuid.UUID
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


class RefundLineIn(BaseModel):
    sale_item_id: uuid.UUID
    quantity: int = Field(gt=0)
    restock: bool = True


class RefundCreate(BaseModel):
    lines: list[RefundLineIn] = Field(min_length=1)
    reason: str | None = None


class RefundOut(BaseModel):
    id: uuid.UUID
    sale_id: uuid.UUID
    processed_by: uuid.UUID | None
    amount_naira: float
    reason: str | None
    created_at: datetime
    sale_status: str
