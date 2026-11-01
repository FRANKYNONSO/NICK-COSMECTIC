import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    GoodsReceipt,
    GoodsReceiptLine,
    IntakeReceipt,
    Product,
    ProductShelfSetting,
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
_WH_BAG_WORDS = {"bag", "bags"}
_WH_PIECE_WORDS = {"piece", "pieces", "pcs", "pc"}


def _wh_norm_unit(label, default="carton"):
    key = (label or "").strip().lower()
    if key in _WH_CARTON_WORDS:
        return "carton"
    if key in _WH_DOZEN_WORDS:
        return "dozen"
    if key in _WH_BAG_WORDS:
        return "bag"
    if key in _WH_PIECE_WORDS:
        return "piece"
    return default


def _wh_fmt_halves(halves):
    return f"{halves / 2:g}"


def _wh_is_component_shape(x):
    return (
        getattr(x, "cartons_count", None) is not None
        or getattr(x, "dozens_count", None) is not None
        or getattr(x, "bags_count", None) is not None
        or getattr(x, "pieces_count", None) is not None
        or bool(getattr(x, "cartons_half", False))
        or bool(getattr(x, "dozens_half", False))
        or bool(getattr(x, "bags_half", False))
    )


def _wh_component_total_pieces(x):
    cartons_ppu = x.cartons_pieces_per_unit or 0
    dozens_ppu = x.dozens_pieces_per_unit or 0
    cartons = (x.cartons_count or 0) * cartons_ppu + (cartons_ppu // 2 if x.cartons_half else 0)
    dozens = (x.dozens_count or 0) * dozens_ppu + (dozens_ppu // 2 if x.dozens_half else 0)
    bags_ppu = getattr(x, "bags_pieces_per_unit", None) or 0
    bags = (getattr(x, "bags_count", None) or 0) * bags_ppu + (
        bags_ppu // 2 if getattr(x, "bags_half", False) else 0
    )
    return cartons + dozens + bags + (x.pieces_count or 0)


def _wh_validate_component_body(body):
    if body.quantity_pieces_override is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A typed total is not used with cartons/dozens/bags/pieces entry - enter the pieces directly instead.",
        )
    if ((body.cartons_count or 0) > 0 or body.cartons_half) and not body.cartons_pieces_per_unit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter pieces per carton for the cartons being pushed.")
    if ((body.dozens_count or 0) > 0 or body.dozens_half) and not body.dozens_pieces_per_unit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter pieces per dozen for the dozens being pushed.")
    if ((getattr(body, "bags_count", None) or 0) > 0 or getattr(body, "bags_half", False)) and not getattr(
        body, "bags_pieces_per_unit", None
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter pieces per bag for the bags being pushed.")
    if _wh_component_total_pieces(body) <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter a quantity greater than 0.")


def _wh_components_from_invoice_line(line_item):
    """(carton_halves, dozen_halves, bag_halves, pieces) the invoice line holds."""
    ch = dh = bh = pcs = 0
    unit = _wh_norm_unit(line_item.unit_label)
    q = line_item.quantity or 0
    if unit == "carton":
        ch += q * 2
    elif unit == "dozen":
        dh += q * 2
    elif unit == "bag":
        bh += q * 2
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
    return ch, dh, bh, pcs


def _wh_transfer_components(t, default_unit="carton"):
    """(carton_halves, dozen_halves, bag_halves, pieces) for a StockTransfer
    row or a push request body, in either the component or the legacy shape."""
    if getattr(t, "unit_label", None) == "mixed" or _wh_is_component_shape(t):
        return (
            (t.cartons_count or 0) * 2 + (1 if t.cartons_half else 0),
            (t.dozens_count or 0) * 2 + (1 if t.dozens_half else 0),
            (getattr(t, "bags_count", None) or 0) * 2 + (1 if getattr(t, "bags_half", False) else 0),
            t.pieces_count or 0,
        )
    ch = dh = bh = pcs = 0
    unit = _wh_norm_unit(t.unit_label, default_unit)
    n = t.unit_count or 0
    if unit == "carton":
        ch += n * 2
    elif unit == "dozen":
        dh += n * 2
    elif unit == "bag":
        bh += n * 2
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
        elif unit == "bag":
            bh += 1
    return ch, dh, bh, pcs


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
        if invoice.trash_state is not None:  # TRASH1
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That invoice is in the Trash. Restore it first.")
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
        .order_by(StockBatch.received_at, StockBatch.id)
    )
    if not dry_run:
        # Real deduction: lock the batch rows until commit so two
        # transfers/sales can't both take the same units. (The dry-run
        # cost preview changes nothing, so it doesn't lock.)
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
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

    from api.v1.transfer_pricing import branch_selling_price_kobo, default_transfer_price

    # XFER1-TRANSFER-AS-SALE: a branch-to-branch transfer is a sale from the
    # sending branch to the receiving one. Default price = the item's selling
    # price, or its wholesale price at/above the wholesale quantity; a price
    # typed by the manager overrides it.
    selling_kobo = await branch_selling_price_kobo(session, body.from_branch_id, variant)
    default_price_kobo, default_basis = default_transfer_price(
        selling_kobo,
        variant.wholesale_price_kobo,
        variant.wholesale_min_quantity,
        quantity_pieces,
    )
    if body.unit_price_naira is None:
        unit_price_kobo, price_basis = default_price_kobo, default_basis
    else:
        unit_price_kobo = Money.from_naira(body.unit_price_naira).kobo
        price_basis = default_basis if unit_price_kobo == default_price_kobo else "custom"
    total_price_kobo = unit_price_kobo * quantity_pieces

    from api.v1.transfer_pricing import (
        branch_shelf_limit,
        plan_transfer_split,
        shelf_pieces,
        transfer_split_text,
    )

    # XFER2-SHELF-OR-WAREHOUSE: lock both warehouse rows in a fixed order (same
    # rule as transfer_warehouse_to_branch), then take from the chosen place
    # first and the other one for any shortfall.
    if str(body.to_branch_id) < str(body.from_branch_id):
        dest_wh_row = await _lock_branch_warehouse_row(session, body.to_branch_id, body.product_id)
        src_wh_row = await _get_or_create_warehouse_row(session, body.from_branch_id, body.product_id)
    else:
        src_wh_row = await _get_or_create_warehouse_row(session, body.from_branch_id, body.product_id)
        dest_wh_row = await _lock_branch_warehouse_row(session, body.to_branch_id, body.product_id)
    shelf_available = await shelf_pieces(session, body.from_branch_id, body.product_id)
    warehouse_available = int(src_wh_row.quantity_pieces)
    plan = plan_transfer_split(
        quantity_pieces,
        body.take_from,
        shelf_available,
        warehouse_available,
        await shelf_pieces(session, body.to_branch_id, body.product_id),
        await branch_shelf_limit(session, body.to_branch_id, body.product_id),
    )
    if plan is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Not enough stock at the source branch to transfer {quantity_pieces} piece(s) -- "
                f"only {shelf_available + warehouse_available} available in total "
                f"(shelf {shelf_available}, warehouse {warehouse_available})."
            ),
        )
    split_text = transfer_split_text(plan)
    shelf_cost_kobo = 0
    if plan["from_shelf"] > 0:
        shelf_avg_kobo = await _fifo_deduct(session, body.product_id, body.from_branch_id, plan["from_shelf"])
        shelf_cost_kobo = shelf_avg_kobo * plan["from_shelf"]
    src_wh_row.quantity_pieces -= plan["from_warehouse"]
    avg_unit_cost_kobo = (
        shelf_cost_kobo + int(variant.cost_price_kobo or 0) * plan["from_warehouse"]
    ) // quantity_pieces
    # XFER1: total_cost_kobo is now only the sending branch's own cost
    # (the quantity-weighted average FIFO cost basis), kept for profit
    # tracking. The receiving branch is charged unit_price_kobo.
    total_cost_kobo = avg_unit_cost_kobo * quantity_pieces

    new_batch = StockBatch(
        product_id=body.product_id,
        branch_id=body.to_branch_id,
        batch_number=f"transfer-{body.unit_label}",
        quantity_received=plan["to_shelf"],
        quantity_remaining=plan["to_shelf"],
        unit_cost_kobo=unit_price_kobo,  # XFER1: the receiving branch's cost is what it paid
    )
    if plan["to_shelf"] > 0:
        session.add(new_batch)
    dest_wh_row.quantity_pieces += plan["to_warehouse"]

    from datetime import datetime, timezone
    from infrastructure.db.models import Sale as ORMSale
    from infrastructure.db.models import SaleItem as ORMSaleItem
    from infrastructure.db.models import SalePayment as ORMSalePayment

    from_branch = await session.get(Branch, body.from_branch_id)
    to_branch = await session.get(Branch, body.to_branch_id)
    route_note = f"Branch transfer: {from_branch.name} \u2192 {to_branch.name}"
    if body.notes:
        route_note += f" ({body.notes})"
    route_note += f" | {split_text}"
    transfer_sale = ORMSale(
        branch_id=body.from_branch_id,
        customer_id=None,
        served_by=current_user.id,
        shift_id=None,
        channel="transfer",
        status="completed",
        subtotal_kobo=total_price_kobo,
        discount_kobo=0,
        total_kobo=total_price_kobo,
        payment_method=body.payment_method,
        local_ref_id=f"transfer-{uuid.uuid4()}",
        notes=route_note,
    )
    session.add(transfer_sale)
    await session.flush()  # gives the sale its id
    session.add(
        ORMSaleItem(
            sale_id=transfer_sale.id,
            product_id=body.product_id,
            stock_batch_id=None,
            quantity=quantity_pieces,
            unit_price_kobo=unit_price_kobo,
            line_total_kobo=total_price_kobo,
        )
    )
    session.add(
        ORMSalePayment(
            sale_id=transfer_sale.id,
            method=body.payment_method,
            amount_kobo=total_price_kobo,
            recorded_by=current_user.id,
            created_at=datetime.now(timezone.utc),
        )
    )

    # XFER5: the receiving branch gets an invoice for what it bought. Saved in
    # the same transaction as the sale and the transfer: all or nothing.
    from datetime import timedelta

    product_row = await session.get(Product, body.product_id)
    arrive_parts = []
    if plan["to_shelf"]:
        arrive_parts.append(f"shelf {plan['to_shelf']}")
    if plan["to_warehouse"]:
        arrive_parts.append(f"warehouse {plan['to_warehouse']}")
    arrive_text = " + ".join(arrive_parts) or "shelf 0"
    invoice_payment = {"transfer": "bank_transfer"}.get(
        body.payment_method, body.payment_method or "not_specified"
    )
    transfer_invoice = PurchaseInvoice(
        branch_id=body.to_branch_id,
        status="completed",
        source="branch_transfer",
        supplier_name=f"{from_branch.name} (branch transfer)",
        invoice_date=(datetime.now(timezone.utc) + timedelta(hours=1)).date(),
        amount_kobo=total_price_kobo,
        amount_paid_kobo=total_price_kobo,
        description=f"Branch transfer from {from_branch.name} to {to_branch.name}. Arrives as {arrive_text}.",
        invoice_number="TRF-" + str(transfer_sale.id)[:8].upper(),
        payment_method=invoice_payment,
        created_by=current_user.id,
    )
    session.add(transfer_invoice)
    await session.flush()  # gives the invoice its id
    session.add(
        PurchaseInvoiceLineItem(
            invoice_id=transfer_invoice.id,
            position=0,
            item_name=product_row.name if product_row is not None else "Transferred item",
            product_id=None,
            quantity=quantity_pieces,
            unit_label="pieces",
            rate_kobo=unit_price_kobo,
            line_total_kobo=total_price_kobo,
        )
    )

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
        notes=((body.notes + " | ") if body.notes else "") + split_text,
        total_cost_kobo=total_cost_kobo,
        payment_method=body.payment_method,
        unit_price_kobo=unit_price_kobo,
        price_basis=price_basis,
        sale_id=transfer_sale.id,
        invoice_id=transfer_invoice.id,
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
        unit_price_naira=Money(transfer.unit_price_kobo).naira if transfer.unit_price_kobo is not None else None,
        total_price_naira=(
            Money(transfer.unit_price_kobo * transfer.quantity_pieces).naira
            if transfer.unit_price_kobo is not None else None
        ),
        price_basis=transfer.price_basis,
        sale_id=transfer.sale_id,
        from_shelf_pieces=plan["from_shelf"],
        from_warehouse_pieces=plan["from_warehouse"],
        to_shelf_pieces=plan["to_shelf"],
        to_warehouse_pieces=plan["to_warehouse"],
        created_by=transfer.created_by,
        created_by_name=current_user.full_name,
        created_at=transfer.created_at,
    )


