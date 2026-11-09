import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import can_view_costs, get_db_session, require_permission
from api.v1.net_sales import (  # PATCH26F
    cost_sale_conds,
    fetch_returns,
    not_spoiled_return_writeoff,
    refunds_by_period,
    revenue_sale_conds,
)
from api.v1.schemas.reports import (
    BookingsReportResponse,
    CancelledBookingRow,
    DataHealthResponse,
    HealthCheckRow,
    StockValuationResponse,
    ValuationBranchRow,
    ValuationCategoryRow,
    LossMonthRow,
    LossTrendResponse,
    CountVarianceEventRow,
    CountVarianceGroupRow,
    CountVarianceProductRow,
    CountVarianceResponse,
    MovementEvent,
    ReturnStatusRow,
    ShortDeliveryRow,
    StockMovementsResponse,
    TransferEventRow,
    TransferProductRow,
    TransferRouteRow,
    TransfersReportResponse,
    TransferTypeRow,
    WaitingReturnRow,
    CategorySalesRow,
    CostTrendRow,
    HourBucketRow,
    PaymentMethodRow,
    PurchaseMonthRow,
    PurchasesReportResponse,
    ReturnedProductRow,
    ReturnsByActionRow,
    ReturnsByReasonRow,
    ReturnsByStaffRow,
    ReturnsReportResponse,
    SalesBreakdownResponse,
    SalesPeriodTotals,
    SupplierSpendRow,
    UnpaidInvoiceRow,
    WeekdayBucketRow,
    ExpiryRow,
    LowStockRow,
    OpenBookingRow,
    PerformanceRecordRow,
    PerformanceRecordsResponse,
    PriceChangeHistoryRow,
    ProductPerformanceRow,
    ProfitSummaryResponse,
    ReservedStockRow,
    SalesSummaryResponse,
    SalesSummaryRow,
)
from infrastructure.db.models import (
    AuditLog,
    Branch,
    Category,
    Customer,
    CustomerRequest,
    ExpiryAlertDismissal,
    GoodsReceipt,
    GoodsReceiptLine,
    IntakeReceipt,
    MetricSnapshot,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Refund,
    RefundItem,
    Sale,
    SaleItem,
    SalePayment,
    StockAdjustment,
    StockBatch,
    StockTransfer,
    Supplier,
    TransferReturn,
    User,
)
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/reports", tags=["reports"])

# All report endpoints are gated on "view_all_reports" -- per the role
# spec, Staff can see their own sales/performance but not company-wide
# revenue, inventory value, or aggregate reports. See docs/architecture
# Section 3 and nickcosmetics_core.domain.users.user's permission table.
_REQUIRE_REPORTS_ACCESS = require_permission("view_all_reports")

# Low-stock is deliberately its own, lighter permission rather than
# "view_all_reports": it's operational shop-floor info (what's running
# out) rather than a financial/aggregate report, so Staff get it too --
# see nickcosmetics_core.domain.users.user's permission table.
_REQUIRE_LOW_STOCK_ACCESS = require_permission("view_low_stock")


