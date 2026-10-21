import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from api.v1.schemas.sales import SaleItemOut


class CouponRefundSummary(BaseModel):
    """Minimal refund info shown against a redeemed coupon in the
    traceability report -- enough to see at a glance whether the sale
    the coupon was applied to was later refunded, without pulling in
    the full refund receipt schema.
    """

    id: uuid.UUID
    amount_naira: float
    status: str
    reason: str | None = None
    created_at: datetime


class CouponIssueRequest(BaseModel):
    discount_amount_naira: float = Field(gt=0)
    # PATCH3C-COUPON-SERVER: reference code of a store credit (CHG-xxxxxx).
    # When given, the server uses the full credit as the amount.
    reference_code: str | None = Field(default=None, max_length=32)
    # Every coupon is fixed at 30 minutes' validity (see
    # COUPON_VALIDITY_MINUTES in the router) -- there is no
    # caller-adjustable duration anymore, so this field no longer
    # exists here. Kept out entirely rather than accepted-and-ignored,
    # so an old client sending it gets a clear validation error
    # instead of silently thinking it worked.


class CouponOut(BaseModel):
    id: uuid.UUID
    code: str
    discount_amount_naira: float
    status: str
    # Lazily-computed view of `status` that accounts for coupons which
    # have passed their expiry but were never touched by a redeem/void
    # call (so the stored `status` column is still "active"). Traceability
    # screens should read this field, not the raw `status`, so an
    # expired-and-unused coupon reads as "expired" rather than "active".
    effective_status: str
    expires_at: datetime
    issued_by: uuid.UUID
    issued_by_name: str | None = None
    redeemed_by: uuid.UUID | None
    redeemed_by_name: str | None = None
    redeemed_sale_id: uuid.UUID | None
    redeemed_at: datetime | None
    created_at: datetime
    # Which branch the coupon was actually redeemed in -- derived from
    # the redeemed sale's branch_id, not stored on the coupon itself
    # (a coupon has no branch of its own until it's used). Lets the
    # super admin's "All Branches" traceability view show exactly
    # where a coupon travelled, and lets a branch-scoped view exclude
    # coupons redeemed elsewhere without hiding them from "All
    # Branches". Null for a coupon that hasn't been redeemed yet.
    redeemed_branch_id: uuid.UUID | None = None
    redeemed_branch_name: str | None = None
    # Populated only on the /history traceability view, and only for
    # a coupon that was actually redeemed -- the line items of the
    # sale it was applied to, so the super admin can see what it was
    # used for the same way a receipt shows items.
    redeemed_items: list[SaleItemOut] | None = None
    # Refund history for the redeemed sale, if any (item 2: traceability
    # should show whether the transaction the coupon was used on was
    # later refunded, consistent with how refunds surface elsewhere).
    # Only ever populated for a redeemed coupon.
    refunds: list[CouponRefundSummary] | None = None
    # PATCH7A-COUPON-CREDIT-LINE: CHG code of the store credit this coupon was
    # made from. Filled only on the /history view; None for hand-made coupons.
    credit_reference_code: str | None = None

    model_config = {"from_attributes": True}


class CouponRedeemRequest(BaseModel):
    code: str
    sale_id: uuid.UUID
    sale_total_naira: float = Field(ge=0)
