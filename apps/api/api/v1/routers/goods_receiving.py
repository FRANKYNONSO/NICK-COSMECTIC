"""Goods receiving, shelf limits and the Total / Warehouse / Shelf stock summary.

Design notes
- Shelf Stock is NOT stored. It is the sum of StockBatch.quantity_remaining for a
  branch + product (the same figure POS sells from). Warehouse Stock stays
  WarehouseStock.quantity_pieces. Total = Warehouse + Shelf is always derived.
- A receipt only changes stock when it is POSTED. Drafts never touch any balance.
- Posting is one transaction. Per product it locks that branch's warehouse_stock
  row, which also serialises shelf-capacity checks, so two simultaneous receipts
  cannot both fit under the same remaining shelf capacity.
- Shelf limits are enforced only for products that have a product_shelf_settings row.
- Alerts are derived on every request (never stored), so they cannot duplicate
  and clear themselves once the shelf is replenished.
- All quantities are in pieces.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_current_user, get_db_session, require_permission
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import (
    Branch,
    GoodsReceipt,
    GoodsReceiptLine,
    Product,
    ProductShelfSetting,
    PurchaseInvoice,
    PurchaseInvoiceLineItem,
    Sale,
    SaleItem,
    StockBatch,
    StockTransfer,
    User,
    WarehouseStock,
)

router = APIRouter(prefix="/goods-receiving", tags=["goods-receiving"])

DEFAULT_MAX_SHELF = 24
DEFAULT_ALERT_LEVEL = 5
LATE_ENTRY_AFTER = timedelta(hours=1)
MAX_LINES_PER_RECEIPT = 500

STATE_LABELS = {
    "out_of_stock": "OUT OF STOCK",
    "shelf_empty": "SHELF EMPTY - REPLENISH FROM WAREHOUSE",
    "low_shelf": "LOW SHELF STOCK - REPLENISH FROM WAREHOUSE",
    "ok": None,
}


# ------------------------------------------------------------------ schemas
class ShelfSettingIn(BaseModel):
    branch_id: uuid.UUID
    product_id: uuid.UUID
    max_shelf_quantity: int = Field(ge=0, le=1000000)
    replenish_alert_level: int = Field(ge=0, le=1000000)
    move_excess_to_warehouse: bool = False  # fix53: only Add Product sets this, to split a new product's starting stock


class PostOptions(BaseModel):
    override_shelf_limit: bool = False
    override_reason: str | None = None
    reconciliation_confirmed: bool = False
    confirm_discrepancy: bool = False
    discrepancy_reason: str | None = None


class ReceiptLineIn(BaseModel):
    product_id: uuid.UUID
    quantity_received: int = Field(ge=0, le=1000000)
    quantity_to_shelf: int = Field(default=0, ge=0, le=1000000)
    invoice_line_item_id: uuid.UUID | None = None
    quantity_invoiced: int | None = Field(default=None, ge=0)
    unit_cost_kobo: int | None = Field(default=None, ge=0)
    notes: str | None = None


class ReceiptIn(PostOptions):
    branch_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    supplier_name: str | None = None
    invoice_reference: str | None = None
    delivered_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)
    notes: str | None = None
    reconciliation_notes: str | None = None
    lines: list[ReceiptLineIn] = Field(default_factory=list)
    post_now: bool = False


class LinkInvoiceIn(BaseModel):
    invoice_id: uuid.UUID
    verify_costs: bool = False
    # receipt line id -> invoice line item id (for lines recorded before the invoice existed)
    line_links: dict[uuid.UUID, uuid.UUID] | None = None
    # receipt line id -> verified unit cost in kobo per piece
    line_costs: dict[uuid.UUID, int] | None = None


# ------------------------------------------------------------------ helpers
def _iso(value):
    return value.isoformat() if value is not None else None


def _bad(detail, code=status.HTTP_400_BAD_REQUEST):
    return HTTPException(code, detail=detail)


def _check_branch_access(current_user, branch_id):
    if current_user.branch_id and branch_id != current_user.branch_id:
        raise _bad("You can only work with your own branch's stock.", status.HTTP_403_FORBIDDEN)


async def _require_branch(session, branch_id):
    if await session.get(Branch, branch_id) is None:
        raise _bad("Branch not found", status.HTTP_404_NOT_FOUND)


async def _get_setting(session, branch_id, product_id):
    result = await session.execute(
        select(ProductShelfSetting).where(
            ProductShelfSetting.branch_id == branch_id,
            ProductShelfSetting.product_id == product_id,
        )
    )
    return result.scalar_one_or_none()


async def _shelf_physical(session, branch_id, product_id):
    total = await session.scalar(
        select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
            StockBatch.branch_id == branch_id,
            StockBatch.product_id == product_id,
            StockBatch.quantity_remaining > 0,
        )
    )
    return int(total or 0)


async def _lock_warehouse_row(session, branch_id, product_id):
    """Create the warehouse row if missing, then lock it until commit. This lock
    is what serialises concurrent receipts / pulls for one branch + product."""
    await session.execute(
        pg_insert(WarehouseStock)
        .values(branch_id=branch_id, product_id=product_id, quantity_pieces=0)
        .on_conflict_do_nothing(index_elements=["branch_id", "product_id"])
    )
    result = await session.execute(
        select(WarehouseStock)
        .where(WarehouseStock.branch_id == branch_id, WarehouseStock.product_id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# fix51: "Reduce stock" input and the shared shelf-take helper
class ShelfReduceIn(BaseModel):
    branch_id: uuid.UUID
    product_id: uuid.UUID
    pieces: int = Field(ge=1)


async def _take_from_shelf_newest_first(session, branch_id, product_id, wanted):
    """fix51: take up to `wanted` pieces off the shelf, newest batches first (the
    same order the shelf-default lowering uses). The caller must already hold the
    warehouse row lock. Returns the number of pieces actually taken."""
    batch_rows = (
        await session.execute(
            select(StockBatch)
            .where(
                StockBatch.branch_id == branch_id,
                StockBatch.product_id == product_id,
                StockBatch.quantity_remaining > 0,
            )
            .order_by(StockBatch.received_at.desc(), StockBatch.id.desc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    left = wanted
    for batch in batch_rows:
        if left <= 0:
            break
        take = min(batch.quantity_remaining, left)
        batch.quantity_remaining -= take
        left -= take
    return wanted - left


async def _stock_figures(session, branch_id, product_ids):
    ids = list(product_ids)
    out = {pid: {"shelf": 0, "expired": 0, "warehouse": 0, "setting": None} for pid in ids}
    if not ids:
        return out
    today = date.today()
    shelf_rows = await session.execute(
        select(
            StockBatch.product_id,
            func.coalesce(func.sum(StockBatch.quantity_remaining), 0),
            func.coalesce(
                func.sum(case((StockBatch.expiry_date <= today, StockBatch.quantity_remaining), else_=0)),
                0,
            ),
        )
        .where(
            StockBatch.branch_id == branch_id,
            StockBatch.product_id.in_(ids),
            StockBatch.quantity_remaining > 0,
        )
        .group_by(StockBatch.product_id)
    )
    for pid, shelf, expired in shelf_rows.all():
        if pid in out:
            out[pid]["shelf"] = int(shelf)
            out[pid]["expired"] = int(expired)
    wh_rows = await session.execute(
        select(WarehouseStock.product_id, WarehouseStock.quantity_pieces).where(
            WarehouseStock.branch_id == branch_id, WarehouseStock.product_id.in_(ids)
        )
    )
    for pid, qty in wh_rows.all():
        if pid in out:
            out[pid]["warehouse"] = int(qty)
    set_rows = await session.execute(
        select(ProductShelfSetting).where(
            ProductShelfSetting.branch_id == branch_id, ProductShelfSetting.product_id.in_(ids)
        )
    )
    for row in set_rows.scalars().all():
        if row.product_id in out:
            out[row.product_id]["setting"] = row
    return out


def _summary(product_id, fig, name=None, sku=None):
    setting = fig["setting"]
    configured = setting is not None
    max_q = setting.max_shelf_quantity if configured else DEFAULT_MAX_SHELF
    alert = setting.replenish_alert_level if configured else DEFAULT_ALERT_LEVEL
    shelf = fig["shelf"]
    expired = fig["expired"]
    sellable = shelf - expired
    warehouse = fig["warehouse"]
    total = shelf + warehouse
    over_limit = bool(configured and shelf > max_q)
    # Alerts use SELLABLE shelf stock (expired units excluded). Capacity and
    # over_limit keep using the physical shelf: expired units still occupy it.
    if sellable + warehouse <= 0:
        state = "out_of_stock"
    elif sellable <= 0:
        state = "shelf_empty"
    elif sellable <= alert and warehouse > 0:
        state = "low_shelf"
    else:
        state = "ok"
    return {
        "product_id": str(product_id),
        "product_name": name,
        "sku": sku,
        "total_stock": total,
        "warehouse_stock": warehouse,
        "shelf_stock": shelf,
        "shelf_sellable_stock": shelf - fig["expired"],
        "shelf_expired_stock": fig["expired"],
        "total_sellable_stock": sellable + warehouse,
        "has_expired_shelf_stock": expired > 0,
        "expired_label": "EXPIRED UNITS ON SHELF - REMOVE" if expired > 0 else None,
        "max_shelf_quantity": max_q,
        "replenish_alert_level": alert,
        "limits_configured": configured,
        "available_shelf_capacity": max(max_q - shelf, 0),
        "over_limit": over_limit,
        "stock_state": state,
        "alert_label": STATE_LABELS[state],
    }


def _receipt_out(receipt):
    return {
        "id": str(receipt.id),
        "branch_id": str(receipt.branch_id),
        "invoice_id": str(receipt.invoice_id) if receipt.invoice_id else None,
        "supplier_name": receipt.supplier_name,
        "invoice_reference": receipt.invoice_reference,
        "status": receipt.status,
        "documentation_status": receipt.documentation_status,
        "cost_status": receipt.cost_status,
        "delivered_at": _iso(receipt.delivered_at),
        "posted_at": _iso(receipt.posted_at),
        "recorded_at": _iso(receipt.created_at),
        "is_late_entry": receipt.is_late_entry,
        "notes": receipt.notes,
        "reconciliation_notes": receipt.reconciliation_notes,
        "reconciliation_status": receipt.reconciliation_status,
        "reconciliation_flags": receipt.reconciliation_flags,
        "stock_count_required": receipt.reconciliation_status == "discrepancy_confirmed",
        "recorded_by": str(receipt.recorded_by) if receipt.recorded_by else None,
        "lines": [
            {
                "id": str(l.id),
                "position": l.position,
                "product_id": str(l.product_id),
                "invoice_line_item_id": str(l.invoice_line_item_id) if l.invoice_line_item_id else None,
                "quantity_invoiced": l.quantity_invoiced,
                "quantity_received": l.quantity_received,
                "quantity_to_shelf": l.quantity_to_shelf,
                "quantity_to_warehouse": l.quantity_to_warehouse,
                "unit_cost_kobo": l.unit_cost_kobo,
                "cost_verified": l.cost_verified,
                "notes": l.notes,
            }
            for l in receipt.lines
        ],
    }


async def _load_receipt(session, receipt_id, lock=False):
    stmt = (
        select(GoodsReceipt)
        .where(GoodsReceipt.id == receipt_id)
        .options(selectinload(GoodsReceipt.lines))
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update()
    receipt = (await session.execute(stmt)).scalar_one_or_none()
    if receipt is None:
        raise _bad("Receipt not found", status.HTTP_404_NOT_FOUND)
    return receipt


async def _find_by_key(session, branch_id, key):
    stmt = (
        select(GoodsReceipt)
        .where(GoodsReceipt.branch_id == branch_id, GoodsReceipt.idempotency_key == key)
        .options(selectinload(GoodsReceipt.lines))
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _received_via_receipts(session, li_id, exclude_receipt_id=None):
    stmt = (
        select(func.coalesce(func.sum(GoodsReceiptLine.quantity_received), 0))
        .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
        .where(GoodsReceiptLine.invoice_line_item_id == li_id, GoodsReceipt.status == "posted")
    )
    if exclude_receipt_id is not None:
        stmt = stmt.where(GoodsReceipt.id != exclude_receipt_id)
    return int(await session.scalar(stmt) or 0)


async def _pushed_old_flow(session, li_id):
    total = await session.scalar(
        select(func.coalesce(func.sum(StockTransfer.quantity_pieces), 0)).where(
            StockTransfer.invoice_line_item_id == li_id,
            StockTransfer.transfer_type == "store_to_warehouse",
        )
    )
    return int(total or 0)


# ------------------------------------------------------------ shelf settings
@router.get("/shelf-settings")
async def list_shelf_settings(
    branch_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _check_branch_access(current_user, branch_id)
    stmt = select(ProductShelfSetting).where(ProductShelfSetting.branch_id == branch_id)
    if product_id:
        stmt = stmt.where(ProductShelfSetting.product_id == product_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "branch_id": str(r.branch_id),
            "product_id": str(r.product_id),
            "max_shelf_quantity": r.max_shelf_quantity,
            "replenish_alert_level": r.replenish_alert_level,
        }
        for r in rows
    ]


@router.put("/shelf-settings")
async def set_shelf_setting(
    body: ShelfSettingIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    _check_branch_access(current_user, body.branch_id)
    await _require_branch(session, body.branch_id)
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise _bad("Product not found", status.HTTP_404_NOT_FOUND)
    if body.replenish_alert_level > body.max_shelf_quantity:
        raise _bad("The replenishment alert level cannot be higher than the maximum shelf quantity.")
    existing = await _get_setting(session, body.branch_id, body.product_id)
    old = None
    if existing is None:
        session.add(
            ProductShelfSetting(
                branch_id=body.branch_id,
                product_id=body.product_id,
                max_shelf_quantity=body.max_shelf_quantity,
                replenish_alert_level=body.replenish_alert_level,
                updated_by=current_user.id,
            )
        )
    else:
        old = {"max": existing.max_shelf_quantity, "alert": existing.replenish_alert_level}
        existing.max_shelf_quantity = body.max_shelf_quantity
        existing.replenish_alert_level = body.replenish_alert_level
        existing.updated_by = current_user.id
    # fix43-shelf-excess: if the shelf holds more than the new limit, move the excess to the warehouse.
    moved_to_warehouse = 0
    shelf_before = await _shelf_physical(session, body.branch_id, body.product_id)
    # fix53: saving a shelf default never moves stock off the shelf. Only a brand-new product (Add Product sends the flag) splits its starting stock. Reduce stock is the deliberate way to send extras back.
    if body.move_excess_to_warehouse and old is None and shelf_before > body.max_shelf_quantity:
        wh_row = await _lock_warehouse_row(session, body.branch_id, body.product_id)
        shelf_before = await _shelf_physical(session, body.branch_id, body.product_id)
        excess = shelf_before - body.max_shelf_quantity
        if excess > 0:
            batch_rows = (
                await session.execute(
                    select(StockBatch)
                    .where(
                        StockBatch.branch_id == body.branch_id,
                        StockBatch.product_id == body.product_id,
                        StockBatch.quantity_remaining > 0,
                    )
                    .order_by(StockBatch.received_at.desc(), StockBatch.id.desc())
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
            left = excess
            for batch in batch_rows:
                if left <= 0:
                    break
                take = min(batch.quantity_remaining, left)
                batch.quantity_remaining -= take
                left -= take
            moved_to_warehouse = excess - left
            wh_row.quantity_pieces += moved_to_warehouse
            # fix51: every shelf-to-warehouse move gets its own StockTransfer row
            if moved_to_warehouse > 0:
                lower_note = (
                    "Shelf default lowered to %d: extra pieces moved back to the warehouse"
                    if old is not None
                    else "Shelf default set to %d: extra pieces moved to the warehouse"
                ) % body.max_shelf_quantity
                session.add(
                    StockTransfer(
                        product_id=body.product_id,
                        from_branch_id=body.branch_id,
                        to_branch_id=body.branch_id,
                        transfer_type="shelf_to_warehouse",
                        unit_label="pieces",
                        unit_count=moved_to_warehouse,
                        pieces_per_unit=1,
                        quantity_pieces=moved_to_warehouse,
                        notes=lower_note,
                        created_by=current_user.id,
                    )
                )
            log_audit_event(
                session,
                user_id=current_user.id,
                action="shelf_limit_excess_to_warehouse",
                entity_type="product",
                entity_id=body.product_id,
                metadata={
                    "branch_id": str(body.branch_id),
                    "limit": body.max_shelf_quantity,
                    "shelf_before": shelf_before,
                    "moved_to_warehouse": moved_to_warehouse,
                },
            )
    # fix46: raising a saved limit fills the shelf from the warehouse, up to the new limit
    moved_to_shelf = 0
    if old is not None and body.max_shelf_quantity > old["max"]:
        wh_fill = await _lock_warehouse_row(session, body.branch_id, body.product_id)
        shelf_now = await _shelf_physical(session, body.branch_id, body.product_id)
        room = body.max_shelf_quantity - shelf_now
        pull = min(room, int(wh_fill.quantity_pieces or 0))
        if pull > 0:
            from api.v1.routers.warehouse import _create_store_batch
            wh_fill.quantity_pieces -= pull
            await _create_store_batch(session, body.branch_id, product, pull, "limit-raise")
            session.add(
                StockTransfer(
                    product_id=body.product_id,
                    from_branch_id=body.branch_id,
                    to_branch_id=body.branch_id,
                    transfer_type="warehouse_to_store",
                    unit_label="pieces",
                    unit_count=pull,
                    pieces_per_unit=1,
                    quantity_pieces=pull,
                    notes="Shelf limit raised: shelf filled from the warehouse",
                    created_by=current_user.id,
                )
            )
            log_audit_event(
                session,
                user_id=current_user.id,
                action="shelf_limit_raise_filled_shelf",
                entity_type="product",
                entity_id=body.product_id,
                metadata={
                    "branch_id": str(body.branch_id),
                    "old_limit": old["max"],
                    "new_limit": body.max_shelf_quantity,
                    "shelf_before": shelf_now,
                    "moved_to_shelf": pull,
                },
            )
            moved_to_shelf = pull
    await session.flush()
    figures = await _stock_figures(session, body.branch_id, [body.product_id])
    summary = _summary(body.product_id, figures[body.product_id], product.name, product.sku)
    summary["moved_to_warehouse"] = moved_to_warehouse
    summary["moved_to_shelf"] = moved_to_shelf
    log_audit_event(
        session,
        user_id=current_user.id,
        action="shelf_limit_changed",
        entity_type="product",
        entity_id=body.product_id,
        metadata={
            "branch_id": str(body.branch_id),
            "old": old,
            "new": {"max": body.max_shelf_quantity, "alert": body.replenish_alert_level},
        },
    )
    await session.commit()
    return summary

# fix51: Reduce stock. Sends only the pieces ABOVE the shelf default back to the warehouse.
@router.post("/shelf-reduce")
async def reduce_shelf_stock(
    body: ShelfReduceIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    _check_branch_access(current_user, body.branch_id)
    await _require_branch(session, body.branch_id)
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise _bad("Product not found", status.HTTP_404_NOT_FOUND)
    setting = await _get_setting(session, body.branch_id, body.product_id)
    default_qty = setting.max_shelf_quantity if setting is not None else DEFAULT_MAX_SHELF
    # same locking order as the rest of this file: warehouse row first, then the shelf batches
    wh_row = await _lock_warehouse_row(session, body.branch_id, body.product_id)
    shelf_before = await _shelf_physical(session, body.branch_id, body.product_id)
    excess = shelf_before - default_qty
    if excess <= 0:
        raise _bad(
            "The shelf holds " + str(shelf_before) + " and the default is " + str(default_qty)
            + ", so there is nothing above the default to send back."
        )
    if body.pieces > excess:
        raise _bad(
            "You can send back at most " + str(excess) + " piece(s): the shelf holds "
            + str(shelf_before) + " and the default is " + str(default_qty) + "."
        )
    moved = await _take_from_shelf_newest_first(session, body.branch_id, body.product_id, body.pieces)
    if moved <= 0:
        raise _bad("Nothing could be taken from the shelf.")
    wh_row.quantity_pieces += moved
    session.add(
        StockTransfer(
            product_id=body.product_id,
            from_branch_id=body.branch_id,
            to_branch_id=body.branch_id,
            transfer_type="shelf_to_warehouse",
            unit_label="pieces",
            unit_count=moved,
            pieces_per_unit=1,
            quantity_pieces=moved,
            notes="Reduce stock: pieces above the shelf default of " + str(default_qty) + " sent back to the warehouse",
            created_by=current_user.id,
        )
    )
    log_audit_event(
        session,
        user_id=current_user.id,
        action="shelf_reduce_to_warehouse",
        entity_type="product",
        entity_id=body.product_id,
        metadata={
            "branch_id": str(body.branch_id),
            "default": default_qty,
            "shelf_before": shelf_before,
            "moved_to_warehouse": moved,
        },
    )
    await session.flush()
    figures = await _stock_figures(session, body.branch_id, [body.product_id])
    summary = _summary(body.product_id, figures[body.product_id], product.name, product.sku)
    summary["moved_to_warehouse"] = moved
    await session.commit()
    return summary


# ------------------------------------------------------------- stock summary
@router.get("/stock-summary")
async def stock_summary(
    branch_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    alerts_only: bool = False,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _check_branch_access(current_user, branch_id)
    if product_id is not None:
        ids = {product_id}
    else:
        ids = set()
        for stmt in (
            select(StockBatch.product_id)
            .where(
                StockBatch.branch_id == branch_id,
                StockBatch.product_id.is_not(None),
                StockBatch.quantity_remaining > 0,
            )
            .distinct(),
            select(WarehouseStock.product_id)
            .where(WarehouseStock.branch_id == branch_id, WarehouseStock.quantity_pieces > 0)
            .distinct(),
            select(ProductShelfSetting.product_id).where(ProductShelfSetting.branch_id == branch_id),
        ):
            ids.update((await session.execute(stmt)).scalars().all())
    ids = list(ids)[:2000]
    if not ids:
        return []
    prod_rows = await session.execute(
        select(Product.id, Product.name, Product.sku).where(
            Product.id.in_(ids), Product.is_deleted.is_(False)
        )
    )
    names = {pid: (name, sku) for pid, name, sku in prod_rows.all()}
    figures = await _stock_figures(session, branch_id, list(names.keys()))
    result = [_summary(pid, figures[pid], names[pid][0], names[pid][1]) for pid in names]
    if alerts_only:
        result = [
            r
            for r in result
            if r["stock_state"] in ("low_shelf", "shelf_empty") or r["over_limit"] or r["has_expired_shelf_stock"]
        ]
    result.sort(key=lambda r: (r["product_name"] or "").casefold())
    return result


# ------------------------------------------------------------------ receipts
async def _apply_input(session, receipt, body, current_user, is_new):
    if len(body.lines) > MAX_LINES_PER_RECEIPT:
        raise _bad(f"A receipt can have at most {MAX_LINES_PER_RECEIPT} lines. Split very large deliveries.")
    product_ids = {l.product_id for l in body.lines}
    if product_ids:
        found = set(
            (
                await session.execute(
                    select(Product.id).where(Product.id.in_(product_ids), Product.is_deleted.is_(False))
                )
            )
            .scalars()
            .all()
        )
        missing = product_ids - found
        if missing:
            raise _bad(f"{len(missing)} product(s) on this receipt were not found or are deleted.", 404)
    errors = []
    for i, l in enumerate(body.lines, start=1):
        if l.quantity_to_shelf > l.quantity_received:
            errors.append(
                f"Row {i}: shelf quantity ({l.quantity_to_shelf}) cannot exceed quantity received ({l.quantity_received})."
            )
    if errors:
        raise _bad(" ".join(errors))
    invoice = None
    if body.invoice_id:
        invoice = await session.get(PurchaseInvoice, body.invoice_id)
        if invoice is None:
            raise _bad("Invoice not found", 404)
        if invoice.source == "branch_transfer":
            raise _bad("This invoice was made by a branch transfer. The goods are already in stock, so it cannot be received.")
        if invoice.branch_id is not None and invoice.branch_id != body.branch_id:
            raise _bad("That invoice was filed for a different branch.")
    now = datetime.now(timezone.utc)
    delivered = body.delivered_at
    if delivered is not None:
        if delivered.tzinfo is None:
            delivered = delivered.replace(tzinfo=timezone.utc)
        if delivered > now + timedelta(minutes=5):
            raise _bad("The delivery time cannot be in the future.")
    receipt.delivered_at = delivered
    receipt.invoice_id = invoice.id if invoice else None
    receipt.supplier_name = body.supplier_name or (invoice.supplier_name if invoice else None)
    receipt.invoice_reference = body.invoice_reference
    receipt.notes = body.notes
    receipt.reconciliation_notes = body.reconciliation_notes
    receipt.documentation_status = "documented" if invoice else "pending"
    if not is_new:
        receipt.lines.clear()
        await session.flush()
    for i, l in enumerate(body.lines):
        receipt.lines.append(
            GoodsReceiptLine(
                position=i,
                product_id=l.product_id,
                invoice_line_item_id=l.invoice_line_item_id,
                quantity_invoiced=l.quantity_invoiced,
                quantity_received=l.quantity_received,
                quantity_to_shelf=l.quantity_to_shelf,
                quantity_to_warehouse=l.quantity_received - l.quantity_to_shelf,
                unit_cost_kobo=l.unit_cost_kobo,
                cost_verified=False,
                notes=l.notes,
            )
        )


LATE_RECONCILE_ROLES = ("super_admin", "branch_manager")


async def _late_line_check(session, receipt, line, product):
    """Compare one late-recorded line with what the system already holds.

    Flags the line when stock for this branch + product entered the system after
    the claimed delivery time (shelf batches other than internal warehouse
    restocks, or warehouse inflows), because this delivery may already be
    recorded. Sales since delivery are reported for context only.
    """
    since = receipt.delivered_at
    branch_id = receipt.branch_id
    pid = line.product_id
    shelf_batches = int(
        await session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_received), 0)).where(
                StockBatch.branch_id == branch_id,
                StockBatch.product_id == pid,
                StockBatch.received_at >= since,
                ~func.coalesce(StockBatch.batch_number, "").like("warehouse-%"),
            )
        )
        or 0
    )
    warehouse_pieces = int(
        await session.scalar(
            select(func.coalesce(func.sum(StockTransfer.quantity_pieces), 0)).where(
                or_(StockTransfer.to_branch_id == branch_id, StockTransfer.from_branch_id == branch_id),
                StockTransfer.product_id == pid,
                StockTransfer.transfer_type.in_(("store_to_warehouse", "receipt_to_warehouse")),
                StockTransfer.created_at >= since,
            )
        )
        or 0
    )
    sold = int(
        await session.scalar(
            select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.branch_id == branch_id,
                SaleItem.product_id == pid,
                Sale.created_at >= since,
                Sale.status.in_(("completed", "partially_refunded", "refunded")),
            )
        )
        or 0
    )
    flags = []
    if shelf_batches > 0 or warehouse_pieces > 0:
        flags.append("STOCK_ENTERED_SINCE_DELIVERY")
    return {
        "product_id": str(pid),
        "product_name": product.name,
        "row": line.position + 1,
        "quantity_received": line.quantity_received,
        "quantity_to_shelf": line.quantity_to_shelf,
        "stock_entered_since_delivery": {"shelf_batches": shelf_batches, "warehouse_pieces": warehouse_pieces},
        "sales_since_delivery": sold,
        "flags": flags,
        "message": (
            f"Row {line.position + 1} ({product.name}): {shelf_batches} unit(s) were added to the shelf and "
            f"{warehouse_pieces} to the warehouse since the delivery time, and {sold} unit(s) were sold. "
            "This delivery may already be recorded. Count the stock, then confirm."
        ),
    }


async def _post_receipt(session, receipt, current_user, opts):
    if receipt.status == "voided":
        raise _bad("This receipt was voided and cannot be posted.", 409)
    if receipt.status != "draft":
        raise _bad("Only draft receipts can be posted.", 409)
    lines = sorted(receipt.lines, key=lambda l: (str(l.product_id), l.position))
    if not lines:
        raise _bad("Add at least one product before confirming receipt.")
    now = datetime.now(timezone.utc)
    is_late = receipt.delivered_at is not None and (now - receipt.delivered_at) > LATE_ENTRY_AFTER
    if is_late:
        if not (receipt.reconciliation_notes or "").strip():
            raise _bad(
                "This delivery is being recorded after the fact. Add reconciliation notes explaining "
                "what was checked against existing stock and sales."
            )
        if not opts.reconciliation_confirmed:
            raise _bad(
                "Confirm that you checked existing stock and sales so this delivery is not counted twice."
            )
    wants_override = bool(opts.override_shelf_limit)
    if wants_override:
        if current_user.role not in ("super_admin", "branch_manager"):
            raise _bad(
                "Only a branch manager or super admin can override a shelf limit.",
                status.HTTP_403_FORBIDDEN,
            )
        if not (opts.override_reason or "").strip():
            raise _bad("A reason is required to override a shelf limit.")

    # ---- pass 1: validate everything (reads + row locks only, no stock changes)
    problems = []
    plans = []
    pending_shelf = {}
    pending_li = {}
    overrides_used = []
    for line in lines:
        label = f"Row {line.position + 1}"
        if line.quantity_received <= 0:
            problems.append(f"{label}: quantity received must be greater than zero.")
            continue
        product = await session.get(Product, line.product_id)
        if product is None or product.is_deleted:
            problems.append(f"{label}: product not found or deleted.")
            continue
        wh_row = await _lock_warehouse_row(session, receipt.branch_id, line.product_id)
        setting = await _get_setting(session, receipt.branch_id, line.product_id)
        shelf_now = await _shelf_physical(session, receipt.branch_id, line.product_id)
        shelf_now += pending_shelf.get(line.product_id, 0)
        if setting is not None and line.quantity_to_shelf > 0:
            remaining = setting.max_shelf_quantity - shelf_now
            if line.quantity_to_shelf > max(remaining, 0):
                if remaining <= 0:
                    msg = (
                        f"{label} ({product.name}): the shelf is already at or over its limit of "
                        f"{setting.max_shelf_quantity} (currently {shelf_now}), so no units can go on the shelf. "
                        "Put them in the warehouse."
                    )
                else:
                    msg = (
                        f"{label} ({product.name}): only {remaining} unit(s) of shelf capacity remain "
                        f"(limit {setting.max_shelf_quantity}, currently {shelf_now}) but "
                        f"{line.quantity_to_shelf} were allocated to the shelf. The rest must stay in the warehouse."
                    )
                if wants_override:
                    overrides_used.append(
                        {
                            "product_id": str(line.product_id),
                            "limit": setting.max_shelf_quantity,
                            "shelf_before": shelf_now,
                            "added": line.quantity_to_shelf,
                        }
                    )
                else:
                    problems.append(msg)
        pending_shelf[line.product_id] = pending_shelf.get(line.product_id, 0) + line.quantity_to_shelf
        li = None
        if line.invoice_line_item_id:
            li = await session.get(PurchaseInvoiceLineItem, line.invoice_line_item_id)
            if li is None:
                problems.append(f"{label}: invoice line not found.")
                continue
            line_invoice = await session.get(PurchaseInvoice, li.invoice_id)
            if line_invoice is not None and line_invoice.source == "branch_transfer":
                problems.append(f"{label}: that invoice line belongs to a branch transfer invoice and cannot be received.")
                continue
            if receipt.invoice_id and li.invoice_id != receipt.invoice_id:
                problems.append(f"{label}: that invoice line belongs to a different invoice.")
                continue
            if li.product_id and li.product_id != line.product_id:
                problems.append(f"{label}: that invoice line is for a different product.")
                continue
            if li.total_pieces is not None:
                before = await _received_via_receipts(session, li.id, exclude_receipt_id=receipt.id)
                old_pushed = await _pushed_old_flow(session, li.id)
                pending = pending_li.get(li.id, 0)
                outstanding = li.total_pieces - before - old_pushed - pending
                if line.quantity_received > max(outstanding, 0):
                    problems.append(
                        f"{label} ({product.name}): {line.quantity_received} received but only "
                        f"{max(outstanding, 0)} still outstanding on the invoice line "
                        f"({li.total_pieces} ordered, {before + old_pushed} already received or pushed)."
                    )
                pending_li[li.id] = pending + line.quantity_received
        plans.append((line, product, wh_row, li))
    if problems:
        raise _bad(" ".join(problems))

    # ---- late-entry reconciliation (reads only; the warehouse rows are already locked)
    recon_status = "not_required"
    recon_flags = None
    if is_late:
        recon_status = "clear"
        discrepancies = []
        for line, product, wh_row, li in plans:
            check = await _late_line_check(session, receipt, line, product)
            if check["flags"]:
                discrepancies.append(check)
        if discrepancies:
            if not opts.confirm_discrepancy:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "RECEIPT_RECONCILIATION_DISCREPANCY",
                        "message": (
                            "Stock for these products was already recorded after the stated delivery time. "
                            "Nothing was posted. Count the stock; a branch manager can confirm with a reason."
                        ),
                        "discrepancies": discrepancies,
                    },
                )
            if current_user.role not in LATE_RECONCILE_ROLES:
                raise _bad(
                    "Only a branch manager or super admin can confirm a reconciliation discrepancy.",
                    status.HTTP_403_FORBIDDEN,
                )
            if not (opts.discrepancy_reason or "").strip():
                raise _bad("A reason is required to confirm a reconciliation discrepancy.")
            recon_status = "discrepancy_confirmed"
            recon_flags = {
                "lines": discrepancies,
                "reason": (opts.discrepancy_reason or "").strip(),
                "confirmed_by": str(current_user.id),
                "confirmed_at": now.isoformat(),
            }

    # ---- pass 2: apply (still inside the same transaction)
    total_received = total_shelf = total_wh = 0
    all_verified = True
    note = f"Goods receipt {receipt.id}"
    for line, product, wh_row, li in plans:
        cost = line.unit_cost_kobo
        verified = bool(line.cost_verified) and cost is not None
        if cost is None and li is not None and li.cost_price_per_piece_kobo:
            cost = int(li.cost_price_per_piece_kobo)
            verified = receipt.invoice_id is not None
        if cost is None:
            cost = int(product.cost_price_kobo or 0)
        line.unit_cost_kobo = cost
        line.cost_verified = verified
        if li is not None and line.quantity_invoiced is None and li.total_pieces is not None:
            line.quantity_invoiced = li.total_pieces
        all_verified = all_verified and verified
        if line.quantity_to_warehouse > 0:
            wh_row.quantity_pieces += line.quantity_to_warehouse
            session.add(
                StockTransfer(
                    product_id=line.product_id,
                    from_branch_id=receipt.branch_id,
                    to_branch_id=receipt.branch_id,
                    transfer_type="receipt_to_warehouse",
                    unit_label="pieces",
                    unit_count=line.quantity_to_warehouse,
                    pieces_per_unit=1,
                    quantity_pieces=line.quantity_to_warehouse,
                    invoice_line_item_id=line.invoice_line_item_id,
                    goods_receipt_id=receipt.id,
                    notes=note,
                    created_by=current_user.id,
                )
            )
        if line.quantity_to_shelf > 0:
            session.add(
                StockBatch(
                    product_id=line.product_id,
                    branch_id=receipt.branch_id,
                    batch_number=f"receipt-{str(receipt.id)[:8]}",
                    quantity_received=line.quantity_to_shelf,
                    quantity_remaining=line.quantity_to_shelf,
                    unit_cost_kobo=cost,
                    expiry_date=product.expiration_date,
                    received_at=now,
                )
            )
            session.add(
                StockTransfer(
                    product_id=line.product_id,
                    from_branch_id=receipt.branch_id,
                    to_branch_id=receipt.branch_id,
                    transfer_type="receipt_to_shelf",
                    unit_label="pieces",
                    unit_count=line.quantity_to_shelf,
                    pieces_per_unit=1,
                    quantity_pieces=line.quantity_to_shelf,
                    invoice_line_item_id=line.invoice_line_item_id,
                    goods_receipt_id=receipt.id,
                    notes=note,
                    created_by=current_user.id,
                )
            )
        total_received += line.quantity_received
        total_shelf += line.quantity_to_shelf
        total_wh += line.quantity_to_warehouse
    receipt.status = "posted"
    receipt.posted_at = now
    receipt.is_late_entry = is_late
    receipt.reconciliation_status = recon_status
    receipt.reconciliation_flags = recon_flags
    receipt.documentation_status = "documented" if receipt.invoice_id else "pending"
    receipt.cost_status = "verified" if (all_verified and receipt.invoice_id) else "unverified"
    log_audit_event(
        session,
        user_id=current_user.id,
        action="goods_receipt_posted",
        entity_type="goods_receipt",
        entity_id=receipt.id,
        metadata={
            "branch_id": str(receipt.branch_id),
            "lines": len(plans),
            "total_received": total_received,
            "total_to_shelf": total_shelf,
            "total_to_warehouse": total_wh,
            "late_entry": is_late,
            "cost_status": receipt.cost_status,
        },
    )
    if recon_status == "discrepancy_confirmed":
        log_audit_event(
            session,
            user_id=current_user.id,
            action="goods_receipt_discrepancy_confirmed",
            entity_type="goods_receipt",
            entity_id=receipt.id,
            metadata={
                "branch_id": str(receipt.branch_id),
                "reason": recon_flags["reason"],
                "flagged_lines": len(recon_flags["lines"]),
            },
        )
    for ov in overrides_used:
        log_audit_event(
            session,
            user_id=current_user.id,
            action="shelf_limit_override",
            entity_type="goods_receipt",
            entity_id=receipt.id,
            metadata=dict(ov, reason=(opts.override_reason or "").strip(), branch_id=str(receipt.branch_id)),
        )
    await session.flush()


@router.post("/receipts", status_code=status.HTTP_201_CREATED)
async def create_receipt(
    body: ReceiptIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    _check_branch_access(current_user, body.branch_id)
    await _require_branch(session, body.branch_id)
    existing = None
    if body.idempotency_key:
        existing = await _find_by_key(session, body.branch_id, body.idempotency_key)
    if existing is None:
        receipt = GoodsReceipt(
            branch_id=body.branch_id,
            status="draft",
            recorded_by=current_user.id,
            idempotency_key=body.idempotency_key,
            lines=[],
        )
        session.add(receipt)
        await _apply_input(session, receipt, body, current_user, is_new=True)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing = await _find_by_key(session, body.branch_id, body.idempotency_key or "")
            if existing is None:
                raise
    if existing is not None:
        # Same operation submitted again: never create or post a second time.
        if body.post_now and existing.status == "draft":
            await _post_receipt(session, existing, current_user, body)
            await session.commit()
            existing = await _load_receipt(session, existing.id)
        return dict(_receipt_out(existing), duplicate=True)
    rid = receipt.id
    if body.post_now:
        await _post_receipt(session, receipt, current_user, body)
    await session.commit()
    return dict(_receipt_out(await _load_receipt(session, rid)), duplicate=False)


@router.get("/receipts")
async def list_receipts(
    branch_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.branch_id:
        branch_id = current_user.branch_id
    stmt = select(GoodsReceipt).options(selectinload(GoodsReceipt.lines))
    if branch_id:
        stmt = stmt.where(GoodsReceipt.branch_id == branch_id)
    if status_filter:
        stmt = stmt.where(GoodsReceipt.status == status_filter)
    stmt = stmt.order_by(GoodsReceipt.created_at.desc()).limit(100)
    rows = (await session.execute(stmt)).scalars().all()
    return [_receipt_out(r) for r in rows]


@router.get("/receipts/outstanding")
async def outstanding_for_invoice(
    invoice_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    invoice = await session.get(PurchaseInvoice, invoice_id)
    if invoice is None:
        raise _bad("Invoice not found", 404)
    if invoice.source == "branch_transfer":
        raise _bad("This invoice was made by a branch transfer. The goods are already in stock, so it cannot be received.")
    if invoice.branch_id is not None:
        _check_branch_access(current_user, invoice.branch_id)
    items = (
        (
            await session.execute(
                select(PurchaseInvoiceLineItem)
                .where(PurchaseInvoiceLineItem.invoice_id == invoice_id)
                .order_by(PurchaseInvoiceLineItem.position)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for li in items:
        received = await _received_via_receipts(session, li.id)
        pushed = await _pushed_old_flow(session, li.id)
        ordered = li.total_pieces
        out.append(
            {
                "invoice_line_item_id": str(li.id),
                "product_id": str(li.product_id) if li.product_id else None,
                "item_name": li.item_name,
                "ordered_pieces": ordered,
                "received_via_receipts": received,
                "pushed_via_old_flow": pushed,
                "outstanding_pieces": max(ordered - received - pushed, 0) if ordered is not None else None,
            }
        )
    return out


@router.get("/receipts/{receipt_id}")
async def get_receipt(
    receipt_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    receipt = await _load_receipt(session, receipt_id)
    _check_branch_access(current_user, receipt.branch_id)
    return _receipt_out(receipt)


@router.put("/receipts/{receipt_id}")
async def update_draft_receipt(
    receipt_id: uuid.UUID,
    body: ReceiptIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    receipt = await _load_receipt(session, receipt_id, lock=True)
    _check_branch_access(current_user, receipt.branch_id)
    if receipt.status != "draft":
        raise _bad("Only draft receipts can be edited.", 409)
    if body.branch_id != receipt.branch_id:
        raise _bad("A receipt cannot be moved to another branch.")
    await _apply_input(session, receipt, body, current_user, is_new=False)
    await session.commit()
    return _receipt_out(await _load_receipt(session, receipt_id))


@router.post("/receipts/{receipt_id}/post")
async def post_receipt(
    receipt_id: uuid.UUID,
    body: PostOptions | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    opts = body or PostOptions()
    receipt = await _load_receipt(session, receipt_id, lock=True)
    _check_branch_access(current_user, receipt.branch_id)
    already = receipt.status == "posted"
    if not already:
        await _post_receipt(session, receipt, current_user, opts)
        await session.commit()
    return dict(_receipt_out(await _load_receipt(session, receipt_id)), already_posted=already)


COST_SUMMARY_CAP = 200


@router.get("/cost-summary")
async def cost_summary(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    """Read-only view of posted receipts whose purchase costs are still unverified.
    Values are kobo. Nothing here is stored; it is derived from the receipts and
    from the shelf batches each receipt created (batch number receipt-<id8>)."""
    effective = current_user.branch_id if current_user.branch_id else branch_id
    stmt = (
        select(GoodsReceipt)
        .where(GoodsReceipt.status == "posted", GoodsReceipt.cost_status == "unverified")
        .options(selectinload(GoodsReceipt.lines))
        .order_by(GoodsReceipt.posted_at.desc())
        .limit(COST_SUMMARY_CAP + 1)
    )
    if effective is not None:
        stmt = stmt.where(GoodsReceipt.branch_id == effective)
    receipts = list((await session.execute(stmt)).scalars().all())
    truncated = len(receipts) > COST_SUMMARY_CAP
    receipts = receipts[:COST_SUMMARY_CAP]
    out = []
    total_received_value = 0
    total_shelf_value = 0
    pending_invoice_value = 0
    for r in receipts:
        pieces = sum(l.quantity_received for l in r.lines)
        value = sum(l.quantity_received * int(l.unit_cost_kobo or 0) for l in r.lines)
        unverified_lines = sum(1 for l in r.lines if not l.cost_verified)
        product_ids = {l.product_id for l in r.lines}
        batch_number = f"receipt-{str(r.id)[:8]}"
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(StockBatch.quantity_remaining), 0),
                    func.coalesce(func.sum(StockBatch.quantity_remaining * StockBatch.unit_cost_kobo), 0),
                ).where(
                    StockBatch.branch_id == r.branch_id,
                    StockBatch.batch_number == batch_number,
                    StockBatch.product_id.in_(product_ids),
                )
            )
        ).one()
        shelf_pieces = int(row[0] or 0)
        shelf_value = int(row[1] or 0)
        total_received_value += value
        total_shelf_value += shelf_value
        if r.invoice_id is None:
            pending_invoice_value += value
        out.append(
            {
                "receipt_id": str(r.id),
                "branch_id": str(r.branch_id),
                "supplier_name": r.supplier_name,
                "invoice_reference": r.invoice_reference,
                "documentation_status": r.documentation_status,
                "delivered_at": _iso(r.delivered_at),
                "posted_at": _iso(r.posted_at),
                "received_pieces": pieces,
                "received_value_kobo": value,
                "unverified_lines": unverified_lines,
                "shelf_remaining_pieces": shelf_pieces,
                "shelf_remaining_value_kobo": shelf_value,
            }
        )
    return {
        "branch_id": str(effective) if effective is not None else None,
        "unverified_receipt_count": len(out),
        "truncated": truncated,
        "receipts": out,
        "totals": {
            "received_value_kobo": total_received_value,
            "shelf_remaining_value_kobo": total_shelf_value,
        },
        "pending_invoice_received_value_kobo": pending_invoice_value,
        "note": "Receipt costs here are not yet confirmed against a supplier invoice.",
    }


async def _sync_batch_costs(session, receipt, current_user):
    """After an invoice is linked to an already-posted receipt, copy the verified
    per-piece cost onto the shelf batches that receipt created (batch number
    receipt-<id8>). Sales store no cost snapshot, so this does not rewrite any
    completed sale. A product is skipped unless every one of its shelf lines on
    this receipt is verified and the batch quantities add up to the receipt's
    shelf quantity. Several lines for one product use the quantity-weighted
    average cost. Stock quantities are never touched."""
    if receipt.status != "posted":
        return
    batch_number = f"receipt-{str(receipt.id)[:8]}"
    by_product = {}
    for l in receipt.lines:
        if l.quantity_to_shelf and l.quantity_to_shelf > 0:
            by_product.setdefault(l.product_id, []).append(l)
    changes = []
    for product_id, plines in by_product.items():
        if not all(l.cost_verified and l.unit_cost_kobo is not None for l in plines):
            continue
        shelf_qty = sum(l.quantity_to_shelf for l in plines)
        weighted = sum(int(l.unit_cost_kobo) * l.quantity_to_shelf for l in plines)
        new_cost = (weighted + shelf_qty // 2) // shelf_qty
        result = await session.execute(
            select(StockBatch)
            .where(
                StockBatch.branch_id == receipt.branch_id,
                StockBatch.product_id == product_id,
                StockBatch.batch_number == batch_number,
            )
            .with_for_update()
        )
        batches = list(result.scalars().all())
        if not batches or sum(b.quantity_received for b in batches) != shelf_qty:
            continue
        for b in batches:
            if b.unit_cost_kobo != new_cost:
                changes.append(
                    {
                        "batch_id": str(b.id),
                        "product_id": str(product_id),
                        "old_unit_cost_kobo": int(b.unit_cost_kobo),
                        "new_unit_cost_kobo": int(new_cost),
                    }
                )
                b.unit_cost_kobo = new_cost
    if changes:
        log_audit_event(
            session,
            user_id=current_user.id,
            action="goods_receipt_batch_costs_corrected",
            entity_type="goods_receipt",
            entity_id=receipt.id,
            metadata={"branch_id": str(receipt.branch_id), "batches": changes},
        )


@router.patch("/receipts/{receipt_id}/link-invoice")
async def link_invoice(
    receipt_id: uuid.UUID,
    body: LinkInvoiceIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
):
    """Attach the supplier invoice to an existing receipt and verify costs.
    Never posts stock: quantities were already recorded by the receipt."""
    receipt = await _load_receipt(session, receipt_id, lock=True)
    _check_branch_access(current_user, receipt.branch_id)
    if receipt.status == "voided":
        raise _bad("This receipt was voided.", 409)
    if receipt.invoice_id is not None and receipt.invoice_id != body.invoice_id:
        raise _bad("This receipt is already linked to a different invoice.", 409)
    invoice = await session.get(PurchaseInvoice, body.invoice_id)
    if invoice is None:
        raise _bad("Invoice not found", 404)
    if invoice.source == "branch_transfer":
        raise _bad("This invoice was made by a branch transfer. The goods are already in stock, so it cannot be received.")
    if invoice.branch_id is not None and invoice.branch_id != receipt.branch_id:
        raise _bad("That invoice was filed for a different branch.")
    receipt.invoice_id = invoice.id
    if not receipt.supplier_name:
        receipt.supplier_name = invoice.supplier_name
    receipt.documentation_status = "documented"
    by_id = {l.id: l for l in receipt.lines}
    for line_id, li_id in (body.line_links or {}).items():
        line = by_id.get(line_id)
        li = await session.get(PurchaseInvoiceLineItem, li_id)
        if line is None or li is None or li.invoice_id != invoice.id:
            raise _bad("A line link points at a receipt line or invoice line that does not match.")
        if li.product_id and li.product_id != line.product_id:
            raise _bad("A line link pairs a receipt line with an invoice line for a different product.")
        line.invoice_line_item_id = li.id
    await session.flush()
    for li_id in {l.invoice_line_item_id for l in receipt.lines if l.invoice_line_item_id}:
        li = await session.get(PurchaseInvoiceLineItem, li_id)
        if li is None or li.total_pieces is None:
            continue
        counted = await _received_via_receipts(session, li.id)
        if receipt.status == "draft":
            counted += sum(l.quantity_received for l in receipt.lines if l.invoice_line_item_id == li.id)
        counted += await _pushed_old_flow(session, li.id)
        if counted > li.total_pieces:
            await session.rollback()
            raise _bad(
                f"Linking would put {counted} pieces against an invoice line that only lists {li.total_pieces}."
            )
    for line_id, cost in (body.line_costs or {}).items():
        line = by_id.get(line_id)
        if line is None or cost < 0:
            raise _bad("A line cost refers to an unknown receipt line or is negative.")
        line.unit_cost_kobo = cost
        line.cost_verified = True
    if body.verify_costs:
        for line in receipt.lines:
            if line.invoice_line_item_id and not line.cost_verified:
                li = await session.get(PurchaseInvoiceLineItem, line.invoice_line_item_id)
                if li is not None and li.cost_price_per_piece_kobo:
                    line.unit_cost_kobo = int(li.cost_price_per_piece_kobo)
                    line.cost_verified = True
    receipt.cost_status = (
        "verified" if receipt.lines and all(l.cost_verified for l in receipt.lines) else "unverified"
    )
    await _sync_batch_costs(session, receipt, current_user)
    log_audit_event(
        session,
        user_id=current_user.id,
        action="goods_receipt_invoice_linked",
        entity_type="goods_receipt",
        entity_id=receipt.id,
        metadata={
            "branch_id": str(receipt.branch_id),
            "invoice_id": str(invoice.id),
            "cost_status": receipt.cost_status,
        },
    )
    await session.commit()
    return _receipt_out(await _load_receipt(session, receipt_id))