@router.get("/transfer-split-preview")
async def preview_transfer_split(
    product_id: uuid.UUID,
    from_branch_id: uuid.UUID,
    to_branch_id: uuid.UUID,
    quantity_pieces: int,
    take_from: str = "shelf",
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_inventory")),
) -> dict:
    """XFER2-SHELF-OR-WAREHOUSE: live figures for the transfer form and the
    confirm screen -- what the sending branch holds (shelf / warehouse), where
    the pieces would be taken from, and how they would land at the receiving
    branch. Read-only: nothing is locked or changed."""
    from api.v1.transfer_pricing import (
        branch_shelf_limit,
        plan_transfer_split,
        shelf_pieces,
        transfer_split_text,
        warehouse_pieces,
    )

    take = "warehouse" if take_from in ("warehouse", "both") else "shelf"
    shelf = await shelf_pieces(session, from_branch_id, product_id)
    warehouse = await warehouse_pieces(session, from_branch_id, product_id)
    limit = await branch_shelf_limit(session, to_branch_id, product_id)
    dest_now = await shelf_pieces(session, to_branch_id, product_id)
    qty = int(quantity_pieces)
    plan = plan_transfer_split(qty, take, shelf, warehouse, dest_now, limit)
    out = {
        "ok": plan is not None,
        "message": None,
        "shelf_available": shelf,
        "warehouse_available": warehouse,
        "total_available": shelf + warehouse,
        "dest_shelf_limit": limit,
        "dest_shelf_now": dest_now,
        "take_from": take,
    }
    if plan is None:
        out["message"] = (
            "Enter a quantity greater than 0."
            if qty <= 0
            else (
                f"Not enough stock at the source branch to transfer {qty} piece(s) -- "
                f"only {shelf + warehouse} available in total (shelf {shelf}, warehouse {warehouse})."
            )
        )
        return out
    out.update(plan)
    out["text"] = transfer_split_text(plan)
    return out


