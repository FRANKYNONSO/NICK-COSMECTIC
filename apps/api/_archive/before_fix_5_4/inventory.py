import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Union

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.routers.catalog import _PUBLIC_BASE_URL
from api.v1.schemas.inventory import (
    LossHistoryRow,
    LossReasonBreakdown,
    LossSummaryOut,
    StockAdjustmentCreate,
    StockAdjustmentOut,
    StockBatchCreate,
    StockBatchOut,
    StockLevelOut,
)
from infrastructure.db.models import Branch, Product, StockAdjustment, StockBatch, User
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


def _batch_to_out(batch: StockBatch) -> StockBatchOut:
    return StockBatchOut(
        id=batch.id,
        product_id=batch.product_id,
        branch_id=batch.branch_id,
        batch_number=batch.batch_number,
        quantity_received=batch.quantity_received,
        quantity_remaining=batch.quantity_remaining,
        unit_cost_naira=Money(batch.unit_cost_kobo).naira,
        expiry_date=batch.expiry_date,
        received_at=batch.received_at,
    )


@router.post("/stock-batches", response_model=StockBatchOut, status_code=status.HTTP_201_CREATED)
async def receive_stock(
    body: StockBatchCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_inventory")),
) -> StockBatchOut:
    product = await session.get(Product, body.product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

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
    _: User = Depends(get_current_user),
) -> list[StockBatchOut]:
    stmt = select(StockBatch)
    if product_id:
        stmt = stmt.where(StockBatch.product_id == product_id)
    if branch_id:
        stmt = stmt.where(StockBatch.branch_id == branch_id)
    stmt = stmt.order_by(StockBatch.received_at)

    result = await session.execute(stmt)
    return [_batch_to_out(b) for b in result.scalars().all()]


