Write-Host "Creating branches + inventory files..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "apps\api\api\v1\schemas" | Out-Null
New-Item -ItemType Directory -Force -Path "apps\api\api\v1\routers" | Out-Null

# --- apps/api/api/v1/schemas/branches.py ---
@'
import uuid

from pydantic import BaseModel, Field


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    phone: str | None = None
    is_warehouse: bool = False


class BranchOut(BaseModel):
    id: uuid.UUID
    name: str
    address: str | None
    phone: str | None
    is_warehouse: bool

    model_config = {"from_attributes": True}
'@ | Set-Content -Path "apps\api\api\v1\schemas\branches.py" -Encoding utf8

Write-Host "  Created apps\api\api\v1\schemas\branches.py" -ForegroundColor Green

# --- apps/api/api/v1/routers/branches.py ---
@'
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.branches import BranchCreate, BranchOut
from infrastructure.db.models import Branch, User

router = APIRouter(prefix="/branches", tags=["branches"])


@router.post("", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
async def create_branch(
    body: BranchCreate,
    session: AsyncSession = Depends(get_db_session),
    # Opening a new branch is a system-wide expansion decision, reserved
    # for Super Admin only -- Branch Managers operate within a branch,
    # they don't create new ones. See docs/architecture Section 3.
    _: User = Depends(require_permission("configure_system")),
) -> Branch:
    existing = await session.execute(select(Branch).where(Branch.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Branch '{body.name}' already exists")

    branch = Branch(**body.model_dump())
    session.add(branch)
    await session.commit()
    await session.refresh(branch)
    return branch


@router.get("", response_model=list[BranchOut])
async def list_branches(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[Branch]:
    result = await session.execute(select(Branch).order_by(Branch.name))
    return list(result.scalars().all())
'@ | Set-Content -Path "apps\api\api\v1\routers\branches.py" -Encoding utf8

Write-Host "  Created apps\api\api\v1\routers\branches.py" -ForegroundColor Green

# --- apps/api/api/v1/schemas/inventory.py ---
@'
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class StockBatchCreate(BaseModel):
    """Receiving new stock. cost_price is in Naira for human input;
    converted to kobo internally (see infrastructure.db.models and
    nickcosmetics_core.domain.money.Money).
    """

    variant_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_received: int = Field(gt=0)
    unit_cost_naira: float = Field(ge=0)
    batch_number: str | None = None
    expiry_date: date | None = None
    purchase_order_item_id: uuid.UUID | None = None


class StockBatchOut(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    branch_id: uuid.UUID
    batch_number: str | None
    quantity_received: int
    quantity_remaining: int
    unit_cost_naira: float
    expiry_date: date | None
    received_at: datetime

    model_config = {"from_attributes": True}


class StockAdjustmentCreate(BaseModel):
    stock_batch_id: uuid.UUID
    quantity_delta: int  # negative for loss/damage/theft, positive for recount-up
    reason: str = Field(pattern="^(damaged|expired|theft|recount|other)$")
    notes: str | None = None


class StockLevelOut(BaseModel):
    """Aggregated available stock for one variant at one branch --
    what the POS/admin dashboard actually wants to display, rather
    than raw per-batch rows.
    """

    variant_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_available: int
    reorder_level: int
    is_low_stock: bool
'@ | Set-Content -Path "apps\api\api\v1\schemas\inventory.py" -Encoding utf8

Write-Host "  Created apps\api\api\v1\schemas\inventory.py" -ForegroundColor Green

# --- apps/api/api/v1/routers/inventory.py ---
@'
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
'@ | Set-Content -Path "apps\api\api\v1\routers\inventory.py" -Encoding utf8

Write-Host "  Created apps\api\api\v1\routers\inventory.py" -ForegroundColor Green

# --- apps/api/main.py (full replacement) ---
@'
from fastapi import FastAPI

from api.v1.routers import auth, branches, catalog, inventory

app = FastAPI(
    title="Nick Cosmetics API",
    version="0.1.0",
    description="Cloud API for the Nick Cosmetics retail platform.",
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(branches.router, prefix="/api/v1")
app.include_router(inventory.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nick-cosmetics-api"}
'@ | Set-Content -Path "apps\api\main.py" -Encoding utf8

Write-Host "  Updated apps\api\main.py" -ForegroundColor Green

Write-Host ""
Write-Host "Done. Now restart the containers:" -ForegroundColor Cyan
Write-Host "  cd infra\docker" -ForegroundColor Yellow
Write-Host "  docker compose -f docker-compose.dev.yml down" -ForegroundColor Yellow
Write-Host "  docker compose -f docker-compose.dev.yml up" -ForegroundColor Yellow
