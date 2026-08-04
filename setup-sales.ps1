Write-Host "Creating sales/checkout files..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "apps\api\api\v1\schemas" | Out-Null
New-Item -ItemType Directory -Force -Path "apps\api\api\v1\routers" | Out-Null
New-Item -ItemType Directory -Force -Path "apps\api\infrastructure\db" | Out-Null

# --- apps/api/api/v1/schemas/sales.py ---
@'
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SaleLineIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(gt=0)
    unit_price_naira: float = Field(ge=0)


class CheckoutRequest(BaseModel):
    branch_id: uuid.UUID
    channel: str = Field(pattern="^(pos|website|mobile)$")
    # Client-generated idempotency key. The desktop POS generates this
    # offline at sale time; retried syncs with the same key never
    # create a duplicate sale. See docs/architecture Section 7.
    local_ref_id: str = Field(min_length=1, max_length=200)
    items: list[SaleLineIn] = Field(min_length=1)
    customer_id: uuid.UUID | None = None
    discount_naira: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|card|transfer|online)$")


class SaleItemOut(BaseModel):
    variant_id: uuid.UUID
    stock_batch_id: uuid.UUID | None
    quantity: int
    unit_price_naira: float
    line_total_naira: float


class SaleOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    customer_id: uuid.UUID | None
    served_by: uuid.UUID | None
    channel: str
    status: str
    subtotal_naira: float
    discount_naira: float
    total_naira: float
    payment_method: str | None
    local_ref_id: str | None
    created_at: datetime
    items: list[SaleItemOut]
'@ | Set-Content -Path "apps\api\api\v1\schemas\sales.py" -Encoding utf8
Write-Host "  Created apps\api\api\v1\schemas\sales.py" -ForegroundColor Green

