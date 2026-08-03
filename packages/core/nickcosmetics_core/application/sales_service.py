from __future__ import annotations

from dataclasses import dataclass

from nickcosmetics_core.domain.inventory.stock import FifoStockAllocator
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import Sale, SaleChannel, SaleItem
from nickcosmetics_core.interfaces.repositories import SaleRepository, StockBatchRepository


@dataclass
class CheckoutLine:
    variant_id: str
    quantity: int
    unit_price: Money


class SalesService:
    """Use case: process a checkout. This is the single place that
    coordinates "deduct stock FIFO + record the sale" — identical
    whether it's called from the cloud API (against Postgres) or the
    offline desktop app (against local SQLite), because both pass in
    repository implementations satisfying the same interface.
    """

    def __init__(
        self,
        stock_repo: StockBatchRepository,
        sale_repo: SaleRepository,
    ) -> None:
        self._stock_repo = stock_repo
        self._sale_repo = sale_repo

    def checkout(
        self,
        *,
        sale_id: str,
        branch_id: str,
        served_by: str,
        channel: SaleChannel,
        local_ref_id: str,
        lines: list[CheckoutLine],
        customer_id: str | None = None,
        discount: Money | None = None,
    ) -> Sale:
        # Idempotency: if this sale was already synced/processed, return
        # the existing record rather than creating (or deducting) again.
        existing = self._sale_repo.get_by_local_ref_id(local_ref_id)
        if existing is not None:
            return existing

        sale = Sale(
            id=sale_id,
            branch_id=branch_id,
            served_by=served_by,
            channel=channel,
            local_ref_id=local_ref_id,
            customer_id=customer_id,
            discount=discount or Money(0),
        )

        for line in lines:
            batches = self._stock_repo.get_batches_for_variant(
                line.variant_id, branch_id
            )
            allocation = FifoStockAllocator.allocate(
                batches, line.variant_id, line.quantity
            )
            for batch, take_qty in allocation:
                batch.quantity_remaining -= take_qty
                self._stock_repo.save_batch(batch)
                sale.add_item(
                    SaleItem(
                        variant_id=line.variant_id,
                        stock_batch_id=batch.id,
                        quantity=take_qty,
                        unit_price=line.unit_price,
                    )
                )

        self._sale_repo.save(sale)
        return sale
