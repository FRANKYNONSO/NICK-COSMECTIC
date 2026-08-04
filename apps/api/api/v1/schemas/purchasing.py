import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


# ------------------------------------------------------------ SUPPLIERS

class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_phone: str | None = None
    contact_email: str | None = None
    address: str | None = None


class SupplierOut(BaseModel):
    id: uuid.UUID
    name: str
    contact_phone: str | None
    contact_email: str | None
    address: str | None

    model_config = {"from_attributes": True}


# ------------------------------------------------------- PURCHASE ORDERS

class PurchaseOrderItemIn(BaseModel):
    variant_id: uuid.UUID
    quantity_ordered: int = Field(gt=0)
    unit_cost_naira: float = Field(ge=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: uuid.UUID
    branch_id: uuid.UUID
    expected_at: date | None = None
    items: list[PurchaseOrderItemIn] = Field(min_length=1)


class PurchaseOrderItemOut(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    quantity_ordered: int
    quantity_received: int
    unit_cost_naira: float


class PurchaseOrderOut(BaseModel):
    id: uuid.UUID
    supplier_id: uuid.UUID | None
    branch_id: uuid.UUID | None
    status: str
    ordered_by: uuid.UUID | None
    ordered_at: datetime | None
    expected_at: date | None
    created_at: datetime
    items: list[PurchaseOrderItemOut]


# --------------------------------------------------------- RECEIVING PO

class ReceiveLineIn(BaseModel):
    purchase_order_item_id: uuid.UUID
    quantity_now: int = Field(gt=0)
    batch_number: str | None = None
    expiry_date: date | None = None


class ReceivePurchaseOrderRequest(BaseModel):
    lines: list[ReceiveLineIn] = Field(min_length=1)