@router.get("/transfer-price-preview")
async def preview_transfer_price(
    product_id: uuid.UUID,
    quantity_pieces: int,
    from_branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_inventory")),
) -> dict:
    """XFER1-TRANSFER-AS-SALE: the default price per piece for a branch-to-
    branch transfer (selling price, or wholesale price at/above the wholesale
    quantity). Price only -- no stock check, so the form can show it live."""
    from api.v1.transfer_pricing import branch_selling_price_kobo, default_transfer_price

    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    qty = max(int(quantity_pieces), 1)
    selling_kobo = (
        await branch_selling_price_kobo(session, from_branch_id, product)
        if from_branch_id is not None else int(product.selling_price_kobo or 0)
    )
    price_kobo, basis = default_transfer_price(
        selling_kobo, product.wholesale_price_kobo, product.wholesale_min_quantity, qty
    )
    return {
        "selling_price_naira": Money(selling_kobo).naira,
        "wholesale_price_naira": (
            Money(product.wholesale_price_kobo).naira if product.wholesale_price_kobo is not None else None
        ),
        "wholesale_min_quantity": product.wholesale_min_quantity,
        "default_unit_price_naira": Money(price_kobo).naira,
        "default_price_basis": basis,
        "default_total_price_naira": Money(price_kobo * qty).naira,
    }


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
    # FOR UPDATE: the caller reads quantity_pieces, checks it, then
    # subtracts/adds -- lock the row so two pulls/pushes at the same
    # moment can't both act on the same starting number.
    result = await session.execute(
        select(WarehouseStock)
        .where(WarehouseStock.branch_id == branch_id, WarehouseStock.product_id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = result.scalar_one_or_none()
    if row is None:
        # INSERT ... ON CONFLICT DO NOTHING so two simultaneous first-ever
        # requests cannot hit the unique (branch_id, product_id) constraint
        # with an IntegrityError; then read the row back under the same lock.
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
        row = result.scalar_one()
    return row


async def _receipt_cost_since(session, branch_id, product_id):
    """Per-piece cost and time of the most recent POSTED goods receipt that sent
    units of this product to this branch's warehouse, or None. The cost is the
    quantity-weighted average of that receipt's warehouse lines for the product.
    Read-only."""
    result = await session.execute(
        select(StockTransfer.goods_receipt_id, StockTransfer.created_at)
        .join(GoodsReceipt, GoodsReceipt.id == StockTransfer.goods_receipt_id)
        .where(
            StockTransfer.transfer_type == "receipt_to_warehouse",
            StockTransfer.product_id == product_id,
            StockTransfer.to_branch_id == branch_id,
            GoodsReceipt.status == "posted",
        )
        .order_by(StockTransfer.created_at.desc())
        .limit(1)
    )
    latest = result.first()
    if latest is None:
        return None
    receipt_id, at = latest
    lines_result = await session.execute(
        select(GoodsReceiptLine).where(
            GoodsReceiptLine.receipt_id == receipt_id,
            GoodsReceiptLine.product_id == product_id,
            GoodsReceiptLine.quantity_to_warehouse > 0,
            GoodsReceiptLine.unit_cost_kobo.is_not(None),
        )
    )
    rows = list(lines_result.scalars().all())
    qty = sum(l.quantity_to_warehouse for l in rows)
    if qty <= 0:
        return None
    total = sum(int(l.unit_cost_kobo) * l.quantity_to_warehouse for l in rows)
    cost = (total + qty // 2) // qty
    if cost <= 0:
        return None
    return cost, at


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
    push_cost = None
    push_at = None
    row = result.first()
    if row is not None:
        transfer, line_item = row
        if transfer.unit_label == "mixed":
            # Component-shape push: the invoice rate is per the line's
            # own unit, so divide by that unit's own pieces-per-unit.
            pieces_per_unit = {
                "carton": transfer.cartons_pieces_per_unit,
                "dozen": transfer.dozens_pieces_per_unit,
                "bag": transfer.bags_pieces_per_unit,
                "piece": 1,
            }.get(_wh_norm_unit(line_item.unit_label))
        else:
            pieces_per_unit = transfer.pieces_per_unit or 1
        per_piece = line_item.rate_kobo // pieces_per_unit if pieces_per_unit else 0
        if per_piece > 0:
            push_cost = per_piece
            push_at = transfer.created_at
    receipt_cost = await _receipt_cost_since(session, branch_id, variant.id)
    if receipt_cost is not None:
        r_cost, r_at = receipt_cost
        if push_cost is None or push_at is None or (r_at is not None and r_at > push_at):
            return r_cost
    if push_cost is not None:
        return push_cost
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
    """(carton_halves, dozen_halves, bag_halves, pieces) already pushed against
    this invoice line, across any branch, old and new shape alike."""
    result = await session.execute(
        select(StockTransfer).where(
            StockTransfer.invoice_line_item_id == line_item.id,
            StockTransfer.transfer_type == "store_to_warehouse",
        )
    )
    default_unit = _wh_norm_unit(line_item.unit_label)
    ch = dh = bh = pcs = 0
    for t in result.scalars().all():
        a, b, c, d = _wh_transfer_components(t, default_unit)
        ch += a
        dh += b
        bh += c
        pcs += d
    return ch, dh, bh, pcs


# PATCH5D-PUSH-GUARD: pieces already received through POSTED Receive Goods receipts
# against this invoice line. The push side must subtract them too.
async def _wh_received_pieces(session: AsyncSession, line_item_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(GoodsReceiptLine.quantity_received), 0))
        .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
        .where(
            GoodsReceiptLine.invoice_line_item_id == line_item_id,
            GoodsReceipt.status == "posted",
        )
    )
    return int(total or 0)


async def _wh_pushed_pieces(session: AsyncSession, line_item_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(StockTransfer.quantity_pieces), 0)).where(
            StockTransfer.invoice_line_item_id == line_item_id,
            StockTransfer.transfer_type == "store_to_warehouse",
        )
    )
    return int(total or 0)


# PATCH6C-LEFT-FIGURE: figures for the push screen. (received, outstanding) in pieces.
# Both are None when the line has no known total or nothing was received.
async def _wh_receipt_figures(session: AsyncSession, line_item) -> tuple[int | None, int | None]:
    if line_item.total_pieces is None:
        return None, None
    received = await _wh_received_pieces(session, line_item.id)
    if received <= 0:
        return None, None
    pushed = await _wh_pushed_pieces(session, line_item.id)
    return received, max(line_item.total_pieces - received - pushed, 0)


