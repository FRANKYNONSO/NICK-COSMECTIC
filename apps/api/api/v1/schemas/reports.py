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
    # Patch 26a: cost comes from the stock batch each line was sold from
    # (falls back to the product's current cost price when a line has no
    # batch). revenue here is line totals, before any sale-level discount.
    cost_naira: float = 0
    profit_naira: float = 0
    margin_percent: float | None = None


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


# ---------------------------------------------------------------- Patch 26a


class ProfitSummaryResponse(BaseModel):
    sales_count: int
    revenue_naira: float
    cost_naira: float
    gross_profit_naira: float
    margin_percent: float | None = None
    losses_naira: float = 0
    net_profit_naira: float = 0
    # Sale lines with no stock batch recorded -- their cost is today's
    # cost price for the product, so it is an estimate.
    estimated_cost_lines: int = 0
    total_lines: int = 0


class OpenBookingRow(BaseModel):
    booking_id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    items_summary: str
    units: int
    total_naira: float
    paid_naira: float
    balance_naira: float
    age_days: int
    created_at: datetime
    pickup_note: str | None = None
    pickup_date: datetime | None = None


class ReservedStockRow(BaseModel):
    product_id: uuid.UUID
    product_name: str
    sku: str | None = None
    on_hand: int
    on_open_bookings: int
    on_customer_orders: int
    sellable: int
    over_committed: bool


class CancelledBookingRow(BaseModel):
    booking_id: uuid.UUID
    branch_name: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    amount_naira: float
    cancelled_at: datetime
    reason: str | None = None


class BookingsReportResponse(BaseModel):
    open_count: int
    units_held: int
    deposits_held_naira: float
    balance_owed_naira: float
    oldest_age_days: int
    customer_order_units: int
    awaiting_refund_count: int
    awaiting_refund_naira: float
    open_bookings: list[OpenBookingRow]
    reserved_stock: list[ReservedStockRow]
    awaiting_refund: list[CancelledBookingRow]


# ---------------------------------------------------------------- Patch 26b


class SalesPeriodTotals(BaseModel):
    sales_count: int
    units: int
    revenue_naira: float
    avg_sale_naira: float
    discounts_naira: float


class PaymentMethodRow(BaseModel):
    method: str
    amount_naira: float
    payments: int
    share_percent: float | None = None


class CategorySalesRow(BaseModel):
    category_name: str
    units: int
    revenue_naira: float
    cost_naira: float
    profit_naira: float
    margin_percent: float | None = None


class HourBucketRow(BaseModel):
    hour: int
    sales_count: int
    revenue_naira: float


class WeekdayBucketRow(BaseModel):
    weekday: int  # 1 = Monday ... 7 = Sunday
    sales_count: int
    revenue_naira: float


class SalesBreakdownResponse(BaseModel):
    date_from: date
    date_to: date
    previous_from: date
    previous_to: date
    current: SalesPeriodTotals
    previous: SalesPeriodTotals
    by_payment_method: list[PaymentMethodRow]
    by_category: list[CategorySalesRow]
    by_hour: list[HourBucketRow]
    by_weekday: list[WeekdayBucketRow]


class ReturnsByActionRow(BaseModel):
    action: str
    count: int
    amount_naira: float


class ReturnsByReasonRow(BaseModel):
    reason: str
    count: int
    amount_naira: float


class ReturnsByStaffRow(BaseModel):
    staff_name: str
    count: int
    amount_naira: float


class ReturnedProductRow(BaseModel):
    product_id: uuid.UUID
    product_name: str
    units_returned: int
    refund_value_naira: float
    units_sold: int
    return_rate_percent: float | None = None
    restocked_units: int
    spoiled_units: int


class ReturnsReportResponse(BaseModel):
    refund_count: int
    refunded_naira: float
    sales_count: int
    sales_revenue_naira: float
    rate_by_count_percent: float | None = None
    rate_by_value_percent: float | None = None
    pending_count: int
    pending_naira: float
    by_action: list[ReturnsByActionRow]
    by_reason: list[ReturnsByReasonRow]
    by_staff: list[ReturnsByStaffRow]
    by_product: list[ReturnedProductRow]


class SupplierSpendRow(BaseModel):
    supplier_name: str
    invoices: int
    goods_naira: float
    extra_cost_naira: float
    total_naira: float
    paid_naira: float
    balance_owed_naira: float
    extra_cost_percent: float | None = None
    last_purchase_date: date | None = None


