from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.exceptions import InsufficientStockError


@dataclass
class ProductVariant:
    id: str
    product_id: str
    variant_name: str
    sku: str
    barcode: str | None
    cost_price: Money
    selling_price: Money
    reorder_level: int = 5


@dataclass
class StockBatch:
    """A single received lot of a variant. Stock is tracked as a
    collection of batches (FIFO), not a single quantity column, so
    expiry dates and cost basis are accurate per batch.
    """

    id: str
    variant_id: str
    branch_id: str
    quantity_received: int
    quantity_remaining: int
    unit_cost: Money
    expiry_date: date | None = None
    batch_number: str | None = None
    received_at: datetime | None = None

    def is_expired(self, as_of: date | None = None) -> bool:
        if self.expiry_date is None:
            return False
        return self.expiry_date <= (as_of or date.today())


class FifoStockAllocator:
    """Determines which batch(es) a sale draws down from, oldest first.

    This is pure domain logic with no DB access — it operates on a list
    of StockBatch objects handed to it by a repository, and returns the
    allocation plan. The caller (application service) is responsible
    for persisting the resulting deductions.
    """

    @staticmethod
    def allocate(
        batches: list[StockBatch], variant_id: str, quantity_needed: int
    ) -> list[tuple[StockBatch, int]]:
        """Returns a list of (batch, quantity_to_deduct) pairs, oldest
        non-expired, non-empty batches first. Raises
        InsufficientStockError if total available stock is too low.
        """
        eligible = sorted(
            (
                b
                for b in batches
                if b.variant_id == variant_id
                and b.quantity_remaining > 0
                and not b.is_expired()
            ),
            key=lambda b: b.received_at or datetime.min,
        )

        available = sum(b.quantity_remaining for b in eligible)
        if available < quantity_needed:
            raise InsufficientStockError(
                variant_id=variant_id, requested=quantity_needed, available=available
            )

        plan: list[tuple[StockBatch, int]] = []
        remaining = quantity_needed
        for batch in eligible:
            if remaining <= 0:
                break
            take = min(batch.quantity_remaining, remaining)
            plan.append((batch, take))
            remaining -= take

        return plan
