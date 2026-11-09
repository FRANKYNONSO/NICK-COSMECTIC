import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Union

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import can_view_costs, get_current_user, get_db_session, require_permission
from api.v1.routers.catalog import _PUBLIC_BASE_URL
from api.v1.schemas.inventory import (
    LossHistoryRow,
    LossReasonBreakdown,
    LossSummaryOut,
    ProductReturnCreditRow,
    StockAdjustmentCreate,
    StockAdjustmentOut,
    StockBatchCreate,
    StockBatchOut,
    StockLevelOut,
)
from infrastructure.db.models import Branch, Product, StockAdjustment, StockBatch, User, WarehouseStock
from infrastructure.db.models import CreditEntry as ORMCreditEntry
from infrastructure.db.models import Refund as ORMRefund
from infrastructure.db.models import RefundItem as ORMRefundItem
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/inventory", tags=["inventory"])

_LOSS_EVIDENCE_DIR = Path("static/uploads/loss-evidence")
_ALLOWED_LOSS_EVIDENCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"}


@router.post("/stock-adjustments/evidence")
async def upload_loss_evidence(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> dict:
    """Uploads the image/video evidence required for a Damaged/Stolen/
    Expired loss report (September 2026 change spec section 4: "All
    three require image or video evidence upload") ahead of POST
    /stock-adjustments -- the returned URL goes in
    StockAdjustmentCreate.image_url. Same pattern as the refund photo
    and purchase invoice photo/video uploads elsewhere in the app.
    """
    original_name = file.filename or "loss-evidence"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_LOSS_EVIDENCE_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_LOSS_EVIDENCE_EXTENSIONS))}",
        )
    _LOSS_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _LOSS_EVIDENCE_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"url": f"{_PUBLIC_BASE_URL}/static/uploads/loss-evidence/{new_filename}"}


def _batch_to_out(batch: StockBatch, show_cost: bool = True) -> StockBatchOut:
    return StockBatchOut(
        id=batch.id,
        product_id=batch.product_id,
        branch_id=batch.branch_id,
        batch_number=batch.batch_number,
        quantity_received=batch.quantity_received,
        quantity_remaining=batch.quantity_remaining,
        unit_cost_naira=Money(batch.unit_cost_kobo).naira if show_cost else 0.0,
        expiry_date=batch.expiry_date,
        received_at=batch.received_at,
    )


