"""Daily rollup job for Performance Records (dashboard redesign
implementation prompt, step 4).

Runs once daily, just after midnight, and writes yesterday's totals
into metric_snapshots -- one row per branch per metric. Uses
INSERT ... ON CONFLICT (branch_id, metric_type, period_start) DO
UPDATE so a re-run (e.g. the server restarted mid-job) never double-
counts, per the acceptance checklist: "metric_snapshots rows are never
overwritten in a way that loses a past day's number -- only ever
inserted or upserted for a day that hasn't been finalized yet."

Registered from an APScheduler AsyncIOScheduler in main.py's FastAPI
startup event, since this project has no pre-existing background-job
scheduler (no celery beat schedule, no other cron-style setup) to hang
this off of instead.
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update  # TRASH1
from sqlalchemy.dialects.postgresql import insert as pg_insert

from infrastructure.db.models import Branch, MetricSnapshot, PurchaseInvoice, Sale, StockAdjustment
from infrastructure.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def _upsert(session, branch_id, metric_type: str, period_start: date, value) -> None:
    stmt = pg_insert(MetricSnapshot).values(
        branch_id=branch_id, metric_type=metric_type, period_start=period_start, value=value
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["branch_id", "metric_type", "period_start"],
        set_={"value": stmt.excluded.value},
    )
    await session.execute(stmt)


async def run_daily_metric_rollup(target_day: date | None = None) -> None:
    """Rolls up `target_day` (default: yesterday, in line with "running
    once daily just after midnight, per branch: sum yesterday's...").
    Exposed with an explicit `target_day` param so a missed day can be
    backfilled manually without waiting for the next scheduled run.
    """
    day = target_day or (date.today() - timedelta(days=1))
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    async with AsyncSessionLocal() as session:
        branch_ids = [row[0] for row in (await session.execute(select(Branch.id))).all()]

        for branch_id in branch_ids:
            # 1. Sum yesterday's completed sales -> revenue row.
            revenue_kobo = int(
                (
                    await session.execute(
                        select(func.coalesce(func.sum(Sale.total_kobo), 0)).where(
                            Sale.status == "completed",
                            Sale.branch_id == branch_id,
                            Sale.created_at >= start,
                            Sale.created_at < end,
                        )
                    )
                ).scalar_one()
                or 0
            )
            await _upsert(session, branch_id, "revenue", day, revenue_kobo / 100)

            # 2. Sum yesterday's invoice landed cost -> inventory_investment
            # row. Deliberate design choice (confirmed Sept 2026): every
            # filed invoice's total landed cost counts toward inventory
            # investment as soon as it's on file, incrementally, even
            # before stock is physically received against it -- NOT
            # StockBatch-based (that was tried and reverted the same day).
            invested_kobo = int(
                (
                    await session.execute(
                        select(
                            func.coalesce(
                                func.sum(
                                    func.coalesce(PurchaseInvoice.amount_kobo, 0)
                                    + func.coalesce(PurchaseInvoice.waybill_cost_kobo, 0)
                                    + func.coalesce(PurchaseInvoice.transit_cost_kobo, 0)
                                    + func.coalesce(PurchaseInvoice.handling_cost_kobo, 0)
                                ),
                                0,
                            )
                        ).where(PurchaseInvoice.branch_id == branch_id, PurchaseInvoice.invoice_date == day, PurchaseInvoice.trash_state.is_(None))
                    )
                ).scalar_one()
                or 0
            )
            await _upsert(session, branch_id, "inventory_investment", day, invested_kobo / 100)

            # 3. Count yesterday's completed sales -> sales row.
            sales_count = int(
                (
                    await session.execute(
                        select(func.count()).select_from(Sale).where(
                            Sale.status == "completed",
                            Sale.branch_id == branch_id,
                            Sale.created_at >= start,
                            Sale.created_at < end,
                        )
                    )
                ).scalar_one()
                or 0
            )
            await _upsert(session, branch_id, "sales", day, sales_count)

            # 4. Sum yesterday's confirmed losses (damaged/expired/stolen) -> losses row.
            # "Confirmed" excludes a theft report still awaiting
            # super-admin confirmation (status='pending_confirmation')
            # -- same completed/theft distinction the Losses KPI and
            # /inventory/losses/summary already use -- and is scoped to
            # damaged/expired/theft only, not recount/other/missing/
            # defective/supplier_return.
            losses_kobo = int(
                (
                    await session.execute(
                        select(func.coalesce(func.sum(StockAdjustment.value_kobo), 0)).where(
                            StockAdjustment.value_kobo.is_not(None),
                            StockAdjustment.status == "completed",
                            StockAdjustment.reason.in_(["damaged", "expired", "theft"]),
                            StockAdjustment.branch_id == branch_id,
                            func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at) >= start,
                            func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at) < end,
                        )
                    )
                ).scalar_one()
                or 0
            )
            await _upsert(session, branch_id, "losses", day, losses_kobo / 100)

        await session.commit()
        logger.info("Metric snapshot rollup complete for %s (%d branches)", day, len(branch_ids))


async def refresh_inventory_investment(session, branch_id, day: date) -> None:
    """TRASH1: re-work the stored inventory_investment figure for ONE branch and
    ONE day, after an invoice dated that day was deleted or restored.

    Only touches a row that already exists (a day the nightly job has already
    rolled up). A day not rolled up yet is left alone: the nightly job will
    compute it correctly because it now skips trashed invoices. Revenue, sales
    and losses are never touched. Runs inside the caller's session, so it
    commits or rolls back together with the delete/restore.
    """
    if branch_id is None:
        return
    invested_kobo = int(
        (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            func.coalesce(PurchaseInvoice.amount_kobo, 0)
                            + func.coalesce(PurchaseInvoice.waybill_cost_kobo, 0)
                            + func.coalesce(PurchaseInvoice.transit_cost_kobo, 0)
                            + func.coalesce(PurchaseInvoice.handling_cost_kobo, 0)
                        ),
                        0,
                    )
                ).where(
                    PurchaseInvoice.branch_id == branch_id,
                    PurchaseInvoice.invoice_date == day,
                    PurchaseInvoice.trash_state.is_(None),
                )
            )
        ).scalar_one()
        or 0
    )
    await session.execute(
        update(MetricSnapshot)
        .where(
            MetricSnapshot.branch_id == branch_id,
            MetricSnapshot.metric_type == "inventory_investment",
            MetricSnapshot.period_start == day,
        )
        .values(value=invested_kobo / 100)
    )
