"""SQLite-backed implementations of the repository interfaces from
nickcosmetics_core.interfaces.repositories.

This is the offline counterpart to apps/api/infrastructure/db/
checkout_adapter.py -- same SalesService, same FifoStockAllocator,
same idempotency guarantee, just backed by a local SQLite Session
instead of async Postgres. Nothing in nickcosmetics_core had to change
to support this; that's the entire point of keeping the domain layer
framework-agnostic (see docs/architecture Section 1.3).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nickcosmetics_core.domain.coupons.coupon import Coupon as DomainCoupon
from nickcosmetics_core.domain.coupons.coupon import CouponStatus
from nickcosmetics_core.domain.inventory.stock import StockBatch as DomainStockBatch
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentMethod, PaymentTender
from nickcosmetics_core.domain.sales.sale import Sale as DomainSale
from nickcosmetics_core.domain.sales.sale import SaleChannel, SaleItem, SaleStatus
from nickcosmetics_core.interfaces.repositories import CouponRepository, SaleRepository, StockBatchRepository

from local_db.models import CachedCoupon, CachedStockBatch, OutboxPayment, OutboxSale, OutboxSaleItem


class LocalStockBatchRepository(StockBatchRepository):
    def __init__(self, session: Session):
        self._session = session

    def get_batches_for_variant(self, variant_id: str, branch_id: str) -> list[DomainStockBatch]:
        rows = self._session.execute(
            select(CachedStockBatch).where(
                CachedStockBatch.variant_id == variant_id,
                CachedStockBatch.branch_id == branch_id,
                CachedStockBatch.quantity_remaining > 0,
            )
        ).scalars().all()

        return [
            DomainStockBatch(
                id=row.id,
                variant_id=row.variant_id,
                branch_id=row.branch_id,
                quantity_received=row.quantity_remaining,  # local cache doesn't track original qty
                quantity_remaining=row.quantity_remaining,
                unit_cost=Money(row.unit_cost_kobo),
                expiry_date=row.expiry_date,
                received_at=row.received_at,
            )
            for row in rows
        ]

    def save_batch(self, batch: DomainStockBatch) -> None:
        row = self._session.get(CachedStockBatch, batch.id)
        if row is not None:
            row.quantity_remaining = batch.quantity_remaining


class LocalSaleRepository(SaleRepository):
    def __init__(self, session: Session):
        self._session = session

    def get_by_local_ref_id(self, local_ref_id: str) -> DomainSale | None:
        row = self._session.execute(
            select(OutboxSale).where(OutboxSale.local_ref_id == local_ref_id)
        ).scalar_one_or_none()
        if row is None:
            return None

        items = self._session.execute(
            select(OutboxSaleItem).where(OutboxSaleItem.outbox_sale_id == row.id)
        ).scalars().all()

        sale = DomainSale(
            id=row.id,
            branch_id=row.branch_id,
            served_by=row.served_by,
            channel=SaleChannel(row.channel),
            local_ref_id=row.local_ref_id,
            discount=Money(row.discount_kobo),
            payment_method=PaymentMethod(row.payment_method) if row.payment_method else None,
            status=SaleStatus.COMPLETED,
            created_at=row.created_at,
        )
        for item in items:
            sale.add_item(
                SaleItem(
                    variant_id=item.variant_id,
                    stock_batch_id=item.stock_batch_id,
                    quantity=item.quantity,
                    unit_price=Money(item.unit_price_kobo),
                )
            )

        payments = self._session.execute(
            select(OutboxPayment).where(OutboxPayment.outbox_sale_id == row.id)
        ).scalars().all()
        sale.payments = [
            PaymentTender(method=PaymentMethod(p.method), amount=Money(p.amount_kobo))
            for p in payments
        ]
        return sale

    def save(self, sale: DomainSale) -> None:
        outbox_sale = OutboxSale(
            id=sale.id,
            local_ref_id=sale.local_ref_id,
            branch_id=sale.branch_id,
            served_by=sale.served_by,
            channel=sale.channel.value,
            payment_method=sale.payment_method.value if sale.payment_method else None,
            discount_kobo=sale.discount.kobo,
            subtotal_kobo=sale.subtotal.kobo,
            total_kobo=sale.total.kobo,
        )
        self._session.add(outbox_sale)

        for item in sale.items:
            self._session.add(
                OutboxSaleItem(
                    outbox_sale_id=sale.id,
                    variant_id=item.variant_id,
                    stock_batch_id=item.stock_batch_id,
                    quantity=item.quantity,
                    unit_price_kobo=item.unit_price.kobo,
                )
            )

        for payment in sale.payments:
            self._session.add(
                OutboxPayment(
                    outbox_sale_id=sale.id,
                    method=payment.method.value,
                    amount_kobo=payment.amount.kobo,
                )
            )


class LocalCouponRepository(CouponRepository):
    """SQLite-backed CouponRepository -- same role for coupons that
    LocalStockBatchRepository/LocalSaleRepository play for stock/sales:
    lets the framework-agnostic CouponService (nickcosmetics_core.
    application.coupon_service) run unmodified against the local
    offline cache.
    """

    def __init__(self, session: Session):
        self._session = session

    def get_by_code(self, code: str) -> DomainCoupon | None:
        row = self._session.execute(
            select(CachedCoupon).where(CachedCoupon.code == code)
        ).scalar_one_or_none()
        if row is None:
            return None
        return DomainCoupon(
            id=row.id,
            code=row.code,
            discount_amount=Money(row.discount_amount_kobo),
            status=CouponStatus(row.status),
            expires_at=row.expires_at,
            issued_by="",  # not tracked locally -- the cache only mirrors what's needed to redeem
            created_at=row.last_synced_at,
            redeemed_sale_id=row.redeemed_sale_id,
            redeemed_at=row.redeemed_at,
        )

    def save(self, coupon: DomainCoupon) -> None:
        row = self._session.get(CachedCoupon, coupon.id)
        if row is None:
            # Shouldn't normally happen -- coupons are only ever
            # created server-side and pulled down by cache_sync -- but
            # guard against it rather than raise AttributeError below.
            return
        row.status = coupon.status.value
        row.redeemed_sale_id = coupon.redeemed_sale_id
        row.redeemed_at = coupon.redeemed_at
