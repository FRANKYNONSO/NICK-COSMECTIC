import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nickcosmetics_core.domain.money import Money

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.warehouse import (
    IntakeReceiptOut,
    LinkInvoiceLineItemProductIn,
    PushableInvoiceLineItemOut,
    PushToWarehouseCreate,
    StockTransferCreate,
    StockTransferOut,
    WarehouseMoveCreate,
    WarehouseStockOut,
    WarehouseStockUnitBreakdown,
    WarehouseTransferToBranchCreate,
)
from infrastructure.db.models import (
    Branch,
    IntakeReceipt,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceLineItem,
    StockBatch,
    StockTransfer,
    User,
    WarehouseStock,
)
from api.v1.routers.catalog import _PUBLIC_BASE_URL

router = APIRouter(prefix="/warehouse", tags=["warehouse"])

# September 2026 change -- push-to-warehouse's own primary unit
# (unit_label: "carton"/"dozen"/"piece", singular) keeps its existing
# convention: pieces-per-unit is always admin-typed, never hardcoded
# to 12 for dozens the way the invoice side is -- "we should still be
# able to manually input it... It shouldn't be a default" (original
# spec). Only the REMAINDER on top of it borrows the invoice's own
# fixed labels/conversions ("dozens"/"pieces", or the two no-quantity
# half-pack labels), since a remainder is always described the same
# way the invoice itself described it.
_WH_DOZEN_PIECES = 12
_WH_HALF_PACK_LABELS = {"half_carton", "half_dozen"}


def _wh_remainder_pieces(
    remainder_unit_label: str | None,
    remainder_quantity: int | None,
    pieces_per_primary_unit: int | None,
) -> int:
    if remainder_unit_label in _WH_HALF_PACK_LABELS:
        # Half of whatever one whole primary unit contains -- same
        # figure the old fixed is_half_partial checkbox used.
        return pieces_per_primary_unit // 2 if pieces_per_primary_unit else 0
    if remainder_unit_label == "dozens":
        return (remainder_quantity or 0) * _WH_DOZEN_PIECES
    if remainder_unit_label == "pieces":
        return remainder_quantity or 0
    return 0


