"""fix_coupon_status_check_constraint

Revision ID: 18a41dc326f3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-15

The ck_coupon_status constraint was created allowing
('active', 'used', 'expired', 'voided') -- leftover from an earlier
version of the coupon schema, back when the code itself wrote 'used'
on redemption (that's why older coupons in the traceability report,
redeemed before this drifted, show correctly). The live CouponStatus
enum (and every current code path) writes 'redeemed' instead, which
the old constraint has never allowed -- every redemption attempt since
that code change has been failing at the DB layer with a
CheckViolationError, silently leaving the coupon stuck at 'active'.

Order matters here: the constraint must be dropped entirely before the
data backfill (old rows at 'used' would violate a constraint that only
allows 'redeemed'), and the new constraint can only go back on after
that backfill (otherwise those same 'used' rows violate it too).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "18a41dc326f3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_coupon_status", "coupons", type_="check")
    op.execute("UPDATE coupons SET status = 'redeemed' WHERE status = 'used'")
    op.create_check_constraint(
        "ck_coupon_status",
        "coupons",
        "status IN ('active', 'redeemed', 'expired', 'voided')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_coupon_status", "coupons", type_="check")
    op.execute("UPDATE coupons SET status = 'used' WHERE status = 'redeemed'")
    op.create_check_constraint(
        "ck_coupon_status",
        "coupons",
        "status IN ('active', 'used', 'expired', 'voided')",
    )