# --- apps/api/infrastructure/db/checkout_adapter.py ---
@'
"""Adapter bridging the async SQLAlchemy database with the
synchronous, framework-agnostic SalesService in nickcosmetics_core.

Pattern: async pre-fetch -> run domain logic in-memory (sync, fast, no
I/O) -> async persist the result. This is what lets the exact same
SalesService/FifoStockAllocator code run unmodified here AND inside
the offline desktop app's synchronous SQLite access later -- the
domain layer never touches FastAPI, SQLAlchemy, or asyncio.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nickcosmetics_core.domain.inventory.stock import StockBatch as DomainStockBatch
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentMethod
from nickcosmetics_core.domain.sales.sale import Sale as DomainSale
from nickcosmetics_core.domain.sales.sale import SaleChannel, SaleItem, SaleStatus
from nickcosmetics_core.interfaces.repositories import SaleRepository, StockBatchRepository

from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import StockBatch as ORMStockBatch


class PreloadedStockBatchRepository(StockBatchRepository):
    """In-memory, synchronous -- wraps StockBatch rows pre-fetched
    from the DB for exactly the variants involved in one checkout
    call. Mutations to `quantity_remaining` happen directly on the
    SQLAlchemy-tracked ORM objects, so they're picked up automatically
    on the caller's next `session.commit()` -- no explicit re-save step.
    """

    def __init__(self, orm_batches: list[ORMStockBatch]):
        self._orm_by_id: dict[uuid.UUID, ORMStockBatch] = {b.id: b for b in orm_batches}

    def get_batches_for_variant(self, variant_id: str, branch_id: str) -> list[DomainStockBatch]:
        vid, bid = uuid.UUID(variant_id), uuid.UUID(branch_id)
        return [
            DomainStockBatch(
                id=str(b.id),
                variant_id=str(b.variant_id),
                branch_id=str(b.branch_id),
                quantity_received=b.quantity_received,
                quantity_remaining=b.quantity_remaining,
                unit_cost=Money(b.unit_cost_kobo),
                expiry_date=b.expiry_date,
                received_at=b.received_at,
            )
            for b in self._orm_by_id.values()
            if b.variant_id == vid and b.branch_id == bid
        ]

    def save_batch(self, batch: DomainStockBatch) -> None:
        self._orm_by_id[uuid.UUID(batch.id)].quantity_remaining = batch.quantity_remaining


class PreloadedSaleRepository(SaleRepository):
    """Wraps a possibly-pre-existing Sale (for idempotency checks) and
    captures whatever SalesService.checkout() ultimately saves, so the
    caller can tell a genuine new sale apart from an idempotent replay.
    """

    def __init__(self, existing_orm_sale: ORMSale | None, existing_orm_items: list[ORMSaleItem]):
        self._existing_sale = existing_orm_sale
        self._existing_items = existing_orm_items
        self.saved_domain_sale: DomainSale | None = None
        self.was_idempotent_hit = False

    def get_by_local_ref_id(self, local_ref_id: str) -> DomainSale | None:
        if self._existing_sale is None:
            return None
        self.was_idempotent_hit = True
        sale = DomainSale(
            id=str(self._existing_sale.id),
            branch_id=str(self._existing_sale.branch_id),
            served_by=str(self._existing_sale.served_by) if self._existing_sale.served_by else "",
            channel=SaleChannel(self._existing_sale.channel),
            local_ref_id=self._existing_sale.local_ref_id or "",
            customer_id=str(self._existing_sale.customer_id) if self._existing_sale.customer_id else None,
            discount=Money(self._existing_sale.discount_kobo),
            payment_method=PaymentMethod(self._existing_sale.payment_method)
            if self._existing_sale.payment_method
            else None,
            status=SaleStatus(self._existing_sale.status),
            created_at=self._existing_sale.created_at,
        )
        for item in self._existing_items:
            sale.add_item(
                SaleItem(
                    variant_id=str(item.variant_id),
                    stock_batch_id=str(item.stock_batch_id) if item.stock_batch_id else "",
                    quantity=item.quantity,
                    unit_price=Money(item.unit_price_kobo),
                )
            )
        return sale

    def save(self, sale: DomainSale) -> None:
        self.saved_domain_sale = sale


async def load_stock_batch_repo(
    session: AsyncSession, variant_ids: list[uuid.UUID], branch_id: uuid.UUID
) -> PreloadedStockBatchRepository:
    result = await session.execute(
        select(ORMStockBatch).where(
            ORMStockBatch.variant_id.in_(variant_ids),
            ORMStockBatch.branch_id == branch_id,
            ORMStockBatch.quantity_remaining > 0,
        )
    )
    return PreloadedStockBatchRepository(list(result.scalars().all()))


async def load_sale_repo(session: AsyncSession, local_ref_id: str) -> PreloadedSaleRepository:
    result = await session.execute(select(ORMSale).where(ORMSale.local_ref_id == local_ref_id))
    existing_sale = result.scalar_one_or_none()

    existing_items: list[ORMSaleItem] = []
    if existing_sale is not None:
        items_result = await session.execute(
            select(ORMSaleItem).where(ORMSaleItem.sale_id == existing_sale.id)
        )
        existing_items = list(items_result.scalars().all())

    return PreloadedSaleRepository(existing_sale, existing_items)


def persist_new_sale(
    session: AsyncSession, domain_sale: DomainSale, payment_method: str | None
) -> ORMSale:
    """Turn a freshly-created domain Sale into ORM rows and stage them
    on the session (caller still needs to commit). Stock batch
    mutations from PreloadedStockBatchRepository.save_batch() are
    already staged automatically via the SQLAlchemy identity map.
    """
    orm_sale = ORMSale(
        id=uuid.UUID(domain_sale.id),
        branch_id=uuid.UUID(domain_sale.branch_id),
        customer_id=uuid.UUID(domain_sale.customer_id) if domain_sale.customer_id else None,
        served_by=uuid.UUID(domain_sale.served_by) if domain_sale.served_by else None,
        channel=domain_sale.channel.value,
        status=domain_sale.status.value,
        subtotal_kobo=domain_sale.subtotal.kobo,
        discount_kobo=domain_sale.discount.kobo,
        total_kobo=domain_sale.total.kobo,
        payment_method=payment_method,
        local_ref_id=domain_sale.local_ref_id,
    )
    session.add(orm_sale)

    for item in domain_sale.items:
        session.add(
            ORMSaleItem(
                sale_id=orm_sale.id,
                variant_id=uuid.UUID(item.variant_id),
                stock_batch_id=uuid.UUID(item.stock_batch_id) if item.stock_batch_id else None,
                quantity=item.quantity,
                unit_price_kobo=item.unit_price.kobo,
                line_total_kobo=item.line_total.kobo,
            )
        )

    return orm_sale
'@ | Set-Content -Path "apps\api\infrastructure\db\checkout_adapter.py" -Encoding utf8
Write-Host "  Created apps\api\infrastructure\db\checkout_adapter.py" -ForegroundColor Green

# --- apps/api/api/v1/routers/sales.py ---
@'
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission
from api.v1.schemas.sales import CheckoutRequest, SaleItemOut, SaleOut
from infrastructure.db.checkout_adapter import (
    load_sale_repo,
    load_stock_batch_repo,
    persist_new_sale,
)
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import User
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import SaleChannel
from nickcosmetics_core.domain.users.user import Role as DomainRole
from nickcosmetics_core.domain.users.user import User as DomainUser
from nickcosmetics_core.exceptions import InsufficientStockError

# SalesService itself, plus the CheckoutLine value object it expects --
# this router is the seam where the framework-agnostic core package
# gets wired into the live async API for the first time.
from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService

router = APIRouter(prefix="/sales", tags=["sales"])


def _to_sale_out(orm_sale: ORMSale, orm_items: list[ORMSaleItem]) -> SaleOut:
    return SaleOut(
        id=orm_sale.id,
        branch_id=orm_sale.branch_id,
        customer_id=orm_sale.customer_id,
        served_by=orm_sale.served_by,
        channel=orm_sale.channel,
        status=orm_sale.status,
        subtotal_naira=Money(orm_sale.subtotal_kobo).naira,
        discount_naira=Money(orm_sale.discount_kobo).naira,
        total_naira=Money(orm_sale.total_kobo).naira,
        payment_method=orm_sale.payment_method,
        local_ref_id=orm_sale.local_ref_id,
        created_at=orm_sale.created_at,
        items=[
            SaleItemOut(
                variant_id=item.variant_id,
                stock_batch_id=item.stock_batch_id,
                quantity=item.quantity,
                unit_price_naira=Money(item.unit_price_kobo).naira,
                line_total_naira=Money(item.line_total_kobo).naira,
            )
            for item in orm_items
        ],
    )


@router.post("/checkout", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
async def checkout(
    body: CheckoutRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> SaleOut:
    variant_ids = [line.variant_id for line in body.items]

    stock_repo = await load_stock_batch_repo(session, variant_ids, body.branch_id)
    sale_repo = await load_sale_repo(session, body.local_ref_id)

    service = SalesService(stock_repo, sale_repo)
    checkout_lines = [
        CheckoutLine(
            variant_id=str(line.variant_id),
            quantity=line.quantity,
            unit_price=Money.from_naira(line.unit_price_naira),
        )
        for line in body.items
    ]

    try:
        domain_sale = service.checkout(
            sale_id=str(uuid.uuid4()),
            branch_id=str(body.branch_id),
            served_by=str(current_user.id),
            channel=SaleChannel(body.channel),
            local_ref_id=body.local_ref_id,
            lines=checkout_lines,
            customer_id=str(body.customer_id) if body.customer_id else None,
            discount=Money.from_naira(body.discount_naira),
        )
    except InsufficientStockError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if sale_repo.was_idempotent_hit:
        # Retried sync of a sale we've already processed -- return the
        # original record unchanged, don't touch stock again.
        result = await session.execute(select(ORMSale).where(ORMSale.id == uuid.UUID(domain_sale.id)))
        orm_sale = result.scalar_one()
        items_result = await session.execute(
            select(ORMSaleItem).where(ORMSaleItem.sale_id == orm_sale.id)
        )
        return _to_sale_out(orm_sale, list(items_result.scalars().all()))

    orm_sale = persist_new_sale(session, domain_sale, body.payment_method)
    await session.commit()
    await session.refresh(orm_sale)

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == orm_sale.id))
    return _to_sale_out(orm_sale, list(items_result.scalars().all()))


@router.get("/{sale_id}", response_model=SaleOut)
async def get_sale(
    sale_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_own_sales")),
) -> SaleOut:
    orm_sale = await session.get(ORMSale, sale_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sale not found")

    domain_user = DomainUser(
        id=str(current_user.id),
        full_name=current_user.full_name,
        role=DomainRole(current_user.role),
        branch_id=str(current_user.branch_id) if current_user.branch_id else None,
    )
    served_by = str(orm_sale.served_by) if orm_sale.served_by else ""
    if not domain_user.can_view_sales_of(served_by):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only view your own sales")

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == sale_id))
    return _to_sale_out(orm_sale, list(items_result.scalars().all()))


@router.get("", response_model=list[SaleOut])
async def list_sales(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_own_sales")),
) -> list[SaleOut]:
    # Staff are hard-restricted to their own sales regardless of query
    # params -- this is enforced server-side, never trusted from the
    # client, per docs/architecture Section 8.
    stmt = select(ORMSale)
    if current_user.role == "staff":
        stmt = stmt.where(ORMSale.served_by == current_user.id)
    if branch_id:
        stmt = stmt.where(ORMSale.branch_id == branch_id)
    stmt = stmt.order_by(ORMSale.created_at.desc())

    result = await session.execute(stmt)
    orm_sales = list(result.scalars().all())
    if not orm_sales:
        return []

    sale_ids = [s.id for s in orm_sales]
    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id.in_(sale_ids)))
    items_by_sale: dict[uuid.UUID, list[ORMSaleItem]] = {}
    for item in items_result.scalars().all():
        items_by_sale.setdefault(item.sale_id, []).append(item)

    return [_to_sale_out(s, items_by_sale.get(s.id, [])) for s in orm_sales]
'@ | Set-Content -Path "apps\api\api\v1\routers\sales.py" -Encoding utf8
Write-Host "  Created apps\api\api\v1\routers\sales.py" -ForegroundColor Green

# --- apps/api/main.py (full replacement) ---
@'
from fastapi import FastAPI

from api.v1.routers import auth, branches, catalog, inventory, sales

app = FastAPI(
    title="Nick Cosmetics API",
    version="0.1.0",
    description="Cloud API for the Nick Cosmetics retail platform.",
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(branches.router, prefix="/api/v1")
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(sales.router, prefix="/api/v1")


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