def _wh_compute_quantity_pieces(
    unit_count: int,
    pieces_per_unit: int,
    remainder_quantity: int | None,
    remainder_unit_label: str | None,
    is_half_partial: bool,
) -> int:
    primary = unit_count * pieces_per_unit
    if remainder_unit_label:
        return primary + _wh_remainder_pieces(remainder_unit_label, remainder_quantity, pieces_per_unit)
    # Deprecated fallback -- a caller that hasn't been migrated to send
    # remainder_unit_label yet still gets the old fixed-half behavior.
    if is_half_partial:
        return primary + (pieces_per_unit // 2 if pieces_per_unit else 0)
    return primary

# ---- Component-based push shape (Sept 2026, second revision) ---------
# Cartons / dozens / pieces are counted independently. Cartons and
# dozens are tracked in HALVES against an invoice line so a ticked
# half-pack is exact; pieces are plain counts.
_WH_CARTON_WORDS = {"carton", "cartons", "ctn", "ctns", "box", "boxes"}
_WH_DOZEN_WORDS = {"dozen", "dozens", "doz", "dz"}
_WH_PIECE_WORDS = {"piece", "pieces", "pcs", "pc"}


def _wh_norm_unit(label, default="carton"):
    key = (label or "").strip().lower()
    if key in _WH_CARTON_WORDS:
        return "carton"
    if key in _WH_DOZEN_WORDS:
        return "dozen"
    if key in _WH_PIECE_WORDS:
        return "piece"
    return default


def _wh_fmt_halves(halves):
    return f"{halves / 2:g}"


def _wh_is_component_shape(x):
    return (
        getattr(x, "cartons_count", None) is not None
        or getattr(x, "dozens_count", None) is not None
        or getattr(x, "pieces_count", None) is not None
        or bool(getattr(x, "cartons_half", False))
        or bool(getattr(x, "dozens_half", False))
    )


def _wh_component_total_pieces(x):
    cartons_ppu = x.cartons_pieces_per_unit or 0
    dozens_ppu = x.dozens_pieces_per_unit or 0
    cartons = (x.cartons_count or 0) * cartons_ppu + (cartons_ppu // 2 if x.cartons_half else 0)
    dozens = (x.dozens_count or 0) * dozens_ppu + (dozens_ppu // 2 if x.dozens_half else 0)
    return cartons + dozens + (x.pieces_count or 0)


def _wh_validate_component_body(body):
    if body.quantity_pieces_override is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A typed total is not used with cartons/dozens/pieces entry - enter the pieces directly instead.",
        )
    if ((body.cartons_count or 0) > 0 or body.cartons_half) and not body.cartons_pieces_per_unit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter pieces per carton for the cartons being pushed.")
    if ((body.dozens_count or 0) > 0 or body.dozens_half) and not body.dozens_pieces_per_unit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter pieces per dozen for the dozens being pushed.")
    if _wh_component_total_pieces(body) <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter a quantity greater than 0.")


def _wh_components_from_invoice_line(line_item):
    """(carton_halves, dozen_halves, pieces) the invoice line holds."""
    ch = dh = pcs = 0
    unit = _wh_norm_unit(line_item.unit_label)
    q = line_item.quantity or 0
    if unit == "carton":
        ch += q * 2
    elif unit == "dozen":
        dh += q * 2
    else:
        pcs += q
    rl = line_item.remainder_unit_label
    rq = line_item.remainder_quantity or 0
    if rl == "half_carton":
        ch += 1
    elif rl == "half_dozen":
        dh += 1
    elif rl == "dozens":
        dh += rq * 2
    elif rl == "pieces":
        pcs += rq
    return ch, dh, pcs


def _wh_transfer_components(t, default_unit="carton"):
    """(carton_halves, dozen_halves, pieces) for a StockTransfer row or a
    push request body, in either the component or the legacy shape."""
    if getattr(t, "unit_label", None) == "mixed" or _wh_is_component_shape(t):
        return (
            (t.cartons_count or 0) * 2 + (1 if t.cartons_half else 0),
            (t.dozens_count or 0) * 2 + (1 if t.dozens_half else 0),
            t.pieces_count or 0,
        )
    ch = dh = pcs = 0
    unit = _wh_norm_unit(t.unit_label, default_unit)
    n = t.unit_count or 0
    if unit == "carton":
        ch += n * 2
    elif unit == "dozen":
        dh += n * 2
    else:
        pcs += n
    rl = t.remainder_unit_label
    rq = t.remainder_quantity or 0
    if rl == "half_carton":
        ch += 1
    elif rl == "half_dozen":
        dh += 1
    elif rl == "dozens":
        dh += rq * 2
    elif rl == "pieces":
        pcs += rq
    elif t.is_half_partial:
        if unit == "carton":
            ch += 1
        elif unit == "dozen":
            dh += 1
    return ch, dh, pcs


_RECEIPT_IMAGE_DIR = Path("static/uploads/receipts")
# Spec: "upload the image or the video" -- a waybill complaint's
# evidence can be either, not photo-only like before.
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
_ALLOWED_MEDIA_EXTENSIONS = _ALLOWED_IMAGE_EXTENSIONS | _ALLOWED_VIDEO_EXTENSIONS


# ------------------------------------------------------- WAYBILL COMPLAINTS
# Was "snap the receipt when a wholesale delivery comes in" -- a plain
# photo + free-typed supplier name, disconnected from anything else on
# file. Now: pick the actual purchase invoice a delivery was on (the
# frontend filters by month/year first, per spec, then lists matching
# invoices), which supplies the supplier and date; the complaint itself
# is the damaged/missing counts, notes, and photo/video evidence.
# Line-itemizing what's actually in the boxes still happens separately
# via /warehouse/transfers as it gets counted and moved, and via
# /inventory/stock-batches as it's shelved -- this endpoint is the
# complaint record, not an inventory-count document.

@router.post(
    "/receipts", response_model=IntakeReceiptOut, status_code=status.HTTP_201_CREATED
)
async def log_waybill_complaint(
    branch_id: uuid.UUID = Form(...),
    invoice_id: uuid.UUID | None = Form(default=None),
    invoice_line_item_id: uuid.UUID | None = Form(default=None),
    item_name: str | None = Form(default=None),
    supplier_name: str | None = Form(default=None),
    item_count: int | None = Form(default=None),
    damaged_count: int | None = Form(default=None),
    missing_count: int | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> IntakeReceiptOut:
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")

    invoice: PurchaseInvoice | None = None
    if invoice_id is not None:
        invoice = await session.get(PurchaseInvoice, invoice_id)
        if invoice is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Selected invoice was not found")
        # The invoice is the source of truth for who made the supply --
        # spec: "select the person that made that supply, referencing
        # the information that is on the invoice." Whatever the client
        # sent for supplier_name is overridden, never merely defaulted
        # to, so the two can't drift out of sync.
        supplier_name = invoice.supplier_name

    line_item: PurchaseInvoiceLineItem | None = None
    if invoice_line_item_id is not None:
        line_item = await session.get(PurchaseInvoiceLineItem, invoice_line_item_id)
        if line_item is None or (invoice_id is not None and line_item.invoice_id != invoice_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Selected invoice item was not found")
        # "This is the product that you gave me 30 cartons" -- default
        # the count being complained about to what the invoice line
        # actually said was delivered, unless the reporter overrode it.
        if item_count is None:
            item_count = line_item.quantity

    original_name = file.filename or "waybill-complaint"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_MEDIA_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_MEDIA_EXTENSIONS))}",
        )

    _RECEIPT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _RECEIPT_IMAGE_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)

    receipt = IntakeReceipt(
        branch_id=branch_id,
        invoice_id=invoice_id,
        invoice_line_item_id=invoice_line_item_id,
        image_url=f"{_PUBLIC_BASE_URL}/static/uploads/receipts/{new_filename}",
        supplier_name=supplier_name,
        # Only stored when there's no real line item to point at -- a
        # manually typed product name for a photo-only invoice.
        manual_item_name=item_name if invoice_line_item_id is None else None,
        item_count=item_count,
        damaged_count=damaged_count,
        missing_count=missing_count,
        notes=notes,
        created_by=current_user.id,
    )
    session.add(receipt)
    await session.commit()
    await session.refresh(receipt)
    return IntakeReceiptOut(
        id=receipt.id,
        branch_id=receipt.branch_id,
        invoice_id=receipt.invoice_id,
        invoice_line_item_id=receipt.invoice_line_item_id,
        invoice_date=invoice.invoice_date.isoformat() if invoice else None,
        line_item_name=line_item.item_name if line_item else receipt.manual_item_name,
        image_url=receipt.image_url,
        supplier_name=receipt.supplier_name,
        item_count=receipt.item_count,
        damaged_count=receipt.damaged_count,
        missing_count=receipt.missing_count,
        notes=receipt.notes,
        created_by=receipt.created_by,
        created_by_name=current_user.full_name,
        received_at=receipt.received_at,
        created_at=receipt.created_at,
    )


