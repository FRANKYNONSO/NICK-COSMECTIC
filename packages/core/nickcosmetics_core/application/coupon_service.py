from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime, timedelta

from nickcosmetics_core.domain.coupons.coupon import Coupon, CouponStatus
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.exceptions import (
    CouponAlreadyUsedError,
    CouponExpiredError,
    CouponNotFoundError,
    CouponVoidedError,
)
from nickcosmetics_core.interfaces.repositories import CouponRepository

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6


def generate_coupon_code() -> str:
    """Matches the format the desktop UI already hints at in its
    placeholder text: 'e.g. SAVE-XXXXXX'. Public so callers that need
    to generate a code without a full sync CouponRepository on hand
    (e.g. the API's async router) can reuse it too.
    """
    suffix = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"SAVE-{suffix}"


class CouponService:
    """Use case: issue and redeem fixed-amount discount coupons.

    Same shape as SalesService — works unmodified against either the
    cloud API's Postgres-backed CouponRepository or the desktop app's
    local SQLite one, because both satisfy the same interface.
    """

    def __init__(self, repo: CouponRepository) -> None:
        self._repo = repo

    def issue(
        self,
        *,
        discount_amount: Money,
        issued_by: str,
        valid_days: int = 30,
        coupon_id: str | None = None,
    ) -> Coupon:
        code = generate_coupon_code()
        now = datetime.utcnow()
        coupon = Coupon(
            id=coupon_id or str(uuid.uuid4()),
            code=code,
            discount_amount=discount_amount,
            status=CouponStatus.ACTIVE,
            expires_at=now + timedelta(days=valid_days),
            issued_by=issued_by,
            created_at=now,
        )
        self._repo.save(coupon)
        return coupon

    def redeem(
        self,
        *,
        code: str,
        cashier_id: str,
        sale_id: str,
        sale_total: Money,
    ) -> Coupon:
        """Validates and marks a coupon as redeemed. Raises one of the
        Coupon*Error exceptions on any invalid state rather than
        silently no-op'ing — callers (see pos_window.py._checkout)
        catch these and surface them to the cashier, then roll back
        the whole sale rather than complete it with an invalid
        discount applied.

        `sale_total` is accepted but not currently validated against
        (no "minimum spend" rule exists yet) — kept as a parameter so
        that rule can be added later without changing every caller.
        """
        coupon = self._repo.get_by_code(code)
        if coupon is None:
            raise CouponNotFoundError(code)

        if coupon.status == CouponStatus.REDEEMED:
            raise CouponAlreadyUsedError(code)
        if coupon.status == CouponStatus.VOIDED:
            raise CouponVoidedError(code)
        if coupon.status == CouponStatus.EXPIRED or coupon.expires_at <= datetime.utcnow():
            raise CouponExpiredError(code)

        coupon.status = CouponStatus.REDEEMED
        coupon.redeemed_by = cashier_id
        coupon.redeemed_sale_id = sale_id
        coupon.redeemed_at = datetime.utcnow()
        self._repo.save(coupon)
        return coupon

    def void(self, *, code: str) -> Coupon:
        """Cancels an unredeemed coupon (e.g. issued by mistake).
        Voiding an already-redeemed coupon is intentionally refused —
        that would silently undo a discount already applied to a
        completed sale.
        """
        coupon = self._repo.get_by_code(code)
        if coupon is None:
            raise CouponNotFoundError(code)
        if coupon.status == CouponStatus.REDEEMED:
            raise CouponAlreadyUsedError(code)

        coupon.status = CouponStatus.VOIDED
        self._repo.save(coupon)
        return coupon
