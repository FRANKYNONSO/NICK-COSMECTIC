"""Local SQLite schema for offline-first operation.

This is deliberately a much smaller schema than the cloud database --
just enough to let the POS keep selling with zero internet:
- A read-only cache of variants/stock, refreshed from the API whenever
  online (see local_db/cache_sync.py).
- An outbox of sales made while offline (or even while online -- every
  sale always goes through the outbox first), pushed to the cloud API
  when connectivity is available (see sync/outbox_sync.py).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_str() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------- CATALOG CACHE

class CachedBranch(Base):
    """Mirrors branches for the branch-picker dropdown when offline.
    Refreshed wholesale from the API by cache_sync.pull_branches().
    """

    __tablename__ = "cached_branches"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # cloud branch UUID as string
    name: Mapped[str] = mapped_column(String, nullable=False)


class CachedVariant(Base):
    """Mirrors just the fields the POS needs to sell an item offline.
    Refreshed wholesale from the API on each successful sync -- this
    table is never the source of truth, the cloud is.
    """

    __tablename__ = "cached_variants"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # cloud variant UUID as string
    product_id: Mapped[str] = mapped_column(String, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    variant_name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, default="Uncategorized")
    sku: Mapped[str] = mapped_column(String, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    selling_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CachedStockBatch(Base):
    """Local mirror of cloud stock_batches for one branch -- what
    FifoStockAllocator (from nickcosmetics_core) actually reads/writes
    when a sale happens offline. Deltas made offline are reconciled
    against the cloud during sync; see sync/outbox_sync.py for the
    conflict-handling notes.
    """

    __tablename__ = "cached_stock_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # cloud batch UUID as string
    variant_id: Mapped[str] = mapped_column(String, ForeignKey("cached_variants.id"), nullable=False)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# --------------------------------------------------------------- OUTBOX

class OutboxSale(Base):
    """Every sale made on this device, online or offline, is written
    here first (and to CachedStockBatch, deducted immediately) before
    anything touches the network. `synced` flips to True once the
    cloud API has confirmed it -- see sync/outbox_sync.py.

    `local_ref_id` is the idempotency key the cloud API's /sales/checkout
    endpoint already understands (see checkout_adapter.py in the API),
    so a sync retry after a dropped connection can never double-sell.

    `voided`/`voided_at` support cancelling a sale made in error --
    see local_db/reports.py:void_sale(). Only allowed while `synced`
    is still False; once a sale has synced, reversing it needs a
    proper refund flow (server-side), not a local void.
    """

    __tablename__ = "outbox_sales"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    local_ref_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    served_by: Mapped[str] = mapped_column(String, nullable=False)
    shift_id: Mapped[str | None] = mapped_column(String, ForeignKey("shifts.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String, default="pos")
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    discount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    subtotal_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OutboxSaleItem(Base):
    __tablename__ = "outbox_sale_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    outbox_sale_id: Mapped[str] = mapped_column(String, ForeignKey("outbox_sales.id"), nullable=False)
    variant_id: Mapped[str] = mapped_column(String, nullable=False)
    stock_batch_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)


class OutboxPayment(Base):
    """One tender applied to an OutboxSale. A cash-only sale still
    gets exactly one row here -- everything downstream (receipts,
    sync, reporting) reads payments as a list, never a single field,
    so split/mixed-tender sales need no special-casing.
    """

    __tablename__ = "outbox_payments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    outbox_sale_id: Mapped[str] = mapped_column(String, ForeignKey("outbox_sales.id"), nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)


# --------------------------------------------------------------- COUPONS

class CachedCoupon(Base):
    """Local mirror of active cloud coupons, so a cashier can apply a
    coupon code with zero connectivity. Refreshed wholesale by
    cache_sync.pull_coupons() -- same replace-and-reinsert pattern as
    CachedVariant/CachedStockBatch. Redemption happens locally first
    (see pos_window.py._checkout -> CouponService.redeem against
    LocalCouponRepository) and is written back to `status` here
    immediately so the same device can't double-redeem it -- syncing
    that redemption up to the cloud (so a *different* device also
    can't reuse it before the next sync) is a follow-up, same caveat
    that already applies to OutboxSale until it's pushed.
    """

    __tablename__ = "cached_coupons"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # cloud coupon UUID as string
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    discount_amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    redeemed_sale_id: Mapped[str | None] = mapped_column(String, nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ------------------------------------------------------- SESSION CACHE

class SessionCache(Base):
    """Persists the last successful login so the app can resume
    without the internet -- e.g. reopened while offline. Always a
    single row (id=1). See local_db/session_cache.py for how this is
    used: checked fresh against the API when reachable, otherwise
    trusted as-is so a cashier isn't locked out of a working offline
    device just because the login screen itself needs connectivity.

    `last_branch_id` remembers the branch this device was last set to
    -- see local_db/session_cache.py:save_last_branch() -- so a
    multi-branch manager's device reopens on the same branch instead
    of defaulting back to the first one in the list every time.
    """

    __tablename__ = "session_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_branch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ------------------------------------------------------------- SHIFTS

class Shift(Base):
    """One cashier's till session at a branch, opened with a counted
    starting float and closed with a counted ending amount -- the
    basic unit of cash reconciliation. `status` is "open" or "closed";
    only one open shift is expected per (branch, user) at a time,
    enforced in local_db/shifts.py rather than at the schema level.
    """

    __tablename__ = "shifts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    opened_by: Mapped[str] = mapped_column(String, nullable=False)
    opened_by_name: Mapped[str] = mapped_column(String, nullable=False)
    opening_float_kobo: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closing_counted_kobo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")


# --------------------------------------------------------- HELD SALES

class HeldSale(Base):
    """A cart parked mid-sale (e.g. to serve another customer) --
    "Hold Sale" in the UI. Resuming it restores the cart, per-line
    discounts, sale-wide discount, and coupon exactly as they were.
    """

    __tablename__ = "held_sales"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    held_by: Mapped[str] = mapped_column(String, nullable=False)
    held_by_name: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    sale_discount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    coupon_code: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HeldSaleItem(Base):
    __tablename__ = "held_sale_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    held_sale_id: Mapped[str] = mapped_column(String, ForeignKey("held_sales.id"), nullable=False)
    variant_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_kobo: Mapped[int] = mapped_column(Integer, default=0)