@router.get("/receipts", response_model=list[IntakeReceiptOut])
async def list_waybill_complaints(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[IntakeReceiptOut]:
    stmt = select(IntakeReceipt).order_by(IntakeReceipt.received_at.desc())
    if branch_id:
        stmt = stmt.where(IntakeReceipt.branch_id == branch_id)
    result = await session.execute(stmt)
    receipts = list(result.scalars().all())
    if not receipts:
        return []

    creator_ids = {r.created_by for r in receipts if r.created_by}
    creator_names: dict[uuid.UUID, str] = {}
    if creator_ids:
        users_result = await session.execute(select(User).where(User.id.in_(creator_ids)))
        creator_names = {u.id: u.full_name for u in users_result.scalars().all()}

    invoice_ids = {r.invoice_id for r in receipts if r.invoice_id}
    invoice_dates: dict[uuid.UUID, str] = {}
    if invoice_ids:
        invoices_result = await session.execute(
            select(PurchaseInvoice).where(PurchaseInvoice.id.in_(invoice_ids))
        )
        invoice_dates = {inv.id: inv.invoice_date.isoformat() for inv in invoices_result.scalars().all()}

    line_item_ids = {r.invoice_line_item_id for r in receipts if r.invoice_line_item_id}
    line_item_names: dict[uuid.UUID, str] = {}
    if line_item_ids:
        items_result = await session.execute(
            select(PurchaseInvoiceLineItem).where(PurchaseInvoiceLineItem.id.in_(line_item_ids))
        )
        line_item_names = {i.id: i.item_name for i in items_result.scalars().all()}

    return [
        IntakeReceiptOut(
            id=r.id,
            branch_id=r.branch_id,
            invoice_id=r.invoice_id,
            invoice_line_item_id=r.invoice_line_item_id,
            invoice_date=invoice_dates.get(r.invoice_id) if r.invoice_id else None,
            line_item_name=line_item_names.get(r.invoice_line_item_id) if r.invoice_line_item_id else r.manual_item_name,
            image_url=r.image_url,
            supplier_name=r.supplier_name,
            item_count=r.item_count,
            damaged_count=r.damaged_count,
            missing_count=r.missing_count,
            notes=r.notes,
            created_by=r.created_by,
            created_by_name=creator_names.get(r.created_by),
            received_at=r.received_at,
            created_at=r.created_at,
        )
        for r in receipts
    ]


# ---------------------------------------------------------- STOCK TRANSFERS
# Moves stock from one branch to another -- in practice almost always
# shop counter -> warehouse branch, occasionally the reverse when the
# counter needs restocking from what's out back. Deducts FIFO (oldest
# batch first) from the source branch and opens one new batch at the
# destination, same cost-basis convention StockBatch uses everywhere
# else, so the destination's stock value isn't lost in the move.

async def _fifo_deduct(
    session: AsyncSession, product_id: uuid.UUID, branch_id: uuid.UUID, quantity: int, dry_run: bool = False
) -> int:
    """Deducts `quantity` from the oldest available batches for a
    variant at a branch. Returns the quantity-weighted average unit
    cost (kobo) of what was actually deducted, for use as the cost
    basis of the new batch at the destination. Raises HTTPException if
    there isn't enough stock to cover the deduction.

    dry_run=True (September 2026 change spec section 19's
    confirmation-receipt step, see the /transfer-cost-preview endpoint
    below) computes the same weighted-average cost and availability
    check WITHOUT mutating any batch -- the caller must not commit
    after this, since nothing was actually changed.
    """
    stmt = (
        select(StockBatch)
        .where(
            StockBatch.product_id == product_id,
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
        )
        .order_by(StockBatch.received_at)
    )
    result = await session.execute(stmt)
    batches = list(result.scalars().all())

    available = sum(b.quantity_remaining for b in batches)
    if available < quantity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Not enough stock at the source branch to push {quantity} unit(s) -- "
                f"only {available} available"
            ),
        )

    remaining_to_deduct = quantity
    cost_weighted_total = 0
    for batch in batches:
        if remaining_to_deduct <= 0:
            break
        take = min(batch.quantity_remaining, remaining_to_deduct)
        if not dry_run:
            batch.quantity_remaining -= take
        cost_weighted_total += take * batch.unit_cost_kobo
        remaining_to_deduct -= take

    return cost_weighted_total // quantity if quantity else 0