class PurchaseMonthRow(BaseModel):
    month: str
    invoices: int
    goods_naira: float
    extra_cost_naira: float
    total_naira: float


class UnpaidInvoiceRow(BaseModel):
    invoice_id: uuid.UUID
    supplier_name: str
    invoice_date: date
    amount_naira: float
    paid_naira: float
    balance_naira: float


class CostTrendRow(BaseModel):
    product_id: uuid.UUID
    product_name: str
    purchases: int
    previous_cost_naira: float
    latest_cost_naira: float
    change_percent: float | None = None
    latest_date: date
    latest_supplier: str | None = None


class PurchasesReportResponse(BaseModel):
    invoice_count: int
    goods_naira: float
    extra_cost_naira: float
    total_naira: float
    paid_naira: float
    balance_owed_naira: float
    adjustments_naira: float
    by_supplier: list[SupplierSpendRow]
    by_month: list[PurchaseMonthRow]
    unpaid_invoices: list[UnpaidInvoiceRow]
    cost_trends: list[CostTrendRow]


# ---------------------------------------------------------------- Patch 26c


class MovementEvent(BaseModel):
    occurred_at: datetime
    kind: str
    label: str
    location: str | None = None
    quantity: int
    direction: str  # 'in' | 'out' | 'moved'
    by_name: str | None = None
    detail: str | None = None


class StockMovementsResponse(BaseModel):
    product_id: uuid.UUID
    product_name: str
    sku: str | None = None
    received_units: int
    sold_units: int
    restocked_units: int
    adjusted_net_units: int
    transferred_units: int
    transfer_count: int
    events: list[MovementEvent]
    truncated: bool = False


class TransferTypeRow(BaseModel):
    transfer_type: str
    label: str
    count: int
    units: int


class TransferRouteRow(BaseModel):
    from_name: str
    to_name: str
    count: int
    units: int
    value_naira: float


class TransferProductRow(BaseModel):
    product_name: str
    transfers: int
    units: int


class TransferEventRow(BaseModel):
    occurred_at: datetime
    product_name: str
    from_name: str
    to_name: str
    label: str
    units: int
    by_name: str | None = None
    notes: str | None = None


class WaitingReturnRow(BaseModel):
    product_name: str
    from_name: str
    to_name: str
    units: int
    amount_naira: float
    requested_at: datetime
    started_by_side: str


class ReturnStatusRow(BaseModel):
    status: str
    count: int
    amount_naira: float


class ShortDeliveryRow(BaseModel):
    occurred_at: datetime
    branch_name: str | None = None
    supplier_name: str | None = None
    product_name: str
    invoiced: int
    received: int
    short: int
    short_value_naira: float


class TransfersReportResponse(BaseModel):
    transfer_count: int
    units_moved: int
    value_naira: float
    return_requests_waiting: int
    return_requests_waiting_naira: float
    short_lines: int
    short_units: int
    short_value_naira: float
    intake_receipts: int
    intake_damaged_units: int
    intake_missing_units: int
    by_type: list[TransferTypeRow]
    by_route: list[TransferRouteRow]
    top_products: list[TransferProductRow]
    recent: list[TransferEventRow]
    waiting_returns: list[WaitingReturnRow]
    returns_by_status: list[ReturnStatusRow]
    short_deliveries: list[ShortDeliveryRow]


class CountVarianceProductRow(BaseModel):
    product_name: str
    recounts: int
    short_units: int
    surplus_units: int
    short_naira: float
    net_naira: float


class CountVarianceGroupRow(BaseModel):
    name: str
    entries: int
    short_units: int
    surplus_units: int
    net_naira: float


class CountVarianceEventRow(BaseModel):
    occurred_at: datetime
    product_name: str
    branch_name: str | None = None
    delta: int
    value_naira: float
    by_name: str | None = None
    notes: str | None = None


class CountVarianceResponse(BaseModel):
    entries: int
    matched_counts: int
    short_units: int
    short_naira: float
    surplus_units: int
    surplus_naira: float
    net_naira: float
    by_product: list[CountVarianceProductRow]
    by_branch: list[CountVarianceGroupRow]
    by_staff: list[CountVarianceGroupRow]
    recent: list[CountVarianceEventRow]
