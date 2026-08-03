"""In-memory repository fakes used only for testing the application
layer in isolation from any real database. Real implementations live
in apps/api/infrastructure/db (SQLAlchemy/Postgres) and
apps/desktop_pos/local_db (SQLAlchemy/SQLite).
"""

from nickcosmetics_core.domain.inventory.stock import StockBatch
from nickcosmetics_core.domain.sales.sale import Sale
from nickcosmetics_core.interfaces.repositories import SaleRepository, StockBatchRepository


class FakeStockBatchRepository(StockBatchRepository):
    def __init__(self, batches: list[StockBatch]):
        self._batches = {b.id: b for b in batches}

    def get_batches_for_variant(self, variant_id: str, branch_id: str) -> list[StockBatch]:
        return [
            b
            for b in self._batches.values()
            if b.variant_id == variant_id and b.branch_id == branch_id
        ]

    def save_batch(self, batch: StockBatch) -> None:
        self._batches[batch.id] = batch


class FakeSaleRepository(SaleRepository):
    def __init__(self):
        self._sales_by_ref: dict[str, Sale] = {}

    def get_by_local_ref_id(self, local_ref_id: str) -> Sale | None:
        return self._sales_by_ref.get(local_ref_id)

    def save(self, sale: Sale) -> None:
        self._sales_by_ref[sale.local_ref_id] = sale
