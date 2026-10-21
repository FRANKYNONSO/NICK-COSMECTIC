"""Adapter bridging the async SQLAlchemy database with the
synchronous, framework-agnostic CouponService in nickcosmetics_core.

Same pattern as checkout_adapter.py: async pre-fetch -> run domain
logic in-memory (sync, fast, no I/O) -> async persist the result.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nickcosmetics_core.domain.coupons.coupon import Coupon as DomainCoupon
from nickcosmetics_core.domain.coupons.coupon import CouponStatus
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.interfaces.repositories import CouponRepository

from infrastructure.db.models import Coupon as ORMCoupon


def _orm_to_domain(row: ORMCoupon) -> DomainCoupon:
    return DomainCoupon(
        code=row.code,
        discount_amount=Money(row.discount_amount_kobo),
        created_by=str(row.created_by),
        created_at=row.created_at,
        expires_at=row.expires_at,
        status=CouponStatus(row.status),
        used_by=str(row.used_by) if row.used_by else None,
        used_at=row.used_at,
        sale_id=str(row.sale_id) if row.sale_id else None,
        discount_applied=(
            Money(row.discount_applied_kobo) if row.discount_applied_kobo is not None else None
        ),
    )


class PreloadedCouponRepository(CouponRepository):
    """In-memory, synchronous -- wraps at most one pre-fetched Coupon
    row (issue/redeem/void all act on a single code per call, unlike
    checkout's multi-variant preload). `existing_orm` is exposed
    publicly so the router can pass it straight into persist_coupon()
    after the domain call, same spirit as
    PreloadedSaleRepository.was_idempotent_hit.
    """

    def __init__(self, existing_orm_coupon: ORMCoupon | None, known_codes: set[str] | None = None):
        self.existing_orm = existing_orm_coupon
        self._known_codes = known_codes or set()

    def get_by_code(self, code: str) -> DomainCoupon | None:
        if self.existing_orm is not None and self.existing_orm.code == code:
            return _orm_to_domain(self.existing_orm)
        return None

    def save(self, coupon: DomainCoupon) -> None:
        # Actual persistence happens in persist_coupon() after the
        # domain call returns -- this just satisfies the interface,
        # same as PreloadedSaleRepository.save() capturing the result.
        pass

    def code_exists(self, code: str) -> bool:
        return code in self._known_codes

    def list_issued(self, since=None, admin_id=None) -> list[DomainCoupon]:
        raise NotImplementedError(
            "Reporting/history endpoints query the DB directly (see routers/coupons.py) "
            "rather than going through this preloaded, single-coupon repository."
        )

    def list_used(self, since=None, cashier_id=None) -> list[DomainCoupon]:
        raise NotImplementedError(
            "Reporting/history endpoints query the DB directly (see routers/coupons.py) "
            "rather than going through this preloaded, single-coupon repository."
        )

    def list_active(self) -> list[DomainCoupon]:
        raise NotImplementedError(
            "Reporting/history endpoints query the DB directly (see routers/coupons.py) "
            "rather than going through this preloaded, single-coupon repository."
        )


async def load_coupon_repo_for_issue(session: AsyncSession) -> PreloadedCouponRepository:
    """Issuing a coupon needs to know which codes are already taken
    (collision check inside CouponService._generate_unique_code) but
    there's no existing row to preload.
    """
    result = await session.execute(select(ORMCoupon.code))
    known_codes = set(result.scalars().all())
    return PreloadedCouponRepository(existing_orm_coupon=None, known_codes=known_codes)


async def load_coupon_repo_by_code(session: AsyncSession, code: str) -> PreloadedCouponRepository:
    """For redeem/void, we only need the one coupon row this call
    is about.
    """
    result = await session.execute(select(ORMCoupon).where(ORMCoupon.code == code))
    existing = result.scalar_one_or_none()
    known = {code} if existing is not None else set()
    return PreloadedCouponRepository(existing_orm_coupon=existing, known_codes=known)


def persist_coupon(
    session: AsyncSession, domain_coupon: DomainCoupon, existing_orm: ORMCoupon | None
) -> ORMCoupon:
    """Turn a domain Coupon (freshly issued, redeemed, or voided) into
    an ORM row and stage it on the session (caller still needs to
    commit). Pass `repo.existing_orm` from the PreloadedCouponRepository
    used for this call -- None for a fresh issue, the pre-fetched row
    for redeem/void.
    """
    if existing_orm is not None:
        orm_coupon = existing_orm
    else:
        orm_coupon = ORMCoupon(
            code=domain_coupon.code, created_by=uuid.UUID(domain_coupon.created_by)
        )
        session.add(orm_coupon)

    orm_coupon.discount_amount_kobo = domain_coupon.discount_amount.kobo
    orm_coupon.created_at = domain_coupon.created_at
    orm_coupon.expires_at = domain_coupon.expires_at
    orm_coupon.status = domain_coupon.status.value
    orm_coupon.used_by = uuid.UUID(domain_coupon.used_by) if domain_coupon.used_by else None
    orm_coupon.used_at = domain_coupon.used_at
    orm_coupon.sale_id = uuid.UUID(domain_coupon.sale_id) if domain_coupon.sale_id else None
    orm_coupon.discount_applied_kobo = (
        domain_coupon.discount_applied.kobo if domain_coupon.discount_applied else None
    )
    return orm_coupon