async def _wh_line_component_fields(session: AsyncSession, line_item) -> dict:
    line_ch, line_dh, line_bh, line_pcs = _wh_components_from_invoice_line(line_item)
    done_ch, done_dh, done_bh, done_pcs = await _wh_pushed_components(session, line_item)
    return {
        "cartons_total_halves": line_ch,
        "cartons_remaining_halves": max(line_ch - done_ch, 0),
        "dozens_total_halves": line_dh,
        "dozens_remaining_halves": max(line_dh - done_dh, 0),
        "bags_total_halves": line_bh,
        "bags_remaining_halves": max(line_bh - done_bh, 0),
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
        .where(PurchaseInvoice.source.is_distinct_from("branch_transfer"))
        .where(PurchaseInvoice.trash_state.is_(None))  # TRASH1
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

    # PATCH5C-WAREHOUSE-PACK-PREFILL: product pack defaults, loaded once for all rows.
    pack_defaults: dict = {}
    wanted_ids = {li.product_id for li, _ in rows if li.product_id is not None}
    wanted_ids.update(name_to_product_id.values())
    if wanted_ids:
        pack_rows = await session.execute(
            select(
                Product.id,
                Product.default_pieces_per_carton,
                Product.default_pieces_per_bag,
                Product.default_pieces_per_roll,
            ).where(Product.id.in_(wanted_ids))
        )
        for pid, d_carton, d_bag, d_roll in pack_rows.all():
            pack_defaults[pid] = (d_carton, d_bag, d_roll)

    out: list[PushableInvoiceLineItemOut] = []
    for line_item, invoice in rows:
        resolved_product_id = line_item.product_id or name_to_product_id.get(line_item.item_name.strip().lower())
        already_pushed = await _pushed_so_far(session, line_item.id)
        remaining = line_item.quantity - already_pushed
        component_fields = await _wh_line_component_fields(session, line_item)
        capped_left = (
            component_fields["cartons_remaining_halves"]
            + component_fields["dozens_remaining_halves"]
            + component_fields["bags_remaining_halves"]
            + component_fields["pieces_remaining"]
        )
        if capped_left <= 0:
            continue
        # PATCH5D-PUSH-GUARD: hide a line that Receive Goods already used up
        if line_item.total_pieces is not None:
            received_pcs = await _wh_received_pieces(session, line_item.id)
            if received_pcs > 0:
                pushed_pcs = await _wh_pushed_pieces(session, line_item.id)
                if line_item.total_pieces - received_pcs - pushed_pcs <= 0:
                    continue
        # PATCH6C-LEFT-FIGURE: received and outstanding pieces for the screen (None when not known)
        receipt_received, receipt_outstanding = await _wh_receipt_figures(session, line_item)
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
                default_pieces_per_carton=pack_defaults.get(resolved_product_id, (None, None, None))[0],
                default_pieces_per_bag=pack_defaults.get(resolved_product_id, (None, None, None))[1],
                default_pieces_per_roll=pack_defaults.get(resolved_product_id, (None, None, None))[2],
                received_pieces=receipt_received,
                outstanding_pieces=receipt_outstanding,
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
    if invoice is not None and invoice.trash_state is not None:  # TRASH1
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That invoice is in the Trash. Restore it first.")
    if invoice is not None and invoice.source == "branch_transfer":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This invoice was made by a branch transfer. Its goods are already in stock and cannot be pushed to the warehouse.",
        )
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
    receipt_received, receipt_outstanding = await _wh_receipt_figures(session, line_item)
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
        default_pieces_per_carton=product.default_pieces_per_carton,
        default_pieces_per_bag=product.default_pieces_per_bag,
        default_pieces_per_roll=product.default_pieces_per_roll,
        received_pieces=receipt_received,
        outstanding_pieces=receipt_outstanding,
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
                detail="Send cartons_count / dozens_count / bags_count / pieces_count, or unit_label + unit_count.",
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
        if invoice is not None and invoice.trash_state is not None:  # TRASH1
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That invoice is in the Trash. Restore it first.")
        if invoice is not None and invoice.source == "branch_transfer":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="This invoice was made by a branch transfer. Its goods are already in stock and cannot be pushed to the warehouse.",
            )
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
        line_ch, line_dh, line_bh, line_pcs = _wh_components_from_invoice_line(line_item)
        done_ch, done_dh, done_bh, done_pcs = await _wh_pushed_components(session, line_item)
        want_ch, want_dh, want_bh, want_pcs = _wh_transfer_components(body, _wh_norm_unit(line_item.unit_label))
        left_ch = max(line_ch - done_ch, 0)
        left_dh = max(line_dh - done_dh, 0)
        left_bh = max(line_bh - done_bh, 0)
        left_pcs = max(line_pcs - done_pcs, 0)
        problems: list[str] = []
        if want_ch > left_ch:
            problems.append(f"{_wh_fmt_halves(want_ch)} carton(s) requested, {_wh_fmt_halves(left_ch)} left on the invoice line")
        if want_dh > left_dh:
            problems.append(f"{_wh_fmt_halves(want_dh)} dozen(s) requested, {_wh_fmt_halves(left_dh)} left on the invoice line")
        if want_bh > left_bh:
            problems.append(f"{_wh_fmt_halves(want_bh)} bag(s) requested, {_wh_fmt_halves(left_bh)} left on the invoice line")
        if line_pcs > 0 and want_pcs > left_pcs:
            problems.append(f"{want_pcs} piece(s) requested, {left_pcs} left on the invoice line")
        if problems:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Cannot push " + line_item.item_name + ": " + "; ".join(problems) + ".",
            )
        # PATCH5D-PUSH-GUARD: goods already received in Receive Goods count against the line too.
        # Only when the line has received goods and a known total; otherwise nothing changes.
        received_pcs = await _wh_received_pieces(session, line_item.id)
        if received_pcs > 0 and line_item.total_pieces is not None:
            pushed_pcs = await _wh_pushed_pieces(session, line_item.id)
            outstanding = max(line_item.total_pieces - received_pcs - pushed_pcs, 0)
            if quantity_pieces > outstanding:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Cannot push " + line_item.item_name + ": " + str(quantity_pieces)
                        + " piece(s) requested, only " + str(outstanding)
                        + " still outstanding on the invoice line (" + str(line_item.total_pieces)
                        + " ordered, " + str(received_pcs) + " already received in Receive Goods, "
                        + str(pushed_pcs) + " already pushed)."
                    ),
                )

    # PUSH-NOTE-FIX (patch 24a): a push with no typed note gets an
    # automatic note saying which invoice the goods came from.
    _push_note = (body.notes or "").strip() or None
    if _push_note is None:
        if body.invoice_line_item_id is not None:
            _text = "Pushed from invoice"
            _sup = ((invoice.supplier_name or "").strip()) if invoice is not None else ""
            if _sup:
                _text += ": " + _sup
            if invoice is not None and invoice.invoice_date is not None:
                _d = invoice.invoice_date
                _text += (", dated " if _sup else " dated ") + str(_d.day) + " " + _d.strftime("%b %Y")
            _num = ((invoice.invoice_number or "").strip()) if invoice is not None else ""
            if _num:
                _text += " (No. " + _num + ")"
            _push_note = _text
        else:
            _push_note = "Pushed from invoice photo (no invoice line linked)"

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
        bags_count=body.bags_count if is_components else None,
        bags_pieces_per_unit=body.bags_pieces_per_unit if is_components else None,
        bags_half=bool(body.bags_half) if is_components else False,
        pieces_count=body.pieces_count if is_components else None,
        invoice_line_item_id=body.invoice_line_item_id,
        notes=_push_note,
        created_by=current_user.id,
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    return transfer


