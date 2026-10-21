from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from nickcosmetics_core.domain.money import Money


class CouponStatus(str, Enum):
    ACTIVE = "active"
    REDEEMED = "redeemed"
    VOIDED = "voided"
    EXPIRED = "expired"


@dataclass
class Coupon:
    """A single-use, fixed-amount discount code.

    Deliberately simple: one code, one flat Money amount off, valid
    until `expires_at`, usable exactly once. No percentage discounts,
    no stacking rules, no per-product restrictions — matches what the
    desktop POS's coupon panel already assumes (see
    apps/desktop_pos/ui/pos_window.py _apply_coupon / _checkout).
    """

    id: str
    code: str
    discount_amount: Money
    status: CouponStatus
    expires_at: datetime
    issued_by: str
    created_at: datetime
    redeemed_by: str | None = None
    redeemed_sale_id: str | None = None
    redeemed_at: datetime | None = None