@router.get("/sales-summary", response_model=SalesSummaryResponse)
async def sales_summary(
    branch_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    group_by: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db_session),
    # view_own_sales rather than the full reports permission -- staff need
    # this for their own dashboard/reports summary. Scoped to their own
    # sales below, same pattern as GET /sales, so a staff caller never
    # sees store-wide totals through this endpoint regardless of the
    # branch_id they pass.
    current_user: User = Depends(require_permission("view_own_sales")),
) -> SalesSummaryResponse:
    period_col = func.date_trunc(group_by, Sale.created_at)

    stmt = (
        select(
            period_col.label("period"),
            func.count(Sale.id).label("sales_count"),
            func.sum(Sale.subtotal_kobo).label("subtotal_kobo"),
            func.sum(Sale.discount_kobo).label("discount_kobo"),
            func.sum(Sale.total_kobo).label("total_kobo"),
        )
        .where(*revenue_sale_conds())  # PATCH26F: customer sales only, partly refunded included
        .group_by(period_col)
        .order_by(period_col)
    )
    if current_user.role != "super_admin":
        # Platform update spec section 7: "staff should see only their
        # own branch's reports, not platform-wide reports" -- widened
        # from the previous served_by==self scoping (their own
        # individual sales only) to their whole assigned branch, same
        # tier as Sales & Receipts / Open Bookings / Refund History
        # (spec section 4). Applies to branch_manager too -- still
        # hard-scoped server-side regardless of whatever branch_id the
        # client sends.
        stmt = stmt.where(Sale.branch_id == current_user.branch_id)
    elif branch_id:
        stmt = stmt.where(Sale.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Sale.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(Sale.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    result = await session.execute(stmt)
    rows = result.all()
    # PATCH26F: money handed back on partly refunded sales, placed in the period of the sale
    _scope_id = current_user.branch_id if current_user.role != "super_admin" else branch_id
    _returns = await fetch_returns(session, _scope_id, date_from, date_to)
    _refunds_by_period = await refunds_by_period(session, _returns, group_by)

    # Section 18.7: "losses... must be automatically deducted from
    # total revenue... sales add to revenue, losses subtract from it."
    # Only damaged/expired/stolen (StockAdjustment) losses are
    # subtracted here -- a "Sold Below Original Price" loss (see
    # sale_losses.py) is deliberately NOT subtracted a second time,
    # since that item was actually sold and its lower price is already
    # what's sitting in Sale.total_kobo above; subtracting its
    # discount again here would double-count the same shortfall.
    loss_when = func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at)
    loss_period_col = func.date_trunc(group_by, loss_when)
    loss_stmt = (
        select(loss_period_col.label("period"), func.sum(StockAdjustment.value_kobo).label("value_kobo"))
        .where(
            StockAdjustment.value_kobo.is_not(None),
            StockAdjustment.status == "completed",
            not_spoiled_return_writeoff(),  # PATCH26F: a refunded spoiled item is not also a loss
        )
        .group_by(loss_period_col)
    )
    if current_user.role != "super_admin":
        loss_stmt = loss_stmt.where(StockAdjustment.branch_id == current_user.branch_id)
    elif branch_id:
        loss_stmt = loss_stmt.where(StockAdjustment.branch_id == branch_id)
    if date_from:
        loss_stmt = loss_stmt.where(loss_when >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        loss_stmt = loss_stmt.where(
            loss_when < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        )
    loss_result = await session.execute(loss_stmt)
    loss_by_period = {row.period.date(): int(row.value_kobo or 0) for row in loss_result.all()}

    summary_rows = [
        SalesSummaryRow(
            period=row.period.date(),
            sales_count=row.sales_count,
            subtotal_naira=Money(int(row.subtotal_kobo or 0)).naira,
            discount_naira=Money(int(row.discount_kobo or 0)).naira,
            total_naira=Money(int(row.total_kobo or 0) - loss_by_period.get(row.period.date(), 0)).naira,
        )
        for row in rows
    ]
    # A loss can fall on a day/week/month with no sales at all (e.g. an
    # expired product written off on a slow day) -- still needs to
    # reduce revenue for that period per section 18.7, so it gets its
    # own zero-sale row rather than being silently dropped.
    covered_periods = {row.period for row in summary_rows}
    for period, loss_kobo in loss_by_period.items():
        if period not in covered_periods and loss_kobo:
            summary_rows.append(
                SalesSummaryRow(
                    period=period, sales_count=0, subtotal_naira=0, discount_naira=0,
                    total_naira=Money(-loss_kobo).naira,
                )
            )
    summary_rows.sort(key=lambda r: r.period)
    for _row in summary_rows:  # PATCH26F
        _row.refunds_naira = _refunds_by_period.get(_row.period, 0) / 100
        _row.net_total_naira = round(_row.total_naira - _row.refunds_naira, 2)

    return SalesSummaryResponse(
        rows=summary_rows,
        grand_total_naira=sum(r.total_naira for r in summary_rows),
        grand_sales_count=sum(r.sales_count for r in summary_rows),
        grand_refunds_naira=round(sum(r.refunds_naira for r in summary_rows), 2),
        grand_net_naira=round(sum(r.net_total_naira for r in summary_rows), 2),
    )


@router.get("/low-stock", response_model=list[LowStockRow])
async def low_stock_report(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_LOW_STOCK_ACCESS),
) -> list[LowStockRow]:
    # branch_id is optional so a super_admin (who isn't pinned to any one
    # branch) can omit it and get stock aggregated across every branch --
    # otherwise this 422s for them while working fine for staff/branch
    # managers, who always have a branch_id to send.
    today = date.today()
    conditions = [
        StockBatch.quantity_remaining > 0,
        (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
    ]
    if current_user.role != "super_admin":
        # Same hard branch-scoping as the sales endpoints -- never trust
        # the client's branch_id for a cashier or branch manager,
        # always pin to their own branch server-side.
        conditions.append(StockBatch.branch_id == current_user.branch_id)
    elif branch_id:
        conditions.append(StockBatch.branch_id == branch_id)
    stmt = (
        select(
            StockBatch.product_id,
            func.sum(StockBatch.quantity_remaining).label("quantity_available"),
        )
        .where(*conditions)
        .group_by(StockBatch.product_id)
    )
    result = await session.execute(stmt)
    availability = {row.product_id: row.quantity_available for row in result.all()}

    product_conditions = [Product.is_deleted.is_(False)]
    # Same hidden_from_staff exclusion as GET /catalog/products (feature
    # spec 2) -- a staff caller's Low Stock page must not surface a
    # product a super admin has deliberately hidden from staff, restock
    # button and all.
    if current_user.role in ("cashier", "store_assistant"):  # PATCHP2E-HIDDEN-ROLES
        product_conditions.append(Product.hidden_from_staff.is_(False))

    products_result = await session.execute(
        select(Product)
        .options(
            selectinload(Product.category),
            selectinload(Product.images),
        )
        .where(*product_conditions)
    )

    show_cost = await can_view_costs(current_user, session)  # PATCHP2C2-COST-VISIBLE
    rows: list[LowStockRow] = []
    any_auto_unmuted = False
    for product in products_result.scalars().all():
        available = availability.get(product.id, 0)
        # Self-healing mute: once a previously-muted product actually
        # has stock again, the mute has served its purpose (nothing
        # left to suppress) -- clear it so a *future* stock-out alerts
        # fresh instead of staying silently muted forever.
        if product.out_of_stock_alert_muted and available > 0:
            product.out_of_stock_alert_muted = False
            any_auto_unmuted = True
        if available <= product.reorder_level:
            primary_image = next((img for img in product.images if img.is_primary), None)
            image_url = primary_image.url if primary_image else (
                product.images[0].url if product.images else None
            )
            rows.append(
                LowStockRow(
                    product_id=product.id,
                    product_name=product.name,
                    name=product.name,
                    sku=product.sku,
                    barcode=product.barcode,
                    category_name=product.category.name if product.category else None,
                    size=product.size,
                    image_url=image_url,
                    quantity_available=available,
                    reorder_level=product.reorder_level,
                    cost_price_naira=Money(product.cost_price_kobo).naira if show_cost else 0.0,
                    selling_price_naira=Money(product.selling_price_kobo).naira,
                    out_of_stock_alert_muted=product.out_of_stock_alert_muted,
                )
            )
    if any_auto_unmuted:
        await session.commit()
    return sorted(rows, key=lambda r: r.quantity_available)


@router.get("/price-change-history", response_model=list[PriceChangeHistoryRow])
async def price_change_history(
    branch_id: uuid.UUID | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> list[PriceChangeHistoryRow]:
    """History behind the notification bell's 'Price changes' item and
    the branch Reports page's price-change section (per-branch price
    override feature spec items 3 and 5). Covers both
    'global_price_changed' (super_admin) and
    'branch_price_override_changed' (branch_manager) audit actions.

    A branch_manager sees their own branch's override changes AND
    every global price change (a global change resets their override,
    so it's relevant context even though it wasn't their own action)
    -- unlike list_audit_log's stricter exclude-if-we-cant-prove-it,
    a global_price_changed event has no branch to scope to by
    definition, since it affects every branch equally.
    """
    stmt = select(AuditLog).options(selectinload(AuditLog.user)).where(
        AuditLog.entity_type == "product",
        AuditLog.action.in_(["global_price_changed", "branch_price_override_changed"]),
    )
    if current_user.role != "super_admin":
        stmt = stmt.where(
            (AuditLog.action == "global_price_changed")
            | (
                (AuditLog.action == "branch_price_override_changed")
                & (AuditLog.log_metadata["branch_id"].astext == str(current_user.branch_id))
            )
        )
    elif branch_id:
        stmt = stmt.where(
            (AuditLog.action == "global_price_changed")
            | (
                (AuditLog.action == "branch_price_override_changed")
                & (AuditLog.log_metadata["branch_id"].astext == str(branch_id))
            )
        )
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(min(limit, 500))

    result = await session.execute(stmt)
    entries = list(result.scalars().all())

    rows: list[PriceChangeHistoryRow] = []
    for e in entries:
        meta = e.log_metadata or {}
        product_id = e.entity_id
        raw_branch_id = meta.get("branch_id")
        rows.append(
            PriceChangeHistoryRow(
                id=e.id,
                action=e.action,
                product_id=product_id,
                product_name=meta.get("product_name"),
                branch_id=uuid.UUID(raw_branch_id) if raw_branch_id else None,
                branch_name=meta.get("branch_name"),
                changed_by_name=e.user.full_name if e.user else None,
                old_price_naira=meta.get("old_price_naira"),
                new_price_naira=meta.get("new_price_naira"),
                created_at=e.created_at,
            )
        )
    return rows


async def _fetch_expiry_batches(
    session: AsyncSession, branch_id: uuid.UUID | None, within_days: int
) -> list[ExpiryRow]:
    """Shared query behind both expiry_report() (the Reports page) and
    my_pending_expiry_alerts() (September 2026 change spec section 6's
    push notification) -- one source of "what's near expiry" so the
    two never quietly drift apart.
    """
    today = date.today()
    cutoff = today + timedelta(days=within_days)

    conditions = [
        StockBatch.quantity_remaining > 0,
        StockBatch.expiry_date.is_not(None),
        StockBatch.expiry_date <= cutoff,
    ]
    if branch_id:
        conditions.append(StockBatch.branch_id == branch_id)

    stmt = (
        select(StockBatch, Product, Supplier)
        .join(Product, StockBatch.product_id == Product.id)
        .outerjoin(PurchaseOrderItem, StockBatch.purchase_order_item_id == PurchaseOrderItem.id)
        .outerjoin(PurchaseOrder, PurchaseOrderItem.purchase_order_id == PurchaseOrder.id)
        .outerjoin(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .options(selectinload(Product.images))
        .where(*conditions)
        .order_by(StockBatch.expiry_date)
    )
    result = await session.execute(stmt)

    rows = []
    for batch, product, supplier in result.all():
        primary_image = next((img for img in product.images if img.is_primary), None)
        image_url = primary_image.url if primary_image else (
            product.images[0].url if product.images else None
        )
        rows.append(
            ExpiryRow(
                stock_batch_id=batch.id,
                product_id=product.id,
                product_name=product.name,
                name=product.name,
                sku=product.sku,
                barcode=product.barcode,
                size=product.size,
                image_url=image_url,
                quantity_remaining=batch.quantity_remaining,
                selling_price_naira=Money(product.selling_price_kobo).naira,
                expiry_date=batch.expiry_date,
                days_until_expiry=(batch.expiry_date - today).days,
                supplier_id=supplier.id if supplier else None,
                supplier_name=supplier.name if supplier else None,
                supplier_phone=supplier.contact_phone if supplier else None,
                supplier_email=supplier.contact_email if supplier else None,
                branch_id=batch.branch_id,
            )
        )
    return rows


@router.get("/expiry", response_model=list[ExpiryRow])
async def expiry_report(
    branch_id: uuid.UUID | None = None,
    within_days: int = Query(default=150, ge=1, le=365),
    session: AsyncSession = Depends(get_db_session),
    # Same access tier as Low Stock (spec: the expiry alert lives right
    # alongside low stock/out of stock on the dashboard and its own
    # page), not the heavier view_all_reports -- shop-floor staff need
    # to see this to act on it, not just admins.
    _: User = Depends(_REQUIRE_LOW_STOCK_ACCESS),
) -> list[ExpiryRow]:
    # Default window is ~5 months (spec: "if an item is five months to
    # go and is still in the inventory"), still overridable via
    # within_days for other callers.
    return await _fetch_expiry_batches(session, branch_id, within_days)


@router.get("/expiry-alerts/my-pending", response_model=list[ExpiryRow])
async def my_pending_expiry_alerts(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_LOW_STOCK_ACCESS),
) -> list[ExpiryRow]:
    """September 2026 change spec section 6: a real push notification
    -- super admin, branch manager, and every staff member at the
    affected branch -- rather than only a number on the dashboard that
    nobody sees unless they happen to load it. Scoped to the current
    user's own branch for branch-scoped roles (branch_manager/staff);
    a super admin (no branch_id) sees near-expiry batches across every
    branch, matching how the Reports page itself scopes "All
    Branches". Excludes anything this user has already dismissed --
    see expiry_alert_dismissals.
    """
    branch_id = current_user.branch_id  # None for a super admin -- all branches
    rows = await _fetch_expiry_batches(session, branch_id, within_days=150)
    if not rows:
        return []
    dismissed_result = await session.execute(
        select(ExpiryAlertDismissal.stock_batch_id).where(ExpiryAlertDismissal.user_id == current_user.id)
    )
    dismissed_ids = {row for row in dismissed_result.scalars().all()}
    return [r for r in rows if r.stock_batch_id not in dismissed_ids]


@router.post("/expiry-alerts/{stock_batch_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_expiry_alert(
    stock_batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_LOW_STOCK_ACCESS),
) -> None:
    """Idempotent -- dismissing an already-dismissed batch (e.g. a
    double-click, or the same alert somehow fetched twice) is a no-op,
    not an error.
    """
    existing = await session.execute(
        select(ExpiryAlertDismissal).where(
            ExpiryAlertDismissal.user_id == current_user.id,
            ExpiryAlertDismissal.stock_batch_id == stock_batch_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    session.add(ExpiryAlertDismissal(user_id=current_user.id, stock_batch_id=stock_batch_id))
    await session.commit()


@router.get("/product-performance", response_model=list[ProductPerformanceRow])
async def product_performance_report(
    branch_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=20, ge=1, le=2000),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> list[ProductPerformanceRow]:
    # PATCH26F: a branch manager only ever sees their own branch, like the other reports
    branch_id = _scope_branch(current_user, branch_id)
    stmt = (
        select(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("quantity_sold"),
            func.sum(SaleItem.line_total_kobo).label("revenue_kobo"),
            func.sum(
                SaleItem.quantity
                * func.coalesce(StockBatch.unit_cost_kobo, Product.cost_price_kobo, 0)
            ).label("cost_kobo"),
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .join(Product, SaleItem.product_id == Product.id)
        .outerjoin(StockBatch, SaleItem.stock_batch_id == StockBatch.id)
        .where(*cost_sale_conds())  # PATCH26F: partly/fully refunded stay in; returns taken off below
        .group_by(SaleItem.product_id)
        .order_by(func.sum(SaleItem.line_total_kobo).desc())
        .limit(limit)
    )
    if branch_id:
        stmt = stmt.where(Sale.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Sale.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(Sale.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    result = await session.execute(stmt)
    performance_rows = result.all()
    _ret = await fetch_returns(session, branch_id, date_from, date_to)  # PATCH26F
    if not performance_rows:
        return []

    product_ids = [row.product_id for row in performance_rows]
    products_result = await session.execute(
        select(Product)
        .options(selectinload(Product.category))
        .where(Product.id.in_(product_ids))
    )
    product_info = {p.id: p for p in products_result.scalars().all()}

    rows = []
    performance_rows = sorted(  # PATCH26F: rank by what is left after returns
        performance_rows,
        key=lambda r: int(r.revenue_kobo or 0) - int((_ret.by_product.get(r.product_id) or {}).get("value", 0)),
        reverse=True,
    )
    for row in performance_rows:
        _pr = _ret.by_product.get(row.product_id) or {}
        net_qty = int(row.quantity_sold or 0) - int(_pr.get("qty", 0))
        net_rev = int(row.revenue_kobo or 0) - int(_pr.get("value", 0))
        net_cost = int(row.cost_kobo or 0) - int(_pr.get("cost_back", 0))
        if net_qty <= 0 and net_rev <= 0:
            continue
        product = product_info.get(row.product_id)
        if product is None:
            continue
        rows.append(
            ProductPerformanceRow(
                product_id=row.product_id,
                product_name=product.name,
                name=product.name,
                sku=product.sku,
                category_name=product.category.name if product.category else None,
                quantity_sold=net_qty,
                revenue_naira=max(net_rev, 0) / 100,
                cost_naira=net_cost / 100,
                profit_naira=(net_rev - net_cost) / 100,
                margin_percent=(
                    round((net_rev - net_cost) * 100 / net_rev, 1)
                    if net_rev > 0
                    else None
                ),
            )
        )
    return rows

# ---------------------------------------------------------------- Patch 26a
# Gross profit, plus the bookings / reserved-stock report.


def _scope_branch(current_user: User, branch_id: uuid.UUID | None) -> uuid.UUID | None:
    # Same rule as sales_summary: anyone who is not a super admin only ever
    # sees their own branch, whatever branch_id they pass.
    if current_user.role != "super_admin":
        return current_user.branch_id
    return branch_id


@router.get("/profit-summary", response_model=ProfitSummaryResponse)
async def profit_summary(
    branch_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> ProfitSummaryResponse:
    branch_id = _scope_branch(current_user, branch_id)

    sale_conds = cost_sale_conds()  # PATCH26F: partly/fully refunded count for cost; transfers never
    if branch_id:
        sale_conds.append(Sale.branch_id == branch_id)
    if date_from:
        sale_conds.append(Sale.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        sale_conds.append(
            Sale.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        )

    # Revenue is Sale.total_kobo, i.e. what was actually charged after any
    # sale-level discount.
    totals = (
        await session.execute(
            select(func.count(Sale.id), func.coalesce(func.sum(Sale.total_kobo), 0)).where(
                *sale_conds, Sale.status != "refunded"
            )
        )
    ).one()
    sales_count = int(totals[0] or 0)
    revenue_kobo = int(totals[1] or 0)
    # PATCH26F: take off what customers got back, at what they actually paid
    _ret = await fetch_returns(session, branch_id, date_from, date_to)
    refunds_kobo = _ret.total_sale_value
    revenue_kobo -= refunds_kobo

    # Cost is what the stock actually cost: the unit cost of the batch each
    # line was sold from, falling back to the product's current cost price
    # for lines that have no batch recorded.
    cost_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        SaleItem.quantity
                        * func.coalesce(StockBatch.unit_cost_kobo, Product.cost_price_kobo, 0)
                    ),
                    0,
                ),
                func.count(SaleItem.id),
                func.coalesce(func.sum(case((SaleItem.stock_batch_id.is_(None), 1), else_=0)), 0),
            )
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(Product, SaleItem.product_id == Product.id)
            .outerjoin(StockBatch, SaleItem.stock_batch_id == StockBatch.id)
            .where(*sale_conds)
        )
    ).one()
    cost_kobo = int(cost_row[0] or 0)
    cost_kobo -= _ret.cost_back_total  # PATCH26F: returned goods that went back on the shelf
    total_lines = int(cost_row[1] or 0)
    estimated_lines = int(cost_row[2] or 0)

    # Approved losses (damaged / expired / stolen) in the same period, same
    # rule the sales summary uses.
    loss_when = func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at)
    loss_conds = [
        StockAdjustment.value_kobo.is_not(None),
        StockAdjustment.status == "completed",
        not_spoiled_return_writeoff(),  # PATCH26F
    ]
    if branch_id:
        loss_conds.append(StockAdjustment.branch_id == branch_id)
    if date_from:
        loss_conds.append(loss_when >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        loss_conds.append(
            loss_when < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        )
    losses_kobo = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(StockAdjustment.value_kobo), 0)).where(*loss_conds)
            )
        ).scalar_one()
        or 0
    )

    gross_kobo = revenue_kobo - cost_kobo
    return ProfitSummaryResponse(
        sales_count=sales_count,
        revenue_naira=revenue_kobo / 100,
        cost_naira=cost_kobo / 100,
        refunds_naira=refunds_kobo / 100,
        gross_profit_naira=gross_kobo / 100,
        margin_percent=round(gross_kobo * 100 / revenue_kobo, 1) if revenue_kobo else None,
        losses_naira=losses_kobo / 100,
        net_profit_naira=(gross_kobo - losses_kobo) / 100,
        estimated_cost_lines=estimated_lines,
        total_lines=total_lines,
    )


