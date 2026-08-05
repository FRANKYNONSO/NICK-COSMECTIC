import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission
from api.v1.schemas.reports import (
    ExpiryRow,
    LowStockRow,
    ProductPerformanceRow,
    SalesSummaryResponse,
    SalesSummaryRow,
)
from infrastructure.db.models import Product, ProductVariant, Sale, SaleItem, StockBatch, User
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/reports", tags=["reports"])

# All report endpoints are gated on "view_all_reports" -- per the role
# spec, Staff can see their own sales/performance but not company-wide
# revenue, inventory value, or aggregate reports. See docs/architecture
# Section 3 and nickcosmetics_core.domain.users.user's permission table.
_REQUIRE_REPORTS_ACCESS = require_permission("view_all_reports")


@router.get("/sales-summary", response_model=SalesSummaryResponse)
async def sales_summary(
    branch_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    group_by: str = Query(default="day", pattern="^(day|week|month)$"),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_REQUIRE_REPORTS_ACCESS),
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
    if branch_id:
        stmt = stmt.where(Sale.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(Sale.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(Sale.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    result = await session.execute(stmt)
    rows = result.all()

    summary_rows = [
        SalesSummaryRow(
            period=row.period.date(),
            sales_count=row.sales_count,
            subtotal_naira=Money(int(row.subtotal_kobo or 0)).naira,
            discount_naira=Money(int(row.discount_kobo or 0)).naira,
            total_naira=Money(int(row.total_kobo or 0)).naira,
        )
        for row in rows
    ]

    return SalesSummaryResponse(
        rows=summary_rows,
        grand_total_naira=sum(r.total_naira for r in summary_rows),
        grand_sales_count=sum(r.sales_count for r in summary_rows),
    )


@router.get("/low-stock", response_model=list[LowStockRow])
async def low_stock_report(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> list[LowStockRow]:
    today = date.today()
    stmt = (
        select(
            StockBatch.variant_id,
            func.sum(StockBatch.quantity_remaining).label("quantity_available"),
        )
        .where(
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
        .group_by(StockBatch.variant_id)
    )
    result = await session.execute(stmt)
    availability = {row.variant_id: row.quantity_available for row in result.all()}

    variants_result = await session.execute(
        select(ProductVariant, Product)
        .join(Product, ProductVariant.product_id == Product.id)
        .where(ProductVariant.is_deleted.is_(False))
    )

    rows: list[LowStockRow] = []
    for variant, product in variants_result.all():
        available = availability.get(variant.id, 0)
        if available <= variant.reorder_level:
            rows.append(
                LowStockRow(
                    variant_id=variant.id,
                    product_name=product.name,
                    variant_name=variant.variant_name,
                    sku=variant.sku,
                    quantity_available=available,
                    reorder_level=variant.reorder_level,
                )
            )
    return sorted(rows, key=lambda r: r.quantity_available)


@router.get("/expiry", response_model=list[ExpiryRow])
async def expiry_report(
    branch_id: uuid.UUID,
    within_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_REQUIRE_REPORTS_ACCESS),
) -> list[ExpiryRow]:
    today = date.today()
    cutoff = today + timedelta(days=within_days)

    stmt = (
        select(StockBatch, ProductVariant, Product)
        .join(ProductVariant, StockBatch.variant_id == ProductVariant.id)
        .join(Product, ProductVariant.product_id == Product.id)
        .where(
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
            StockBatch.expiry_date.is_not(None),
            StockBatch.expiry_date <= cutoff,
        )
        .order_by(StockBatch.expiry_date)
    )
    result = await session.execute(stmt)

    rows = [
        ExpiryRow(
            stock_batch_id=batch.id,
            variant_id=variant.id,
            product_name=product.name,
            variant_name=variant.variant_name,
            sku=variant.sku,
            quantity_remaining=batch.quantity_remaining,
            expiry_date=batch.expiry_date,
            days_until_expiry=(batch.expiry_date - today).days,
        )
        for batch, variant, product in result.all()
    ]
    return rows


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
            SaleItem.variant_id,
            func.sum(SaleItem.quantity).label("quantity_sold"),
            func.sum(SaleItem.line_total_kobo).label("revenue_kobo"),
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(Sale.status == "completed")
        .group_by(SaleItem.variant_id)
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

    variant_ids = [row.variant_id for row in performance_rows]
    variants_result = await session.execute(
        select(ProductVariant, Product)
        .join(Product, ProductVariant.product_id == Product.id)
        .where(ProductVariant.id.in_(variant_ids))
    )
    variant_info = {v.id: (v, p) for v, p in variants_result.all()}

    rows = []
    for row in performance_rows:
        info = variant_info.get(row.variant_id)
        if info is None:
            continue
        variant, product = info
        rows.append(
            ProductPerformanceRow(
                variant_id=row.variant_id,
                product_name=product.name,
                variant_name=variant.variant_name,
                sku=variant.sku,
                quantity_sold=row.quantity_sold,
                revenue_naira=Money(int(row.revenue_kobo)).naira,
            )
        )
    return rows