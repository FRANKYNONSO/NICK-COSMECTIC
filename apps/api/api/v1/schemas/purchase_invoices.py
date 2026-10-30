import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# NOTE: rebuilt Oct 2026 from the Sep 5 copy + what routers/purchase_invoices.py
# and the PurchaseInvoice / PurchaseInvoiceLineItem models actually use.
# Unit labels are free text here (cartons / dozens / bags / pieces /
# half_carton / half_dozen ...), so no change was needed for "bags".


def _blank_to_none(value):
    """The invoice form sends '' for an empty date input."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


class PurchaseInvoiceLineItemIn(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)
    # Optional link back to the catalog product this line was picked
    # from via the Item autocomplete. Null for a line typed freehand.
    product_id: uuid.UUID | None = None
    quantity: int = Field(default=1, ge=1)
    # Cartons / dozens / bags / pieces -- how the product was purchased.
    unit_label: str = Field(default="pieces", min_length=1, max_length=40)
    # Rate is optional -- some invoices come with a total and no
    # per-unit rate.
    rate_naira: float | None = Field(default=None, ge=0)
    # Only sent when the total was typed by hand; otherwise the server
    # computes quantity x rate.
    line_total_naira: float | None = Field(default=None, ge=0)
    # Cost-per-piece calculator (Sept 2026).
    pieces_per_carton: int | None = Field(default=None, ge=1)
    remainder_quantity: int | None = Field(default=None, ge=1)
    remainder_unit_label: str | None = Field(default=None, min_length=1, max_length=40)


class PurchaseInvoiceLineItemUpdate(BaseModel):
    """Edit an existing line in place by id; any field left out keeps
    its current value."""

    id: uuid.UUID
    item_name: str | None = Field(default=None, min_length=1, max_length=200)
    product_id: uuid.UUID | None = None
    quantity: int | None = Field(default=None, ge=1)
    unit_label: str | None = Field(default=None, min_length=1, max_length=40)
    rate_naira: float | None = Field(default=None, ge=0)
    line_total_naira: float | None = Field(default=None, ge=0)
    pieces_per_carton: int | None = Field(default=None, ge=1)
    remainder_quantity: int | None = Field(default=None, ge=1)
    remainder_unit_label: str | None = Field(default=None, min_length=1, max_length=40)


class PurchaseInvoiceLineItemOut(BaseModel):
    id: uuid.UUID
    item_name: str
    product_id: uuid.UUID | None = None
    quantity: int
    unit_label: str | None = None
    # Deprecated -- only populated on invoices filed before the
    # cost-price field was removed from entry.
    cost_price_naira: float | None = None
    rate_naira: float | None = None
    line_total_naira: float | None = None
    pieces_per_carton: int | None = None
    remainder_quantity: int | None = None
    remainder_unit_label: str | None = None
    total_pieces: int | None = None
    cost_price_per_piece_naira: float | None = None

    model_config = {"from_attributes": True}


class PurchaseInvoiceCreate(BaseModel):
    branch_id: uuid.UUID | None = None
    # 'draft' = a bare invoice started from the Add Product workflow
    # (no supplier/date required yet); 'completed' = a normal filed one.
    status: str = Field(default="completed", pattern="^(draft|completed)$")
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_date: date | None = None
    amount_naira: float | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None
    supplier_address: str | None = Field(default=None, max_length=300)
    supplier_phone: str | None = Field(default=None, max_length=40)
    payment_method: str | None = Field(
        default=None, pattern="^(not_specified|bank_transfer|on_credit)$"
    )
    amount_paid_naira: float | None = Field(default=None, ge=0)
    # Landed-cost extras: dispatch, transit/waybill, handling.
    waybill_cost_naira: float | None = Field(default=None, ge=0)
    transit_cost_naira: float | None = Field(default=None, ge=0)
    handling_cost_naira: float | None = Field(default=None, ge=0)
    # Check and Balance for damaged/returned goods.
    adjustment_amount_naira: float | None = Field(default=None, ge=0)
    adjustment_status: str | None = Field(
        default=None, pattern="^(pending|balanced|replaced|refunded)$"
    )
    adjustment_note: str | None = Field(default=None, max_length=2000)
    items: list[PurchaseInvoiceLineItemIn] = Field(default_factory=list)

    _date_blank = field_validator("invoice_date", mode="before")(_blank_to_none)


class PurchaseInvoiceUpdate(BaseModel):
    """PATCH body -- only fields actually sent are applied."""

    status: str | None = Field(default=None, pattern="^(draft|completed)$")
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_date: date | None = None
    amount_naira: float | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = None
    supplier_address: str | None = Field(default=None, max_length=300)
    supplier_phone: str | None = Field(default=None, max_length=40)
    payment_method: str | None = Field(
        default=None, pattern="^(not_specified|bank_transfer|on_credit)$"
    )
    amount_paid_naira: float | None = Field(default=None, ge=0)
    waybill_cost_naira: float | None = Field(default=None, ge=0)
    transit_cost_naira: float | None = Field(default=None, ge=0)
    handling_cost_naira: float | None = Field(default=None, ge=0)
    adjustment_amount_naira: float | None = Field(default=None, ge=0)
    adjustment_status: str | None = Field(
        default=None, pattern="^(pending|balanced|replaced|refunded)$"
    )
    adjustment_note: str | None = Field(default=None, max_length=2000)
    add_items: list[PurchaseInvoiceLineItemIn] = Field(default_factory=list)
    update_items: list[PurchaseInvoiceLineItemUpdate] = Field(default_factory=list)
    remove_item_ids: list[uuid.UUID] = Field(default_factory=list)

    _date_blank = field_validator("invoice_date", mode="before")(_blank_to_none)


class PurchaseInvoiceOut(BaseModel):
    id: uuid.UUID
    status: str = "completed"
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    invoice_date: date
    amount_naira: float | None = None
    description: str | None = None
    image_url: str | None = None
    # Deprecated -- only populated on invoices filed before the field
    # was removed from the form.
    invoice_number: str | None = None
    # XFER5: 'branch_transfer' for the invoice a branch transfer makes; empty otherwise.
    source: str | None = None
    supplier_address: str | None = None
    supplier_phone: str | None = None
    payment_method: str | None = None
    amount_paid_naira: float | None = None
    balance_naira: float | None = None
    waybill_cost_naira: float | None = None
    transit_cost_naira: float | None = None
    handling_cost_naira: float | None = None
    total_landed_cost_naira: float | None = None
    adjustment_amount_naira: float | None = None
    adjustment_status: str | None = None
    adjustment_note: str | None = None
    created_by_name: str
    created_at: datetime
    items: list[PurchaseInvoiceLineItemOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TotalInvestedOut(BaseModel):
    """"Total Amount Invested in Inventory" -- a running total of every
    invoice's landed cost. total_landed_cost_naira is the KPI figure to
    display and is never reduced by losses; total_losses_naira /
    net_invested_naira are kept for any consumer that wants a
    losses-adjusted view.
    """

    total_landed_cost_naira: float
    total_losses_naira: float
    net_invested_naira: float