def _booking_items_summary(lines, products) -> str:
    parts = []
    for line in lines[:3]:
        product = products.get(line.product_id)
        parts.append(f"{product.name if product else 'Item'} x{line.quantity}")
    if len(lines) > 3:
        parts.append(f"+{len(lines) - 3} more")
    return ", ".join(parts)


@router.get("/bookings-report", response_model=BookingsReportResponse)
async def bookings_report(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> BookingsReportResponse:
    branch_id = _scope_branch(current_user, branch_id)
    now = datetime.now(timezone.utc)

    # Open bookings (status 'booking') with their lines.
    booking_stmt = (
        select(Sale)
        .options(selectinload(Sale.items))
        .where(Sale.status == "booking")
        .order_by(Sale.created_at.asc())
    )
    if branch_id:
        booking_stmt = booking_stmt.where(Sale.branch_id == branch_id)
    bookings = (await session.execute(booking_stmt)).scalars().all()

    # Cancelled bookings whose deposit is still waiting to be sent back.
    refund_stmt = (
        select(Refund, Sale)
        .join(Sale, Refund.sale_id == Sale.id)
        .where(Refund.status == "pending", Sale.status == "cancelled")
        .order_by(Refund.created_at.asc())
    )
    if branch_id:
        refund_stmt = refund_stmt.where(Sale.branch_id == branch_id)
    pending_refunds = (await session.execute(refund_stmt)).all()

    # Customer pre-orders whose item has arrived: units held back from the POS.
    order_stmt = select(CustomerRequest).where(
        CustomerRequest.status == "arrived", CustomerRequest.hold_pieces > 0
    )
    if branch_id:
        order_stmt = order_stmt.where(CustomerRequest.branch_id == branch_id)
    customer_orders = (await session.execute(order_stmt)).scalars().all()

    # Lookups
    booking_ids = [b.id for b in bookings]
    paid_by_sale: dict = {}
    if booking_ids:
        paid_rows = await session.execute(
            select(SalePayment.sale_id, func.coalesce(func.sum(SalePayment.amount_kobo), 0))
            .where(SalePayment.sale_id.in_(booking_ids))
            .group_by(SalePayment.sale_id)
        )
        paid_by_sale = {r[0]: int(r[1] or 0) for r in paid_rows.all()}

    customer_ids = {b.customer_id for b in bookings if b.customer_id}
    customer_ids |= {sale.customer_id for _, sale in pending_refunds if sale.customer_id}
    customers: dict = {}
    if customer_ids:
        customers = {
            c.id: c
            for c in (await session.execute(select(Customer).where(Customer.id.in_(customer_ids))))
            .scalars()
            .all()
        }
    branch_names = {
        b.id: b.name for b in (await session.execute(select(Branch))).scalars().all()
    }

    product_ids = {line.product_id for b in bookings for line in b.items}
    product_ids |= {o.product_id for o in customer_orders if o.product_id}
    products: dict = {}
    if product_ids:
        products = {
            p.id: p
            for p in (await session.execute(select(Product).where(Product.id.in_(product_ids))))
            .scalars()
            .all()
        }

    # Open bookings
    open_rows = []
    held_by_booking: dict = {}
    deposits_total = 0
    balance_total = 0
    oldest_age = 0
    for b in bookings:
        created = b.created_at if b.created_at.tzinfo else b.created_at.replace(tzinfo=timezone.utc)
        age_days = max((now - created).days, 0)
        oldest_age = max(oldest_age, age_days)
        paid_kobo = paid_by_sale.get(b.id, 0)
        balance_kobo = max(int(b.total_kobo) - paid_kobo, 0)
        deposits_total += paid_kobo
        balance_total += balance_kobo
        units = 0
        for line in b.items:
            units += line.quantity
            held_by_booking[line.product_id] = held_by_booking.get(line.product_id, 0) + line.quantity
        customer = customers.get(b.customer_id) if b.customer_id else None
        open_rows.append(
            OpenBookingRow(
                booking_id=b.id,
                branch_id=b.branch_id,
                branch_name=branch_names.get(b.branch_id),
                customer_name=customer.full_name if customer else None,
                customer_phone=customer.phone if customer else None,
                items_summary=_booking_items_summary(b.items, products),
                units=units,
                total_naira=int(b.total_kobo) / 100,
                paid_naira=paid_kobo / 100,
                balance_naira=balance_kobo / 100,
                age_days=age_days,
                created_at=b.created_at,
                pickup_note=b.notes,
                pickup_date=b.pickup_date,
            )
        )
    open_rows.sort(key=lambda r: r.age_days, reverse=True)

    # Reserved stock per product: on hand vs what is promised to customers.
    held_by_order: dict = {}
    for o in customer_orders:
        if o.product_id:
            held_by_order[o.product_id] = held_by_order.get(o.product_id, 0) + (o.hold_pieces or 0)
    held_ids = set(held_by_booking) | set(held_by_order)
    on_hand: dict = {}
    if held_ids:
        stock_stmt = (
            select(StockBatch.product_id, func.coalesce(func.sum(StockBatch.quantity_remaining), 0))
            .where(StockBatch.product_id.in_(held_ids))
            .group_by(StockBatch.product_id)
        )
        if branch_id:
            stock_stmt = stock_stmt.where(StockBatch.branch_id == branch_id)
        on_hand = {r[0]: int(r[1] or 0) for r in (await session.execute(stock_stmt)).all()}
    reserved_rows = []
    for pid in held_ids:
        product = products.get(pid)
        if product is None:
            continue
        stock = on_hand.get(pid, 0)
        booked = held_by_booking.get(pid, 0)
        ordered = held_by_order.get(pid, 0)
        sellable = stock - booked - ordered
        reserved_rows.append(
            ReservedStockRow(
                product_id=pid,
                product_name=product.name,
                sku=product.sku,
                on_hand=stock,
                on_open_bookings=booked,
                on_customer_orders=ordered,
                sellable=sellable,
                over_committed=sellable < 0,
            )
        )
    reserved_rows.sort(key=lambda r: (not r.over_committed, -(r.on_open_bookings + r.on_customer_orders)))

    # Cancelled bookings awaiting a customer refund
    refund_rows = []
    refund_total = 0
    for refund, sale in pending_refunds:
        customer = customers.get(sale.customer_id) if sale.customer_id else None
        refund_total += int(refund.amount_kobo)
        refund_rows.append(
            CancelledBookingRow(
                booking_id=sale.id,
                branch_name=branch_names.get(sale.branch_id),
                customer_name=customer.full_name if customer else None,
                customer_phone=customer.phone if customer else None,
                amount_naira=int(refund.amount_kobo) / 100,
                cancelled_at=refund.created_at,
                reason=refund.reason,
            )
        )

    return BookingsReportResponse(
        open_count=len(open_rows),
        units_held=sum(held_by_booking.values()),
        deposits_held_naira=deposits_total / 100,
        balance_owed_naira=balance_total / 100,
        oldest_age_days=oldest_age,
        customer_order_units=sum(held_by_order.values()),
        awaiting_refund_count=len(refund_rows),
        awaiting_refund_naira=refund_total / 100,
        open_bookings=open_rows[:200],
        reserved_stock=reserved_rows[:100],
        awaiting_refund=refund_rows[:200],
    )


# ---------------------------------------------------------------- Patch 26b
# Sales breakdowns, returns & refunds, purchases & suppliers.

_LAGOS = timezone(timedelta(hours=1))  # Lagos has no daylight saving


def _period_conds(column, date_from, date_to):
    conds = []
    if date_from:
        conds.append(column >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conds.append(column < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    return conds


def _pct(part, whole):
    return round(part * 100 / whole, 1) if whole else None


def _bucket_sales_by_time(rows):
    # rows: (created_at, total_kobo). Returns 24 hour buckets and 7 weekday
    # buckets (Monday first), each as [sales_count, revenue_kobo], in Lagos time.
    hours = [[0, 0] for _ in range(24)]
    days = [[0, 0] for _ in range(7)]
    for created, total in rows:
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        local = created.astimezone(_LAGOS)
        hours[local.hour][0] += 1
        hours[local.hour][1] += int(total or 0)
        days[local.weekday()][0] += 1
        days[local.weekday()][1] += int(total or 0)
    return hours, days


def _effective_piece_cost(line):
    # Cost of ONE piece on an invoice line, so prices stay comparable between
    # deliveries even when one invoice was in cartons and the next in pieces.
    if line.cost_price_per_piece_kobo:
        return int(line.cost_price_per_piece_kobo)
    if (line.unit_label or "pieces") == "pieces" and not line.remainder_quantity and line.rate_kobo:
        return int(line.rate_kobo)
    return None


def _summarise_cost_trends(entries, names):
    # entries: (product_id, invoice_date, cost_kobo, supplier_label)
    grouped = {}
    for product_id, invoice_date, cost, supplier in entries:
        grouped.setdefault(product_id, []).append((invoice_date, cost, supplier))
    rows = []
    for product_id, purchases in grouped.items():
        if len(purchases) < 2 or product_id not in names:
            continue
        purchases.sort(key=lambda p: p[0])
        previous_cost = purchases[-2][1]
        latest_date, latest_cost, latest_supplier = purchases[-1]
        rows.append(
            {
                "product_id": product_id,
                "product_name": names[product_id],
                "purchases": len(purchases),
                "previous_cost_kobo": previous_cost,
                "latest_cost_kobo": latest_cost,
                "change_percent": round((latest_cost - previous_cost) * 100 / previous_cost, 1)
                if previous_cost
                else None,
                "latest_date": latest_date,
                "latest_supplier": latest_supplier,
            }
        )
    rows.sort(key=lambda r: abs(r["change_percent"] or 0), reverse=True)
    return rows


async def _sales_period_totals(session, branch_id, d_from, d_to) -> SalesPeriodTotals:
    conds = [*revenue_sale_conds(), *_period_conds(Sale.created_at, d_from, d_to)]  # PATCH26F
    if branch_id:
        conds.append(Sale.branch_id == branch_id)
    row = (
        await session.execute(
            select(
                func.count(Sale.id),
                func.coalesce(func.sum(Sale.total_kobo), 0),
                func.coalesce(func.sum(Sale.discount_kobo), 0),
            ).where(*conds)
        )
    ).one()
    units = (
        await session.execute(
            select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .select_from(SaleItem)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(*conds)
        )
    ).scalar_one()
    count = int(row[0] or 0)
    revenue_kobo = int(row[1] or 0)
    _ret = await fetch_returns(session, branch_id, d_from, d_to)  # PATCH26F
    revenue_kobo -= _ret.total_sale_value
    return SalesPeriodTotals(
        sales_count=count,
        units=max(int(units or 0) - _ret.revenue_units, 0),
        revenue_naira=revenue_kobo / 100,
        avg_sale_naira=(revenue_kobo / count / 100) if count else 0,
        discounts_naira=int(row[2] or 0) / 100,
    )


@router.get("/sales-breakdown", response_model=SalesBreakdownResponse)
async def sales_breakdown(
    date_from: date = Query(...),
    date_to: date = Query(...),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> SalesBreakdownResponse:
    branch_id = _scope_branch(current_user, branch_id)

    # The comparison period is the same number of days straight before.
    length = (date_to - date_from).days + 1
    previous_to = date_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=length - 1)

    current = await _sales_period_totals(session, branch_id, date_from, date_to)
    previous = await _sales_period_totals(session, branch_id, previous_from, previous_to)

    # PATCH26F: payment, hour and weekday tables stay "money taken at the till" (partly refunded
    # sales included, fully refunded left out as before); the category table is netted.
    conds = [*revenue_sale_conds(), *_period_conds(Sale.created_at, date_from, date_to)]
    cat_conds = [*cost_sale_conds(), *_period_conds(Sale.created_at, date_from, date_to)]
    if branch_id:
        cat_conds.append(Sale.branch_id == branch_id)
    if branch_id:
        conds.append(Sale.branch_id == branch_id)

    # --- by payment method: sale_payments rows, plus older sales that have none
    methods: dict = {}

    def add_method(method, amount, count):
        key = (method or "unknown").strip().lower() or "unknown"
        entry = methods.setdefault(key, [0, 0])
        entry[0] += int(amount or 0)
        entry[1] += int(count or 0)

    pay_rows = await session.execute(
        select(SalePayment.method, func.sum(SalePayment.amount_kobo), func.count(SalePayment.id))
        .select_from(SalePayment)
        .join(Sale, SalePayment.sale_id == Sale.id)
        .where(*conds)
        .group_by(SalePayment.method)
    )
    for method, amount, count in pay_rows.all():
        add_method(method, amount, count)
    legacy_rows = await session.execute(
        select(Sale.payment_method, func.sum(Sale.total_kobo), func.count(Sale.id))
        .where(*conds, ~exists().where(SalePayment.sale_id == Sale.id))
        .group_by(Sale.payment_method)
    )
    for method, amount, count in legacy_rows.all():
        add_method(method, amount, count)
    method_total = sum(v[0] for v in methods.values())
    payment_rows = [
        PaymentMethodRow(
            method=key,
            amount_naira=v[0] / 100,
            payments=v[1],
            share_percent=_pct(v[0], method_total),
        )
        for key, v in sorted(methods.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

    # --- by category (line totals, before any sale-level discount)
    cost_expr = func.sum(
        SaleItem.quantity * func.coalesce(StockBatch.unit_cost_kobo, Product.cost_price_kobo, 0)
    )
    cat_rows = await session.execute(
        select(
            Category.name,
            func.coalesce(func.sum(SaleItem.quantity), 0),
            func.coalesce(func.sum(SaleItem.line_total_kobo), 0),
            func.coalesce(cost_expr, 0),
        )
        .select_from(SaleItem)
        .join(Sale, SaleItem.sale_id == Sale.id)
        .join(Product, SaleItem.product_id == Product.id)
        .outerjoin(Category, Product.category_id == Category.id)
        .outerjoin(StockBatch, SaleItem.stock_batch_id == StockBatch.id)
        .where(*cat_conds)
        .group_by(Category.name)
        .order_by(func.sum(SaleItem.line_total_kobo).desc())
    )
    category_rows = []
    cat_returns = await fetch_returns(session, branch_id, date_from, date_to)  # PATCH26F
    for name, units, revenue, cost in cat_rows.all():
        _cr = cat_returns.by_category.get(name) or {}
        units = int(units or 0) - int(_cr.get("qty", 0))
        revenue = int(revenue or 0) - int(_cr.get("value", 0))
        cost = int(cost or 0) - int(_cr.get("cost_back", 0))
        if units <= 0 and revenue <= 0:
            continue
        revenue = int(revenue or 0)
        cost = int(cost or 0)
        category_rows.append(
            CategorySalesRow(
                category_name=name or "Uncategorised",
                units=int(units or 0),
                revenue_naira=revenue / 100,
                cost_naira=cost / 100,
                profit_naira=(revenue - cost) / 100,
                margin_percent=_pct(revenue - cost, revenue),
            )
        )

    # --- by hour of day and day of week (Lagos time)
    stamp_rows = (await session.execute(select(Sale.created_at, Sale.total_kobo).where(*conds))).all()
    hours, days = _bucket_sales_by_time(stamp_rows)

    return SalesBreakdownResponse(
        date_from=date_from,
        date_to=date_to,
        previous_from=previous_from,
        previous_to=previous_to,
        current=current,
        previous=previous,
        by_payment_method=payment_rows,
        by_category=category_rows,
        by_hour=[HourBucketRow(hour=h, sales_count=v[0], revenue_naira=v[1] / 100) for h, v in enumerate(hours)],
        by_weekday=[
            WeekdayBucketRow(weekday=i + 1, sales_count=v[0], revenue_naira=v[1] / 100)
            for i, v in enumerate(days)
        ],
    )


@router.get("/returns-report", response_model=ReturnsReportResponse)
async def returns_report(
    date_from: date = Query(...),
    date_to: date = Query(...),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> ReturnsReportResponse:
    branch_id = _scope_branch(current_user, branch_id)

    # Cancelled bookings also create refunds (the deposit going back) -- those
    # are not returns, and are covered in the bookings section instead.
    refund_conds = [Sale.status != "cancelled", *_period_conds(Refund.created_at, date_from, date_to)]
    if branch_id:
        refund_conds.append(Sale.branch_id == branch_id)
    refunds = (
        (
            await session.execute(
                select(Refund)
                .join(Sale, Refund.sale_id == Sale.id)
                .where(*refund_conds)
                .order_by(Refund.created_at.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )

    # Base for the refund rate: every sale in the same period.
    base_conds = [
        Sale.channel != "transfer",  # PATCH26F
        Sale.status.in_(["completed", "partially_refunded", "refunded"]),
        *_period_conds(Sale.created_at, date_from, date_to),
    ]
    if branch_id:
        base_conds.append(Sale.branch_id == branch_id)
    base = (
        await session.execute(
            select(func.count(Sale.id), func.coalesce(func.sum(Sale.total_kobo), 0)).where(*base_conds)
        )
    ).one()
    sales_count = int(base[0] or 0)
    sales_revenue_kobo = int(base[1] or 0)

    # Refunds waiting to be paid out by transfer (not limited to the period).
    pending_conds = [Refund.status == "pending", Sale.status != "cancelled"]
    if branch_id:
        pending_conds.append(Sale.branch_id == branch_id)
    pending = (
        await session.execute(
            select(func.count(Refund.id), func.coalesce(func.sum(Refund.amount_kobo), 0))
            .select_from(Refund)
            .join(Sale, Refund.sale_id == Sale.id)
            .where(*pending_conds)
        )
    ).one()

    refunded_kobo = sum(int(r.amount_kobo or 0) for r in refunds)

    # by payout method
    actions: dict = {}
    for r in refunds:
        key = (r.action or "other").strip().lower() or "other"
        entry = actions.setdefault(key, [0, 0])
        entry[0] += 1
        entry[1] += int(r.amount_kobo or 0)
    action_rows = [
        ReturnsByActionRow(action=k, count=v[0], amount_naira=v[1] / 100)
        for k, v in sorted(actions.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

    # by reason (free text, so grouped ignoring case and spacing)
    reasons: dict = {}
    for r in refunds:
        raw = (r.reason or "").strip()
        key = raw.lower() or "(no reason given)"
        entry = reasons.setdefault(key, {"label": raw or "(no reason given)", "count": 0, "kobo": 0})
        entry["count"] += 1
        entry["kobo"] += int(r.amount_kobo or 0)
    reason_rows = [
        ReturnsByReasonRow(reason=v["label"], count=v["count"], amount_naira=v["kobo"] / 100)
        for v in sorted(reasons.values(), key=lambda v: v["count"], reverse=True)[:10]
    ]

    # by staff member who processed it
    staff_ids = {r.processed_by for r in refunds if r.processed_by}
    staff_names: dict = {}
    if staff_ids:
        staff_names = {
            u.id: u.full_name
            for u in (await session.execute(select(User).where(User.id.in_(staff_ids)))).scalars().all()
        }
    staff: dict = {}
    for r in refunds:
        name = staff_names.get(r.processed_by) if r.processed_by else None
        entry = staff.setdefault(name or "Unknown", [0, 0])
        entry[0] += 1
        entry[1] += int(r.amount_kobo or 0)
    staff_rows = [
        ReturnsByStaffRow(staff_name=k, count=v[0], amount_naira=v[1] / 100)
        for k, v in sorted(staff.items(), key=lambda kv: kv[1][0], reverse=True)[:15]
    ]

    # by product (from the itemised refund lines)
    item_conds = [Sale.status != "cancelled", *_period_conds(Refund.created_at, date_from, date_to)]
    if branch_id:
        item_conds.append(Sale.branch_id == branch_id)
    item_rows = (
        await session.execute(
            select(
                SaleItem.product_id,
                func.coalesce(func.sum(RefundItem.quantity), 0),
                func.coalesce(func.sum(RefundItem.line_total_kobo), 0),
                func.coalesce(func.sum(case((RefundItem.restocked.is_(True), RefundItem.quantity), else_=0)), 0),
                func.coalesce(func.sum(case((RefundItem.spoiled.is_(True), RefundItem.quantity), else_=0)), 0),
            )
            .select_from(RefundItem)
            .join(Refund, RefundItem.refund_id == Refund.id)
            .join(Sale, Refund.sale_id == Sale.id)
            .join(SaleItem, RefundItem.sale_item_id == SaleItem.id)
            .where(*item_conds)
            .group_by(SaleItem.product_id)
            .order_by(func.sum(RefundItem.line_total_kobo).desc())
            .limit(25)
        )
    ).all()
    product_rows = []
    if item_rows:
        returned_ids = [r[0] for r in item_rows]
        names = {
            p.id: p.name
            for p in (await session.execute(select(Product).where(Product.id.in_(returned_ids)))).scalars().all()
        }
        sold = {
            r[0]: int(r[1] or 0)
            for r in (
                await session.execute(
                    select(SaleItem.product_id, func.coalesce(func.sum(SaleItem.quantity), 0))
                    .select_from(SaleItem)
                    .join(Sale, SaleItem.sale_id == Sale.id)
                    .where(*base_conds, SaleItem.product_id.in_(returned_ids))
                    .group_by(SaleItem.product_id)
                )
            ).all()
        }
        for product_id, units, value, restocked, spoiled in item_rows:
            if product_id not in names:
                continue
            units_sold = sold.get(product_id, 0)
            product_rows.append(
                ReturnedProductRow(
                    product_id=product_id,
                    product_name=names[product_id],
                    units_returned=int(units or 0),
                    refund_value_naira=int(value or 0) / 100,
                    units_sold=units_sold,
                    return_rate_percent=_pct(int(units or 0), units_sold),
                    restocked_units=int(restocked or 0),
                    spoiled_units=int(spoiled or 0),
                )
            )

    return ReturnsReportResponse(
        refund_count=len(refunds),
        refunded_naira=refunded_kobo / 100,
        sales_count=sales_count,
        sales_revenue_naira=sales_revenue_kobo / 100,
        rate_by_count_percent=_pct(len(refunds), sales_count),
        rate_by_value_percent=_pct(refunded_kobo, sales_revenue_kobo),
        pending_count=int(pending[0] or 0),
        pending_naira=int(pending[1] or 0) / 100,
        by_action=action_rows,
        by_reason=reason_rows,
        by_staff=staff_rows,
        by_product=product_rows,
    )


@router.get("/purchases-report", response_model=PurchasesReportResponse)
async def purchases_report(
    date_from: date = Query(...),
    date_to: date = Query(...),
    trend_days: int = Query(default=365, ge=30, le=1825),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> PurchasesReportResponse:
    branch_id = _scope_branch(current_user, branch_id)

    # Filed supplier invoices only (not drafts, not the paperwork a branch
    # transfer generates).
    base_conds = [
        PurchaseInvoice.status == "completed",
        or_(PurchaseInvoice.source.is_(None), PurchaseInvoice.source != "branch_transfer"),
    ]
    if branch_id:
        base_conds.append(PurchaseInvoice.branch_id == branch_id)

    invoices = (
        (
            await session.execute(
                select(PurchaseInvoice)
                .where(
                    *base_conds,
                    PurchaseInvoice.invoice_date >= date_from,
                    PurchaseInvoice.invoice_date <= date_to,
                )
                .order_by(PurchaseInvoice.invoice_date.asc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )

    supplier_ids = {i.supplier_id for i in invoices if i.supplier_id}
    registry: dict = {}
    if supplier_ids:
        registry = {
            s.id: s.name
            for s in (await session.execute(select(Supplier).where(Supplier.id.in_(supplier_ids)))).scalars().all()
        }

    def supplier_label(inv):
        if inv.supplier_id and inv.supplier_id in registry:
            return registry[inv.supplier_id]
        return (inv.supplier_name or "").strip() or "Unknown supplier"

    totals = {"goods": 0, "extra": 0, "paid": 0, "owed": 0, "adjust": 0}
    suppliers: dict = {}
    months: dict = {}
    unpaid = []
    for inv in invoices:
        goods = int(inv.amount_kobo or 0)
        extra = int(inv.waybill_cost_kobo or 0) + int(inv.transit_cost_kobo or 0) + int(inv.handling_cost_kobo or 0)
        paid = int(inv.amount_paid_kobo or 0)
        owed = max(goods - paid, 0)
        totals["goods"] += goods
        totals["extra"] += extra
        totals["paid"] += paid
        totals["owed"] += owed
        totals["adjust"] += int(inv.adjustment_amount_kobo or 0)

        label = supplier_label(inv)
        key = str(inv.supplier_id) if inv.supplier_id else label.lower()
        s = suppliers.setdefault(
            key,
            {"name": label, "invoices": 0, "goods": 0, "extra": 0, "paid": 0, "owed": 0, "last": None},
        )
        s["invoices"] += 1
        s["goods"] += goods
        s["extra"] += extra
        s["paid"] += paid
        s["owed"] += owed
        if s["last"] is None or inv.invoice_date > s["last"]:
            s["last"] = inv.invoice_date

        month_key = inv.invoice_date.strftime("%Y-%m")
        m = months.setdefault(month_key, {"invoices": 0, "goods": 0, "extra": 0})
        m["invoices"] += 1
        m["goods"] += goods
        m["extra"] += extra

        if owed > 0:
            unpaid.append(
                UnpaidInvoiceRow(
                    invoice_id=inv.id,
                    supplier_name=label,
                    invoice_date=inv.invoice_date,
                    amount_naira=goods / 100,
                    paid_naira=paid / 100,
                    balance_naira=owed / 100,
                )
            )

    supplier_rows = [
        SupplierSpendRow(
            supplier_name=s["name"],
            invoices=s["invoices"],
            goods_naira=s["goods"] / 100,
            extra_cost_naira=s["extra"] / 100,
            total_naira=(s["goods"] + s["extra"]) / 100,
            paid_naira=s["paid"] / 100,
            balance_owed_naira=s["owed"] / 100,
            extra_cost_percent=_pct(s["extra"], s["goods"]),
            last_purchase_date=s["last"],
        )
        for s in sorted(suppliers.values(), key=lambda s: s["goods"] + s["extra"], reverse=True)
    ]
    month_rows = [
        PurchaseMonthRow(
            month=k,
            invoices=v["invoices"],
            goods_naira=v["goods"] / 100,
            extra_cost_naira=v["extra"] / 100,
            total_naira=(v["goods"] + v["extra"]) / 100,
        )
        for k, v in sorted(months.items())
    ]

    # Cost trends: per-piece cost of each product across recent deliveries
    # (a longer look-back than the period, so there is something to compare).
    trend_from = date_to - timedelta(days=trend_days)
    line_rows = (
        await session.execute(
            select(PurchaseInvoiceLineItem, PurchaseInvoice.invoice_date, PurchaseInvoice.supplier_id, PurchaseInvoice.supplier_name)
            .join(PurchaseInvoice, PurchaseInvoiceLineItem.invoice_id == PurchaseInvoice.id)
            .where(
                *base_conds,
                PurchaseInvoice.invoice_date >= trend_from,
                PurchaseInvoice.invoice_date <= date_to,
                PurchaseInvoiceLineItem.product_id.is_not(None),
            )
        )
    ).all()
    trend_entries = []
    trend_supplier_ids = {r[2] for r in line_rows if r[2]}
    trend_registry = dict(registry)
    missing = trend_supplier_ids - set(registry)
    if missing:
        trend_registry.update(
            {s.id: s.name for s in (await session.execute(select(Supplier).where(Supplier.id.in_(missing)))).scalars().all()}
        )
    for line, invoice_date, supplier_id, supplier_name in line_rows:
        cost = _effective_piece_cost(line)
        if cost is None:
            continue
        label = trend_registry.get(supplier_id) if supplier_id else None
        trend_entries.append((line.product_id, invoice_date, cost, label or (supplier_name or "").strip() or None))
    trend_names: dict = {}
    trend_product_ids = {e[0] for e in trend_entries}
    if trend_product_ids:
        trend_names = {
            p.id: p.name
            for p in (await session.execute(select(Product).where(Product.id.in_(trend_product_ids)))).scalars().all()
        }
    trend_rows = [
        CostTrendRow(
            product_id=t["product_id"],
            product_name=t["product_name"],
            purchases=t["purchases"],
            previous_cost_naira=t["previous_cost_kobo"] / 100,
            latest_cost_naira=t["latest_cost_kobo"] / 100,
            change_percent=t["change_percent"],
            latest_date=t["latest_date"],
            latest_supplier=t["latest_supplier"],
        )
        for t in _summarise_cost_trends(trend_entries, trend_names)[:20]
    ]

    return PurchasesReportResponse(
        invoice_count=len(invoices),
        goods_naira=totals["goods"] / 100,
        extra_cost_naira=totals["extra"] / 100,
        total_naira=(totals["goods"] + totals["extra"]) / 100,
        paid_naira=totals["paid"] / 100,
        balance_owed_naira=totals["owed"] / 100,
        adjustments_naira=totals["adjust"] / 100,
        by_supplier=supplier_rows,
        by_month=month_rows,
        unpaid_invoices=unpaid[:15],
        cost_trends=trend_rows,
    )


# ---------------------------------------------------------------- Patch 26c
# Stock movement history, transfers & receiving, stock count variance.

_ADJUSTMENT_LABELS = {
    "damaged": "Damaged",
    "expired": "Expired",
    "theft": "Theft",
    "recount": "Stock count",
    "other": "Other adjustment",
    "missing": "Missing",
    "defective": "Defective",
    "supplier_return": "Returned to supplier",
}
_TRANSFER_LABELS = {
    "branch_to_branch": "Branch to branch",
    "store_to_warehouse": "Store to warehouse",
    "warehouse_to_store": "Warehouse to store",
    "warehouse_to_branch": "Warehouse to branch",
    "receipt_to_warehouse": "Delivery to warehouse",
    "receipt_to_shelf": "Delivery to shelf",
    "shelf_to_warehouse": "Shelf to warehouse",
}


def _label(mapping, key):
    return mapping.get(key) or (key or "").replace("_", " ").capitalize() or "Other"


def _as_utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _merge_events(events, limit):
    # Newest first; returns (events kept, whether any were cut off).
    ordered = sorted(events, key=lambda e: _as_utc(e["occurred_at"]), reverse=True)
    return ordered[:limit], len(ordered) > limit


def _transfer_direction(from_branch, to_branch, branch_id):
    if branch_id and from_branch != to_branch:
        if to_branch == branch_id:
            return "in"
        if from_branch == branch_id:
            return "out"
    return "moved"


def _variance_value(delta, cost_kobo):
    return abs(int(delta or 0)) * int(cost_kobo or 0)


async def _branch_names(session):
    return {b.id: b.name for b in (await session.execute(select(Branch))).scalars().all()}


async def _user_names(session, ids):
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {
        u.id: u.full_name
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    }


@router.get("/stock-movements", response_model=StockMovementsResponse)
async def stock_movements(
    product_id: uuid.UUID = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> StockMovementsResponse:
    branch_id = _scope_branch(current_user, branch_id)
    product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    events = []

    # Goods received (posted receipts only)
    received_when = func.coalesce(GoodsReceipt.posted_at, GoodsReceipt.delivered_at, GoodsReceipt.created_at)
    recv_stmt = (
        select(
            GoodsReceiptLine.quantity_received,
            GoodsReceipt.supplier_name,
            GoodsReceipt.invoice_reference,
            GoodsReceipt.branch_id,
            GoodsReceipt.recorded_by,
            received_when,
        )
        .join(GoodsReceipt, GoodsReceiptLine.receipt_id == GoodsReceipt.id)
        .where(
            GoodsReceiptLine.product_id == product_id,
            GoodsReceipt.status == "posted",
            GoodsReceiptLine.quantity_received > 0,
            *_period_conds(received_when, date_from, date_to),
        )
        .order_by(received_when.desc())
        .limit(300)
    )
    if branch_id:
        recv_stmt = recv_stmt.where(GoodsReceipt.branch_id == branch_id)
    for qty, supplier, reference, b_id, by, occurred in (await session.execute(recv_stmt)).all():
        events.append(
            {
                "occurred_at": occurred,
                "kind": "received",
                "label": f"Received from {supplier}" if supplier else "Goods received",
                "branch_id": b_id,
                "quantity": int(qty),
                "direction": "in",
                "by_id": by,
                "detail": f"Invoice {reference}" if reference else None,
            }
        )

    # Sold
    sale_stmt = (
        select(SaleItem.quantity, Sale.id, Sale.created_at, Sale.branch_id, Sale.served_by)
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(
            SaleItem.product_id == product_id,
            Sale.status.in_(["completed", "partially_refunded", "refunded"]),
            *_period_conds(Sale.created_at, date_from, date_to),
        )
        .order_by(Sale.created_at.desc())
        .limit(300)
    )
    if branch_id:
        sale_stmt = sale_stmt.where(Sale.branch_id == branch_id)
    for qty, sale_id, occurred, b_id, by in (await session.execute(sale_stmt)).all():
        events.append(
            {
                "occurred_at": occurred,
                "kind": "sold",
                "label": "Sold",
                "branch_id": b_id,
                "quantity": -int(qty),
                "direction": "out",
                "by_id": by,
                "detail": f"Sale {str(sale_id)[:8]}",
            }
        )

    # Customer returns
    return_stmt = (
        select(
            RefundItem.quantity,
            RefundItem.restocked,
            RefundItem.spoiled,
            Refund.created_at,
            Refund.processed_by,
            Sale.branch_id,
            Refund.reason,
        )
        .select_from(RefundItem)
        .join(Refund, RefundItem.refund_id == Refund.id)
        .join(Sale, Refund.sale_id == Sale.id)
        .join(SaleItem, RefundItem.sale_item_id == SaleItem.id)
        .where(
            SaleItem.product_id == product_id,
            Sale.status != "cancelled",
            *_period_conds(Refund.created_at, date_from, date_to),
        )
        .order_by(Refund.created_at.desc())
        .limit(300)
    )
    if branch_id:
        return_stmt = return_stmt.where(Sale.branch_id == branch_id)
    for qty, restocked, spoiled, occurred, by, b_id, reason in (await session.execute(return_stmt)).all():
        if restocked:
            label, signed, kind = "Returned by customer (back in stock)", int(qty), "refund_restock"
        elif spoiled:
            label, signed, kind = "Returned by customer (spoiled)", 0, "refund_other"
        else:
            label, signed, kind = "Returned by customer (not restocked)", 0, "refund_other"
        events.append(
            {
                "occurred_at": occurred,
                "kind": kind,
                "label": label,
                "branch_id": b_id,
                "quantity": signed,
                "direction": "in" if signed else "moved",
                "by_id": by,
                "detail": reason,
            }
        )

    # Adjustments (damage, expiry, theft, stock counts, ...)
    adj_stmt = (
        select(
            StockAdjustment.quantity_delta,
            StockAdjustment.reason,
            StockAdjustment.created_at,
            StockAdjustment.adjusted_by,
            StockAdjustment.branch_id,
            StockAdjustment.notes,
        )
        .join(StockBatch, StockAdjustment.stock_batch_id == StockBatch.id)
        .where(
            StockBatch.product_id == product_id,
            StockAdjustment.status == "completed",
            *_period_conds(StockAdjustment.created_at, date_from, date_to),
        )
        .order_by(StockAdjustment.created_at.desc())
        .limit(300)
    )
    if branch_id:
        adj_stmt = adj_stmt.where(StockAdjustment.branch_id == branch_id)
    for delta, reason, occurred, by, b_id, notes in (await session.execute(adj_stmt)).all():
        events.append(
            {
                "occurred_at": occurred,
                "kind": "adjustment",
                "label": _label(_ADJUSTMENT_LABELS, reason),
                "branch_id": b_id,
                "quantity": int(delta),
                "direction": "in" if delta > 0 else "out",
                "by_id": by,
                "detail": notes,
            }
        )

    # Transfers between branches / warehouse / shelf
    transfer_stmt = (
        select(StockTransfer)
        .where(
            StockTransfer.product_id == product_id,
            *_period_conds(StockTransfer.created_at, date_from, date_to),
        )
        .order_by(StockTransfer.created_at.desc())
        .limit(300)
    )
    if branch_id:
        transfer_stmt = transfer_stmt.where(
            or_(StockTransfer.from_branch_id == branch_id, StockTransfer.to_branch_id == branch_id)
        )
    for t in (await session.execute(transfer_stmt)).scalars().all():
        direction = _transfer_direction(t.from_branch_id, t.to_branch_id, branch_id)
        qty = int(t.quantity_pieces or 0)
        events.append(
            {
                "occurred_at": t.created_at,
                "kind": "transfer",
                "label": _label(_TRANSFER_LABELS, t.transfer_type),
                "branch_id": None,
                "from_id": t.from_branch_id,
                "to_id": t.to_branch_id,
                "quantity": -qty if direction == "out" else qty,
                "direction": direction,
                "by_id": t.created_by,
                "detail": t.notes,
            }
        )

    # Transfer returns that completed (stock sent back)
    tr_stmt = (
        select(TransferReturn)
        .where(
            TransferReturn.product_id == product_id,
            TransferReturn.status == "completed",
            *_period_conds(TransferReturn.requested_at, date_from, date_to),
        )
        .order_by(TransferReturn.requested_at.desc())
        .limit(100)
    )
    if branch_id:
        tr_stmt = tr_stmt.where(
            or_(TransferReturn.from_branch_id == branch_id, TransferReturn.to_branch_id == branch_id)
        )
    for r in (await session.execute(tr_stmt)).scalars().all():
        # Goods travel back from the receiving side to the sending side.
        direction = _transfer_direction(r.to_branch_id, r.from_branch_id, branch_id)
        qty = int(r.quantity_pieces or 0)
        events.append(
            {
                "occurred_at": r.approved_at or r.responded_at or r.requested_at,
                "kind": "transfer_return",
                "label": "Transfer return",
                "branch_id": None,
                "from_id": r.to_branch_id,
                "to_id": r.from_branch_id,
                "quantity": -qty if direction == "out" else qty,
                "direction": direction,
                "by_id": r.requested_by,
                "detail": r.note,
            }
        )

    kept, truncated = _merge_events(events, 400)
    branch_names = await _branch_names(session)
    user_names = await _user_names(session, {e.get("by_id") for e in kept})

    rows = []
    for e in kept:
        if e.get("from_id") or e.get("to_id"):
            location = f"{branch_names.get(e.get('from_id'), '?')} -> {branch_names.get(e.get('to_id'), '?')}"
        else:
            location = branch_names.get(e.get("branch_id"))
        rows.append(
            MovementEvent(
                occurred_at=e["occurred_at"],
                kind=e["kind"],
                label=e["label"],
                location=location,
                quantity=e["quantity"],
                direction=e["direction"],
                by_name=user_names.get(e.get("by_id")),
                detail=e.get("detail"),
            )
        )

    return StockMovementsResponse(
        product_id=product.id,
        product_name=product.name,
        sku=product.sku,
        received_units=sum(e["quantity"] for e in kept if e["kind"] == "received"),
        sold_units=-sum(e["quantity"] for e in kept if e["kind"] == "sold"),
        restocked_units=sum(e["quantity"] for e in kept if e["kind"] == "refund_restock"),
        adjusted_net_units=sum(e["quantity"] for e in kept if e["kind"] == "adjustment"),
        transferred_units=sum(abs(e["quantity"]) for e in kept if e["kind"] == "transfer"),
        transfer_count=sum(1 for e in kept if e["kind"] == "transfer"),
        events=rows,
        truncated=truncated,
    )


@router.get("/transfers-report", response_model=TransfersReportResponse)
async def transfers_report(
    date_from: date = Query(...),
    date_to: date = Query(...),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> TransfersReportResponse:
    branch_id = _scope_branch(current_user, branch_id)
    branch_names = await _branch_names(session)

    # --- transfers in the window
    t_conds = _period_conds(StockTransfer.created_at, date_from, date_to)
    if branch_id:
        t_conds.append(or_(StockTransfer.from_branch_id == branch_id, StockTransfer.to_branch_id == branch_id))
    transfers = (
        (
            await session.execute(
                select(StockTransfer).where(*t_conds).order_by(StockTransfer.created_at.desc()).limit(5000)
            )
        )
        .scalars()
        .all()
    )

    # --- return requests (transfer returns)
    ret_branch = (
        [or_(TransferReturn.from_branch_id == branch_id, TransferReturn.to_branch_id == branch_id)]
        if branch_id
        else []
    )
    waiting = (
        (
            await session.execute(
                select(TransferReturn)
                .where(TransferReturn.status == "requested", *ret_branch)
                .order_by(TransferReturn.requested_at.asc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    waiting_total = (
        await session.execute(
            select(func.count(TransferReturn.id), func.coalesce(func.sum(TransferReturn.amount_kobo), 0)).where(
                TransferReturn.status == "requested", *ret_branch
            )
        )
    ).one()
    status_rows = (
        await session.execute(
            select(
                TransferReturn.status,
                func.count(TransferReturn.id),
                func.coalesce(func.sum(TransferReturn.amount_kobo), 0),
            )
            .where(*_period_conds(TransferReturn.requested_at, date_from, date_to), *ret_branch)
            .group_by(TransferReturn.status)
        )
    ).all()

    # --- deliveries received short of what the invoice said
    received_when = func.coalesce(GoodsReceipt.posted_at, GoodsReceipt.delivered_at, GoodsReceipt.created_at)
    short_stmt = (
        select(GoodsReceiptLine, GoodsReceipt, received_when)
        .join(GoodsReceipt, GoodsReceiptLine.receipt_id == GoodsReceipt.id)
        .where(
            GoodsReceipt.status == "posted",
            GoodsReceiptLine.quantity_invoiced.is_not(None),
            GoodsReceiptLine.quantity_received < GoodsReceiptLine.quantity_invoiced,
            *_period_conds(received_when, date_from, date_to),
        )
        .order_by((GoodsReceiptLine.quantity_invoiced - GoodsReceiptLine.quantity_received).desc())
        .limit(200)
    )
    if branch_id:
        short_stmt = short_stmt.where(GoodsReceipt.branch_id == branch_id)
    short_rows = (await session.execute(short_stmt)).all()

    # --- damaged / missing counts on the waybill complaints
    intake_conds = _period_conds(IntakeReceipt.received_at, date_from, date_to)
    if branch_id:
        intake_conds.append(IntakeReceipt.branch_id == branch_id)
    intake = (
        await session.execute(
            select(
                func.count(IntakeReceipt.id),
                func.coalesce(func.sum(IntakeReceipt.damaged_count), 0),
                func.coalesce(func.sum(IntakeReceipt.missing_count), 0),
            ).where(*intake_conds)
        )
    ).one()

    # --- names
    product_ids = {t.product_id for t in transfers[:25]}
    product_ids |= {t.product_id for t in transfers}
    product_ids |= {w.product_id for w in waiting}
    product_ids |= {row[0].product_id for row in short_rows}
    products = {}
    if product_ids:
        products = {
            p.id: p.name
            for p in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
        }
    user_names = await _user_names(session, {t.created_by for t in transfers[:25]})

    # --- aggregate transfers
    types: dict = {}
    routes: dict = {}
    per_product: dict = {}
    for t in transfers:
        units = int(t.quantity_pieces or 0)
        value = int(t.total_cost_kobo or 0)
        entry = types.setdefault(t.transfer_type, [0, 0])
        entry[0] += 1
        entry[1] += units
        if t.from_branch_id != t.to_branch_id:
            route = routes.setdefault((t.from_branch_id, t.to_branch_id), [0, 0, 0])
            route[0] += 1
            route[1] += units
            route[2] += value
        pp = per_product.setdefault(t.product_id, [0, 0])
        pp[0] += 1
        pp[1] += units

    short_value_kobo = 0
    short_units = 0
    short_list = []
    for line, receipt, occurred in short_rows:
        short = int(line.quantity_invoiced or 0) - int(line.quantity_received or 0)
        value = short * int(line.unit_cost_kobo or 0)
        short_units += short
        short_value_kobo += value
        short_list.append(
            ShortDeliveryRow(
                occurred_at=occurred,
                branch_name=branch_names.get(receipt.branch_id),
                supplier_name=receipt.supplier_name,
                product_name=products.get(line.product_id, "Unknown product"),
                invoiced=int(line.quantity_invoiced or 0),
                received=int(line.quantity_received or 0),
                short=short,
                short_value_naira=value / 100,
            )
        )

    return TransfersReportResponse(
        transfer_count=len(transfers),
        units_moved=sum(int(t.quantity_pieces or 0) for t in transfers),
        value_naira=sum(int(t.total_cost_kobo or 0) for t in transfers) / 100,
        return_requests_waiting=int(waiting_total[0] or 0),
        return_requests_waiting_naira=int(waiting_total[1] or 0) / 100,
        short_lines=len(short_rows),
        short_units=short_units,
        short_value_naira=short_value_kobo / 100,
        intake_receipts=int(intake[0] or 0),
        intake_damaged_units=int(intake[1] or 0),
        intake_missing_units=int(intake[2] or 0),
        by_type=[
            TransferTypeRow(
                transfer_type=k, label=_label(_TRANSFER_LABELS, k), count=v[0], units=v[1]
            )
            for k, v in sorted(types.items(), key=lambda kv: kv[1][0], reverse=True)
        ],
        by_route=[
            TransferRouteRow(
                from_name=branch_names.get(k[0], "Unknown"),
                to_name=branch_names.get(k[1], "Unknown"),
                count=v[0],
                units=v[1],
                value_naira=v[2] / 100,
            )
            for k, v in sorted(routes.items(), key=lambda kv: kv[1][1], reverse=True)[:15]
        ],
        top_products=[
            TransferProductRow(product_name=products.get(pid, "Unknown product"), transfers=v[0], units=v[1])
            for pid, v in sorted(per_product.items(), key=lambda kv: kv[1][1], reverse=True)[:15]
        ],
        recent=[
            TransferEventRow(
                occurred_at=t.created_at,
                product_name=products.get(t.product_id, "Unknown product"),
                from_name=branch_names.get(t.from_branch_id, "Unknown"),
                to_name=branch_names.get(t.to_branch_id, "Unknown"),
                label=_label(_TRANSFER_LABELS, t.transfer_type),
                units=int(t.quantity_pieces or 0),
                by_name=user_names.get(t.created_by),
                notes=t.notes,
            )
            for t in transfers[:25]
        ],
        waiting_returns=[
            WaitingReturnRow(
                product_name=products.get(w.product_id, "Unknown product"),
                from_name=branch_names.get(w.from_branch_id, "Unknown"),
                to_name=branch_names.get(w.to_branch_id, "Unknown"),
                units=int(w.quantity_pieces or 0),
                amount_naira=int(w.amount_kobo or 0) / 100,
                requested_at=w.requested_at,
                started_by_side=w.started_by_side,
            )
            for w in waiting
        ],
        returns_by_status=[
            ReturnStatusRow(status=s, count=int(c or 0), amount_naira=int(a or 0) / 100)
            for s, c, a in status_rows
        ],
        short_deliveries=short_list[:25],
    )


@router.get("/stock-count-variance", response_model=CountVarianceResponse)
async def stock_count_variance(
    date_from: date = Query(...),
    date_to: date = Query(...),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> CountVarianceResponse:
    branch_id = _scope_branch(current_user, branch_id)

    # A stock count is saved as an adjustment with reason 'recount': the
    # quantity_delta is what the count changed (negative = short, positive = over).
    conds = [
        StockAdjustment.reason == "recount",
        StockAdjustment.status == "completed",
        *_period_conds(StockAdjustment.created_at, date_from, date_to),
    ]
    if branch_id:
        conds.append(StockAdjustment.branch_id == branch_id)
    rows = (
        await session.execute(
            select(StockAdjustment, StockBatch.product_id, StockBatch.custom_name, StockBatch.unit_cost_kobo)
            .join(StockBatch, StockAdjustment.stock_batch_id == StockBatch.id)
            .where(*conds)
            .order_by(StockAdjustment.created_at.desc())
            .limit(5000)
        )
    ).all()

    product_ids = {r[1] for r in rows if r[1]}
    products = {}
    if product_ids:
        products = {
            p.id: p.name
            for p in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
        }
    branch_names = await _branch_names(session)
    user_names = await _user_names(session, {r[0].adjusted_by for r in rows})

    totals = {"short_units": 0, "short_kobo": 0, "surplus_units": 0, "surplus_kobo": 0, "matched": 0}
    by_product: dict = {}
    by_branch: dict = {}
    by_staff: dict = {}

    def bump(bucket, key, delta, value_kobo):
        e = bucket.setdefault(key, {"n": 0, "short": 0, "surplus": 0, "short_kobo": 0, "net_kobo": 0})
        e["n"] += 1
        if delta < 0:
            e["short"] += -delta
            e["short_kobo"] += value_kobo
            e["net_kobo"] -= value_kobo
        elif delta > 0:
            e["surplus"] += delta
            e["net_kobo"] += value_kobo

    recent = []
    for adj, pid, custom_name, cost in rows:
        delta = int(adj.quantity_delta or 0)
        value = _variance_value(delta, cost)
        if delta == 0:
            totals["matched"] += 1
        elif delta < 0:
            totals["short_units"] += -delta
            totals["short_kobo"] += value
        else:
            totals["surplus_units"] += delta
            totals["surplus_kobo"] += value
        name = products.get(pid) or custom_name or "Unknown product"
        bump(by_product, name, delta, value)
        bump(by_branch, branch_names.get(adj.branch_id, "Unknown branch"), delta, value)
        bump(by_staff, user_names.get(adj.adjusted_by, "Unknown"), delta, value)
        if len(recent) < 25:
            recent.append(
                CountVarianceEventRow(
                    occurred_at=adj.created_at,
                    product_name=name,
                    branch_name=branch_names.get(adj.branch_id),
                    delta=delta,
                    value_naira=(value if delta >= 0 else -value) / 100,
                    by_name=user_names.get(adj.adjusted_by),
                    notes=adj.notes,
                )
            )

    def groups(bucket, limit):
        ordered = sorted(bucket.items(), key=lambda kv: (kv[1]["short_kobo"], kv[1]["n"]), reverse=True)[:limit]
        return [
            CountVarianceGroupRow(
                name=k, entries=v["n"], short_units=v["short"], surplus_units=v["surplus"], net_naira=v["net_kobo"] / 100
            )
            for k, v in ordered
        ]

    product_rows = [
        CountVarianceProductRow(
            product_name=k,
            recounts=v["n"],
            short_units=v["short"],
            surplus_units=v["surplus"],
            short_naira=v["short_kobo"] / 100,
            net_naira=v["net_kobo"] / 100,
        )
        for k, v in sorted(by_product.items(), key=lambda kv: (kv[1]["short_kobo"], kv[1]["n"]), reverse=True)[:20]
    ]

    return CountVarianceResponse(
        entries=len(rows),
        matched_counts=totals["matched"],
        short_units=totals["short_units"],
        short_naira=totals["short_kobo"] / 100,
        surplus_units=totals["surplus_units"],
        surplus_naira=totals["surplus_kobo"] / 100,
        net_naira=(totals["surplus_kobo"] - totals["short_kobo"]) / 100,
        by_product=product_rows,
        by_branch=groups(by_branch, 15),
        by_staff=groups(by_staff, 15),
        recent=recent,
    )


# ---------------------------------------------------------------- Patch 26d
# Loss trend: write-offs against sales, month by month.


def _month_keys(today, months):
    keys = []
    for back in range(months - 1, -1, -1):
        year, month = today.year, today.month - back
        while month <= 0:
            month += 12
            year -= 1
        keys.append(f"{year:04d}-{month:02d}")
    return keys


def _bucket_by_month(rows):
    # rows: (timestamp, kobo) -> {"YYYY-MM": kobo}, in Lagos time.
    out = {}
    for stamp, kobo in rows:
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        key = stamp.astimezone(_LAGOS).strftime("%Y-%m")
        out[key] = out.get(key, 0) + int(kobo or 0)
    return out


@router.get("/loss-trend", response_model=LossTrendResponse)
async def loss_trend(
    months: int = Query(default=6, ge=2, le=12),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> LossTrendResponse:
    branch_id = _scope_branch(current_user, branch_id)
    keys = _month_keys(datetime.now(_LAGOS).date(), months)
    start = date(int(keys[0][:4]), int(keys[0][5:7]), 1)

    # Approved write-offs, same rule as the profit summary and losses page.
    loss_when = func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at)
    loss_stmt = select(loss_when, StockAdjustment.value_kobo).where(
        StockAdjustment.value_kobo.is_not(None),
        StockAdjustment.status == "completed",
        not_spoiled_return_writeoff(),  # PATCH26F
        *_period_conds(loss_when, start, None),
    )
    sale_stmt = select(Sale.id, Sale.created_at, Sale.total_kobo).where(  # PATCH26F
        *revenue_sale_conds(), *_period_conds(Sale.created_at, start, None)
    )
    if branch_id:
        loss_stmt = loss_stmt.where(StockAdjustment.branch_id == branch_id)
        sale_stmt = sale_stmt.where(Sale.branch_id == branch_id)
    losses = _bucket_by_month((await session.execute(loss_stmt)).all())
    _sale_rows = (await session.execute(sale_stmt)).all()  # PATCH26F: revenue after refunds
    _ret = await fetch_returns(session, branch_id, start, None)
    revenue = _bucket_by_month(
        [(created, int(total or 0) - _ret.sale_value.get(sid, 0)) for sid, created, total in _sale_rows]
    )

    rows = [
        LossMonthRow(
            month=k,
            losses_naira=losses.get(k, 0) / 100,
            revenue_naira=revenue.get(k, 0) / 100,
            loss_percent=_pct(losses.get(k, 0), revenue.get(k, 0)),
        )
        for k in keys
    ]
    total_losses = sum(losses.get(k, 0) for k in keys)
    total_revenue = sum(revenue.get(k, 0) for k in keys)
    return LossTrendResponse(
        months=rows,
        total_losses_naira=total_losses / 100,
        total_revenue_naira=total_revenue / 100,
        overall_loss_percent=_pct(total_losses, total_revenue),
        worst_month=max(keys, key=lambda k: losses.get(k, 0)) if total_losses else None,
    )


# ---------------------------------------------------------------- Patch 26e
# Stock valuation at batch cost, and data-health checks.


@router.get("/stock-valuation", response_model=StockValuationResponse)
async def stock_valuation(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> StockValuationResponse:
    branch_id = _scope_branch(current_user, branch_id)
    conds = [StockBatch.quantity_remaining > 0]
    if branch_id:
        conds.append(StockBatch.branch_id == branch_id)

    qty = StockBatch.quantity_remaining
    batch_cost = qty * StockBatch.unit_cost_kobo
    # Today's price list, for comparison: custom items have no product, so
    # they fall back to what the batch cost.
    current_cost = qty * func.coalesce(Product.cost_price_kobo, StockBatch.unit_cost_kobo)
    retail = qty * func.coalesce(Product.selling_price_kobo, 0)

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(qty), 0),
                func.coalesce(func.sum(batch_cost), 0),
                func.coalesce(func.sum(current_cost), 0),
                func.coalesce(func.sum(retail), 0),
                func.coalesce(func.sum(case((StockBatch.unit_cost_kobo <= 0, qty), else_=0)), 0),
                func.coalesce(func.sum(case((StockBatch.unit_cost_kobo <= 0, 1), else_=0)), 0),
            )
            .select_from(StockBatch)
            .outerjoin(Product, StockBatch.product_id == Product.id)
            .where(*conds)
        )
    ).one()

    cat_rows = (
        await session.execute(
            select(
                Category.name,
                func.coalesce(func.sum(qty), 0),
                func.coalesce(func.sum(batch_cost), 0),
                func.coalesce(func.sum(retail), 0),
            )
            .select_from(StockBatch)
            .outerjoin(Product, StockBatch.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .where(*conds)
            .group_by(Category.name)
            .order_by(func.sum(batch_cost).desc())
            .limit(40)
        )
    ).all()

    branch_rows = (
        await session.execute(
            select(StockBatch.branch_id, func.coalesce(func.sum(qty), 0), func.coalesce(func.sum(batch_cost), 0))
            .where(*conds)
            .group_by(StockBatch.branch_id)
            .order_by(func.sum(batch_cost).desc())
        )
    ).all()
    branch_names = await _branch_names(session)

    batch_cost_kobo = int(totals[1] or 0)
    return StockValuationResponse(
        units=int(totals[0] or 0),
        batch_cost_naira=batch_cost_kobo / 100,
        current_cost_naira=int(totals[2] or 0) / 100,
        retail_naira=int(totals[3] or 0) / 100,
        cost_difference_naira=(int(totals[2] or 0) - batch_cost_kobo) / 100,
        uncosted_units=int(totals[4] or 0),
        uncosted_batches=int(totals[5] or 0),
        by_category=[
            ValuationCategoryRow(
                category_name=name or "Uncategorised / custom items",
                units=int(units or 0),
                batch_cost_naira=int(cost or 0) / 100,
                retail_naira=int(value or 0) / 100,
                margin_percent=_pct(int(value or 0) - int(cost or 0), int(value or 0)),
            )
            for name, units, cost, value in cat_rows
        ],
        by_branch=[
            ValuationBranchRow(
                branch_name=branch_names.get(b_id, "Unknown branch"), units=int(units or 0), batch_cost_naira=int(cost or 0) / 100
            )
            for b_id, units, cost in branch_rows
        ],
    )


def _health_row(key, title, count, why, fix, amount_kobo=None):
    count = int(count or 0)
    return HealthCheckRow(
        key=key,
        title=title,
        count=count,
        amount_naira=(int(amount_kobo or 0) / 100) if amount_kobo is not None else None,
        status="warn" if count else "ok",
        why=why,
        fix=fix,
    )


@router.get("/data-health", response_model=DataHealthResponse)
async def data_health(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> DataHealthResponse:
    branch_id = _scope_branch(current_user, branch_id)
    now = datetime.now(timezone.utc)
    today = datetime.now(_LAGOS).date()
    since_90 = now - timedelta(days=90)
    since_30 = now - timedelta(days=30)

    def at(column):
        return [column == branch_id] if branch_id else []

    async def one(stmt):
        return (await session.execute(stmt)).one()

    checks = []

    # 1. Products with stock but no cost price
    stocked = exists().where(
        StockBatch.product_id == Product.id, StockBatch.quantity_remaining > 0, *at(StockBatch.branch_id)
    )
    row = await one(
        select(func.count(Product.id)).where(
            Product.cost_price_kobo <= 0, Product.status != "discontinued", stocked
        )
    )
    checks.append(_health_row(
        "products_no_cost", "Products in stock with no cost price", row[0],
        "Profit, margin and the ABC ranking treat these as pure profit, so they look better than they are.",
        "Set the cost price on each of these products.",
    ))

    # 2. Stock batches with no cost
    row = await one(
        select(func.count(StockBatch.id)).where(
            StockBatch.quantity_remaining > 0, StockBatch.unit_cost_kobo <= 0, *at(StockBatch.branch_id)
        )
    )
    checks.append(_health_row(
        "batches_no_cost", "Stock batches with no cost", row[0],
        "Stock value is understated and sales from these batches show no cost.",
        "Edit the batch or the delivery and enter what it cost.",
    ))

    # 3. Negative stock
    row = await one(
        select(func.count(StockBatch.id)).where(StockBatch.quantity_remaining < 0, *at(StockBatch.branch_id))
    )
    checks.append(_health_row(
        "negative_stock", "Stock batches below zero", row[0],
        "Something was sold or moved that was never received into the system, so counts and values are off.",
        "Find the missing delivery or correct the count for these batches.",
    ))

    # 4. Expired stock still on the shelf
    row = await one(
        select(
            func.count(StockBatch.id),
            func.coalesce(func.sum(StockBatch.quantity_remaining * StockBatch.unit_cost_kobo), 0),
        ).where(
            StockBatch.quantity_remaining > 0,
            StockBatch.expiry_date.is_not(None),
            StockBatch.expiry_date < today,
            *at(StockBatch.branch_id),
        )
    )
    checks.append(_health_row(
        "expired_stock", "Expired stock still counted as on hand", row[0],
        "Your stock value includes goods you cannot sell.",
        "Write the expired batches off as losses.",
        amount_kobo=row[1],
    ))

    # 5. Priced below cost
    row = await one(
        select(func.count(Product.id)).where(
            Product.status == "active",
            Product.cost_price_kobo > 0,
            Product.selling_price_kobo < Product.cost_price_kobo,
        )
    )
    checks.append(_health_row(
        "below_cost", "Products priced below their cost", row[0],
        "Every sale of these loses money.",
        "Check the selling price (or the cost price, if it was typed wrongly).",
    ))

    # 6. Sales lines with no batch (cost is an estimate)
    row = await one(
        select(func.count(SaleItem.id))
        .select_from(SaleItem)
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(
            Sale.status == "completed",
            SaleItem.stock_batch_id.is_(None),
            Sale.created_at >= since_90,
            *at(Sale.branch_id),
        )
    )
    checks.append(_health_row(
        "estimated_cost_lines", "Sale lines from the last 90 days with no batch recorded", row[0],
        "Their cost comes from today's cost price, so profit on them is an estimate.",
        "Nothing to fix for past sales; make sure new sales always pick a stock batch.",
    ))

    # 7. Payments that do not add up to the sale total
    paid = (
        select(SalePayment.sale_id.label("sid"), func.sum(SalePayment.amount_kobo).label("paid"))
        .group_by(SalePayment.sale_id)
        .subquery()
    )
    row = await one(
        select(func.count(Sale.id), func.coalesce(func.sum(paid.c.paid - Sale.total_kobo), 0))
        .select_from(Sale)
        .join(paid, paid.c.sid == Sale.id)
        .where(
            Sale.status == "completed",
            Sale.created_at >= since_90,
            paid.c.paid != Sale.total_kobo,
            *at(Sale.branch_id),
        )
    )
    checks.append(_health_row(
        "payment_mismatch", "Sales (last 90 days) where recorded payments differ from the total", row[0],
        "The payment-method figures in the sales breakdown will not match sales for these.",
        "Open a few of them: if the difference is only change given, it can be ignored.",
        amount_kobo=row[1],
    ))

    # 8. Recent sales with no payment record at all
    row = await one(
        select(func.count(Sale.id)).where(
            Sale.status == "completed",
            Sale.created_at >= since_30,
            ~exists().where(SalePayment.sale_id == Sale.id),
            *at(Sale.branch_id),
        )
    )
    checks.append(_health_row(
        "no_payment_record", "Sales from the last 30 days with no payment recorded", row[0],
        "They are missing from the payment-method breakdown.",
        "Check how these sales were rung up at the till.",
    ))

    # 9. Transfer refunds not paid out after a day
    row = await one(
        select(func.count(Refund.id), func.coalesce(func.sum(Refund.amount_kobo), 0))
        .select_from(Refund)
        .join(Sale, Refund.sale_id == Sale.id)
        .where(
            Refund.status == "pending",
            Sale.status != "cancelled",
            Refund.created_at < now - timedelta(days=1),
            *at(Sale.branch_id),
        )
    )
    checks.append(_health_row(
        "refunds_overdue", "Refunds still waiting to be paid out after 24 hours", row[0],
        "Customers are waiting for their money.",
        "Send the transfers and mark the refunds as processed.",
        amount_kobo=row[1],
    ))

    # 10. Old unpaid supplier invoices
    owed = func.coalesce(PurchaseInvoice.amount_kobo, 0) - func.coalesce(PurchaseInvoice.amount_paid_kobo, 0)
    row = await one(
        select(func.count(PurchaseInvoice.id), func.coalesce(func.sum(owed), 0)).where(
            PurchaseInvoice.status == "completed",
            or_(PurchaseInvoice.source.is_(None), PurchaseInvoice.source != "branch_transfer"),
            owed > 0,
            PurchaseInvoice.invoice_date < today - timedelta(days=30),
            *at(PurchaseInvoice.branch_id),
        )
    )
    checks.append(_health_row(
        "suppliers_overdue", "Supplier invoices unpaid for more than 30 days", row[0],
        "Money you owe, and a risk to your supplier relationships.",
        "Pay them or record the payments already made.",
        amount_kobo=row[1],
    ))

    # 11. Stock adjustments waiting for a decision
    row = await one(
        select(
            func.count(StockAdjustment.id), func.coalesce(func.sum(func.coalesce(StockAdjustment.value_kobo, 0)), 0)
        ).where(
            StockAdjustment.status.in_(["pending_review", "pending_confirmation"]),
            StockAdjustment.created_at < now - timedelta(days=3),
            *at(StockAdjustment.branch_id),
        )
    )
    checks.append(_health_row(
        "adjustments_waiting", "Stock adjustments waiting for approval for over 3 days", row[0],
        "Until they are approved, losses and stock counts do not include them.",
        "Review and approve or reject them.",
        amount_kobo=row[1],
    ))

    # 12. Bookings left open for a long time
    row = await one(
        select(func.count(Sale.id)).where(
            Sale.status == "booking", Sale.created_at < since_30, *at(Sale.branch_id)
        )
    )
    checks.append(_health_row(
        "old_bookings", "Bookings open for more than 30 days", row[0],
        "The stock is held back from sale the whole time.",
        "Contact the customers, then complete or cancel the bookings.",
    ))

    # 13. Items refunded more times than they were sold (PATCH26F)
    over_rows = (
        await session.execute(
            select(SaleItem.id)
            .select_from(RefundItem)
            .join(SaleItem, RefundItem.sale_item_id == SaleItem.id)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(*at(Sale.branch_id))
            .group_by(SaleItem.id, SaleItem.quantity)
            .having(func.sum(RefundItem.quantity) > SaleItem.quantity)
        )
    ).all()
    checks.append(_health_row(
        "over_refunded", "Sale items refunded more times than they were sold", len(over_rows),
        "The refund screen paid back more than was sold. Reports only count the units that were sold, "
        "but the extra money really left the till.",
        "Look the sales up in Refund History. The refund screen does not yet stop a second refund of the same item.",
    ))

    checks.sort(key=lambda c: (c.status != "warn", -c.count))
    return DataHealthResponse(
        checks=checks,
        attention_count=sum(1 for c in checks if c.status == "warn"),
        total_checks=len(checks),
    )


_METRIC_TYPES = {"revenue", "inventory_investment", "sales", "losses"}


async def _live_today_value(session: AsyncSession, metric_type: str, branch_id: uuid.UUID | None) -> float:
    """The daily rollup job (main.py) only writes yesterday's row and
    earlier -- "today" isn't in metric_snapshots yet, so the `daily`
    view below computes it live from today's transactions the same
    way the dashboard's own KPI cards already do, then prepends it as
    a clearly-marked in-progress row.
    """
    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(days=1)

    if metric_type == "revenue":
        stmt = select(func.coalesce(func.sum(Sale.total_kobo), 0)).where(
            Sale.status == "completed", Sale.created_at >= start, Sale.created_at < end
        )
        if branch_id:
            stmt = stmt.where(Sale.branch_id == branch_id)
        kobo = int((await session.execute(stmt)).scalar_one() or 0)
        return Money(kobo).naira
    if metric_type == "inventory_investment":
        stmt = select(
            func.coalesce(
                func.sum(
                    func.coalesce(PurchaseInvoice.amount_kobo, 0)
                    + func.coalesce(PurchaseInvoice.waybill_cost_kobo, 0)
                    + func.coalesce(PurchaseInvoice.transit_cost_kobo, 0)
                    + func.coalesce(PurchaseInvoice.handling_cost_kobo, 0)
                ),
                0,
            )
        ).where(PurchaseInvoice.invoice_date == today, PurchaseInvoice.trash_state.is_(None))  # TRASH1
        if branch_id:
            stmt = stmt.where(PurchaseInvoice.branch_id == branch_id)
        else:
            # XFER5: all branches together must not count transfer invoices twice.
            stmt = stmt.where(PurchaseInvoice.source.is_distinct_from("branch_transfer"))
        kobo = int((await session.execute(stmt)).scalar_one() or 0)
        return Money(kobo).naira
    if metric_type == "sales":
        stmt = select(func.count()).select_from(Sale).where(
            Sale.status == "completed", Sale.created_at >= start, Sale.created_at < end
        )
        if branch_id:
            stmt = stmt.where(Sale.branch_id == branch_id)
        return float((await session.execute(stmt)).scalar_one() or 0)
    # losses
    stmt = select(func.coalesce(func.sum(StockAdjustment.value_kobo), 0)).where(
        StockAdjustment.value_kobo.is_not(None),
        StockAdjustment.status == "completed",
        func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at) >= start,
        func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at) < end,
    )
    if branch_id:
        stmt = stmt.where(StockAdjustment.branch_id == branch_id)
    kobo = int((await session.execute(stmt)).scalar_one() or 0)
    return Money(kobo).naira


def _period_label(d: date, period: str) -> str:
    if period == "yearly":
        return str(d.year)
    if period == "monthly":
        return d.strftime("%b %Y")
    if period == "weekly":
        return f"Wk of {d.strftime('%b %d, %Y')}"
    return d.strftime("%b %d, %Y")


@router.get("/performance-records", response_model=PerformanceRecordsResponse)
async def performance_records(
    metric: str = Query(pattern="^(revenue|inventory_investment|sales|losses)$"),
    period: str = Query(default="daily", pattern="^(daily|weekly|monthly|yearly)$"),
    branch_id: uuid.UUID | None = None,
    limit: int = Query(default=90, ge=1, le=365),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> PerformanceRecordsResponse:
    """Dashboard redesign implementation prompt, step 4: replaces the
    old resettable Total Revenue/Total Invested tracking (step 1) with
    permanent per-day rows that are only ever inserted or upserted for
    a day that hasn't been finalized yet -- see MetricSnapshot's
    docstring and the daily rollup job in main.py. `value` is stored as
    naira already for revenue/inventory_investment/losses (converted
    from kobo at snapshot time) and as a plain count for `sales`.
    """
    conditions = [MetricSnapshot.metric_type == metric]
    if branch_id:
        conditions.append(MetricSnapshot.branch_id == branch_id)

    all_time_stmt = select(func.coalesce(func.sum(MetricSnapshot.value), 0)).where(*conditions)
    all_time_total = float((await session.execute(all_time_stmt)).scalar_one() or 0)
    all_time_total += await _live_today_value(session, metric, branch_id)

    rows: list[PerformanceRecordRow] = []
    if period == "daily":
        stmt = (
            select(MetricSnapshot.period_start, MetricSnapshot.value)
            .where(*conditions)
            .order_by(MetricSnapshot.period_start.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = [
            PerformanceRecordRow(
                period_label=_period_label(r.period_start, "daily"),
                period_start=r.period_start,
                value_naira=float(r.value),
            )
            for r in result.all()
        ]
        # "Today" isn't in metric_snapshots yet (the nightly job hasn't
        # run for it) -- prepend it as a clearly-marked in-progress row
        # rather than silently omitting the current day.
        today_value = await _live_today_value(session, metric, branch_id)
        rows.insert(
            0,
            PerformanceRecordRow(
                period_label="Today",
                period_start=date.today(),
                value_naira=today_value,
                in_progress=True,
            ),
        )
    else:
        trunc_unit = {"weekly": "week", "monthly": "month", "yearly": "year"}[period]
        period_col = func.date_trunc(trunc_unit, MetricSnapshot.period_start)
        stmt = (
            select(period_col.label("period"), func.sum(MetricSnapshot.value).label("value"))
            .where(*conditions)
            .group_by(period_col)
            .order_by(period_col.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = [
            PerformanceRecordRow(
                period_label=_period_label(r.period.date(), period),
                period_start=r.period.date(),
                value_naira=float(r.value or 0),
            )
            for r in result.all()
        ]

        # The nightly rollup job only finalizes snapshots through
        # yesterday, so the bucket covering the current week/month/year
        # is always missing today's contribution -- merge it in live
        # and flag the bucket in_progress, same idea as the `daily`
        # branch above but merged into the existing bucket instead of
        # always prepended fresh.
        today = date.today()
        current_period_start = {
            "weekly": today - timedelta(days=today.weekday()),
            "monthly": today.replace(day=1),
            "yearly": today.replace(month=1, day=1),
        }[period]
        today_value = await _live_today_value(session, metric, branch_id)
        if rows and rows[0].period_start == current_period_start:
            rows[0] = PerformanceRecordRow(
                period_label=rows[0].period_label,
                period_start=rows[0].period_start,
                value_naira=rows[0].value_naira + today_value,
                in_progress=True,
            )
        else:
            rows.insert(
                0,
                PerformanceRecordRow(
                    period_label=_period_label(current_period_start, period),
                    period_start=current_period_start,
                    value_naira=today_value,
                    in_progress=True,
                ),
            )

    return PerformanceRecordsResponse(all_time_total_naira=all_time_total, rows=rows)