async def _enforce_shelf_limit(
    session: AsyncSession,
    branch_id: uuid.UUID,
    product: Product,
    quantity_pieces: int,
    current_user: User | None = None,
    override: bool = False,
    override_reason: str | None = None,
) -> None:
    """Rejects a shelf addition that would take this branch's shop-floor stock
    for the product over its configured maximum. Only enforced where a
    product_shelf_settings row exists. Call it while the warehouse_stock row
    is locked: that lock serialises restocks and goods receipts.
    """
    setting = (
        await session.execute(
            select(ProductShelfSetting)
            .where(
                ProductShelfSetting.branch_id == branch_id,
                ProductShelfSetting.product_id == product.id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if setting is None:
        return
    shelf_now = int(
        await session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
                StockBatch.branch_id == branch_id,
                StockBatch.product_id == product.id,
                StockBatch.quantity_remaining > 0,
            )
        )
        or 0
    )
    remaining = setting.max_shelf_quantity - shelf_now
    if quantity_pieces > max(remaining, 0):
        if remaining <= 0:
            detail = (
                f"The shelf for {product.name} is already at or over its limit of "
                f"{setting.max_shelf_quantity} (currently {shelf_now}), so no more units can go on the shelf."
            )
        else:
            detail = (
                f"Only {remaining} unit(s) of shelf capacity remain for {product.name} "
                f"(limit {setting.max_shelf_quantity}, currently {shelf_now}) but "
                f"{quantity_pieces} were requested. Replenish at most {remaining}; the rest stays in the warehouse."
            )
        if override:
            if current_user is None or current_user.role not in ("super_admin", "branch_manager"):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail="Only a branch manager or super admin can override a shelf limit.",
                )
            if current_user.branch_id and current_user.branch_id != branch_id:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail="You can only override shelf limits in your own branch.",
                )
            if not (override_reason or "").strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="A reason is required to override a shelf limit.",
                )
            from infrastructure.db.audit import log_audit_event as _log_override

            _log_override(
                session,
                user_id=current_user.id,
                action="shelf_limit_override",
                entity_type="product",
                entity_id=product.id,
                metadata={
                    "branch_id": str(branch_id),
                    "limit": setting.max_shelf_quantity,
                    "shelf_before": shelf_now,
                    "added": quantity_pieces,
                    "reason": (override_reason or "").strip(),
                },
            )
            return
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=detail)


@router.post("/pull-to-store", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED)
async def pull_to_store(
    branch_id: uuid.UUID,
    body: WarehouseMoveCreate,
    override_shelf_limit: bool = False,
    override_reason: str | None = None,
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
    # fix45: restocking from the warehouse may put more on the shelf than the shelf limit
    # (for example a big customer order). The saved limit is left unchanged.
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
        notes=body.notes or "Restock: moved from the warehouse to the shelf",  # fix52
        created_by=current_user.id,
    )
    session.add(transfer)
    await session.commit()
    await session.refresh(transfer)
    return transfer


async def _lock_branch_warehouse_row(
    session: AsyncSession, branch_id: uuid.UUID, product_id: uuid.UUID
) -> WarehouseStock:
    """Locks this branch's warehouse row for the product until commit,
    creating a zero row first if there is none. Used for the DESTINATION of a
    transfer so it is serialised against goods receipts and restocks there.
    """
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


@router.post("/transfer-to-branch", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED)
async def transfer_warehouse_to_branch(
    branch_id: uuid.UUID,
    body: WarehouseTransferToBranchCreate,
    override_shelf_limit: bool = False,
    override_reason: str | None = None,
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
    # Lock the source and destination warehouse rows in a fixed order (by branch
    # id): a transfer, a restock and a goods receipt can then never all fit under
    # the same remaining shelf capacity, and two opposite transfers cannot deadlock.
    if str(body.to_branch_id) < str(branch_id):
        await _lock_branch_warehouse_row(session, body.to_branch_id, body.product_id)
        warehouse_row = await _get_or_create_warehouse_row(session, branch_id, body.product_id)
    else:
        warehouse_row = await _get_or_create_warehouse_row(session, branch_id, body.product_id)
        await _lock_branch_warehouse_row(session, body.to_branch_id, body.product_id)
    if warehouse_row.quantity_pieces < quantity_pieces:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Not enough stock in the warehouse to send {quantity_pieces} unit(s) -- "
                f"only {warehouse_row.quantity_pieces} available"
            ),
        )
    await _enforce_shelf_limit(
        session,
        body.to_branch_id,
        variant,
        quantity_pieces,
        current_user=current_user,
        override=override_shelf_limit,
        override_reason=override_reason,
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
    """(carton_halves, dozen_halves, bag_halves, pieces) from a legacy row's remainder
    alone -- used for rows whose main unit is a freehand label (rolls...)."""
    rl = t.remainder_unit_label
    rq = t.remainder_quantity or 0
    ch = 1 if rl == "half_carton" else 0
    dh = 1 if rl == "half_dozen" else (rq * 2 if rl == "dozens" else 0)
    pcs = rq if rl == "pieces" else 0
    return ch, dh, 0, pcs


def _wh_breakdown_text(acc):
    parts = [
        _wh_halves_text(acc["ch"], "carton", "cartons"),
        _wh_halves_text(acc.get("bh", 0), "bag", "bags"),
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
    prod_rows = products_result.all()
    lookup = {p.id: (p.name, c.name if c else None) for p, c in prod_rows}
    # PATCH9A-UNITS: describe the CURRENT piece total in the product's own units
    # (100 pieces at 24 per carton = 4 cartons + 4 pieces), not the push history.
    from infrastructure.units import format_split, load_unit_sizes, load_units, pack_units_for_product, split_pieces

    prod_by_id = {p.id: p for p, _c in prod_rows}
    all_units = await load_units(session)
    sizes_by_product = await load_unit_sizes(session, list(prod_by_id.keys()))
    packs_by_row = {r.id: pack_units_for_product(all_units, prod_by_id.get(r.product_id), sizes_by_product.get(r.product_id)) for r in rows}

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
            {"ch": 0, "dh": 0, "bh": 0, "pcs": 0, "other": {}},
        )
        if t.unit_label == "mixed":
            ch, dh, bh, pcs = _wh_transfer_components(t)
        elif _wh_norm_unit(t.unit_label, None) is None:
            acc["other"][t.unit_label] = acc["other"].get(t.unit_label, 0) + (t.unit_count or 0)
            ch, dh, bh, pcs = _wh_remainder_only_components(t)
        else:
            ch, dh, bh, pcs = _wh_transfer_components(t)
        acc["ch"] += ch
        acc["dh"] += dh
        acc["bh"] += bh
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
            unit_breakdown=[
                WarehouseStockUnitBreakdown(
                    unit_label=p.label if n == 1 else p.plural_label, unit_count=n
                )
                for p, n in split_pieces(r.quantity_pieces, packs_by_row[r.id])[0]
            ],
            breakdown_text=format_split(r.quantity_pieces, packs_by_row[r.id]),
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

    # fix54: product names so the history tables can show which item moved
    product_ids = {t.product_id for t in transfers}
    product_names: dict[uuid.UUID, str] = {}
    if product_ids:
        products_result = await session.execute(select(Product).where(Product.id.in_(product_ids)))
        product_names = {pr.id: pr.name for pr in products_result.scalars().all()}

    # RET2: how many pieces of each transfer have gone back, and how many are asked for.
    returned_map = {}
    open_map = {}
    ret_rows = (
        await session.execute(
            select(
                TransferReturn.transfer_id,
                TransferReturn.status,
                func.coalesce(func.sum(TransferReturn.quantity_pieces), 0),
            )
            .where(TransferReturn.transfer_id.in_([t.id for t in transfers]))
            .group_by(TransferReturn.transfer_id, TransferReturn.status)
        )
    ).all()
    for ret_tid, ret_status, ret_qty in ret_rows:
        if ret_status == "completed":
            returned_map[ret_tid] = returned_map.get(ret_tid, 0) + int(ret_qty)
        elif ret_status in ("requested", "accepted"):
            open_map[ret_tid] = open_map.get(ret_tid, 0) + int(ret_qty)

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
            bags_count=t.bags_count,
            bags_pieces_per_unit=t.bags_pieces_per_unit,
            bags_half=t.bags_half,
            pieces_count=t.pieces_count,
            receipt_id=t.receipt_id,
            notes=t.notes,
            total_cost_naira=Money(t.total_cost_kobo).naira if t.total_cost_kobo is not None else None,
            payment_method=t.payment_method,
            unit_price_naira=Money(t.unit_price_kobo).naira if t.unit_price_kobo is not None else None,
            total_price_naira=Money(t.unit_price_kobo * t.quantity_pieces).naira if t.unit_price_kobo is not None else None,
            price_basis=t.price_basis,
            sale_id=t.sale_id,
            invoice_id=t.invoice_id,
            returned_pieces=returned_map.get(t.id, 0),
            open_return_pieces=open_map.get(t.id, 0),
            created_by=t.created_by,
            created_by_name=creator_names.get(t.created_by),
            product_name=product_names.get(t.product_id),
            created_at=t.created_at,
        )
        for t in transfers
    ]



