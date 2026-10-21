import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db_session, require_permission
from api.v1.schemas.reports import (
    ExpiryRow,
    LowStockRow,
    PerformanceRecordRow,
    PerformanceRecordsResponse,
    PriceChangeHistoryRow,
    ProductPerformanceRow,
    SalesSummaryResponse,
    SalesSummaryRow,
)
from infrastructure.db.models import (
    AuditLog,
    ExpiryAlertDismissal,
    MetricSnapshot,
    Product,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseOrderItem,
    Sale,
    SaleItem,
    StockAdjustment,
    StockBatch,
    Supplier,
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
        .where(Sale.status == "completed")
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

    # Section 18.7: "losses... must be automatically deducted from
    # total revenue... sales add to revenue, losses subtract from it."
    # Only damaged/expired/stolen (StockAdjustment) losses are
    # subtracted here -- a "Sold Below Original Price" loss (see
    # sale_losses.py) is deliberately NOT subtracted a second time,
    # since that item was actually sold and its lower price is already
    # what's sitting in Sale.total_kobo above; subtracting its
    # discount again here would double-count the same shortfall.
    loss_period_col = func.date_trunc(group_by, StockAdjustment.created_at)
    loss_stmt = (
        select(loss_period_col.label("period"), func.sum(StockAdjustment.value_kobo).label("value_kobo"))
        .where(StockAdjustment.value_kobo.is_not(None))
        .group_by(loss_period_col)
    )
    if current_user.role != "super_admin":
        loss_stmt = loss_stmt.where(StockAdjustment.branch_id == current_user.branch_id)
    elif branch_id:
        loss_stmt = loss_stmt.where(StockAdjustment.branch_id == branch_id)
    if date_from:
        loss_stmt = loss_stmt.where(StockAdjustment.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        loss_stmt = loss_stmt.where(
            StockAdjustment.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        )
    loss_result = await session.execute(loss_stmt)
    loss_by_period = {row.period.date(): int(row.value_kobo or 0) for row in loss_result.all()}

    summary_rows = [
        SalesSummaryRow(
            period=row.period.date(),
            sales_count=row.sales_count,
            subtotal_naira=Money(int(row.subtotal_kobo or 0)).naira,
            discount_naira=Money(int(row.discount_kobo or 0)).naira,
            total_naira=Money(max(0, int(row.total_kobo or 0) - loss_by_period.get(row.period.date(), 0))).naira,
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

    return SalesSummaryResponse(
        rows=summary_rows,
        grand_total_naira=sum(r.total_naira for r in summary_rows),
        grand_sales_count=sum(r.sales_count for r in summary_rows),
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
    if current_user.role == "staff":
        product_conditions.append(Product.hidden_from_staff.is_(False))

    products_result = await session.execute(
        select(Product)
        .options(
            selectinload(Product.category),
            selectinload(Product.images),
        )
        .where(*product_conditions)
    )

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
                    cost_price_naira=Money(product.cost_price_kobo).naira,
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
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> list[ProductPerformanceRow]:
    stmt = (
        select(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("quantity_sold"),
            func.sum(SaleItem.line_total_kobo).label("revenue_kobo"),
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(Sale.status == "completed")
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
    for row in performance_rows:
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
                quantity_sold=row.quantity_sold,
                revenue_naira=Money(int(row.revenue_kobo)).naira,
            )
        )
    return rows

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
        ).where(PurchaseInvoice.invoice_date == today)
        if branch_id:
            stmt = stmt.where(PurchaseInvoice.branch_id == branch_id)
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
        StockAdjustment.created_at >= start,
        StockAdjustment.created_at < end,
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