@router.get("/stock-levels", response_model=list[StockLevelOut])
async def stock_levels(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[StockLevelOut]:
    """Aggregated available stock per variant at a branch -- sums only
    non-expired batches with remaining quantity, matching the
    eligibility rule FifoStockAllocator uses in nickcosmetics_core, so
    what's shown here is exactly what checkout would actually be able
    to sell.
    """
    today = date.today()
    stmt = (
        select(
            StockBatch.product_id,
            func.sum(StockBatch.quantity_remaining).label("quantity_available"),
        )
        .where(
            StockBatch.branch_id == branch_id,
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
                branch_id=branch_id,
                quantity_available=row.quantity_available,
                reorder_level=reorder_level,
                is_low_stock=row.quantity_available <= reorder_level,
            )
        )
    return levels


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


@router.post("/stock-adjustments", status_code=status.HTTP_201_CREATED)
async def create_stock_adjustment(
    body: StockAdjustmentCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> dict:
    batch = await session.get(StockBatch, body.stock_batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Stock batch not found")

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

    if not is_pending_theft:
        new_remaining = batch.quantity_remaining + body.quantity_delta
        if new_remaining < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Adjustment would make quantity_remaining negative "
                    f"({batch.quantity_remaining} + {body.quantity_delta} = {new_remaining})"
                ),
            )
        batch.quantity_remaining = new_remaining

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
    product_for_value = await session.get(Product, batch.product_id)
    value_kobo = (
        abs(body.quantity_delta) * (product_for_value.selling_price_kobo if product_for_value else 0)
        if body.quantity_delta < 0 else None
    )
    adjustment = StockAdjustment(
        stock_batch_id=body.stock_batch_id,
        branch_id=batch.branch_id,
        adjusted_by=current_user.id,
        quantity_delta=body.quantity_delta,
        reason=body.reason,
        value_kobo=value_kobo,
        notes=body.notes,
        intake_receipt_id=body.intake_receipt_id,
        damage_caused_by=body.damage_caused_by if body.reason == "damaged" else None,
        damage_staff_name=body.damage_staff_name if body.reason == "damaged" else None,
        theft_staff_on_duty=body.theft_staff_on_duty if body.reason == "theft" else None,
        theft_noticed_at=body.theft_noticed_at if body.reason == "theft" else None,
        theft_caused_by=body.theft_caused_by if body.reason == "theft" else None,
        theft_by_staff_name=body.theft_by_staff_name if body.reason == "theft" else None,
        image_url=body.image_url,
        status="pending_confirmation" if is_pending_theft else "completed",
    )
    session.add(adjustment)
    await session.commit()
    return {
        "stock_batch_id": str(batch.id),
        "quantity_remaining": batch.quantity_remaining,
        "status": adjustment.status,
    }


@router.post("/stock-adjustments/{adjustment_id}/confirm-theft", response_model=StockAdjustmentOut)
async def confirm_theft_adjustment(
    adjustment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    # Super admin only -- spec section 4: theft is notified to the
    # super admin specifically for confirmation, not left to whoever
    # has ordinary manage_inventory access (that's who can *report*
    # one, same as damaged/expired).
    current_user: User = Depends(require_permission("manage_users")),
) -> StockAdjustmentOut:
    adjustment = await session.get(StockAdjustment, adjustment_id)
    if adjustment is None or adjustment.reason != "theft":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Theft report not found")
    if adjustment.status != "pending_confirmation":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This report has already been confirmed.")

    batch = await session.get(StockBatch, adjustment.stock_batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Stock batch not found")
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
    if adjustment.reviewed_at is None:
        adjustment.reviewed_by = current_user.id
        adjustment.reviewed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(adjustment)
    return await _adjustment_to_out(session, adjustment)


@router.get("/stock-adjustments/pending-review", response_model=list[StockAdjustmentOut])
async def list_pending_review_adjustments(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> list[StockAdjustmentOut]:
    """Backs the dashboard notification banner for section 4: every
    pending theft report (regardless of who's viewing -- confirmation
    is a super-admin action) plus every not-yet-reviewed damaged/
    expired loss, scoped to the current admin's own branch unless
    they're a super admin (branch_id is None).
    """
    conditions = [
        (StockAdjustment.reason == "theft") & (StockAdjustment.status == "pending_confirmation")
        | (StockAdjustment.reason.in_(["damaged", "expired"]) & StockAdjustment.reviewed_at.is_(None))
    ]
    if current_user.branch_id is not None:
        conditions.append(StockAdjustment.branch_id == current_user.branch_id)
    result = await session.execute(
        select(StockAdjustment).where(*conditions).order_by(StockAdjustment.created_at.desc()).limit(50)
    )
    adjustments = list(result.scalars().all())
    return [await _adjustment_to_out(session, a) for a in adjustments]


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
    ).where(StockAdjustment.value_kobo.is_not(None))
    if effective_branch_id is not None:
        stmt = stmt.where(StockAdjustment.branch_id == effective_branch_id)
    if start_date:
        stmt = stmt.where(StockAdjustment.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(StockAdjustment.created_at <= datetime.combine(end_date, datetime.max.time()))
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

    stmt = (
        select(StockAdjustment)
        .options(selectinload(StockAdjustment.stock_batch).selectinload(StockBatch.product).selectinload(Product.images))
        .where(StockAdjustment.value_kobo.is_not(None))
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
        ))

    rows.sort(key=lambda r: r.created_at, reverse=True)
    rows = rows[:limit]

    branch_ids = {r.branch_id for r in rows if r.branch_id}
    branches_by_id = {}
    if branch_ids:
        b = await session.execute(select(Branch).where(Branch.id.in_(branch_ids)))
        branches_by_id = {row.id: row.name for row in b.scalars().all()}
    user_ids = {r.recorded_by for r in rows if r.recorded_by}
    users_by_id = {}
    if user_ids:
        u = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {row.id: row.full_name for row in u.scalars().all()}
    for r in rows:
        r.branch_name = branches_by_id.get(r.branch_id) if r.branch_id else None
        r.recorded_by_name = users_by_id.get(r.recorded_by) if r.recorded_by else None

    return rows