@router.post("/stock-batches", response_model=StockBatchOut, status_code=status.HTTP_201_CREATED)
async def receive_stock(
    body: StockBatchCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockBatchOut:
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Server-side enforcement (previously only the screens enforced
    # these, so anyone with a login could call this endpoint directly).
    #
    # 1) A user whose account is pinned to a branch can only receive
    #    stock at that branch. Super admins have no pinned branch and
    #    can act on any branch -- same rule the warehouse endpoints use.
    if current_user.branch_id and body.branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only add stock to your own branch.",
        )
    if await session.get(Branch, body.branch_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")

    # 2) This endpoint is only for a brand-new product's STARTING stock
    #    (what Add Product sends right after creating the product).
    #    Once a product has any stock on record, more stock must come
    #    in through the warehouse (invoice -> warehouse -> Pull to
    #    store) so it stays tied to an invoice, not typed in freehand.
    existing_batches = await session.scalar(
        select(func.count())
        .select_from(StockBatch)
        .where(StockBatch.product_id == body.product_id)
    )
    if existing_batches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "This product already has stock on record. Add more stock through "
                "the Warehouse (Pull to store) so it stays tied to an invoice."
            ),
        )

    # Spec section 4: "This traceability must also apply to items that
    # were restocked later, not just the original stock-in." Every
    # restock (including the very first one, made at product-creation
    # time) becomes its own StockBatch row with its own expiry_date --
    # defaulting to whatever's on file for the product (spec section 3)
    # when the person receiving stock doesn't override it with a more
    # specific date for this particular batch.
    expiry_date = body.expiry_date if body.expiry_date is not None else product.expiration_date

    batch = StockBatch(
        product_id=body.product_id,
        branch_id=body.branch_id,
        purchase_order_item_id=body.purchase_order_item_id,
        batch_number=body.batch_number,
        quantity_received=body.quantity_received,
        quantity_remaining=body.quantity_received,
        unit_cost_kobo=Money.from_naira(body.unit_cost_naira).kobo,
        expiry_date=expiry_date,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return _batch_to_out(batch)


@router.get("/stock-batches", response_model=list[StockBatchOut])
async def list_stock_batches(
    product_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[StockBatchOut]:
    stmt = select(StockBatch)
    if product_id:
        stmt = stmt.where(StockBatch.product_id == product_id)
    # Branch-scoped for anyone who isn't super_admin -- never trust a
    # client-supplied branch_id for a cashier or branch manager.
    if current_user.role != "super_admin":
        stmt = stmt.where(StockBatch.branch_id == current_user.branch_id)
    elif branch_id:
        stmt = stmt.where(StockBatch.branch_id == branch_id)
    stmt = stmt.order_by(StockBatch.received_at)

    result = await session.execute(stmt)
    show_cost = await can_view_costs(current_user, session)  # PATCHP2C2-COST-VISIBLE
    return [_batch_to_out(b, show_cost=show_cost) for b in result.scalars().all()]


@router.get("/stock-levels", response_model=list[StockLevelOut])
async def stock_levels(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[StockLevelOut]:
    """Aggregated available stock per variant at a branch -- sums only
    non-expired batches with remaining quantity, matching the
    eligibility rule FifoStockAllocator uses in nickcosmetics_core, so
    what's shown here is exactly what checkout would actually be able
    to sell.
    """
    # Branch-scoped for anyone who isn't super_admin -- the requested
    # branch_id is ignored for a cashier/branch manager, always their
    # own branch server-side.
    effective_branch_id = (
        current_user.branch_id if current_user.role != "super_admin" else branch_id
    )
    today = date.today()
    stmt = (
        select(
            StockBatch.product_id,
            func.sum(StockBatch.quantity_remaining).label("quantity_available"),
        )
        .where(
            StockBatch.branch_id == effective_branch_id,
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
        .group_by(StockBatch.product_id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return []

    product_ids = [row.product_id for row in rows]
    variants_result = await session.execute(
        select(Product).where(Product.id.in_(product_ids))
    )
    reorder_levels = {v.id: v.reorder_level for v in variants_result.scalars().all()}

    levels = []
    for row in rows:
        reorder_level = reorder_levels.get(row.product_id, 0)
        levels.append(
            StockLevelOut(
                product_id=row.product_id,
                branch_id=effective_branch_id,
                quantity_available=row.quantity_available,
                reorder_level=reorder_level,
                is_low_stock=row.quantity_available <= reorder_level,
            )
        )
    return levels


# A loss taken from the WAREHOUSE has no shelf batch to hang its
# StockAdjustment on (stock_adjustments.stock_batch_id is required, and
# every loss report finds the product through that batch). So -- the same
# receive-then-write-off pattern sales.py already uses for a spoiled
# refund -- each warehouse loss gets its own throwaway batch that is
# created already at 0 remaining (never sellable), and the quantity comes
# off warehouse_stock instead. The batch_number prefix is how the rest of
# this file tells such a batch apart from a real shelf batch.
_WAREHOUSE_LOSS_PREFIX = "warehouse-loss-"


def _is_warehouse_loss_batch(batch: StockBatch | None) -> bool:
    return bool(batch is not None and (batch.batch_number or "").startswith(_WAREHOUSE_LOSS_PREFIX))


async def _locked_warehouse_row(
    session: AsyncSession, branch_id: uuid.UUID, product_id: uuid.UUID
) -> WarehouseStock | None:
    return (
        await session.execute(
            select(WarehouseStock)
            .where(WarehouseStock.branch_id == branch_id, WarehouseStock.product_id == product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


@router.get("/warehouse-qty")
async def warehouse_quantity(
    product_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Pieces of one product sitting in the warehouse -- lets the Record a
    loss form offer 'Use all' for the warehouse. Branch-scoped like the
    other inventory reads: a pinned user always gets their own branch.
    """
    effective_branch_id = current_user.branch_id or branch_id
    stmt = select(func.coalesce(func.sum(WarehouseStock.quantity_pieces), 0)).where(
        WarehouseStock.product_id == product_id
    )
    if effective_branch_id:
        stmt = stmt.where(WarehouseStock.branch_id == effective_branch_id)
    return {"quantity_pieces": int((await session.scalar(stmt)) or 0)}


async def _adjustment_to_out(session: AsyncSession, adjustment: StockAdjustment) -> StockAdjustmentOut:
    """Single-row version of the bulk conversion in list_stock_adjustments
    below -- used by the section-4 confirm/review endpoints, which only
    ever operate on one adjustment at a time, so the extra couple of
    queries here don't add up the way they would in a list.
    """
    batch = await session.get(StockBatch, adjustment.stock_batch_id)
    product = await session.get(Product, batch.product_id) if batch else None
    branch = await session.get(Branch, adjustment.branch_id) if adjustment.branch_id else None
    user_ids = {uid for uid in (adjustment.adjusted_by, adjustment.reviewed_by) if uid}
    user_names: dict[uuid.UUID, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        user_names = {u.id: u.full_name for u in users_result.scalars().all()}
    return StockAdjustmentOut(
        id=adjustment.id,
        stock_batch_id=adjustment.stock_batch_id,
        branch_id=adjustment.branch_id,
        branch_name=branch.name if branch else None,
        product_id=batch.product_id if batch else None,
        product_name=product.name if product else None,
        quantity_delta=adjustment.quantity_delta,
        reason=adjustment.reason,
        value_naira=(adjustment.value_kobo / 100) if adjustment.value_kobo is not None else None,
        notes=adjustment.notes,
        intake_receipt_id=adjustment.intake_receipt_id,
        damage_caused_by=adjustment.damage_caused_by,
        damage_staff_name=adjustment.damage_staff_name,
        theft_staff_on_duty=adjustment.theft_staff_on_duty,
        theft_noticed_at=adjustment.theft_noticed_at,
        theft_caused_by=adjustment.theft_caused_by,
        theft_by_staff_name=adjustment.theft_by_staff_name,
        image_url=adjustment.image_url,
        status=adjustment.status,
        reviewed_by=adjustment.reviewed_by,
        reviewed_by_name=user_names.get(adjustment.reviewed_by) if adjustment.reviewed_by else None,
        reviewed_at=adjustment.reviewed_at,
        adjusted_by=adjustment.adjusted_by,
        adjusted_by_name=user_names.get(adjustment.adjusted_by) if adjustment.adjusted_by else None,
        created_at=adjustment.created_at,
    )


async def _fefo_slices(
    session: AsyncSession, body: StockAdjustmentCreate, current_user: User
) -> list[tuple[StockBatch, int]]:
    """No batch chosen: work out which batches a loss comes out of.
    Soonest expiry first (no-expiry batches last, then oldest received),
    spilling into the next batch when one isn't enough. Only for losses
    (negative quantity_delta) -- a stock increase has to name its batch.
    """
    if body.quantity_delta >= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Pick a specific batch to add stock to.",
        )
    if body.product_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a product.")
    branch_id = current_user.branch_id or body.branch_id
    if branch_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a branch first.")
    result = await session.execute(
        select(StockBatch)
        .where(
            StockBatch.product_id == body.product_id,
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    batches = sorted(
        result.scalars().all(),
        key=lambda b: (b.expiry_date is None, b.expiry_date or date.max, b.received_at),
    )
    needed = -body.quantity_delta
    available = sum(b.quantity_remaining for b in batches)
    if needed > available:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Only {available} in stock for this product at this branch -- can't record a loss of {needed}.",
        )
    slices: list[tuple[StockBatch, int]] = []
    for b in batches:
        if needed <= 0:
            break
        take = min(b.quantity_remaining, needed)
        slices.append((b, -take))
        needed -= take
    return slices


async def _warehouse_slice(
    session: AsyncSession, body: StockAdjustmentCreate, current_user: User
) -> tuple[list[tuple[StockBatch, int]], WarehouseStock]:
    """Warehouse loss: checks the warehouse has enough (row locked until
    commit) and makes the throwaway 0-remaining batch the loss row hangs
    on. The caller deducts warehouse_stock (immediately, or at theft
    confirmation).
    """
    if body.quantity_delta >= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="A warehouse loss must reduce stock.")
    if body.product_id is None or body.stock_batch_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A warehouse loss is recorded against the product, not a shelf batch.",
        )
    branch_id = current_user.branch_id or body.branch_id
    if branch_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a branch first.")
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    row = await _locked_warehouse_row(session, branch_id, body.product_id)
    needed = -body.quantity_delta
    available = row.quantity_pieces if row is not None else 0
    if row is None or needed > available:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Only {available} in the warehouse for this product -- can't record a loss of {needed}.",
        )
    batch = _new_warehouse_loss_batch(product, branch_id, needed)
    session.add(batch)
    await session.flush()
    return [(batch, body.quantity_delta)], row


def _new_warehouse_loss_batch(product: Product, branch_id: uuid.UUID, quantity: int) -> StockBatch:
    """The throwaway, never-sellable (0 remaining) batch a warehouse loss
    row hangs on -- see _WAREHOUSE_LOSS_PREFIX above.
    """
    return StockBatch(
        product_id=product.id,
        branch_id=branch_id,
        batch_number=f"{_WAREHOUSE_LOSS_PREFIX}{uuid.uuid4()}",
        quantity_received=quantity,
        quantity_remaining=0,
        unit_cost_kobo=product.cost_price_kobo or 0,
    )


async def _any_slices(
    session: AsyncSession, body: StockAdjustmentCreate, current_user: User
) -> tuple[list[tuple[StockBatch, int]], WarehouseStock | None]:
    """The product's TOTAL stock (shelf + warehouse) is what can be lost:
    comes off the shelf first, soonest-expiring batch first, and whatever
    is still owed after the shelf is empty comes out of the warehouse.
    Shelf batches and the warehouse row are all locked until commit.
    """
    if body.quantity_delta >= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a specific batch to add stock to.")
    if body.product_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a product.")
    branch_id = current_user.branch_id or body.branch_id
    if branch_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a branch first.")
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    result = await session.execute(
        select(StockBatch)
        .where(
            StockBatch.product_id == body.product_id,
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    batches = sorted(
        result.scalars().all(),
        key=lambda b: (b.expiry_date is None, b.expiry_date or date.max, b.received_at),
    )
    row = await _locked_warehouse_row(session, branch_id, body.product_id)
    shelf_available = sum(b.quantity_remaining for b in batches)
    warehouse_available = row.quantity_pieces if row is not None else 0
    needed = -body.quantity_delta
    if needed > shelf_available + warehouse_available:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Only {shelf_available + warehouse_available} in stock "
                f"({shelf_available} on the shelf + {warehouse_available} in the warehouse) "
                f"-- can't record a loss of {needed}."
            ),
        )
    slices: list[tuple[StockBatch, int]] = []
    for b in batches:  # shelf first
        if needed <= 0:
            break
        take = min(b.quantity_remaining, needed)
        slices.append((b, -take))
        needed -= take
    if needed > 0:  # shelf is empty -- the rest comes from the warehouse
        wh_batch = _new_warehouse_loss_batch(product, branch_id, needed)
        session.add(wh_batch)
        await session.flush()
        slices.append((wh_batch, -needed))
    return slices, row


@router.post("/stock-adjustments", status_code=status.HTTP_201_CREATED)
async def create_stock_adjustment(
    body: StockAdjustmentCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> dict:
    warehouse_row: WarehouseStock | None = None
    if body.location == "warehouse":
        slices, warehouse_row = await _warehouse_slice(session, body, current_user)
    elif body.location == "any" and body.stock_batch_id is None:
        slices, warehouse_row = await _any_slices(session, body, current_user)
    elif body.stock_batch_id is not None:
        # Locked read (FOR UPDATE) so a sale or transfer touching this same
        # batch at the same moment can't overwrite the adjustment below.
        batch = (
            await session.execute(
                select(StockBatch)
                .where(StockBatch.id == body.stock_batch_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Stock batch not found")

        # A user pinned to a branch can only adjust that branch's stock.
        if current_user.branch_id and batch.branch_id != current_user.branch_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="You can only adjust stock at your own branch.",
            )
        slices = [(batch, body.quantity_delta)]
    else:
        # No batch chosen -- FEFO across the product's batches at the branch
        # (already branch-scoped: a pinned user's own branch is used).
        slices = await _fefo_slices(session, body, current_user)

    # Item 11 / September 2026 change spec section 4: accountability
    # (and evidence) is required, not optional, for the reasons that
    # most need someone to be answerable for what happened.
    if body.reason == "damaged":
        if not body.damage_caused_by:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Record whether the item was damaged by a customer or a staff member.",
            )
        if body.damage_caused_by == "staff" and not (body.damage_staff_name or "").strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Name the staff member responsible for the damage.",
            )
        if not (body.notes or "").strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Write a note explaining what happened to the item.",
            )
    if body.reason == "theft":
        # theft_staff_on_duty (a free-text "who was on shift" list,
        # separate from theft_caused_by below) was removed from the
        # Record-a-loss form -- it was confusing ("I really don't know
        # what this means") and, being required, silently blocked
        # every theft submission where the person left it blank. Kept
        # as an accepted-but-optional field on the schema so old rows
        # that have it still display correctly.
        if not body.theft_caused_by:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Record whether the item was stolen by a customer or a staff member.",
            )
        if body.theft_caused_by == "staff" and not (body.theft_by_staff_name or "").strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Name the staff member responsible for the theft.",
            )
    if body.reason in ("damaged", "theft", "expired") and not (body.image_url or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Upload an image or video of the item as evidence.",
        )

    # September 2026 change spec section 4: theft is reported first,
    # notifying the super admin, and only deducted from inventory once
    # they confirm it (see confirm_theft_adjustment below) -- "a
    # completed stolen-item report shouldn't remain open as a live
    # complaint" implies it's genuinely open (not yet reflected in
    # stock) until then. Every other reason (including damaged and
    # expired) still deducts immediately, matching the spec's closing
    # goal that "the inventory count should automatically decrease" --
    # for damaged/expired that happens right away; for theft it
    # happens at confirmation instead of never happening automatically
    # at all.
    is_pending_theft = body.reason == "theft"
    adjustment_status = (
        "pending_confirmation" if is_pending_theft
        else "pending_review" if body.reason in ("damaged", "expired")
        else "completed"
    )

    # Losses (any negative delta) capture their monetary value at the
    # product's CURRENT selling price on Inventory -- not the stock
    # batch's own cost (unit_cost_kobo), since that figure is left at
    # ₦0 by several stock-intake paths (starting stock added directly
    # on Add Product, a warehouse restock with no cost basis to
    # derive from) and there's no way for staff to notice or correct
    # it before a loss gets recorded against that batch. Whatever
    # price is shown on Inventory for this product right now is what
    # a loss of it is valued at -- simple, and always matches what's
    # visibly on file. A positive delta (e.g. a recount finding more
    # stock than expected) isn't a loss, so it gets no value. A
    # pending theft report still captures this now (the value it
    # WOULD be if confirmed), rather than waiting for confirmation to
    # compute it.
    loss_group_id = uuid.uuid4()
    product_for_value = await session.get(Product, slices[0][0].product_id)
    unit_price_kobo = product_for_value.selling_price_kobo if product_for_value else 0

    # One StockAdjustment row per batch touched (a single row when the
    # loss fits in one batch), all inside this one transaction -- either
    # every slice is recorded or none is.
    for batch, delta in slices:
        if not is_pending_theft and _is_warehouse_loss_batch(batch):
            # Warehouse slice: off the warehouse ledger (availability was
            # checked under lock when the slices were built); the throwaway
            # batch stays at 0 remaining.
            warehouse_row.quantity_pieces += delta
        elif not is_pending_theft:
            new_remaining = batch.quantity_remaining + delta
            if new_remaining < 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Adjustment would make quantity_remaining negative "
                        f"({batch.quantity_remaining} + {delta} = {new_remaining})"
                    ),
                )
            batch.quantity_remaining = new_remaining
        session.add(StockAdjustment(
            stock_batch_id=batch.id,
            loss_group_id=loss_group_id,
            branch_id=batch.branch_id,
            adjusted_by=current_user.id,
            quantity_delta=delta,
            reason=body.reason,
            value_kobo=abs(delta) * unit_price_kobo if delta < 0 else None,
            notes=body.notes,
            intake_receipt_id=body.intake_receipt_id,
            damage_caused_by=body.damage_caused_by if body.reason == "damaged" else None,
            damage_staff_name=body.damage_staff_name if body.reason == "damaged" else None,
            theft_staff_on_duty=body.theft_staff_on_duty if body.reason == "theft" else None,
            theft_noticed_at=body.theft_noticed_at if body.reason == "theft" else None,
            theft_caused_by=body.theft_caused_by if body.reason == "theft" else None,
            theft_by_staff_name=body.theft_by_staff_name if body.reason == "theft" else None,
            image_url=body.image_url,
            status=adjustment_status,
        ))
    await session.commit()
    first = slices[0][0]
    return {
        "stock_batch_id": str(first.id),
        "quantity_remaining": warehouse_row.quantity_pieces if _is_warehouse_loss_batch(first) else first.quantity_remaining,
        "status": adjustment_status,
        "batches_used": len(slices),
    }


@router.post("/stock-adjustments/{adjustment_id}/confirm-theft", response_model=StockAdjustmentOut)
async def confirm_theft_adjustment(
    adjustment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    # Super admin only -- spec section 4: theft is notified to the
    # super admin specifically for confirmation, not left to whoever
    # has ordinary manage_inventory access (that's who can *report*
    # one, same as damaged/expired).
    current_user: User = Depends(require_permission("confirm_theft_losses")),  # PATCHP3C
) -> StockAdjustmentOut:
    adjustment = await session.get(StockAdjustment, adjustment_id)
    if adjustment is None or adjustment.reason != "theft":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Theft report not found")
    if current_user.role != "super_admin":  # PATCHP3I-BRANCH
        theft_batch = await session.get(StockBatch, adjustment.stock_batch_id)
        if (
            current_user.branch_id is None
            or theft_batch is None
            or theft_batch.branch_id != current_user.branch_id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Theft report not found")
    if adjustment.status != "pending_confirmation":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This report has already been confirmed.")

    batch = (
        await session.execute(
            select(StockBatch)
            .where(StockBatch.id == adjustment.stock_batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Stock batch not found")
    if _is_warehouse_loss_batch(batch):
        # Warehouse theft: comes off the warehouse ledger now that it is confirmed.
        wh_row = await _locked_warehouse_row(session, batch.branch_id, batch.product_id)
        wh_available = wh_row.quantity_pieces if wh_row is not None else 0
        if wh_row is None or wh_available + adjustment.quantity_delta < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Confirming this would make the warehouse stock negative "
                    f"({wh_available} + {adjustment.quantity_delta})"
                ),
            )
        wh_row.quantity_pieces = wh_available + adjustment.quantity_delta
    else:
        new_remaining = batch.quantity_remaining + adjustment.quantity_delta
        if new_remaining < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Confirming this would make quantity_remaining negative "
                    f"({batch.quantity_remaining} + {adjustment.quantity_delta} = {new_remaining})"
                ),
            )
        batch.quantity_remaining = new_remaining
    adjustment.status = "completed"
    adjustment.reviewed_by = current_user.id
    adjustment.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(adjustment)
    return await _adjustment_to_out(session, adjustment)


@router.post("/stock-adjustments/{adjustment_id}/mark-reviewed", response_model=StockAdjustmentOut)
async def mark_adjustment_reviewed(
    adjustment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    # manage_inventory (not manage_users, unlike theft confirmation
    # above) -- spec: damaged notifies "both the super admin and the
    # branch manager", so a branch manager needs to be able to
    # acknowledge this too, not only a super admin.
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockAdjustmentOut:
    """Pure acknowledgement -- no stock change -- for the
    damaged/expired notification (spec section 4: "notify... super
    admin and branch manager" for damaged; "trigger a notification the
    admin can review" for expired). Theft doesn't use this: confirming
    a theft report (above) sets reviewed_by/reviewed_at as part of
    that same action instead.
    """
    adjustment = await session.get(StockAdjustment, adjustment_id)
    if adjustment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Adjustment not found")
    if adjustment.status == "pending_review":
        # Acknowledging a loss that is waiting for review now means
        # approving it, so it needs a value on file first.
        if not adjustment.value_kobo:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Set a value for this loss before approving it.",
            )
        for sibling in await _group_rows(session, adjustment):
            if sibling.status == "pending_review":
                sibling.status = "completed"
                sibling.reviewed_by = current_user.id
                sibling.reviewed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(adjustment)
    if adjustment.reviewed_at is None:
        adjustment.reviewed_by = current_user.id
        adjustment.reviewed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(adjustment)
    return await _adjustment_to_out(session, adjustment)


async def _group_rows(session: AsyncSession, adjustment: StockAdjustment) -> list[StockAdjustment]:
    """Every row that came from the same loss submission (just this row
    for older losses with no group)."""
    if adjustment.loss_group_id is None:
        return [adjustment]
    result = await session.execute(
        select(StockAdjustment)
        .where(StockAdjustment.loss_group_id == adjustment.loss_group_id)
        .order_by(StockAdjustment.created_at, StockAdjustment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all()) or [adjustment]


async def _combine_rows(session: AsyncSession, rows: list[StockAdjustment]) -> list[StockAdjustmentOut]:
    """Review-list rows with one row per LOSS: parts that came from the
    same submission (shelf batches + warehouse) are merged into one entry
    with the total quantity and value. Anything else passes through."""
    groups: dict[uuid.UUID, list[StockAdjustment]] = {}
    order: list[tuple[str, object]] = []
    for r in rows:
        if r.status == "pending_review" and r.loss_group_id is not None:
            if r.loss_group_id not in groups:
                groups[r.loss_group_id] = []
                order.append(("g", r.loss_group_id))
            groups[r.loss_group_id].append(r)
        else:
            order.append(("r", r))
    out: list[StockAdjustmentOut] = []
    for kind, item in order:
        members = groups[item] if kind == "g" else [item]
        rep = members[0]
        base = await _adjustment_to_out(session, rep)
        shelf = warehouse = 0
        for m in members:
            b = await session.get(StockBatch, m.stock_batch_id)
            q = abs(m.quantity_delta)
            if _is_warehouse_loss_batch(b):
                warehouse += q
            else:
                shelf += q
        values = [m.value_kobo for m in members if m.value_kobo is not None]
        out.append(base.model_copy(update={
            "quantity_delta": sum(m.quantity_delta for m in members),
            "value_naira": (sum(values) / 100) if values else None,
            "loss_group_id": rep.loss_group_id,
            "parts": len(members),
            "shelf_quantity": shelf,
            "warehouse_quantity": warehouse,
        }))
    return out


async def _load_pending_review_group(
    session: AsyncSession, adjustment_id: uuid.UUID, current_user: User
) -> list[StockAdjustment]:
    adjustment = (
        await session.execute(
            select(StockAdjustment)
            .where(StockAdjustment.id == adjustment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if adjustment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Loss not found")
    if current_user.branch_id is not None and adjustment.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only review losses at your own branch.")
    pending = [a for a in await _group_rows(session, adjustment) if a.status == "pending_review"]
    if not pending:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This loss is not waiting for review.")
    return pending


@router.post("/stock-adjustments/{adjustment_id}/approve", response_model=StockAdjustmentOut)
async def approve_loss(
    adjustment_id: uuid.UUID,
    value_naira: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockAdjustmentOut:
    """Approve a damaged/expired loss waiting for review -- ALL parts of
    it (shelf batches + warehouse) at once. value_naira is the TOTAL for
    the whole loss; it is split across the parts in proportion to
    quantity.
    """
    rows = await _load_pending_review_group(session, adjustment_id, current_user)
    total_qty = sum(abs(r.quantity_delta) for r in rows) or 1
    raw = (value_naira or "").strip()
    if raw:
        try:
            amount = float(raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The value must be a number.")
        total_kobo = Money.from_naira(amount).kobo if amount > 0 else 0
    else:
        total_kobo = sum(r.value_kobo or 0 for r in rows)
    if total_kobo <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Enter the value of this loss (in naira) before approving it.",
        )
    remaining = total_kobo
    now = datetime.now(timezone.utc)
    for i, r in enumerate(rows):
        share = remaining if i == len(rows) - 1 else round(total_kobo * abs(r.quantity_delta) / total_qty)
        remaining -= share
        r.value_kobo = share
        r.status = "completed"
        r.reviewed_by = current_user.id
        r.reviewed_at = now
    await session.commit()
    await session.refresh(rows[0])
    return await _adjustment_to_out(session, rows[0])


@router.post("/stock-adjustments/{adjustment_id}/reject", response_model=StockAdjustmentOut)
async def reject_loss(
    adjustment_id: uuid.UUID,
    reason: str | None = Query(default=None, max_length=300),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> StockAdjustmentOut:
    """Reject a damaged/expired loss -- ALL parts of it. The stock that
    was deducted goes back to where it came from: shelf batches back on
    the shelf, the warehouse part back in the warehouse.
    """
    why = (reason or "").strip()
    if not why:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Say why this is not a loss.")
    rows = await _load_pending_review_group(session, adjustment_id, current_user)
    now = datetime.now(timezone.utc)
    for r in rows:
        qty = abs(r.quantity_delta)
        batch = (
            await session.execute(
                select(StockBatch)
                .where(StockBatch.id == r.stock_batch_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if batch is not None:
            if _is_warehouse_loss_batch(batch):
                wh_row = await _locked_warehouse_row(session, r.branch_id or batch.branch_id, batch.product_id)
                if wh_row is None:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail="The warehouse record for this product is missing, so the stock can't be put back.",
                    )
                wh_row.quantity_pieces += qty
            else:
                batch.quantity_remaining += qty
        r.status = "rejected"
        r.notes = ((r.notes + " | ") if r.notes else "") + "Rejected: " + why
        r.reviewed_by = current_user.id
        r.reviewed_at = now
    await session.commit()
    await session.refresh(rows[0])
    return await _adjustment_to_out(session, rows[0])


@router.get("/stock-adjustments/{adjustment_id}/refund-details")
async def loss_refund_details(
    adjustment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> dict:
    """What was filled in on the refund that produced a spoiled-item
    loss, so it can be reviewed before the loss is approved. The loss
    row only carries the sale id (in its note), so the refund is found
    by sale + product + quantity + closest time.
    """
    adjustment = await session.get(StockAdjustment, adjustment_id)
    if adjustment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Loss not found")
    if current_user.branch_id is not None and adjustment.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only view losses at your own branch.")
    m = re.search(r"sale ([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", adjustment.notes or "")
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This loss was not created from a refund, so there are no refund details.")
    sale_id = uuid.UUID(m.group(1))
    batch = await session.get(StockBatch, adjustment.stock_batch_id)

    stmt = (
        select(ORMRefundItem, ORMRefund, ORMSaleItem)
        .select_from(ORMRefundItem)
        .join(ORMRefund, ORMRefundItem.refund_id == ORMRefund.id)
        .join(ORMSaleItem, ORMRefundItem.sale_item_id == ORMSaleItem.id)
        .where(ORMRefund.sale_id == sale_id, ORMRefundItem.spoiled.is_(True))
    )
    if batch is not None:
        stmt = stmt.where(ORMSaleItem.product_id == batch.product_id)
    rows = (await session.execute(stmt)).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The refund for this loss could not be found.")

    want_qty = abs(adjustment.quantity_delta)

    def _closeness(row) -> tuple:
        item, refund, _sale_item = row
        try:
            gap = abs((refund.created_at - adjustment.created_at).total_seconds())
        except TypeError:
            gap = 0.0
        return (item.quantity != want_qty, gap)

    item, refund, sale_item = min(rows, key=_closeness)

    processed_by_name = None
    if refund.processed_by:
        u = await session.get(User, refund.processed_by)
        processed_by_name = u.full_name if u else None
    sale = await session.get(ORMSale, sale_id)
    branch = await session.get(Branch, sale.branch_id) if sale and sale.branch_id else None
    product = await session.get(Product, sale_item.product_id) if sale_item.product_id else None

    return {
        "refund_id": str(refund.id),
        "sale_id": str(sale_id),
        "refunded_at": refund.created_at.isoformat() if refund.created_at else None,
        "reason": refund.reason,
        "photo_url": refund.image_url,
        "processed_by_name": processed_by_name,
        "branch_name": branch.name if branch else None,
        "action": refund.action,
        "refund_status": refund.status,
        "refund_amount_naira": Money(refund.amount_kobo).naira,
        "bank_name": refund.bank_name,
        "account_number": refund.account_number,
        "account_name": refund.account_name,
        "product_name": item.product_name or (product.name if product else None),
        "unit_label": item.unit_label,
        "quantity": item.quantity,
        "unit_price_naira": Money(item.unit_price_kobo).naira,
        "line_total_naira": Money(item.line_total_kobo).naira,
        "restocked": bool(item.restocked),
        "spoiled": bool(item.spoiled),
    }

@router.get("/stock-adjustments/pending-review", response_model=list[StockAdjustmentOut])
async def list_pending_review_adjustments(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> list[StockAdjustmentOut]:
    """Backs the dashboard banner and the Losses review list: every
    pending theft report plus every loss waiting for review (one entry
    per loss, even when it came out of several batches), scoped to the
    current admin's branch unless they're a super admin.
    """
    conditions = [
        ((StockAdjustment.reason == "theft") & (StockAdjustment.status == "pending_confirmation"))
        | (StockAdjustment.status == "pending_review")
    ]
    if current_user.branch_id is not None:
        conditions.append(StockAdjustment.branch_id == current_user.branch_id)
    result = await session.execute(
        select(StockAdjustment).where(*conditions).order_by(StockAdjustment.created_at.desc()).limit(200)
    )
    return await _combine_rows(session, list(result.scalars().all()))


@router.get("/stock-adjustments", response_model=list[StockAdjustmentOut])
async def list_stock_adjustments(
    branch_id: uuid.UUID | None = None,
    reason: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> list[StockAdjustmentOut]:
    """Loss/adjustment history. Branch isolation matches every other
    list endpoint in the app: a non-super-admin only ever sees their
    own branch regardless of what `branch_id` they pass, a super admin
    sees the branch they ask for, and omitting it entirely gives the
    "All Branches" view.
    """
    effective_branch_id = current_user.branch_id if current_user.branch_id is not None else branch_id

    stmt = select(StockAdjustment)
    if effective_branch_id is not None:
        stmt = stmt.where(StockAdjustment.branch_id == effective_branch_id)
    if reason:
        stmt = stmt.where(StockAdjustment.reason == reason)
    if start_date:
        stmt = stmt.where(StockAdjustment.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(StockAdjustment.created_at <= datetime.combine(end_date, datetime.max.time()))
    stmt = stmt.order_by(StockAdjustment.created_at.desc())

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if not rows:
        return []

    batch_ids = {r.stock_batch_id for r in rows}
    batches_result = await session.execute(select(StockBatch).where(StockBatch.id.in_(batch_ids)))
    variant_by_batch = {b.id: b.product_id for b in batches_result.scalars().all()}

    user_ids = {r.adjusted_by for r in rows if r.adjusted_by} | {r.reviewed_by for r in rows if r.reviewed_by}
    user_names: dict[uuid.UUID, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        user_names = {u.id: u.full_name for u in users_result.scalars().all()}

    return [
        StockAdjustmentOut(
            id=r.id,
            stock_batch_id=r.stock_batch_id,
            branch_id=r.branch_id,
            product_id=variant_by_batch.get(r.stock_batch_id),
            quantity_delta=r.quantity_delta,
            reason=r.reason,
            value_naira=(r.value_kobo / 100) if r.value_kobo is not None else None,
            notes=r.notes,
            intake_receipt_id=r.intake_receipt_id,
            damage_caused_by=r.damage_caused_by,
            damage_staff_name=r.damage_staff_name,
            theft_staff_on_duty=r.theft_staff_on_duty,
            theft_noticed_at=r.theft_noticed_at,
            theft_caused_by=r.theft_caused_by,
            theft_by_staff_name=r.theft_by_staff_name,
            image_url=r.image_url,
            status=r.status,
            reviewed_by=r.reviewed_by,
            reviewed_by_name=user_names.get(r.reviewed_by) if r.reviewed_by else None,
            reviewed_at=r.reviewed_at,
            adjusted_by=r.adjusted_by,
            adjusted_by_name=user_names.get(r.adjusted_by) if r.adjusted_by else None,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/losses/summary", response_model=LossSummaryOut)
async def losses_summary(
    branch_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> LossSummaryOut:
    """Powers the Super Admin dashboard's Losses card -- a total plus
    a per-reason breakdown, over losses only (positive/recount-up
    adjustments carry no value_kobo and are excluded, same as the
    dashboard's other cards showing only what actually hurt the
    business).
    """
    effective_branch_id = current_user.branch_id if current_user.branch_id is not None else branch_id

    stmt = select(
        StockAdjustment.reason,
        func.sum(func.abs(StockAdjustment.quantity_delta)).label("quantity"),
        func.coalesce(func.sum(StockAdjustment.value_kobo), 0).label("value_kobo"),
    ).where(StockAdjustment.value_kobo.is_not(None), StockAdjustment.status == "completed")
    if effective_branch_id is not None:
        stmt = stmt.where(StockAdjustment.branch_id == effective_branch_id)
    if start_date:
        stmt = stmt.where(func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at) >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(func.coalesce(StockAdjustment.reviewed_at, StockAdjustment.created_at) <= datetime.combine(end_date, datetime.max.time()))
    stmt = stmt.group_by(StockAdjustment.reason)

    result = await session.execute(stmt)
    rows = result.all()

    by_reason = [
        LossReasonBreakdown(reason=row.reason, quantity=row.quantity, value_naira=row.value_kobo / 100)
        for row in rows
    ]

    # "Sold Below Original Price" was retired as a loss category along
    # with SaleLoss/the checkout discount path it tracked (spec
    # section 3) -- only the three StockAdjustment-backed categories
    # (damaged/expired/theft) remain.
    return LossSummaryOut(
        total_value_naira=sum(b.value_naira for b in by_reason),
        total_quantity=sum(b.quantity for b in by_reason),
        by_reason=by_reason,
    )


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end - timedelta(days=1)


# PATCH8A-PRODUCT-RETURNS-CREDIT
@router.get("/products/{product_id}/returns-and-credit", response_model=list[ProductReturnCreditRow])
async def product_returns_and_credit(
    product_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> list[ProductReturnCreditRow]:
    """Refund lines and goods taken on credit for one product (shown in the
    Inventory History window). Read-only. A user pinned to one branch sees
    only that branch; others may narrow the list with branch_id.
    """
    effective_branch_id = current_user.branch_id if current_user.branch_id is not None else branch_id

    refund_stmt = (
        select(ORMRefundItem, ORMRefund, ORMSale)
        .select_from(ORMRefundItem)
        .join(ORMRefund, ORMRefundItem.refund_id == ORMRefund.id)
        .join(ORMSaleItem, ORMRefundItem.sale_item_id == ORMSaleItem.id)
        .join(ORMSale, ORMRefund.sale_id == ORMSale.id)
        .where(ORMSaleItem.product_id == product_id)
    )
    if effective_branch_id is not None:
        refund_stmt = refund_stmt.where(ORMSale.branch_id == effective_branch_id)
    refund_stmt = refund_stmt.order_by(ORMRefund.created_at.desc()).limit(limit)
    refund_rows = (await session.execute(refund_stmt)).all()

    credit_stmt = (
        select(ORMCreditEntry)
        .where(ORMCreditEntry.product_id == product_id)
        .where(ORMCreditEntry.quantity.is_not(None))
        .where(ORMCreditEntry.stock_deducted.is_(True))
    )
    if effective_branch_id is not None:
        credit_stmt = credit_stmt.where(ORMCreditEntry.branch_id == effective_branch_id)
    credit_stmt = credit_stmt.order_by(ORMCreditEntry.opened_at.desc()).limit(limit)
    credits = (await session.execute(credit_stmt)).scalars().all()

    branch_ids = {sale.branch_id for _item, _refund, sale in refund_rows if sale.branch_id}
    branch_ids |= {c.branch_id for c in credits if c.branch_id}
    branch_names: dict[uuid.UUID, str] = {}
    if branch_ids:
        names_result = await session.execute(select(Branch.id, Branch.name).where(Branch.id.in_(branch_ids)))
        branch_names = {bid: bname for bid, bname in names_result.all()}

    credit_status_words = {"outstanding": "outstanding", "partially_paid": "part paid", "paid": "paid"}
    rows: list[ProductReturnCreditRow] = []
    for item, refund, sale in refund_rows:
        if item.spoiled:
            what = "Refund, sent to warehouse (spoiled)"
        elif item.restocked:
            what = "Refund, put back on shelf"
        else:
            what = "Refund, not put back"
        rows.append(
            ProductReturnCreditRow(
                kind="refund",
                happened_at=refund.created_at,
                branch_id=sale.branch_id,
                branch_name=branch_names.get(sale.branch_id) if sale.branch_id else None,
                what=what,
                quantity=item.quantity,
                detail=("Reason: " + refund.reason.strip()) if refund.reason and refund.reason.strip() else None,
            )
        )
    for c in credits:
        rows.append(
            ProductReturnCreditRow(
                kind="credit",
                happened_at=c.opened_at,
                branch_id=c.branch_id,
                branch_name=branch_names.get(c.branch_id) if c.branch_id else None,
                what="Taken on credit",
                quantity=c.quantity,
                detail=c.customer_full_name + ", " + credit_status_words.get(c.status, c.status),
            )
        )
    rows.sort(key=lambda r: r.happened_at, reverse=True)
    return rows[:limit]


@router.get("/losses/history", response_model=list[LossHistoryRow])
async def losses_history(
    branch_id: uuid.UUID | None = None,
    category: str | None = None,
    year: int | None = None,
    month: int | None = None,
    limit: int = 300,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> list[LossHistoryRow]:
    """Losses History (spec section 18.4) -- every individual loss
    record, permanently kept (spec section 18.6: the dashboard total
    resets monthly, but nothing here ever does), searchable by month
    and filterable by category/branch. `category` accepts any
    StockAdjustment reason ('damaged', 'expired', 'theft', etc.).
    'sold_below_price' was retired as a category along with SaleLoss
    (spec section 3 -- that checkout discount path no longer exists).
    """
    effective_branch_id = current_user.branch_id if current_user.branch_id is not None else branch_id
    start_date = end_date = None
    if year and month:
        start_date, end_date = _month_bounds(year, month)

    rows: list[LossHistoryRow] = []
    wh_flags: dict[uuid.UUID, bool] = {}

    stmt = (
        select(StockAdjustment)
        .options(selectinload(StockAdjustment.stock_batch).selectinload(StockBatch.product).selectinload(Product.images))
        .where(StockAdjustment.value_kobo.is_not(None), StockAdjustment.status == "completed")
    )
    if category:
        stmt = stmt.where(StockAdjustment.reason == category)
    if effective_branch_id is not None:
        stmt = stmt.where(StockAdjustment.branch_id == effective_branch_id)
    if start_date:
        stmt = stmt.where(StockAdjustment.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(StockAdjustment.created_at <= datetime.combine(end_date, datetime.max.time()))
    stmt = stmt.order_by(StockAdjustment.created_at.desc()).limit(limit)
    adjustments = (await session.execute(stmt)).scalars().all()
    for adj in adjustments:
        product = adj.stock_batch.product if adj.stock_batch else None
        primary_image = next((img for img in product.images if img.is_primary), None) if product else None
        # Item 11: fold the accountability fields into the notes
        # shown on this report -- "who's answerable" needs to be
        # visible here, not just captured at record-time, and
        # adding it to the existing free-text notes column avoids
        # widening this already-9-column table for two reasons
        # that only ever apply to one row each.
        accountability_bits = []
        wh_flags[adj.id] = bool(_is_warehouse_loss_batch(adj.stock_batch))
        if adj.reason == "damaged" and adj.damage_caused_by:
            accountability_bits.append(
                f"Damaged by: {adj.damage_staff_name} (staff)" if adj.damage_caused_by == "staff" else "Damaged by: customer"
            )
        if adj.reason == "theft" and adj.theft_caused_by:
            accountability_bits.append(
                f"Stolen by: {adj.theft_by_staff_name} (staff)" if adj.theft_caused_by == "staff" else "Stolen by: customer"
            )
        # theft_staff_on_duty is legacy -- the "Staff on duty
        # (comma-separated)" field it came from was removed from the
        # Record-a-loss form (confusing, and its required-ness was
        # silently blocking submissions), but old rows that still have
        # it keep showing it here.
        if adj.reason == "theft" and adj.theft_staff_on_duty:
            accountability_bits.append(f"On duty: {', '.join(adj.theft_staff_on_duty)}")
        if adj.reason == "theft" and adj.theft_noticed_at:
            accountability_bits.append(f"Noticed missing: {adj.theft_noticed_at.strftime('%H:%M')}")
        combined_notes = " · ".join(accountability_bits + ([adj.notes] if adj.notes else []))
        rows.append(LossHistoryRow(
            id=adj.id,
            loss_group_id=adj.loss_group_id,
            category=adj.reason,
            product_id=product.id if product else None,
            product_name=product.name if product else None,
            sku=product.sku if product else None,
            image_url=primary_image.url if primary_image else None,
            quantity=abs(adj.quantity_delta),
            value_naira=Money(adj.value_kobo).naira,
            branch_id=adj.branch_id,
            notes=combined_notes or None,
            recorded_by=adj.adjusted_by,
            created_at=adj.created_at,
            evidence_url=adj.image_url,
            status=adj.status,
            reviewed_by=adj.reviewed_by,
            reviewed_at=adj.reviewed_at,
        ))

    # One row per LOSS: parts from the same submission (shelf batches +
    # warehouse) are merged, with the shelf/warehouse split noted.
    split: dict[uuid.UUID, list[int]] = {}
    for r in rows:
        split[r.id] = [0, 0]
        split[r.id][1 if wh_flags.get(r.id) else 0] += r.quantity
    merged: list[LossHistoryRow] = []
    by_group: dict[uuid.UUID, LossHistoryRow] = {}
    for r in rows:
        g = r.loss_group_id
        if g is None:
            merged.append(r)
        elif g in by_group:
            base = by_group[g]
            base.quantity += r.quantity
            base.value_naira += r.value_naira
            base.parts += 1
            split[base.id][0] += split[r.id][0]
            split[base.id][1] += split[r.id][1]
        else:
            by_group[g] = r
            merged.append(r)
    for r in merged:
        shelf_q, wh_q = split[r.id]
        if wh_q > 0:
            tag = "From warehouse" if shelf_q == 0 else f"Shelf {shelf_q} + Warehouse {wh_q}"
            r.notes = "; ".join([tag] + ([r.notes] if r.notes else []))
    rows = merged

    rows.sort(key=lambda r: r.created_at, reverse=True)
    rows = rows[:limit]

    branch_ids = {r.branch_id for r in rows if r.branch_id}
    branches_by_id = {}
    if branch_ids:
        b = await session.execute(select(Branch).where(Branch.id.in_(branch_ids)))
        branches_by_id = {row.id: row.name for row in b.scalars().all()}
    user_ids = {r.recorded_by for r in rows if r.recorded_by} | {r.reviewed_by for r in rows if r.reviewed_by}
    users_by_id = {}
    if user_ids:
        u = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {row.id: row.full_name for row in u.scalars().all()}
    for r in rows:
        r.branch_name = branches_by_id.get(r.branch_id) if r.branch_id else None
        r.recorded_by_name = users_by_id.get(r.recorded_by) if r.recorded_by else None
        r.reviewed_by_name = users_by_id.get(r.reviewed_by) if r.reviewed_by else None

    return rows