# ===================================================================== RET2
# Branch transfer returns: goods go back to the branch that sent them, linked to
# the first transfer. Never a new sale and never a new invoice.
from datetime import timezone as _tr_tz  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from infrastructure.db.models import Sale as ORMSale  # noqa: E402
from infrastructure.db.models import SaleItem as ORMSaleItem  # noqa: E402
from infrastructure.db.models import SalePayment as ORMSalePayment  # noqa: E402
from infrastructure.db.models import TransferReturn  # noqa: E402
from infrastructure.db.models import AuditLog as _TrAuditLog  # noqa: E402
from sqlalchemy.orm import selectinload as _tr_selectinload  # noqa: E402


class TransferReturnCreate(BaseModel):
    transfer_id: uuid.UUID
    quantity_pieces: int = Field(gt=0)
    note: str | None = None
    # Only a super admin must say which side he acts for.
    side: str | None = Field(default=None, pattern="^(sender|receiver)$")
    # RET4: optional account details, kept only when the receiver side starts the return.
    bank_name: str | None = Field(default=None, max_length=120)
    account_number: str | None = Field(default=None, max_length=40)
    account_name: str | None = Field(default=None, max_length=120)


class TransferReturnApprove(BaseModel):
    payment_method: str = Field(pattern="^(cash|transfer)$")


class TransferReturnAccept(BaseModel):
    # RET4: optional account details typed by the receiving branch when it accepts.
    bank_name: str | None = Field(default=None, max_length=120)
    account_number: str | None = Field(default=None, max_length=40)
    account_name: str | None = Field(default=None, max_length=120)


class TransferReturnStepOut(BaseModel):
    action: str
    user_name: str | None = None
    branch_id: uuid.UUID | None = None
    payment_method: str | None = None
    quantity_pieces: int | None = None
    created_at: datetime


class TransferReturnOut(BaseModel):
    id: uuid.UUID
    transfer_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None = None
    from_branch_id: uuid.UUID
    to_branch_id: uuid.UUID
    started_by_side: str
    quantity_pieces: int
    unit_price_naira: float
    amount_naira: float
    status: str
    note: str | None = None
    payment_method: str | None = None
    refund_bank_name: str | None = None
    refund_account_number: str | None = None
    refund_account_name: str | None = None
    requested_by_name: str | None = None
    requested_at: datetime
    responded_at: datetime | None = None
    approved_at: datetime | None = None
    taken_shelf_pieces: int | None = None
    taken_warehouse_pieces: int | None = None
    landed_shelf_pieces: int | None = None
    landed_warehouse_pieces: int | None = None


def _tr_now():
    return datetime.now(_tr_tz.utc)


def _tr_in_branch(user, branch_id):
    if user.role == "super_admin":
        return True
    return user.branch_id is not None and user.branch_id == branch_id


def _tr_clean(value):
    # Account details: trimmed text, or None when empty.
    text = (value or "").strip()
    return text or None


def _tr_log(session, user, action, ret, acting_branch_id, extra=None):
    # RET4: one audit record per step, saved in the same commit as the step.
    from infrastructure.db.audit import log_audit_event

    meta = {
        "transfer_id": str(ret.transfer_id),
        "quantity_pieces": ret.quantity_pieces,
        "amount_kobo": int(ret.amount_kobo),
        "from_branch_id": str(ret.from_branch_id),
        "to_branch_id": str(ret.to_branch_id),
        "acting_branch_id": str(acting_branch_id) if acting_branch_id else None,
        "started_by_side": ret.started_by_side,
    }
    if extra:
        meta.update(extra)
    log_audit_event(
        session,
        user_id=user.id,
        action=action,
        entity_type="transfer_return",
        entity_id=ret.id,
        metadata=meta,
    )


async def _tr_counts(session, transfer_id):
    rows = (
        await session.execute(
            select(TransferReturn.status, func.coalesce(func.sum(TransferReturn.quantity_pieces), 0))
            .where(TransferReturn.transfer_id == transfer_id)
            .group_by(TransferReturn.status)
        )
    ).all()
    done = sum(int(q) for s, q in rows if s == "completed")
    open_ = sum(int(q) for s, q in rows if s in ("requested", "accepted"))
    return done, open_


