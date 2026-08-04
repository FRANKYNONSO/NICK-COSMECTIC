"""Adapter bridging the async SQLAlchemy database with the
synchronous, framework-agnostic SalesService in nickcosmetics_core.

Pattern: async pre-fetch -> run domain logic in-memory (sync, fast, no
I/O) -> async persist the result. This is what lets the exact same
SalesService/FifoStockAllocator code run unmodified here AND inside
the offline desktop app's synchronous SQLite access later -- the
domain layer never touches FastAPI, SQLAlchemy, or asyncio.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nickcosmetics_core.domain.inventory.stock import StockBatch as DomainStockBatch
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentMethod
from nickcosmetics_core.domain.sales.sale import Sale as DomainSale
from nickcosmetics_core.domain.sales.sale import SaleChannel, SaleItem, SaleStatus
from nickcosmetics_core.interfaces.repositories import SaleRepository, StockBatchRepository

from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import StockBatch as ORMStockBatch


class PreloadedStockBatchRepository(StockBatchRepository):
    """In-memory, synchronous -- wraps StockBatch rows pre-fetched
    from the DB for exactly the variants involved in one checkout
    call. Mutations to `quantity_remaining` happen directly on the
    SQLAlchemy-tracked ORM objects, so they're picked up automatically
    on the caller's next `session.commit()` -- no explicit re-save step.
    """

    def __init__(self, orm_batches: list[ORMStockBatch]):
        self._orm_by_id: dict[uuid.UUID, ORMStockBatch] = {b.id: b for b in orm_batches}

    def get_batches_for_variant(self, variant_id: str, branch_id: str) -> list[DomainStockBatch]:
        vid, bid = uuid.UUID(variant_id), uuid.UUID(branch_id)
        return [
            DomainStockBatch(
                id=str(b.id),
                variant_id=str(b.variant_id),
                branch_id=str(b.branch_id),
                quantity_received=b.quantity_received,
                quantity_remaining=b.quantity_remaining,
                unit_cost=Money(b.unit_cost_kobo),
                expiry_date=b.expiry_date,
                received_at=b.received_at,
            )
            for b in self._orm_by_id.values()
            if b.variant_id == vid and b.branch_id == bid
        ]

    def save_batch(self, batch: DomainStockBatch) -> None:
        self._orm_by_id[uuid.UUID(batch.id)].quantity_remaining = batch.quantity_remaining


class PreloadedSaleRepository(SaleRepository):
    """Wraps a possibly-pre-existing Sale (for idempotency checks) and
    captures whatever SalesService.checkout() ultimately saves, so the
    caller can tell a genuine new sale apart from an idempotent replay.
    """

    def __init__(self, existing_orm_sale: ORMSale | None, existing_orm_items: list[ORMSaleItem]):
        self._existing_sale = existing_orm_sale
        self._existing_items = existing_orm_items
        self.saved_domain_sale: DomainSale | None = None
        self.was_idempotent_hit = False

    def get_by_local_ref_id(self, local_ref_id: str) -> DomainSale | None:
        if self._existing_sale is None:
            return None
        self.was_idempotent_hit = True
        sale = DomainSale(
            id=str(self._existing_sale.id),
            branch_id=str(self._existing_sale.branch_id),
            served_by=str(self._existing_sale.served_by) if self._existing_sale.served_by else "",
            channel=SaleChannel(self._existing_sale.channel),
            local_ref_id=self._existing_sale.local_ref_id or "",
            customer_id=str(self._existing_sale.customer_id) if self._existing_sale.customer_id else None,
            discount=Money(self._existing_sale.discount_kobo),
            payment_method=PaymentMethod(self._existing_sale.payment_method)
            if self._existing_sale.payment_method
            else None,
            status=SaleStatus(self._existing_sale.status),
            created_at=self._existing_sale.created_at,
        )
        for item in self._existing_items:
            sale.add_item(
                SaleItem(
                    variant_id=str(item.variant_id),
                    stock_batch_id=str(item.stock_batch_id) if item.stock_batch_id else "",
                    quantity=item.quantity,
                    unit_price=Money(item.unit_price_kobo),
                )
            )
        return sale

    def save(self, sale: DomainSale) -> None:
        self.saved_domain_sale = sale


async def load_stock_batch_repo(
    session: AsyncSession, variant_ids: list[uuid.UUID], branch_id: uuid.UUID
) -> PreloadedStockBatchRepository:
    result = await session.execute(
        select(ORMStockBatch).where(
            ORMStockBatch.variant_id.in_(variant_ids),
            ORMStockBatch.branch_id == branch_id,
            ORMStockBatch.quantity_remaining > 0,
        )
    )
    return PreloadedStockBatchRepository(list(result.scalars().all()))


async def load_sale_repo(session: AsyncSession, local_ref_id: str) -> PreloadedSaleRepository:
    result = await session.execute(select(ORMSale).where(ORMSale.local_ref_id == local_ref_id))
    existing_sale = result.scalar_one_or_none()

    existing_items: list[ORMSaleItem] = []
    if existing_sale is not None:
        items_result = await session.execute(
            select(ORMSaleItem).where(ORMSaleItem.sale_id == existing_sale.id)
        )
        existing_items = list(items_result.scalars().all())

    return PreloadedSaleRepository(existing_sale, existing_items)


def persist_new_sale(
    session: AsyncSession, domain_sale: DomainSale, payment_method: str | None
) -> ORMSale:
    """Turn a freshly-created domain Sale into ORM rows and stage them
    on the session (caller still needs to commit). Stock batch
    mutations from PreloadedStockBatchRepository.save_batch() are
    already staged automatically via the SQLAlchemy identity map.
    """
    orm_sale = ORMSale(
        id=uuid.UUID(domain_sale.id),
        branch_id=uuid.UUID(domain_sale.branch_id),
        customer_id=uuid.UUID(domain_sale.customer_id) if domain_sale.customer_id else None,
        served_by=uuid.UUID(domain_sale.served_by) if domain_sale.served_by else None,
        channel=domain_sale.channel.value,
        status=domain_sale.status.value,
        subtotal_kobo=domain_sale.subtotal.kobo,
        discount_kobo=domain_sale.discount.kobo,
        total_kobo=domain_sale.total.kobo,
        payment_method=payment_method,
        local_ref_id=domain_sale.local_ref_id,
    )
    session.add(orm_sale)

    for item in domain_sale.items:
        session.add(
            ORMSaleItem(
                sale_id=orm_sale.id,
                variant_id=uuid.UUID(item.variant_id),
                stock_batch_id=uuid.UUID(item.stock_batch_id) if item.stock_batch_id else None,
                quantity=item.quantity,
                unit_price_kobo=item.unit_price.kobo,
                line_total_kobo=item.line_total.kobo,
            )
        )

    return orm_sale
