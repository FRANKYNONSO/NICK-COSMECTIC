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
    # A part-payment reservation: the sale total hasn't been fully
    # tendered yet. Stock is NOT deducted while a booking sits in this
    # status (September 2026 change spec section 9 -- previously it
    # was deducted up front like a completed sale; that's the bug this
    # status comment used to describe). Transitions to COMPLETED
    # (customer pays the balance -- stock is deducted at that point,
    # see add_booking_payment()) or CANCELLED (booking abandoned,
    # nothing to restock since nothing was ever deducted).
    BOOKING = "booking"


class PaymentMethod(str, Enum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    # "online" was removed (Aug 2026 POS fix pass): it was always just
    # a second name for "transfer" (money paid "online" still lands as
    # a bank transfer), which only confused cashiers about which to
    # pick at checkout. Any historical Sale rows already saved with
    # payment_method="online" are untouched -- this only stops new
    # sales from being recorded that way.


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
class PaymentTender:
    """One payment instrument applied to a sale. A sale can be settled
    with a single tender (the common case) or several (split/mixed
    payment, e.g. part cash + part transfer) — `Sale.payments` is
    always a list, even for a single-tender sale, so callers never
    need two code paths.
    """

    method: PaymentMethod
    amount: Money


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
    coupon_code: str | None = None
    coupon_discount: Money = field(default_factory=lambda: Money(0))
    payment_method: PaymentMethod | None = None
    payments: list[PaymentTender] = field(default_factory=list)
    status: SaleStatus = SaleStatus.COMPLETED
    # Free-text note -- currently only used for a booking's flexible
    # pickup window ("later this week"), null on an ordinary sale.
    notes: str | None = None
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

    @property
    def amount_tendered(self) -> Money:
        total = Money(0)
        for payment in self.payments:
            total = total + payment.amount
        return total

    @property
    def change_due(self) -> Money:
        """Positive when the customer overpaid (e.g. handed over more
        cash than the total) — the amount owed back to them.
        """
        excess = self.amount_tendered - self.total
        return excess if excess.kobo > 0 else Money(0)

    def add_item(self, item: SaleItem) -> None:
        self.items.append(item)