async def _tr_get_locked(session, return_id):
    row = (
        await session.execute(
            select(TransferReturn)
            .where(TransferReturn.id == return_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Return request not found")
    return row


async def _tr_build_out(session, rows):
    if not rows:
        return []
    pids = {r.product_id for r in rows}
    pn = {
        p.id: p.name
        for p in (await session.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
    }
    uids = {r.requested_by for r in rows}
    un = {
        u.id: u.full_name
        for u in (await session.execute(select(User).where(User.id.in_(uids)))).scalars().all()
    }
    return [
        TransferReturnOut(
            id=r.id,
            transfer_id=r.transfer_id,
            product_id=r.product_id,
            product_name=pn.get(r.product_id),
            from_branch_id=r.from_branch_id,
            to_branch_id=r.to_branch_id,
            started_by_side=r.started_by_side,
            quantity_pieces=r.quantity_pieces,
            unit_price_naira=Money(r.unit_price_kobo).naira,
            amount_naira=Money(r.amount_kobo).naira,
            status=r.status,
            note=r.note,
            payment_method=r.payment_method,
            refund_bank_name=r.refund_bank_name,
            refund_account_number=r.refund_account_number,
            refund_account_name=r.refund_account_name,
            requested_by_name=un.get(r.requested_by),
            requested_at=r.requested_at,
            responded_at=r.responded_at,
            approved_at=r.approved_at,
            taken_shelf_pieces=r.taken_shelf_pieces,
            taken_warehouse_pieces=r.taken_warehouse_pieces,
            landed_shelf_pieces=r.landed_shelf_pieces,
            landed_warehouse_pieces=r.landed_warehouse_pieces,
        )
        for r in rows
    ]


@router.post("/transfer-returns", response_model=TransferReturnOut, status_code=status.HTTP_201_CREATED)
async def create_transfer_return(
    body: TransferReturnCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> TransferReturnOut:
    transfer = (
        await session.execute(
            select(StockTransfer)
            .where(StockTransfer.id == body.transfer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    if (
        transfer.transfer_type != "branch_to_branch"
        or transfer.sale_id is None
        or transfer.invoice_id is None
        or transfer.unit_price_kobo is None
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This transfer cannot be returned (it has no sale or no invoice).",
        )
    if current_user.role == "super_admin":
        if body.side is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Say which side you act for: sender or receiver.")
        side = body.side
    elif current_user.branch_id == transfer.from_branch_id:
        side = "sender"
    elif current_user.branch_id == transfer.to_branch_id:
        side = "receiver"
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This transfer is not from or to your branch.")
    if side != "receiver":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only the branch that received the goods can ask to return them.",
        )
    done, open_ = await _tr_counts(session, transfer.id)
    available = transfer.quantity_pieces - done - open_
    if body.quantity_pieces > available:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "You can return at most %d piece(s) of this transfer "
                "(sent %d, already returned %d, already asked for %d)."
                % (max(available, 0), transfer.quantity_pieces, done, open_)
            ),
        )
    now = _tr_now()
    row = TransferReturn(
        transfer_id=transfer.id,
        product_id=transfer.product_id,
        from_branch_id=transfer.from_branch_id,
        to_branch_id=transfer.to_branch_id,
        started_by_side=side,
        quantity_pieces=body.quantity_pieces,
        unit_price_kobo=transfer.unit_price_kobo,
        amount_kobo=body.quantity_pieces * transfer.unit_price_kobo,
        status="requested" if side == "sender" else "accepted",
        note=body.note,
        requested_by=current_user.id,
        requested_at=now,
        responded_by=None if side == "sender" else current_user.id,
        responded_at=None if side == "sender" else now,
        refund_bank_name=_tr_clean(body.bank_name) if side == "receiver" else None,
        refund_account_number=_tr_clean(body.account_number) if side == "receiver" else None,
        refund_account_name=_tr_clean(body.account_name) if side == "receiver" else None,
    )
    session.add(row)
    await session.flush()
    _tr_log(
        session,
        current_user,
        "transfer_return_requested",
        row,
        row.from_branch_id if side == "sender" else row.to_branch_id,
    )
    await session.commit()
    await session.refresh(row)
    return (await _tr_build_out(session, [row]))[0]


@router.get("/transfer-returns", response_model=list[TransferReturnOut])
async def list_transfer_returns(
    branch_id: uuid.UUID | None = None,
    transfer_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> list[TransferReturnOut]:
    effective = current_user.branch_id if current_user.branch_id is not None else branch_id
    stmt = select(TransferReturn).order_by(TransferReturn.requested_at.desc())
    if effective is not None:
        stmt = stmt.where(
            (TransferReturn.from_branch_id == effective) | (TransferReturn.to_branch_id == effective)
        )
    if transfer_id is not None:
        stmt = stmt.where(TransferReturn.transfer_id == transfer_id)
    rows = list((await session.execute(stmt)).scalars().all())
    return await _tr_build_out(session, rows)


@router.get("/transfer-returns/{return_id}/history", response_model=list[TransferReturnStepOut])
async def transfer_return_history(
    return_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> list[TransferReturnStepOut]:
    r = (
        await session.execute(select(TransferReturn).where(TransferReturn.id == return_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Return request not found")
    if not (_tr_in_branch(current_user, r.from_branch_id) or _tr_in_branch(current_user, r.to_branch_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This return is not from or to your branch.")
    entries = list(
        (
            await session.execute(
                select(_TrAuditLog)
                .options(_tr_selectinload(_TrAuditLog.user))
                .where(_TrAuditLog.entity_type == "transfer_return", _TrAuditLog.entity_id == return_id)
                .order_by(_TrAuditLog.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out = []
    for e in entries:
        meta = e.log_metadata or {}
        bid = meta.get("acting_branch_id")
        out.append(
            TransferReturnStepOut(
                action=e.action,
                user_name=e.user.full_name if e.user else None,
                branch_id=uuid.UUID(bid) if bid else None,
                payment_method=meta.get("payment_method"),
                quantity_pieces=meta.get("quantity_pieces"),
                created_at=e.created_at,
            )
        )
    return out


@router.post("/transfer-returns/{return_id}/accept", response_model=TransferReturnOut)
async def accept_transfer_return(
    return_id: uuid.UUID,
    body: TransferReturnAccept | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> TransferReturnOut:
    r = await _tr_get_locked(session, return_id)
    if r.status != "requested" or r.started_by_side != "sender":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This request is not waiting for your answer.")
    if not _tr_in_branch(current_user, r.to_branch_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the branch that received the goods can accept.")
    r.status = "accepted"
    r.responded_by = current_user.id
    r.responded_at = _tr_now()
    if body is not None:
        r.refund_bank_name = _tr_clean(body.bank_name)
        r.refund_account_number = _tr_clean(body.account_number)
        r.refund_account_name = _tr_clean(body.account_name)
    _tr_log(session, current_user, "transfer_return_accepted", r, r.to_branch_id)
    await session.commit()
    await session.refresh(r)
    return (await _tr_build_out(session, [r]))[0]


@router.post("/transfer-returns/{return_id}/decline", response_model=TransferReturnOut)
async def decline_transfer_return(
    return_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> TransferReturnOut:
    r = await _tr_get_locked(session, return_id)
    if r.status == "requested" and r.started_by_side == "sender":
        answering = r.to_branch_id
    elif r.status == "accepted" and r.started_by_side == "receiver":
        answering = r.from_branch_id
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This request is not waiting for your answer.")
    if not _tr_in_branch(current_user, answering):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This request is not waiting for your branch.")
    r.status = "declined"
    r.responded_by = current_user.id
    r.responded_at = _tr_now()
    _tr_log(session, current_user, "transfer_return_declined", r, answering)
    await session.commit()
    await session.refresh(r)
    return (await _tr_build_out(session, [r]))[0]


@router.post("/transfer-returns/{return_id}/cancel", response_model=TransferReturnOut)
async def cancel_transfer_return(
    return_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> TransferReturnOut:
    r = await _tr_get_locked(session, return_id)
    if r.status not in ("requested", "accepted"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This request is already closed.")
    starter = r.from_branch_id if r.started_by_side == "sender" else r.to_branch_id
    if not _tr_in_branch(current_user, starter):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the branch that asked can cancel.")
    r.status = "cancelled"
    r.responded_by = current_user.id
    r.responded_at = _tr_now()
    _tr_log(session, current_user, "transfer_return_cancelled", r, starter)
    await session.commit()
    await session.refresh(r)
    return (await _tr_build_out(session, [r]))[0]


@router.post("/transfer-returns/{return_id}/approve", response_model=TransferReturnOut)
async def approve_transfer_return(
    return_id: uuid.UUID,
    body: TransferReturnApprove,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> TransferReturnOut:
    from api.v1.transfer_pricing import branch_shelf_limit, plan_transfer_split, shelf_pieces

    r = await _tr_get_locked(session, return_id)
    if r.status != "accepted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This request is not ready for a refund.")
    if not _tr_in_branch(current_user, r.from_branch_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only the branch that sent the goods (and gets the money back to pay) can approve the refund.",
        )
    transfer = (
        await session.execute(
            select(StockTransfer)
            .where(StockTransfer.id == r.transfer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    done, _open = await _tr_counts(session, transfer.id)
    qty = r.quantity_pieces
    if done + qty > transfer.quantity_pieces:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="More than the transfer sent would be returned.")
    amount = qty * r.unit_price_kobo
    giver = r.to_branch_id
    getter = r.from_branch_id

    # Lock both warehouse rows in a fixed order (same rule as a transfer).
    if str(getter) < str(giver):
        get_wh = await _lock_branch_warehouse_row(session, getter, r.product_id)
        giv_wh = await _lock_branch_warehouse_row(session, giver, r.product_id)
    else:
        giv_wh = await _lock_branch_warehouse_row(session, giver, r.product_id)
        get_wh = await _lock_branch_warehouse_row(session, getter, r.product_id)
    giver_shelf = await shelf_pieces(session, giver, r.product_id)
    giver_wh_pieces = int(giv_wh.quantity_pieces)
    plan = plan_transfer_split(
        qty,
        "shelf",
        giver_shelf,
        giver_wh_pieces,
        await shelf_pieces(session, getter, r.product_id),
        await branch_shelf_limit(session, getter, r.product_id),
    )
    if plan is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "The branch that must give the pieces back holds only %d piece(s) now "
                "(shelf %d, warehouse %d), so %d cannot go back."
                % (giver_shelf + giver_wh_pieces, giver_shelf, giver_wh_pieces, qty)
            ),
        )
    if plan["from_shelf"] > 0:
        await _fifo_deduct(session, r.product_id, giver, plan["from_shelf"])
    giv_wh.quantity_pieces -= plan["from_warehouse"]

    # The sender gets the pieces back at its own first cost. No invoice is made.
    if transfer.total_cost_kobo is not None and transfer.quantity_pieces:
        unit_cost = int(transfer.total_cost_kobo) // int(transfer.quantity_pieces)
    else:
        prod = await session.get(Product, r.product_id)
        unit_cost = int(prod.cost_price_kobo or 0) if prod is not None else 0
    if plan["to_shelf"] > 0:
        session.add(
            StockBatch(
                product_id=r.product_id,
                branch_id=getter,
                batch_number="return-" + str(r.id)[:8],
                quantity_received=plan["to_shelf"],
                quantity_remaining=plan["to_shelf"],
                unit_cost_kobo=unit_cost,
            )
        )
    get_wh.quantity_pieces += plan["to_warehouse"]

    # The sale at the sending branch.
    sale = (
        await session.execute(
            select(ORMSale)
            .where(ORMSale.id == transfer.sale_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    remaining_after = transfer.quantity_pieces - done - qty
    if remaining_after <= 0:
        sale.status = "refunded"
    else:
        item = (
            await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == sale.id))
        ).scalars().first()
        sale.subtotal_kobo = max(int(sale.subtotal_kobo) - amount, 0)
        sale.total_kobo = max(int(sale.total_kobo) - amount, 0)
        if item is not None:
            item.quantity = max(int(item.quantity) - qty, 0)
            item.line_total_kobo = max(int(item.line_total_kobo) - amount, 0)
        pay = (
            await session.execute(select(ORMSalePayment).where(ORMSalePayment.sale_id == sale.id))
        ).scalars().first()
        if pay is not None:
            pay.amount_kobo = max(int(pay.amount_kobo) - amount, 0)
    sale.notes = (sale.notes or "") + " | Returned %d pcs (return %s)" % (qty, str(r.id)[:8])

    # The invoice at the receiving branch always goes down.
    invoice = (
        await session.execute(
            select(PurchaseInvoice)
            .where(PurchaseInvoice.id == transfer.invoice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    invoice.amount_kobo = max(int(invoice.amount_kobo or 0) - amount, 0)
    invoice.amount_paid_kobo = max(int(invoice.amount_paid_kobo or 0) - amount, 0)
    invoice.description = (invoice.description or "") + " Returned %d pcs." % qty
    inv_line = (
        await session.execute(
            select(PurchaseInvoiceLineItem).where(PurchaseInvoiceLineItem.invoice_id == invoice.id)
        )
    ).scalars().first()
    if inv_line is not None:
        inv_line.quantity = max(int(inv_line.quantity) - qty, 0)
        inv_line.line_total_kobo = max(int(inv_line.line_total_kobo) - amount, 0)

    r.status = "completed"
    r.payment_method = body.payment_method
    r.approved_by = current_user.id
    r.approved_at = _tr_now()
    r.taken_shelf_pieces = plan["from_shelf"]
    r.taken_warehouse_pieces = plan["from_warehouse"]
    r.landed_shelf_pieces = plan["to_shelf"]
    r.landed_warehouse_pieces = plan["to_warehouse"]
    _tr_log(
        session,
        current_user,
        "transfer_return_approved",
        r,
        r.from_branch_id,
        {
            "payment_method": body.payment_method,
            "landed_shelf_pieces": plan["to_shelf"],
            "landed_warehouse_pieces": plan["to_warehouse"],
        },
    )
    await session.commit()
    await session.refresh(r)
    return (await _tr_build_out(session, [r]))[0]