@router.post(
    "/transfers", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED
)
async def create_stock_transfer(
    body: StockTransferCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockTransferOut:
    # Spec section 13: "Do not allow users to arbitrarily change the
    # originating branch if their current branch is already defined
    # by their account/session." A super admin's account has no fixed
    # branch_id (they can operate any branch from the dashboard
    # picker), so only staff/branch_manager -- whose account *does*
    # pin a branch -- are enforced here.
    if current_user.branch_id and body.from_branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only transfer stock out of your own branch.",
        )
    variant = await session.get(Product, body.product_id)
    if variant is None or variant.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product variant not found")
    if body.from_branch_id == body.to_branch_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Source and destination branch must differ"
        )
    for branch_id in (body.from_branch_id, body.to_branch_id):
        if await session.get(Branch, branch_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Branch {branch_id} not found")

    quantity_pieces = body.unit_count * body.pieces_per_unit

    avg_unit_cost_kobo = await _fifo_deduct(
        session, body.product_id, body.from_branch_id, quantity_pieces
    )
    # September 2026 change spec section 19: priced at the item's
    # original invoice/supplier cost (this same quantity-weighted
    # average FIFO cost basis), never the retail selling price -- this
    # is an internal accountability transfer between branches of the
    # same business, not a retail sale.
    total_cost_kobo = avg_unit_cost_kobo * quantity_pieces

    new_batch = StockBatch(
        product_id=body.product_id,
        branch_id=body.to_branch_id,
        batch_number=f"transfer-{body.unit_label}",
        quantity_received=quantity_pieces,
        quantity_remaining=quantity_pieces,
        unit_cost_kobo=avg_unit_cost_kobo,
    )
    session.add(new_batch)

    transfer = StockTransfer(
        product_id=body.product_id,
        from_branch_id=body.from_branch_id,
        to_branch_id=body.to_branch_id,
        transfer_type="branch_to_branch",
        unit_label=body.unit_label,
        unit_count=body.unit_count,
        pieces_per_unit=body.pieces_per_unit,
        quantity_pieces=quantity_pieces,
        receipt_id=body.receipt_id,
        notes=body.notes,
        total_cost_kobo=total_cost_kobo,
        payment_method=body.payment_method,
        created_by=current_user.id,
    )
    session.add(transfer)

    await session.commit()
    await session.refresh(transfer)
    return StockTransferOut(
        id=transfer.id,
        product_id=transfer.product_id,
        from_branch_id=transfer.from_branch_id,
        to_branch_id=transfer.to_branch_id,
        transfer_type=transfer.transfer_type,
        unit_label=transfer.unit_label,
        unit_count=transfer.unit_count,
        pieces_per_unit=transfer.pieces_per_unit,
        quantity_pieces=transfer.quantity_pieces,
        quantity_pieces_override=transfer.quantity_pieces_override,
        is_half_partial=transfer.is_half_partial,
        receipt_id=transfer.receipt_id,
        notes=transfer.notes,
        total_cost_naira=Money(transfer.total_cost_kobo).naira if transfer.total_cost_kobo is not None else None,
        payment_method=transfer.payment_method,
        created_by=transfer.created_by,
        created_by_name=current_user.full_name,
        created_at=transfer.created_at,
    )


@router.get("/transfer-cost-preview")
async def preview_transfer_cost(
    product_id: uuid.UUID,
    from_branch_id: uuid.UUID,
    quantity_pieces: int,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_inventory")),
) -> dict:
    """September 2026 change spec section 19: "On confirming, show a
    receipt-style summary (items, cost, who processed it) before final
    confirmation." The exact weighted-average cost can only be known
    once FIFO deduction actually runs, so this computes it for the
    confirmation screen via the same _fifo_deduct logic (dry_run=True,
    so nothing is actually deducted) rather than showing an approximate
    figure that might not match what the real transfer settles at a
    moment later.
    """
    avg_unit_cost_kobo = await _fifo_deduct(session, product_id, from_branch_id, quantity_pieces, dry_run=True)
    return {
        "unit_cost_naira": Money(avg_unit_cost_kobo).naira,
        "total_cost_naira": Money(avg_unit_cost_kobo * quantity_pieces).naira,
    }


# ------------------------------------------------------ WAREHOUSE LEDGER
# The three warehouse-specific moves. Unlike create_stock_transfer
# above (a direct shop-to-shop transfer, still supported for branches
# that want to move sellable stock without a warehouse leg), these
# read/write WarehouseStock -- a branch's own backstock, invisible to
# POS/Inventory/low-stock -- as one side of the move.

async def _get_or_create_warehouse_row(
    session: AsyncSession, branch_id: uuid.UUID, product_id: uuid.UUID
) -> WarehouseStock:
    result = await session.execute(
        select(WarehouseStock).where(
            WarehouseStock.branch_id == branch_id, WarehouseStock.product_id == product_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = WarehouseStock(branch_id=branch_id, product_id=product_id, quantity_pieces=0)
        session.add(row)
        await session.flush()
    return row


async def _restock_cost_basis_kobo(
    session: AsyncSession, branch_id: uuid.UUID, variant: Product
) -> int:
    """Best available per-piece cost for a shop-floor restock out of
    the warehouse. Warehouse stock isn't cost-lot-tracked the way shop
    StockBatches are (it's just a running quantity, see
    WarehouseStock) -- so this doesn't try to track exactly which
    physical units are being pulled, just the most defensible cost
    figure available:
      1) the invoice rate behind the most recent store_to_warehouse
         push for this product at this branch, converted from
         "per carton/roll/etc" to per-piece using that same push's own
         pieces_per_unit -- real supplier cost, correctly scaled;
      2) falling back to the product's own on-file cost price
         (Product.cost_price_kobo) when no such push exists (e.g. a
         manual/photo-only push with no invoice link);
      3) falling back to 0 only when neither is available -- a
         genuinely never-priced product, which previously was silent
         (losses on it always read NGN0.00 with no way to tell why).
    """
    result = await session.execute(
        select(StockTransfer, PurchaseInvoiceLineItem)
        .join(
            PurchaseInvoiceLineItem,
            StockTransfer.invoice_line_item_id == PurchaseInvoiceLineItem.id,
        )
        .where(
            StockTransfer.transfer_type == "store_to_warehouse",
            StockTransfer.product_id == variant.id,
            StockTransfer.to_branch_id == branch_id,
        )
        .order_by(StockTransfer.created_at.desc())
        .limit(1)
    )
    row = result.first()
    if row is not None:
        transfer, line_item = row
        if transfer.unit_label == "mixed":
            # Component-shape push: the invoice rate is per the line's
            # own unit, so divide by that unit's own pieces-per-unit.
            pieces_per_unit = {
                "carton": transfer.cartons_pieces_per_unit,
                "dozen": transfer.dozens_pieces_per_unit,
                "piece": 1,
            }.get(_wh_norm_unit(line_item.unit_label))
        else:
            pieces_per_unit = transfer.pieces_per_unit or 1
        per_piece = line_item.rate_kobo // pieces_per_unit if pieces_per_unit else 0
        if per_piece > 0:
            return per_piece
    return variant.cost_price_kobo or 0


async def _create_store_batch(
    session: AsyncSession, branch_id: uuid.UUID, variant: Product, quantity_pieces: int, label: str
) -> None:
    """Restocks the shop floor at `branch_id` from the warehouse side
    of a move -- see _restock_cost_basis_kobo above for how its cost
    basis is chosen.
    """
    unit_cost_kobo = await _restock_cost_basis_kobo(session, branch_id, variant)
    session.add(
        StockBatch(
            product_id=variant.id,
            branch_id=branch_id,
            batch_number=f"warehouse-{label}",
            quantity_received=quantity_pieces,
            quantity_remaining=quantity_pieces,
            unit_cost_kobo=unit_cost_kobo,
        )
    )


async def _wh_pushed_components(session: AsyncSession, line_item):
    """(carton_halves, dozen_halves, pieces) already pushed against this
    invoice line, across any branch, old and new shape alike."""
    result = await session.execute(
        select(StockTransfer).where(
            StockTransfer.invoice_line_item_id == line_item.id,
            StockTransfer.transfer_type == "store_to_warehouse",
        )
    )
    default_unit = _wh_norm_unit(line_item.unit_label)
    ch = dh = pcs = 0
    for t in result.scalars().all():
        a, b, c = _wh_transfer_components(t, default_unit)
        ch += a
        dh += b
        pcs += c
    return ch, dh, pcs


async def _wh_line_component_fields(session: AsyncSession, line_item) -> dict:
    line_ch, line_dh, line_pcs = _wh_components_from_invoice_line(line_item)
    done_ch, done_dh, done_pcs = await _wh_pushed_components(session, line_item)
    return {
        "cartons_total_halves": line_ch,
        "cartons_remaining_halves": max(line_ch - done_ch, 0),
        "dozens_total_halves": line_dh,
        "dozens_remaining_halves": max(line_dh - done_dh, 0),
        "pieces_total": line_pcs,
        "pieces_remaining": max(line_pcs - done_pcs, 0),
    }


async def _pushed_so_far(session: AsyncSession, invoice_line_item_id: uuid.UUID) -> int:
    """Sum of unit_count already pushed to the warehouse against this
    invoice line, across any branch (an invoice line item can only
    ever be pushed once its full quantity is used up, regardless of
    who pushed which part of it).

    Measured in the invoice's own unit (e.g. dozens, cartons) -- the
    same unit line_item.quantity is recorded in -- NOT in individual
    pieces. quantity_pieces (unit_count x pieces_per_unit) is a piece-
    level figure used only for warehouse stock levels; it must never
    be compared against line_item.quantity, which is a count of the
    original invoice unit and has no idea how many pieces are inside
    each one.
    """
    result = await session.execute(
        select(func.coalesce(func.sum(StockTransfer.unit_count), 0)).where(
            StockTransfer.invoice_line_item_id == invoice_line_item_id,
            StockTransfer.transfer_type == "store_to_warehouse",
        )
    )
    return int(result.scalar_one())


@router.get("/pushable-invoice-items", response_model=list[PushableInvoiceLineItemOut])
async def list_pushable_invoice_items(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> list[PushableInvoiceLineItemOut]:
    """Invoice line items eligible to be pushed to `branch_id`'s
    warehouse -- linked to a catalog variant, filed against this
    branch or a store-wide invoice, and not already fully pushed.
    Backs the "push to warehouse" picker: spec: "if the item is not
    on the invoice... it should not be pushed to warehouse," so this
    endpoint is the only way the frontend can select something to
    push -- there's no freehand product/variant picker for this move
    anymore.

    Update Request item 12 bug fix: a line item only got `product_id`
    filled in if the person entering the invoice happened to click a
    suggestion in the Item autocomplete dropdown rather than just
    typing the name and moving on -- so an invoice that clearly had
    the item on it could still fail to show up here, because the
    *line* never got linked even though the *product* very much
    existed. Line items without product_id are now also resolved by
    an exact (case-insensitive) name match against the catalog as a
    fallback, so "the item clearly exists" is enough on its own,
    without requiring that earlier click. Genuinely unmatched lines
    (no product_id AND no name match) still fall through to the
    existing "invoice was just a photo -- enter manually" flow.
    """
    if current_user.branch_id and branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only view invoice items for your own branch.",
        )
    result = await session.execute(
        select(PurchaseInvoiceLineItem, PurchaseInvoice)
        .join(PurchaseInvoice, PurchaseInvoiceLineItem.invoice_id == PurchaseInvoice.id)
        .where((PurchaseInvoice.branch_id == branch_id) | (PurchaseInvoice.branch_id.is_(None)))
        .order_by(PurchaseInvoice.invoice_date.desc())
    )
    rows = result.all()

    # Fallback name-match lookup, built once rather than per-row.
    unlinked_names = {li.item_name.strip().lower() for li, _ in rows if li.product_id is None}
    name_to_product_id: dict[str, uuid.UUID] = {}
    if unlinked_names:
        product_rows = await session.execute(
            select(Product.id, Product.name).where(Product.is_deleted.is_(False))
        )
        for pid, pname in product_rows.all():
            key = pname.strip().lower()
            if key in unlinked_names:
                name_to_product_id[key] = pid

    out: list[PushableInvoiceLineItemOut] = []
    for line_item, invoice in rows:
        resolved_product_id = line_item.product_id or name_to_product_id.get(line_item.item_name.strip().lower())
        already_pushed = await _pushed_so_far(session, line_item.id)
        remaining = line_item.quantity - already_pushed
        component_fields = await _wh_line_component_fields(session, line_item)
        capped_left = (
            component_fields["cartons_remaining_halves"]
            + component_fields["dozens_remaining_halves"]
            + component_fields["pieces_remaining"]
        )
        if capped_left <= 0:
            continue
        out.append(
            PushableInvoiceLineItemOut(
                invoice_line_item_id=line_item.id,
                invoice_id=invoice.id,
                invoice_date=invoice.invoice_date.isoformat(),
                supplier_id=invoice.supplier_id,
                supplier_name=invoice.supplier_name or "Unknown supplier",
                item_name=line_item.item_name,
                product_id=resolved_product_id,
                is_unmatched=resolved_product_id is None,
                unit_label=line_item.unit_label,
                quantity=line_item.quantity,
                already_pushed=already_pushed,
                remaining=remaining,
                **component_fields,
                pieces_per_carton=line_item.pieces_per_carton,
                remainder_quantity=line_item.remainder_quantity,
                remainder_unit_label=line_item.remainder_unit_label,
                total_pieces=line_item.total_pieces,
            )
        )
    return out


@router.patch("/invoice-line-items/{line_item_id}/link-product", response_model=PushableInvoiceLineItemOut)
async def link_invoice_line_item_product(
    line_item_id: uuid.UUID,
    body: LinkInvoiceLineItemProductIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> PushableInvoiceLineItemOut:
    """Permanently link a purchase-invoice line item to a catalog
    product, for lines that were typed in without using the Item
    autocomplete (so `product_id` was never set) and whose name
    doesn't exactly match any catalog product name either -- e.g.
    "VIVA SOAP" on the invoice vs. "viva" in the catalog. Fixing the
    link here means every future lookup (push-to-warehouse, invoice
    detail, reports) recognizes it correctly from then on, not just
    for one push.
    """
    line_item = await session.get(PurchaseInvoiceLineItem, line_item_id)
    if line_item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice line item not found")
    invoice = await session.get(PurchaseInvoice, line_item.invoice_id)
    if (
        invoice is not None
        and invoice.branch_id is not None
        and current_user.branch_id
        and invoice.branch_id != current_user.branch_id
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only re-link invoice items for your own branch.",
        )
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    line_item.product_id = body.product_id
    await session.commit()
    await session.refresh(line_item)

    already_pushed = await _pushed_so_far(session, line_item.id)
    remaining = line_item.quantity - already_pushed
    component_fields = await _wh_line_component_fields(session, line_item)
    return PushableInvoiceLineItemOut(
        **component_fields,
        invoice_line_item_id=line_item.id,
        invoice_id=line_item.invoice_id,
        invoice_date=invoice.invoice_date.isoformat() if invoice else "",
        supplier_id=invoice.supplier_id if invoice else None,
        supplier_name=(invoice.supplier_name or "Unknown supplier") if invoice else "",
        item_name=line_item.item_name,
        product_id=line_item.product_id,
        is_unmatched=False,
        unit_label=line_item.unit_label,
        quantity=line_item.quantity,
        already_pushed=already_pushed,
        remaining=remaining,
    )


@router.post("/push-to-warehouse", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED)
async def push_to_warehouse(
    branch_id: uuid.UUID,
    body: PushToWarehouseCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockTransfer:
    """Shop floor -> this branch's own warehouse. Spec: this is a
    documentation move, NOT a deduction against live sellable stock --
    "it should manually impute any data of information you want to
    put there," recording that N cartons out of what was actually
    bought on an invoice are sitting in the back, unused. It never
    touches StockBatch. Normally it's only allowed against an item
    that's genuinely on an invoice on file, capped at that invoice
    line's own quantity minus whatever's already been pushed against
    it (spec: "if the item is not on the invoice... it should not be
    pushed to warehouse") -- but when the invoice was only ever saved
    as a photo (no invoice_line_item_id sent), it's pushed straight
    against product_id instead, with no line to validate or cap
    against.
    """
    if current_user.branch_id and branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only push stock to your own branch's warehouse.",
        )
    variant = await session.get(Product, body.product_id)
    if variant is None or variant.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product variant not found")
    if await session.get(Branch, branch_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")

    # September 2026 change -- carries any remainder the invoice
    # itself recorded ("20 cartons + 3 dozens", "1 carton + half a
    # carton", etc) through to the push, not just a fixed half. An
    # optional direct override still lets the total be typed by hand
    # instead of always trusting the calculation.
    is_components = _wh_is_component_shape(body)
    if is_components:
        _wh_validate_component_body(body)
        quantity_pieces = _wh_component_total_pieces(body)
    else:
        if body.unit_label is None or body.unit_count is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Send cartons_count / dozens_count / pieces_count, or unit_label + unit_count.",
            )
        calculated_quantity_pieces = _wh_compute_quantity_pieces(
            body.unit_count,
            body.pieces_per_unit,
            body.remainder_quantity,
            body.remainder_unit_label,
            body.is_half_partial,
        )
        quantity_pieces = (
            body.quantity_pieces_override
            if body.quantity_pieces_override is not None
            else calculated_quantity_pieces
        )

    if body.invoice_line_item_id is not None:
        line_item = await session.get(PurchaseInvoiceLineItem, body.invoice_line_item_id)
        if line_item is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="That invoice line item was not found. Only items that are on an invoice can be pushed to warehouse.",
            )
        if line_item.product_id is not None:
            if line_item.product_id != body.product_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="That invoice line item is for a different product/variant.",
                )
        else:
            # Item 12 fix: this line was never explicitly linked to a
            # catalog product at invoice-entry time (see the fallback
            # name-match in list_pushable_invoice_items above), so
            # there's nothing to compare body.product_id against
            # directly -- confirm it by name instead, so an arbitrary
            # product_id still can't be pushed against someone else's
            # invoice line.
            if variant.name.strip().lower() != line_item.item_name.strip().lower():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="That invoice line item is for a different product/variant.",
                )
        invoice = await session.get(PurchaseInvoice, line_item.invoice_id)
        if invoice is not None and invoice.branch_id is not None and invoice.branch_id != branch_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="That invoice was filed for a different branch.",
            )

        # Second revision: cartons and dozens are each capped against
        # what is left of their own component on the invoice line (in
        # halves, so a half-pack is exact). Pieces are only capped when
        # the invoice line itself has a pieces part; otherwise they are
        # a free-form conversion and uncapped.
        line_ch, line_dh, line_pcs = _wh_components_from_invoice_line(line_item)
        done_ch, done_dh, done_pcs = await _wh_pushed_components(session, line_item)
        want_ch, want_dh, want_pcs = _wh_transfer_components(body, _wh_norm_unit(line_item.unit_label))
        left_ch = max(line_ch - done_ch, 0)
        left_dh = max(line_dh - done_dh, 0)
        left_pcs = max(line_pcs - done_pcs, 0)
        problems: list[str] = []
        if want_ch > left_ch:
            problems.append(f"{_wh_fmt_halves(want_ch)} carton(s) requested, {_wh_fmt_halves(left_ch)} left on the invoice line")
        if want_dh > left_dh:
            problems.append(f"{_wh_fmt_halves(want_dh)} dozen(s) requested, {_wh_fmt_halves(left_dh)} left on the invoice line")
        if line_pcs > 0 and want_pcs > left_pcs:
            problems.append(f"{want_pcs} piece(s) requested, {left_pcs} left on the invoice line")
        if problems:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Cannot push " + line_item.item_name + ": " + "; ".join(problems) + ".",
            )

    # NOTE: deliberately no shop-floor StockBatch deduction here -- see
    # docstring. Pushing to the warehouse documents backstock; it does
    # not remove anything from what's sellable on the shop floor.
    warehouse_row = await _get_or_create_warehouse_row(session, branch_id, body.product_id)
    warehouse_row.quantity_pieces += quantity_pieces

    transfer = StockTransfer(
        product_id=body.product_id,
        from_branch_id=branch_id,
        to_branch_id=branch_id,
        transfer_type="store_to_warehouse",
        unit_label="mixed" if is_components else body.unit_label,
        unit_count=0 if is_components else body.unit_count,
        pieces_per_unit=1 if is_components else body.pieces_per_unit,
        quantity_pieces=quantity_pieces,
        is_half_partial=False if is_components else body.is_half_partial,
        remainder_quantity=None if is_components else body.remainder_quantity,
        remainder_unit_label=None if is_components else body.remainder_unit_label,
        quantity_pieces_override=None if is_components else body.quantity_pieces_override,
        cartons_count=body.cartons_count if is_components else None,
        cartons_pieces_per_unit=body.cartons_pieces_per_unit if is_components else None,
        cartons_half=bool(body.cartons_half) if is_components else False,
        dozens_count=body.dozens_count if is_components else None,
        dozens_pieces_per_unit=body.dozens_pieces_per_unit if is_components else None,
        dozens_half=bool(body.dozens_half) if is_components else False,
        pieces_count=body.pieces_count if is_components else None,
        invoice_line_item_id=body.invoice_line_item_id,
        notes=body.notes,
        created_by=current_user.id,
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    return transfer


@router.post("/pull-to-store", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED)
async def pull_to_store(
    branch_id: uuid.UUID,
    body: WarehouseMoveCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockTransfer:
    """This branch's own warehouse -> its shop floor (restocking from
    backstock).
    """
    if current_user.branch_id and branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only pull stock from your own branch's warehouse.",
        )
    variant = await session.get(Product, body.product_id)
    if variant is None or variant.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product variant not found")
    if await session.get(Branch, branch_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")

    quantity_pieces = body.unit_count * body.pieces_per_unit
    warehouse_row = await _get_or_create_warehouse_row(session, branch_id, body.product_id)
    if warehouse_row.quantity_pieces < quantity_pieces:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Not enough stock in the warehouse to pull {quantity_pieces} unit(s) -- "
                f"only {warehouse_row.quantity_pieces} available"
            ),
        )
    warehouse_row.quantity_pieces -= quantity_pieces
    await _create_store_batch(session, branch_id, variant, quantity_pieces, body.unit_label)

    transfer = StockTransfer(
        product_id=body.product_id,
        from_branch_id=branch_id,
        to_branch_id=branch_id,
        transfer_type="warehouse_to_store",
        unit_label=body.unit_label,
        unit_count=body.unit_count,
        pieces_per_unit=body.pieces_per_unit,
        quantity_pieces=quantity_pieces,
        notes=body.notes,
        created_by=current_user.id,
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    return transfer


@router.post("/transfer-to-branch", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED)
async def transfer_warehouse_to_branch(
    branch_id: uuid.UUID,
    body: WarehouseTransferToBranchCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockTransfer:
    """This branch's warehouse -> a *different* branch's shop floor
    directly. Spec: "there are possibilities that they will need to
    push an item that is already existing on a particular warehouse
    to another store in different branch."
    """
    if current_user.branch_id and branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only send stock out of your own branch's warehouse.",
        )
    if branch_id == body.to_branch_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Source and destination branch must differ")
    variant = await session.get(Product, body.product_id)
    if variant is None or variant.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product variant not found")
    for bid in (branch_id, body.to_branch_id):
        if await session.get(Branch, bid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Branch {bid} not found")

    quantity_pieces = body.unit_count * body.pieces_per_unit
    warehouse_row = await _get_or_create_warehouse_row(session, branch_id, body.product_id)
    if warehouse_row.quantity_pieces < quantity_pieces:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Not enough stock in the warehouse to send {quantity_pieces} unit(s) -- "
                f"only {warehouse_row.quantity_pieces} available"
            ),
        )
    warehouse_row.quantity_pieces -= quantity_pieces
    await _create_store_batch(session, body.to_branch_id, variant, quantity_pieces, body.unit_label)

    transfer = StockTransfer(
        product_id=body.product_id,
        from_branch_id=branch_id,
        to_branch_id=body.to_branch_id,
        transfer_type="warehouse_to_branch",
        unit_label=body.unit_label,
        unit_count=body.unit_count,
        pieces_per_unit=body.pieces_per_unit,
        quantity_pieces=quantity_pieces,
        notes=body.notes,
        created_by=current_user.id,
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    return transfer


def _wh_halves_text(halves, one, many):
    """Halves -> "2 cartons", "1 carton", "2\u00bd cartons", "\u00bd carton"."""
    if halves <= 0:
        return None
    whole, half = divmod(halves, 2)
    if whole == 0:
        return f"\u00bd {one}"
    number = f"{whole}\u00bd" if half else f"{whole}"
    return f"{number} {one if (whole == 1 and not half) else many}"


def _wh_remainder_only_components(t):
    """(carton_halves, dozen_halves, pieces) from a legacy row's remainder
    alone -- used for rows whose main unit is a freehand label (rolls...)."""
    rl = t.remainder_unit_label
    rq = t.remainder_quantity or 0
    ch = 1 if rl == "half_carton" else 0
    dh = 1 if rl == "half_dozen" else (rq * 2 if rl == "dozens" else 0)
    pcs = rq if rl == "pieces" else 0
    return ch, dh, pcs


def _wh_breakdown_text(acc):
    parts = [
        _wh_halves_text(acc["ch"], "carton", "cartons"),
        _wh_halves_text(acc["dh"], "dozen", "dozens"),
    ]
    if acc["pcs"] > 0:
        parts.append(f"{acc['pcs']} piece" + ("" if acc["pcs"] == 1 else "s"))
    for label, n in sorted(acc["other"].items()):
        if n > 0:
            parts.append(f"{n} {label}" if n == 1 or label.endswith("s") else f"{n} {label}s")
    return " + ".join(p for p in parts if p) or None


@router.get("/stock", response_model=list[WarehouseStockOut])
async def list_warehouse_stock(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[WarehouseStockOut]:
    """What's currently sitting in a branch's warehouse -- spec:
    warehouse stock "should also have its own section to store that
    data."
    """
    from infrastructure.db.models import Category, Product

    stmt = select(WarehouseStock).where(WarehouseStock.quantity_pieces > 0)
    if branch_id:
        stmt = stmt.where(WarehouseStock.branch_id == branch_id)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if not rows:
        return []

    product_ids = {r.product_id for r in rows}
    products_result = await session.execute(
        select(Product, Category)
        .outerjoin(Category, Product.category_id == Category.id)
        .where(Product.id.in_(product_ids))
    )
    lookup = {p.id: (p.name, c.name if c else None) for p, c in products_result.all()}

    push_result = await session.execute(
        select(StockTransfer).where(
            StockTransfer.transfer_type == "store_to_warehouse",
            StockTransfer.product_id.in_(product_ids),
        )
    )
    # (branch, product) -> what was actually pushed: cartons/dozens in
    # halves (so a ticked half-pack is exact), pieces plain, plus any
    # freehand unit labels (e.g. "rolls") that are not carton/dozen/piece.
    totals: dict[tuple[uuid.UUID, uuid.UUID], dict] = {}
    for t in push_result.scalars().all():
        acc = totals.setdefault(
            (t.to_branch_id, t.product_id),
            {"ch": 0, "dh": 0, "pcs": 0, "other": {}},
        )
        if t.unit_label == "mixed":
            ch, dh, pcs = _wh_transfer_components(t)
        elif _wh_norm_unit(t.unit_label, None) is None:
            acc["other"][t.unit_label] = acc["other"].get(t.unit_label, 0) + (t.unit_count or 0)
            ch, dh, pcs = _wh_remainder_only_components(t)
        else:
            ch, dh, pcs = _wh_transfer_components(t)
        acc["ch"] += ch
        acc["dh"] += dh
        acc["pcs"] += pcs

    rows.sort(key=lambda r: (lookup.get(r.product_id, (None, None))[0] or "").casefold())

    return [
        WarehouseStockOut(
            id=r.id,
            branch_id=r.branch_id,
            product_id=r.product_id,
            product_name=lookup.get(r.product_id, (None, None))[0],
            name=lookup.get(r.product_id, (None, None))[0],
            category_name=lookup.get(r.product_id, (None, None))[1],
            quantity_pieces=r.quantity_pieces,
            unit_breakdown=[],
            breakdown_text=_wh_breakdown_text(totals[(r.branch_id, r.product_id)])
            if (r.branch_id, r.product_id) in totals
            else None,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/transfers", response_model=list[StockTransferOut])
async def list_stock_transfers(
    branch_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    transfer_type: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[StockTransferOut]:
    stmt = select(StockTransfer).order_by(StockTransfer.created_at.desc())
    if branch_id:
        stmt = stmt.where(
            (StockTransfer.from_branch_id == branch_id) | (StockTransfer.to_branch_id == branch_id)
        )
    if product_id:
        stmt = stmt.where(StockTransfer.product_id == product_id)
    if transfer_type:
        stmt = stmt.where(StockTransfer.transfer_type == transfer_type)
    result = await session.execute(stmt)
    transfers = list(result.scalars().all())
    if not transfers:
        return []

    creator_ids = {t.created_by for t in transfers if t.created_by}
    creator_names: dict[uuid.UUID, str] = {}
    if creator_ids:
        users_result = await session.execute(select(User).where(User.id.in_(creator_ids)))
        creator_names = {u.id: u.full_name for u in users_result.scalars().all()}

    return [
        StockTransferOut(
            id=t.id,
            product_id=t.product_id,
            from_branch_id=t.from_branch_id,
            to_branch_id=t.to_branch_id,
            transfer_type=t.transfer_type,
            unit_label=t.unit_label,
            unit_count=t.unit_count,
            pieces_per_unit=t.pieces_per_unit,
            quantity_pieces=t.quantity_pieces,
            quantity_pieces_override=t.quantity_pieces_override,
            is_half_partial=t.is_half_partial,
            remainder_quantity=t.remainder_quantity,
            remainder_unit_label=t.remainder_unit_label,
            cartons_count=t.cartons_count,
            cartons_pieces_per_unit=t.cartons_pieces_per_unit,
            cartons_half=t.cartons_half,
            dozens_count=t.dozens_count,
            dozens_pieces_per_unit=t.dozens_pieces_per_unit,
            dozens_half=t.dozens_half,
            pieces_count=t.pieces_count,
            receipt_id=t.receipt_id,
            notes=t.notes,
            total_cost_naira=Money(t.total_cost_kobo).naira if t.total_cost_kobo is not None else None,
            payment_method=t.payment_method,
            created_by=t.created_by,
            created_by_name=creator_names.get(t.created_by),
            created_at=t.created_at,
        )
        for t in transfers
    ]
