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


class CachedVariant(Base):
    """Mirrors just the fields the POS needs to sell an item offline.
    Refreshed wholesale from the API on each successful sync -- this
    table is never the source of truth, the cloud is.
    """

    __tablename__ = "cached_variants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    variant_name: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    selling_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CachedStockBatch(Base):
    """Local mirror of cloud stock_batches for one branch."""

    __tablename__ = "cached_stock_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    variant_id: Mapped[str] = mapped_column(String, ForeignKey("cached_variants.id"), nullable=False)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OutboxSale(Base):
    """Every sale made on this device, online or offline, is written
    here first before anything touches the network.
    """

    __tablename__ = "outbox_sales"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    local_ref_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    served_by: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, default="pos")
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    discount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    subtotal_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)


class OutboxSaleItem(Base):
    __tablename__ = "outbox_sale_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    outbox_sale_id: Mapped[str] = mapped_column(String, ForeignKey("outbox_sales.id"), nullable=False)
    variant_id: Mapped[str] = mapped_column(String, nullable=False)
    stock_batch_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)


class SessionCache(Base):
    """Persists the last successful login so the app can resume
    without the internet. Always a single row (id=1).
    """

    __tablename__ = "session_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_branch_id: Mapped[str | None] = mapped_column(String, nullable=True)


class CachedBranch(Base):
    """Local mirror of the cloud branches list."""

    __tablename__ = "cached_branches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
