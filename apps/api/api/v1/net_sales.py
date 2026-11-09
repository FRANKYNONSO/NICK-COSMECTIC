"""Patch 26f -- refund-aware sales, revenue and profit figures.

What the reports used to do: count only sales whose status is "completed".
That dropped a PARTIALLY refunded sale from revenue and profit completely,
and it counted branch transfers (channel "transfer") as customer sales.

What they do now:

* Counted sales are customer sales (any channel except "transfer") whose
  status is completed, partially_refunded or refunded.
* A "refunded" sale nets to zero revenue (its goods came back).
* Returned goods are read from the itemised refund lines (RefundItem). Each
  line is capped at the units that were actually sold (the refund screen does
  not stop a second refund of the same item), and is valued at what the
  customer really paid: the line price scaled by Sale.total / Sale.subtotal,
  so a coupon discount is not refunded twice in the figures.
* Cost: the cost of returned units that went back on the shelf (restocked)
  is taken off the cost of goods sold. Spoiled or not-restocked returns keep
  their cost.
* The "spoiled item returned in refund" write-offs that the refund screen
  creates are left out of the losses figures, otherwise the same item would
  be counted twice (once as a refund, once as a loss).
* The period a refund belongs to is the SALE's date, not the refund's date.

The functions that touch the database build plain SELECTs with no GROUP BY
and no computed labels in GROUP BY (all grouping happens in Python), which
avoids the PostgreSQL bind-parameter problem seen before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from infrastructure.db.models import (
    Category,
    Product,
    Refund,
    RefundItem,
    Sale,
    SaleItem,
    StockAdjustment,
    StockBatch,
)

COUNTED_STATUSES = ("completed", "partially_refunded", "refunded")
REVENUE_STATUSES = ("completed", "partially_refunded")
SPOILED_RETURN_NOTE = "Spoiled item returned in refund on sale"


# ------------------------------------------------------------ SQL conditions


def revenue_sale_conds():
    """Sales that produce revenue: customer sales still (partly) standing."""
    return [Sale.status.in_(REVENUE_STATUSES), Sale.channel != "transfer"]


def cost_sale_conds():
    """Sales whose goods left the shop, including fully refunded ones (an
    unrestocked or spoiled return keeps its cost)."""
    return [Sale.status.in_(COUNTED_STATUSES), Sale.channel != "transfer"]


def not_spoiled_return_writeoff():
    """Excludes the auto-created write-off of a spoiled refunded item."""
    return or_(
        StockAdjustment.notes.is_(None),
        ~StockAdjustment.notes.startswith(SPOILED_RETURN_NOTE),
    )


def _period(column, date_from, date_to):
    conds = []
    if date_from:
        conds.append(column >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conds.append(column < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    return conds


# ------------------------------------------------------------ pure calculation


@dataclass
class ReturnsData:
    # kobo returned (what the customer actually paid for the returned goods),
    # summed over sales that count as revenue (not "refunded" ones)
    total_sale_value: int = 0
    # units returned on those same sales
    revenue_units: int = 0
    # cost of returned units that went back on the shelf (all counted sales)
    cost_back_total: int = 0
    # sale_id -> kobo returned (revenue-side sales only)
    sale_value: dict = field(default_factory=dict)
    # product_id -> {"qty", "value", "cost_back"}; value is at line price
    by_product: dict = field(default_factory=dict)
    # category name (or None) -> same shape
    by_category: dict = field(default_factory=dict)


def _bump(bucket, key, qty, value, cost_back):
    entry = bucket.setdefault(key, {"qty": 0, "value": 0, "cost_back": 0})
    entry["qty"] += qty
    entry["value"] += value
    entry["cost_back"] += cost_back


def compute_returns(rows) -> ReturnsData:
    """rows: dicts, oldest refund first, one per refund line, with keys
    sale_id, sale_item_id, qty, restocked, sold_qty, unit_price, product_id,
    category_name, unit_cost, status, subtotal, total.
    """
    out = ReturnsData()
    taken: dict = {}
    per_sale: dict = {}
    for r in rows:
        sold = int(r["sold_qty"] or 0)
        done = taken.get(r["sale_item_id"], 0)
        take = max(min(int(r["qty"] or 0), sold - done), 0)
        taken[r["sale_item_id"]] = done + take

        price = int(r["unit_price"] or 0)
        subtotal = int(r["subtotal"] or 0)
        total = int(r["total"] or 0)
        full = take * price
        # What the customer really paid for those goods (discount-aware).
        if subtotal > 0:
            paid_part = int(round(full * min(total / subtotal, 1.0)))
        else:
            paid_part = full
        cost_back = take * int(r["unit_cost"] or 0) if r["restocked"] else 0

        _bump(out.by_product, r["product_id"], take, full, cost_back)
        _bump(out.by_category, r["category_name"], take, full, cost_back)
        out.cost_back_total += cost_back

        if r["status"] != "refunded":
            s = per_sale.setdefault(r["sale_id"], {"value": 0, "units": 0, "total": total})
            s["value"] += paid_part
            s["units"] += take

    for sale_id, s in per_sale.items():
        value = min(s["value"], s["total"])  # never return more than was paid
        out.sale_value[sale_id] = value
        out.total_sale_value += value
        out.revenue_units += s["units"]
    return out


# ------------------------------------------------------------ database access


async def fetch_returns(session, branch_id, date_from, date_to) -> ReturnsData:
    """Itemised returns for counted sales in the period (by SALE date)."""
    conds = [*cost_sale_conds(), *_period(Sale.created_at, date_from, date_to)]
    if branch_id:
        conds.append(Sale.branch_id == branch_id)
    unit_cost = func.coalesce(StockBatch.unit_cost_kobo, Product.cost_price_kobo, 0)
    result = await session.execute(
        select(
            Refund.sale_id.label("sale_id"),
            RefundItem.sale_item_id.label("sale_item_id"),
            RefundItem.quantity.label("qty"),
            RefundItem.restocked.label("restocked"),
            SaleItem.quantity.label("sold_qty"),
            SaleItem.unit_price_kobo.label("unit_price"),
            SaleItem.product_id.label("product_id"),
            Category.name.label("category_name"),
            unit_cost.label("unit_cost"),
            Sale.status.label("status"),
            Sale.subtotal_kobo.label("subtotal"),
            Sale.total_kobo.label("total"),
        )
        .select_from(RefundItem)
        .join(Refund, RefundItem.refund_id == Refund.id)
        .join(Sale, Refund.sale_id == Sale.id)
        .join(SaleItem, RefundItem.sale_item_id == SaleItem.id)
        .join(Product, SaleItem.product_id == Product.id)
        .outerjoin(Category, Product.category_id == Category.id)
        .outerjoin(StockBatch, SaleItem.stock_batch_id == StockBatch.id)
        .where(*conds)
        .order_by(Refund.created_at, RefundItem.id)
    )
    rows = [
        {
            "sale_id": r.sale_id,
            "sale_item_id": r.sale_item_id,
            "qty": r.qty,
            "restocked": r.restocked,
            "sold_qty": r.sold_qty,
            "unit_price": r.unit_price,
            "product_id": r.product_id,
            "category_name": r.category_name,
            "unit_cost": r.unit_cost,
            "status": r.status,
            "subtotal": r.subtotal,
            "total": r.total,
        }
        for r in result.all()
    ]
    return compute_returns(rows)


async def refunds_by_period(session, returns: ReturnsData, group_by: str) -> dict:
    """{period start date: kobo returned}, using the same date_trunc the sales
    summary uses so the refund always lands on the period of its sale."""
    ids = list(returns.sale_value.keys())
    out: dict = {}
    period_col = func.date_trunc(group_by, Sale.created_at)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        result = await session.execute(
            select(Sale.id.label("sale_id"), period_col.label("period")).where(Sale.id.in_(chunk))
        )
        for sale_id, period in result.all():
            key = period.date()
            out[key] = out.get(key, 0) + returns.sale_value.get(sale_id, 0)
    return out
