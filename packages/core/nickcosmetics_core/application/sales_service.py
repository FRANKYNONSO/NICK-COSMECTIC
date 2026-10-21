from __future__ import annotations

from dataclasses import dataclass, field

from nickcosmetics_core.domain.inventory.stock import FifoStockAllocator
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentTender, Sale, SaleChannel, SaleItem, SaleStatus
from nickcosmetics_core.exceptions import PaymentMismatchError
from nickcosmetics_core.interfaces.repositories import SaleRepository, StockBatchRepository


@dataclass
class CheckoutLine:
    variant_id: str
    quantity: int
    unit_price: Money
    # Per-line discount (e.g. a cashier-applied markdown on one item),
    # distinct from the sale-wide `discount` passed to checkout().
    # Rolled into Sale.discount as a whole -- see checkout() below --
    # since SaleItem itself has no discount field; the domain only
    # tracks discount as a single total, same as it always has.
    discount: Money = field(default_factory=lambda: Money(0))


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
        coupon_code: str | None = None,
        coupon_discount: Money | None = None,
        payments: list[PaymentTender] | None = None,
        status: SaleStatus = SaleStatus.COMPLETED,
        notes: str | None = None,
    ) -> Sale:
        # Idempotency: if this sale was already synced/processed, return
        # the existing record rather than creating (or deducting) again.
        existing = self._sale_repo.get_by_local_ref_id(local_ref_id)
        if existing is not None:
            return existing

        # Total discount = the sale-wide discount (manual markdown +
        # any coupon, already combined by the caller) plus every
        # per-line discount. Combining them here -- rather than
        # trusting the caller to have pre-summed everything into
        # `discount` -- is what keeps CheckoutLine.discount from being
        # silently dropped at checkout time.
        line_discount_total = Money(0)
        for line in lines:
            line_discount_total = line_discount_total + line.discount
        total_discount = (discount or Money(0)) + line_discount_total

        sale = Sale(
            id=sale_id,
            branch_id=branch_id,
            served_by=served_by,
            channel=channel,
            local_ref_id=local_ref_id,
            customer_id=customer_id,
            discount=total_discount,
            coupon_code=coupon_code,
            coupon_discount=coupon_discount or Money(0),
            status=status,
            notes=notes,
        )

        for line in lines:
            batches = self._stock_repo.get_batches_for_variant(
                line.variant_id, branch_id
            )
            allocation = FifoStockAllocator.allocate(
                batches, line.variant_id, line.quantity
            )
            for batch, take_qty in allocation:
                # September 2026 change spec section 9: an open booking
                # is a reservation on money not yet paid, not a
                # reservation on stock -- "the customer hasn't paid in
                # full, so the item is still physically in the shop and
                # still counts as available stock. Only a completed
                # sale ... should reduce stock." FifoStockAllocator
                # still runs above so the sale item can record which
                # batch it *would* draw from (cost basis, traceability),
                # but that batch's quantity_remaining is deliberately
                # left untouched until the booking is actually paid in
                # full and completed -- see add_booking_payment() in
                # api/v1/routers/sales.py, which re-runs a real
                # allocation (against whatever stock genuinely remains
                # at that later point) and performs the actual
                # deduction then. Open Credit is unaffected by this --
                # it still deducts immediately here, same as always,
                # since credit.py never passes status=BOOKING.
                if status != SaleStatus.BOOKING:
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

        if payments:
            tendered = Money(0)
            for payment in payments:
                tendered = tendered + payment.amount
            # Undertendering is rejected for a normal checkout -- but a
            # booking (status=BOOKING) is a deliberate part-payment, so
            # the floor check only applies when the sale is meant to be
            # fully paid. Overtendering is always fine either way -- see
            # Sale.change_due -- so this only ever checks the floor.
            if status == SaleStatus.COMPLETED and tendered.kobo < sale.total.kobo:
                raise PaymentMismatchError(
                    total_kobo=sale.total.kobo, tendered_kobo=tendered.kobo
                )
            sale.payments = list(payments)
            sale.payment_method = payments[0].method

        self._sale_repo.save(sale)
        return sale