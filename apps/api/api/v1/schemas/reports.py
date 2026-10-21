import uuid
from datetime import date, datetime

from pydantic import BaseModel


class SalesSummaryRow(BaseModel):
    period: date
    sales_count: int
    subtotal_naira: float
    discount_naira: float
    total_naira: float


class SalesSummaryResponse(BaseModel):
    rows: list[SalesSummaryRow]
    grand_total_naira: float
    grand_sales_count: int


class LowStockRow(BaseModel):
    product_id: uuid.UUID
    product_name: str
    name: str
    sku: str
    barcode: str | None = None
    category_name: str | None = None
    size: str | None = None
    # Product photo, so the Low Stock / Out of Stock alert tables can
    # show a thumbnail (click-to-enlarge) instead of a bare row of text
    # -- otherwise a staff member re-ordering stock has no quick way to
    # confirm which physical item this row refers to.
    image_url: str | None = None
    quantity_available: int
    reorder_level: int
    # Current price/cost, surfaced so the Low Stock page's inline
    # restock panel (spec section 5) can show "keep current" defaults
    # without a second round trip per row.
    cost_price_naira: float
    selling_price_naira: float
    # Whether this variant's out-of-stock dashboard alert has been
    # dismissed by someone who doesn't intend to restock it soon.
    # Purely a dashboard-alert suppression -- it still shows up on the
    # ordinary Low Stock / Inventory pages either way. Automatically
    # cleared the next time this variant is actually restocked (see
    # POST /catalog/variants/{id}/restock in warehouse.py), so a
    # future stock-out isn't silently muted too.
    out_of_stock_alert_muted: bool = False


class ExpiryRow(BaseModel):
    stock_batch_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    name: str
    sku: str
    barcode: str | None = None
    size: str | None = None
    # Product photo, same as LowStockRow -- the Expiry Alert list (spec:
    # "should also be showing the image of the product ... zoomable")
    # needs a thumbnail per row, not just text.
    image_url: str | None = None
    quantity_remaining: int
    # Spec section 4: "showing image, name, and price for each" --
    # the product's current selling price, same figure shown
    # everywhere else in the catalog.
    selling_price_naira: float = 0
    expiry_date: date
    days_until_expiry: int
    # Who this batch was bought from, when the batch can be traced back
    # to a purchase order with a supplier on it (spec: "the product
    # should be able to also show the name of the person that we
    # actually purchased that particular product from"). Not every
    # batch has this -- older/manually-adjusted batches may not.
    # Contact details are included alongside the name so a return/
    # refund request can actually be initiated from this view (spec:
    # "trace the item back to the supplier... so a return/refund
    # request can be initiated with that supplier"), not just
    # informational.
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    supplier_phone: str | None = None
    supplier_email: str | None = None
    # September 2026 change spec section 6 -- which branch this batch
    # is sitting in, so a push notification recipient (a specific
    # staff member/branch manager) can tell it's theirs to act on.
    branch_id: uuid.UUID | None = None


class ProductPerformanceRow(BaseModel):
    product_id: uuid.UUID
    product_name: str
    name: str
    sku: str
    category_name: str | None = None
    quantity_sold: int
    revenue_naira: float


class PerformanceRecordRow(BaseModel):
    """One row in a Performance Records period list -- either a raw
    daily metric_snapshots row, a GROUP BY date_trunc(...) rollup for
    weekly/monthly/yearly, or (for `daily` only) the synthetic
    in-progress "Today" row computed live since the nightly job hasn't
    run for it yet.
    """

    period_label: str
    period_start: date
    value_naira: float
    in_progress: bool = False


class PerformanceRecordsResponse(BaseModel):
    all_time_total_naira: float
    rows: list[PerformanceRecordRow]


class PriceChangeHistoryRow(BaseModel):
    id: uuid.UUID
    action: str
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None
    changed_by_name: str | None = None
    old_price_naira: float | None = None
    new_price_naira: float | None = None
    created_at: datetime


class NotificationItem(BaseModel):
    type: str
    label: str
    sub: str
    count: int
    href: str


class NotificationsSummary(BaseModel):
    items: list[NotificationItem]
    total: int
