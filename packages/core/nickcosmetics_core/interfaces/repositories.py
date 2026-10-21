"""Abstract repository contracts.

The domain/application layers depend only on these interfaces, never
on SQLAlchemy or any concrete storage technology. Each app
(apps/api, apps/desktop_pos) provides its own implementation —
apps/api's talks to PostgreSQL, apps/desktop_pos's talks to local
SQLite — but both satisfy the same contract, so `SalesService` (in
application/sales_service.py) works unmodified against either.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nickcosmetics_core.domain.coupons.coupon import Coupon
from nickcosmetics_core.domain.inventory.stock import StockBatch
from nickcosmetics_core.domain.sales.sale import Sale


class StockBatchRepository(ABC):
    @abstractmethod
    def get_batches_for_variant(
        self, variant_id: str, branch_id: str
    ) -> list[StockBatch]:
        """Return all non-empty stock batches for a variant at a branch,
        used as input to FifoStockAllocator.
        """

    @abstractmethod
    def save_batch(self, batch: StockBatch) -> None:
        """Persist a batch's updated quantity_remaining."""


class SaleRepository(ABC):
    @abstractmethod
    def get_by_local_ref_id(self, local_ref_id: str) -> Sale | None:
        """Look up an existing sale by its client-generated idempotency
        key. Used to make sale creation idempotent under sync retries.
        """

    @abstractmethod
    def save(self, sale: Sale) -> None:
        """Persist a new or updated sale."""


class CouponRepository(ABC):
    @abstractmethod
    def get_by_code(self, code: str) -> Coupon | None:
        """Look up a coupon by its human-entered code (case-insensitive
        at the call site — callers normalize to uppercase before
        calling this, matching pos_window.py's `.strip().upper()`).
        """

    @abstractmethod
    def save(self, coupon: Coupon) -> None:
        """Persist a new or updated coupon (issue, or mark redeemed)."""
