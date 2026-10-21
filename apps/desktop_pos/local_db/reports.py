"""Local-only reporting and void support, computed from the outbox --
which holds every sale this device has made regardless of sync
status, so these numbers are correct even fully offline.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from local_db.models import CachedStockBatch, OutboxSale, OutboxSaleItem
from nickcosmetics_core.exceptions import SaleAlreadySyncedError, SaleAlreadyVoidedError


def todays_sales_summary(session: Session, branch_id: str) -> dict:
    """Count and total (naira) of today's non-voided sales at this
    branch, for the branch-manager-facing "Today" label. Uses the
    device's local clock/midnight boundary -- fine for a single-branch
    till, not meant as an authoritative cross-timezone report.
    """
    start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.execute(
        select(OutboxSale).where(
            OutboxSale.branch_id == branch_id,
            OutboxSale.created_at >= start_of_day,
            OutboxSale.voided.is_(False),
        )
    ).scalars().all()

    return {
        "count": len(rows),
        "total_naira": sum(row.total_kobo for row in rows) / 100,
    }


def list_recent_sales(session: Session, branch_id: str, hours: int = 24) -> list[OutboxSale]:
    """Recent local sales for this branch, newest first -- feeds the
    "Void Sale" dialog. Deliberately scoped to a recent window rather
    than the whole outbox history, since voiding an old sale that's
    likely already synced elsewhere isn't a supported flow here.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    return session.execute(
        select(OutboxSale)
        .where(OutboxSale.branch_id == branch_id, OutboxSale.created_at >= since)
        .order_by(OutboxSale.created_at.desc())
    ).scalars().all()


def void_sale(session: Session, outbox_sale_id: str) -> OutboxSale:
    """Cancels a local sale that hasn't synced yet, restoring the
    stock it deducted. Refuses to void a sale that's already synced --
    reversing those needs a server-side refund so cloud-side stock and
    financials stay correct too, not just this device's local mirror.
    """
    sale = session.get(OutboxSale, outbox_sale_id)
    if sale is None:
        raise ValueError(f"Sale '{outbox_sale_id}' not found")
    if sale.voided:
        raise SaleAlreadyVoidedError(sale.id)
    if sale.synced:
        raise SaleAlreadySyncedError(sale.id)

    items = session.execute(
        select(OutboxSaleItem).where(OutboxSaleItem.outbox_sale_id == sale.id)
    ).scalars().all()

    for item in items:
        batch = session.get(CachedStockBatch, item.stock_batch_id)
        if batch is not None:
            # Batch may no longer exist locally (e.g. catalog was
            # re-synced and it emptied out / expired since); nothing
            # to restore it to in that case, so just skip it rather
            # than fail the whole void.
            batch.quantity_remaining += item.quantity

    sale.voided = True
    sale.voided_at = datetime.utcnow()
    session.commit()
    return sale
