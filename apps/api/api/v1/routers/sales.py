import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission
from api.v1.schemas.sales import CheckoutRequest, RefundCreate, RefundOut, SaleItemOut, SaleOut
from infrastructure.db.checkout_adapter import (
    load_sale_repo,
    load_stock_batch_repo,
    persist_new_sale,
)
from infrastructure.db.models import Refund as ORMRefund
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import StockBatch as ORMStockBatch
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
                id=item.id,
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


@router.post("/{sale_id}/refund", response_model=RefundOut, status_code=status.HTTP_201_CREATED)
async def refund_sale(
    sale_id: uuid.UUID,
    body: RefundCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> RefundOut:
    """Process a full or partial refund against a sale.

    The refunded amount is always computed server-side from the
    original sale-item prices (quantity x unit_price), never taken as
    a client-supplied figure -- this prevents a refund request from
    crediting an arbitrary amount.

    Per line, `restock=True` returns the quantity to the exact stock
    batch it was originally sold from (accurate FIFO cost basis);
    `restock=False` credits the customer without restoring stock, for
    damaged/opened items that can't be resold.

    Note: this implementation does not persist a per-line refund
    ledger (the `refunds` table stores only an aggregate amount per
    refund event, matching the schema in docs/architecture Section
    4.2), so it does not prevent the same sale item from being
    refunded more than once across separate requests. A future
    migration adding a `refund_items` table would close this gap --
    flagged here rather than silently assumed away.
    """
    orm_sale = await session.get(ORMSale, sale_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sale not found")
    if orm_sale.status not in ("completed", "partially_refunded"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot refund a sale with status '{orm_sale.status}'",
        )

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == sale_id))
    sale_items_by_id = {i.id: i for i in items_result.scalars().all()}

    amount_kobo = 0
    for line in body.lines:
        sale_item = sale_items_by_id.get(line.sale_item_id)
        if sale_item is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"Sale item {line.sale_item_id} not found on this sale",
            )
        if line.quantity > sale_item.quantity:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot refund {line.quantity} of item {sale_item.id}: "
                    f"only {sale_item.quantity} were sold"
                ),
            )

        amount_kobo += line.quantity * sale_item.unit_price_kobo

        if line.restock and sale_item.stock_batch_id is not None:
            batch = await session.get(ORMStockBatch, sale_item.stock_batch_id)
            if batch is not None:
                batch.quantity_remaining += line.quantity

    refund = ORMRefund(
        sale_id=sale_id,
        processed_by=current_user.id,
        amount_kobo=amount_kobo,
        reason=body.reason,
    )
    session.add(refund)

    # Recompute sale status from cumulative refunded amount vs total.
    prior_refunded_result = await session.execute(
        select(func.sum(ORMRefund.amount_kobo)).where(ORMRefund.sale_id == sale_id)
    )
    prior_refunded_kobo = int(prior_refunded_result.scalar() or 0)
    total_refunded_kobo = prior_refunded_kobo + amount_kobo

    if total_refunded_kobo >= orm_sale.total_kobo:
        orm_sale.status = "refunded"
    else:
        orm_sale.status = "partially_refunded"

    await session.commit()
    await session.refresh(refund)

    return RefundOut(
        id=refund.id,
        sale_id=refund.sale_id,
        processed_by=refund.processed_by,
        amount_naira=Money(refund.amount_kobo).naira,
        reason=refund.reason,
        created_at=refund.created_at,
        sale_status=orm_sale.status,
    )