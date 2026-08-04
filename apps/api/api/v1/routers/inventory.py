import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.inventory import (
    StockAdjustmentCreate,
    StockBatchCreate,
    StockBatchOut,
    StockLevelOut,
)
from infrastructure.db.models import ProductVariant, StockAdjustment, StockBatch, User
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _batch_to_out(batch: StockBatch) -> StockBatchOut:
    return StockBatchOut(
        id=batch.id,
        variant_id=batch.variant_id,
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
    variant = await session.get(ProductVariant, body.variant_id)
    if variant is None or variant.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product variant not found")

    batch = StockBatch(
        variant_id=body.variant_id,
        branch_id=body.branch_id,
        purchase_order_item_id=body.purchase_order_item_id,
        batch_number=body.batch_number,
        quantity_received=body.quantity_received,
        quantity_remaining=body.quantity_received,
        unit_cost_kobo=Money.from_naira(body.unit_cost_naira).kobo,
        expiry_date=body.expiry_date,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return _batch_to_out(batch)


@router.get("/stock-batches", response_model=list[StockBatchOut])
async def list_stock_batches(
    variant_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[StockBatchOut]:
    stmt = select(StockBatch)
    if variant_id:
        stmt = stmt.where(StockBatch.variant_id == variant_id)
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
            StockBatch.variant_id,
            func.sum(StockBatch.quantity_remaining).label("quantity_available"),
        )
        .where(
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
        .group_by(StockBatch.variant_id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return []

    variant_ids = [row.variant_id for row in rows]
    variants_result = await session.execute(
        select(ProductVariant).where(ProductVariant.id.in_(variant_ids))
    )
    reorder_levels = {v.id: v.reorder_level for v in variants_result.scalars().all()}

    levels = []
    for row in rows:
        reorder_level = reorder_levels.get(row.variant_id, 0)
        levels.append(
            StockLevelOut(
                variant_id=row.variant_id,
                branch_id=branch_id,
                quantity_available=row.quantity_available,
                reorder_level=reorder_level,
                is_low_stock=row.quantity_available <= reorder_level,
            )
        )
    return levels


@router.post("/stock-adjustments", status_code=status.HTTP_201_CREATED)
async def create_stock_adjustment(
    body: StockAdjustmentCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> dict:
    batch = await session.get(StockBatch, body.stock_batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Stock batch not found")

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
    adjustment = StockAdjustment(
        stock_batch_id=body.stock_batch_id,
        adjusted_by=current_user.id,
        quantity_delta=body.quantity_delta,
        reason=body.reason,
        notes=body.notes,
    )
    session.add(adjustment)
    await session.commit()
    return {"stock_batch_id": str(batch.id), "quantity_remaining": batch.quantity_remaining}
