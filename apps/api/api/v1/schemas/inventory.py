import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class StockBatchCreate(BaseModel):
    """Receiving new stock. cost_price is in Naira for human input;
    converted to kobo internally (see infrastructure.db.models and
    nickcosmetics_core.domain.money.Money).
    """

    product_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_received: int = Field(gt=0)
    unit_cost_naira: float = Field(ge=0)
    batch_number: str | None = None
    expiry_date: date | None = None
    purchase_order_item_id: uuid.UUID | None = None


class StockBatchOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
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
    quantity_delta: int  # negative for a loss (expired/damaged/missing/defective/supplier_return/theft/other), positive for recount-up
    reason: str = Field(
        pattern="^(damaged|expired|theft|recount|other|missing|defective|supplier_return)$"
    )
    notes: str | None = None
    # Optional link to the waybill/damaged-item complaint this loss was
    # recorded from (see IntakeReceipt) -- e.g. filing a
    # supplier_return loss for the same delivery a complaint was
    # already logged against.
    intake_receipt_id: uuid.UUID | None = None
    # Item 11 accountability fields -- only meaningful for their one
    # matching reason; the router doesn't require them for any other
    # reason.
    damage_caused_by: str | None = Field(default=None, pattern="^(customer|staff)$")
    damage_staff_name: str | None = Field(default=None, max_length=200)
    theft_staff_on_duty: list[str] | None = None
    theft_noticed_at: datetime | None = None
    # September 2026 change spec section 4: who actually did it (as
    # opposed to theft_staff_on_duty above, which is who was on
    # shift) -- mirrors damage_caused_by/damage_staff_name's shape.
    theft_caused_by: str | None = Field(default=None, pattern="^(customer|staff)$")
    theft_by_staff_name: str | None = Field(default=None, max_length=200)
    # Required by the router (not enforced here at the schema layer,
    # since the requirement only applies to damaged/theft/expired, not
    # every reason) -- "all three require image or video evidence
    # upload".
    image_url: str | None = None


class StockAdjustmentOut(BaseModel):
    id: uuid.UUID
    stock_batch_id: uuid.UUID
    branch_id: uuid.UUID | None
    branch_name: str | None = None
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    quantity_delta: int
    reason: str
    value_naira: float | None
    notes: str | None
    intake_receipt_id: uuid.UUID | None
    damage_caused_by: str | None = None
    damage_staff_name: str | None = None
    theft_staff_on_duty: list[str] | None = None
    theft_noticed_at: datetime | None = None
    theft_caused_by: str | None = None
    theft_by_staff_name: str | None = None
    image_url: str | None = None
    # 'pending_confirmation' only ever applies to theft -- every other
    # reason is 'completed' the moment it's created (quantity is
    # deducted immediately). See create_stock_adjustment /
    # confirm_theft_adjustment in the router.
    status: str = "completed"
    reviewed_by: uuid.UUID | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    adjusted_by: uuid.UUID | None
    adjusted_by_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LossReasonBreakdown(BaseModel):
    reason: str
    quantity: int
    value_naira: float


class LossSummaryOut(BaseModel):
    """Feeds the Super Admin dashboard's Losses card. Branch-scoped the
    same way every other dashboard figure is: a non-super-admin (or a
    super admin who passed branch_id) sees only their branch's losses;
    omitting branch_id entirely (super admin, "All Branches") sums
    across every branch.
    """

    total_value_naira: float
    total_quantity: int
    by_reason: list[LossReasonBreakdown]


class LossHistoryRow(BaseModel):
    """One row in the unified Losses History (spec section 18.4) --
    every StockAdjustment-backed loss category (damaged/expired/
    theft/etc.). "sold_below_price" (formerly backed by SaleLoss) was
    retired along with the checkout discount path it tracked (spec
    section 3).
    """

    id: uuid.UUID
    category: str
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    sku: str | None = None
    image_url: str | None = None
    quantity: int
    value_naira: float
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None
    notes: str | None = None
    sale_id: uuid.UUID | None = None
    recorded_by: uuid.UUID | None = None
    recorded_by_name: str | None = None
    created_at: datetime


class ProductReturnCreditRow(BaseModel):
    """PATCH8A-PRODUCT-RETURNS-CREDIT: one line of a product's "Returns and
    credit" history (a refund line, or goods taken on credit).
    """

    kind: str
    happened_at: datetime
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None
    what: str
    quantity: int
    detail: str | None = None


class StockLevelOut(BaseModel):
    """Aggregated available stock for one variant at one branch --
    what the POS/admin dashboard actually wants to display, rather
    than raw per-batch rows.
    """

    product_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_available: int
    reorder_level: int
    is_low_stock: bool
