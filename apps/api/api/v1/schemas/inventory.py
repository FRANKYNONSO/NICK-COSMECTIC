import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class StockBatchCreate(BaseModel):
    """Receiving new stock. cost_price is in Naira for human input;
    converted to kobo internally (see infrastructure.db.models and
    nickcosmetics_core.domain.money.Money).
    """

    variant_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_received: int = Field(gt=0)
    unit_cost_naira: float = Field(ge=0)
    batch_number: str | None = None
    expiry_date: date | None = None
    purchase_order_item_id: uuid.UUID | None = None


class StockBatchOut(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    branch_id: uuid.UUID
    batch_number: str | None
    quantity_received: int
    quantity_remaining: int
    unit_cost_naira: float
    expiry_date: date | None
    received_at: datetime

    model_config = {"from_attributes": True}


class StockAdjustmentCreate(BaseModel):
    stock_batch_id: uuid.UUID
    quantity_delta: int  # negative for loss/damage/theft, positive for recount-up
    reason: str = Field(pattern="^(damaged|expired|theft|recount|other)$")
    notes: str | None = None


class StockLevelOut(BaseModel):
    """Aggregated available stock for one variant at one branch --
    what the POS/admin dashboard actually wants to display, rather
    than raw per-batch rows.
    """

    variant_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_available: int
    reorder_level: int
    is_low_stock: bool
