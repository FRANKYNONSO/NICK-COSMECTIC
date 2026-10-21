"""One-time backfill for metric_snapshots history.

Walks every day from the earliest sale/invoice/stock-adjustment record
through yesterday, calling the same run_daily_metric_rollup used by the
daily job -- so backfilled numbers use identical logic to future daily
runs. Safe to re-run: the underlying upsert never double-counts.
"""
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from infrastructure.db.models import PurchaseInvoice, Sale, StockAdjustment
from infrastructure.db.session import AsyncSessionLocal
from tasks.metric_snapshots import run_daily_metric_rollup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _earliest_date() -> date | None:
    async with AsyncSessionLocal() as session:
        candidates = []

        sale_min = (await session.execute(select(func.min(Sale.created_at)))).scalar_one()
        if sale_min:
            candidates.append(sale_min.date())

        invoice_min = (await session.execute(select(func.min(PurchaseInvoice.invoice_date)))).scalar_one()
        if invoice_min:
            candidates.append(invoice_min)

        adj_min = (await session.execute(select(func.min(StockAdjustment.created_at)))).scalar_one()
        if adj_min:
            candidates.append(adj_min.date())

        return min(candidates) if candidates else None


async def backfill() -> None:
    start = await _earliest_date()
    if start is None:
        logger.info("No sales, invoices, or stock adjustments found -- nothing to backfill.")
        return

    yesterday = date.today() - timedelta(days=1)
    day = start
    count = 0

    while day <= yesterday:
        await run_daily_metric_rollup(target_day=day)
        count += 1
        day += timedelta(days=1)

    logger.info("Backfill complete: %d days rolled up (%s -> %s)", count, start, yesterday)


if __name__ == "__main__":
    asyncio.run(backfill())
