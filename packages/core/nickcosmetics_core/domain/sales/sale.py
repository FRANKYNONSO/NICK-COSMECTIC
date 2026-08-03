from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from nickcosmetics_core.domain.money import Money


class SaleChannel(str, Enum):
    POS = "pos"
    WEBSITE = "website"
    MOBILE = "mobile"


class SaleStatus(str, Enum):
    COMPLETED = "completed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    CANCELLED = "cancelled"


class PaymentMethod(str, Enum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    ONLINE = "online"


@dataclass
class SaleItem:
    variant_id: str
    stock_batch_id: str
    quantity: int
    unit_price: Money

    @property
    def line_total(self) -> Money:
        return self.unit_price * self.quantity


@dataclass
class Sale:
    """Aggregate root for a completed (or in-progress) sale.

    `local_ref_id` is the offline-generated idempotency key — it is
    what allows the same sale to be synced from a desktop POS multiple
    times (e.g. after a retried network request) without ever creating
    a duplicate record. Always set on the client at sale-creation time,
    never generated server-side.
    """

    id: str
    branch_id: str
    served_by: str
    channel: SaleChannel
    local_ref_id: str
    items: list[SaleItem] = field(default_factory=list)
    customer_id: str | None = None
    discount: Money = field(default_factory=lambda: Money(0))
    payment_method: PaymentMethod | None = None
    status: SaleStatus = SaleStatus.COMPLETED
    created_at: datetime | None = None

    @property
    def subtotal(self) -> Money:
        total = Money(0)
        for item in self.items:
            total = total + item.line_total
        return total

    @property
    def total(self) -> Money:
        return self.subtotal - self.discount

    def add_item(self, item: SaleItem) -> None:
        self.items.append(item)
