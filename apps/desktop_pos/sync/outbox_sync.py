"""Pushes every unsynced sale in the local outbox to the cloud API.

Called automatically right after a sale (best-effort -- if it's
offline, the sale just stays queued), and manually via the "Sync Now"
button for a device that's been offline a while.

Reuses the exact `local_ref_id` generated at sale time, so the cloud
API's idempotency check (see apps/api/infrastructure/db/
checkout_adapter.py) guarantees a retried sync -- even one that
partially succeeded before a dropped connection -- can never create a
duplicate sale or double-deduct stock.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from api_client import ApiClient, ApiError
from local_db.models import OutboxSale, OutboxSaleItem
from nickcosmetics_core.domain.money import Money


def push_outbox(api: ApiClient, session: Session) -> tuple[int, int]:
    """Returns (synced_count, failed_count)."""
    pending = session.execute(
        select(OutboxSale).where(OutboxSale.synced.is_(False))
    ).scalars().all()

    synced_count = 0
    failed_count = 0

    for sale in pending:
        items = session.execute(
            select(OutboxSaleItem).where(OutboxSaleItem.outbox_sale_id == sale.id)
        ).scalars().all()

        api_items = [
            {
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "unit_price_naira": Money(item.unit_price_kobo).naira,
            }
            for item in items
        ]

        try:
            api.checkout(
                branch_id=sale.branch_id,
                items=api_items,
                payment_method=sale.payment_method or "cash",
                discount_naira=Money(sale.discount_kobo).naira,
                local_ref_id=sale.local_ref_id,
            )
        except ApiError as exc:
            sale.sync_error = exc.detail
            failed_count += 1
            continue
        except Exception as exc:
            # Network-level failure (offline, timeout, DNS, etc.) --
            # not a rejection, just "try again later."
            sale.sync_error = f"Connection error: {exc}"
            failed_count += 1
            continue

        sale.synced = True
        sale.synced_at = datetime.utcnow()
        sale.sync_error = None
        synced_count += 1

    session.commit()
    return synced_count, failed_count


def pending_count(session: Session) -> int:
    return len(
        session.execute(select(OutboxSale.id).where(OutboxSale.synced.is_(False))).all()
    )
