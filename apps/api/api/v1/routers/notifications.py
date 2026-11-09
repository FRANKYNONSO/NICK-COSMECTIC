import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission  # PATCHP3-NOTIF
from api.v1.schemas.reports import NotificationItem, NotificationsSummary
from infrastructure.db.models import (
    ExpiryAlertDismissal,
    AuditLog,
    PendingChange,
    Product,
    Refund,
    Sale,
    StockAdjustment,
    StockBatch,
    StorefrontOrder,
    StorefrontOrderSale,
    User,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/summary", response_model=NotificationsSummary)
async def notifications_summary(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    # Dashboard redesign implementation prompt, step 5: the bell
    # replaces five removed dashboard KPI cards, all of which were
    # already admin-only-section (Low stock/Out of stock are visible
    # to staff too, but this aggregator only covers the four tiers
    # that were admin-only -- see step 3's card list). Kept at the
    # same super_admin/branch_manager tier as the pending-refund and
    # pending-change badges it replaces, since those are the two
    # counts here that only that tier could ever act on anyway.
    current_user: User = Depends(require_permission("view_dashboard_notifications")),
) -> NotificationsSummary:
    effective_branch_id = current_user.branch_id if current_user.role != "super_admin" else branch_id

    # Low stock / Out of stock -- same query shape as
    # reports.py::low_stock_report(), reused here as a plain count
    # rather than duplicating the full per-product row-building logic
    # that endpoint needs (image URLs, cost/selling price, etc) since
    # the bell only needs a number.
    today = date.today()
    stock_conditions = [
        StockBatch.quantity_remaining > 0,
        (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
    ]
    if effective_branch_id:
        stock_conditions.append(StockBatch.branch_id == effective_branch_id)
    availability_stmt = (
        select(StockBatch.product_id, func.sum(StockBatch.quantity_remaining).label("available"))
        .where(*stock_conditions)
        .group_by(StockBatch.product_id)
    )
    availability = {row.product_id: row.available for row in (await session.execute(availability_stmt)).all()}

    products_result = await session.execute(
        select(Product.id, Product.reorder_level).where(Product.is_deleted.is_(False))
    )
    low_stock_count = 0
    out_of_stock_count = 0
    for product_id, reorder_level in products_result.all():
        available = availability.get(product_id, 0)
        if available <= reorder_level:
            low_stock_count += 1
        if available <= 0:
            out_of_stock_count += 1

    # Pending refunds -- same conditions as
    # sales.py::pending_refund_count().
    refund_conditions = [Refund.status == "pending"]
    if effective_branch_id:
        refund_count = await session.scalar(
            select(func.count())
            .select_from(Refund)
            .join(Sale, Refund.sale_id == Sale.id)
            .where(*refund_conditions, Sale.branch_id == effective_branch_id)
        )
    else:
        refund_count = await session.scalar(select(func.count()).select_from(Refund).where(*refund_conditions))

    # Pending change -- same conditions as
    # sales.py::pending_change_count().
    change_conditions = [PendingChange.status.in_(["pending", "outstanding"])]
    if effective_branch_id:
        change_conditions.append(PendingChange.branch_id == effective_branch_id)
    change_count = await session.scalar(select(func.count()).select_from(PendingChange).where(*change_conditions))

    # Loss reports awaiting review -- same conditions as
    # inventory.py::list_pending_review_adjustments().
    loss_conditions = [
        (StockAdjustment.reason == "theft") & (StockAdjustment.status == "pending_confirmation")
        | (StockAdjustment.reason.in_(["damaged", "expired"]) & StockAdjustment.reviewed_at.is_(None))
    ]
    if effective_branch_id:
        loss_conditions.append(StockAdjustment.branch_id == effective_branch_id)
    loss_count = await session.scalar(select(func.count()).select_from(StockAdjustment).where(*loss_conditions))

    # Branch price-change notifications (per-branch price override
    # feature spec item 4) -- deliberately NOT scoped by
    # effective_branch_id for super_admin: they see this count across
    # every branch regardless of which branch they're currently
    # viewing, unlike every other item in this file. branch_manager
    # still only sees their own branch's changes, same as everything
    # else here.
    price_change_window_start = datetime.now(timezone.utc) - timedelta(days=7)
    price_change_conditions = [
        AuditLog.action == "branch_price_override_changed",
        AuditLog.created_at >= price_change_window_start,
    ]
    if current_user.role != "super_admin":
        price_change_conditions.append(
            AuditLog.log_metadata["branch_id"].astext == str(current_user.branch_id)
        )
    # Expiry alert notification -- reuses the exact same query as
    # my_pending_expiry_alerts() (reports.py) via a local import to
    # avoid a routers<->routers import cycle, same pattern already
    # used in coupons.py for _resolve_product_names. Deliberately
    # per-USER (excludes batches this specific user already
    # dismissed), not just per-branch -- two people at the same
    # branch can see different counts if one has dismissed alerts the
    # other hasn't, matching the existing dashboard banner's behavior.
    from api.v1.routers.reports import _fetch_expiry_batches
    expiry_branch_id = current_user.branch_id if current_user.role != "super_admin" else None
    expiry_rows = await _fetch_expiry_batches(session, expiry_branch_id, within_days=150)
    if expiry_rows:
        dismissed_result = await session.execute(
            select(ExpiryAlertDismissal.stock_batch_id).where(ExpiryAlertDismissal.user_id == current_user.id)
        )
        dismissed_ids = {row for row in dismissed_result.scalars().all()}
        expiry_count = len([r for r in expiry_rows if r.stock_batch_id not in dismissed_ids])
    else:
        expiry_count = 0
    price_change_count = await session.scalar(
        select(func.count()).select_from(AuditLog).where(*price_change_conditions)
    )

    # Website (storefront) orders still waiting to be handled (status "new").
    # A branch manager only counts orders that include a sale from their own
    # branch; a super admin counts all (or the branch picked on the dashboard).
    website_order_conditions = [StorefrontOrder.status == "new"]
    if effective_branch_id:
        website_order_conditions.append(
            select(StorefrontOrderSale.id)
            .where(
                StorefrontOrderSale.order_id == StorefrontOrder.id,
                StorefrontOrderSale.branch_id == effective_branch_id,
            )
            .exists()
        )
    website_order_count = await session.scalar(
        select(func.count()).select_from(StorefrontOrder).where(*website_order_conditions)
    )

    # Customer pre-orders (a customer paid a deposit for an item we sourced)
    # whose item has reached the shelf and who have not collected yet.
    # Wrapped so a problem here can never take the whole bell down.
    try:
        from api.v1.routers.customer_requests import count_waiting_customers

        customer_waiting_count = await count_waiting_customers(session, effective_branch_id)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("customer order count failed")
        await session.rollback()
        customer_waiting_count = 0

    items = [
        NotificationItem(
            type="website_orders",
            label="Website orders",
            sub="New, waiting to be handled",
            count=website_order_count or 0,
            href="sales.html#website-orders",
        ),
        NotificationItem(
            type="customer_requests",
            label="Customer orders waiting",
            sub="Item arrived - contact the customer",
            count=customer_waiting_count or 0,
            href="customer-requests.html#arrived",
        ),
        NotificationItem(
            type="low_stock", label="Low stock", sub="Needs reorder", count=low_stock_count, href="low-stock.html#low-stock"
        ),
        NotificationItem(
            type="out_of_stock",
            label="Out of stock",
            sub="Zero units left",
            count=out_of_stock_count,
            href="low-stock.html#out-of-stock",
        ),
        NotificationItem(
            type="pending_refunds",
            label="Pending refunds",
            sub="Awaiting approval",
            count=refund_count or 0,
            href="sales.html#refunds",
        ),
        NotificationItem(
            type="pending_change",
            label="Pending change",
            sub="Owed to customers",
            count=change_count or 0,
            href="sales.html#pending-change",
        ),
        NotificationItem(
            type="loss_reports",
            label="Loss reports",
            sub="Awaiting review",
            count=loss_count or 0,
            href="sales.html#losses",
        ),
        NotificationItem(
            type="price_changes",
            label="Price changes",
            sub="Branch overrides set",
            count=price_change_count or 0,
            href="reports.html#price-changes",
        ),
        NotificationItem(
            type="expiry",
            label="Expiry alert",
            sub="Approaching expiry date",
            count=expiry_count,
            href="low-stock.html#expiry",
        ),
    ]
    return NotificationsSummary(items=items, total=sum(i.count for i in items))
