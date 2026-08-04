import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.purchasing import (
    PurchaseOrderCreate,
    PurchaseOrderItemOut,
    PurchaseOrderOut,
    ReceivePurchaseOrderRequest,
    SupplierCreate,
    SupplierOut,
)
from infrastructure.db.models import (
    ProductVariant,
    PurchaseOrder,
    PurchaseOrderItem,
    StockBatch,
    Supplier,
    User,
)
from nickcosmetics_core.domain.money import Money

router = APIRouter(tags=["purchasing"])


# ------------------------------------------------------------ SUPPLIERS

@router.post("/suppliers", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    body: SupplierCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_suppliers")),
) -> Supplier:
    supplier = Supplier(**body.model_dump())
    session.add(supplier)
    await session.commit()
    await session.refresh(supplier)
    return supplier


@router.get("/suppliers", response_model=list[SupplierOut])
async def list_suppliers(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[Supplier]:
    result = await session.execute(
        select(Supplier).where(Supplier.is_deleted.is_(False)).order_by(Supplier.name)
    )
    return list(result.scalars().all())


# ------------------------------------------------------- PURCHASE ORDERS

def _po_to_out(po: PurchaseOrder, items: list[PurchaseOrderItem]) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=po.id,
        supplier_id=po.supplier_id,
        branch_id=po.branch_id,
        status=po.status,
        ordered_by=po.ordered_by,
        ordered_at=po.ordered_at,
        expected_at=po.expected_at,
        created_at=po.created_at,
        items=[
            PurchaseOrderItemOut(
                id=i.id,
                variant_id=i.variant_id,
                quantity_ordered=i.quantity_ordered,
                quantity_received=i.quantity_received,
                unit_cost_naira=Money(i.unit_cost_kobo).naira,
            )
            for i in items
        ],
    )


@router.post(
    "/purchase-orders", response_model=PurchaseOrderOut, status_code=status.HTTP_201_CREATED
)
async def create_purchase_order(
    body: PurchaseOrderCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_inventory")),
) -> PurchaseOrderOut:
    supplier = await session.get(Supplier, body.supplier_id)
    if supplier is None or supplier.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Supplier not found")

    for line in body.items:
        variant = await session.get(ProductVariant, line.variant_id)
        if variant is None or variant.is_deleted:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=f"Product variant {line.variant_id} not found"
            )

    po = PurchaseOrder(
        supplier_id=body.supplier_id,
        branch_id=body.branch_id,
        status="draft",
        expected_at=body.expected_at,
    )
    session.add(po)
    await session.flush()  # assigns po.id without ending the transaction

    items = [
        PurchaseOrderItem(
            purchase_order_id=po.id,
            variant_id=line.variant_id,
            quantity_ordered=line.quantity_ordered,
            unit_cost_kobo=Money.from_naira(line.unit_cost_naira).kobo,
        )
        for line in body.items
    ]
    session.add_all(items)
    await session.commit()
    await session.refresh(po)

    return _po_to_out(po, items)


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
async def list_purchase_orders(
    status_filter: str | None = None,
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[PurchaseOrderOut]:
    stmt = select(PurchaseOrder)
    if status_filter:
        stmt = stmt.where(PurchaseOrder.status == status_filter)
    if branch_id:
        stmt = stmt.where(PurchaseOrder.branch_id == branch_id)
    stmt = stmt.order_by(PurchaseOrder.created_at.desc())

    result = await session.execute(stmt)
    orders = list(result.scalars().all())
    if not orders:
        return []

    order_ids = [o.id for o in orders]
    items_result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id.in_(order_ids))
    )
    items_by_po: dict[uuid.UUID, list[PurchaseOrderItem]] = {}
    for item in items_result.scalars().all():
        items_by_po.setdefault(item.purchase_order_id, []).append(item)

    return [_po_to_out(o, items_by_po.get(o.id, [])) for o in orders]


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
async def get_purchase_order(
    po_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> PurchaseOrderOut:
    po = await session.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Purchase order not found")

    items_result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po_id)
    )
    return _po_to_out(po, list(items_result.scalars().all()))


@router.post("/purchase-orders/{po_id}/mark-ordered", response_model=PurchaseOrderOut)
async def mark_purchase_order_ordered(
    po_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_inventory")),
) -> PurchaseOrderOut:
    po = await session.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Purchase order not found")
    if po.status != "draft":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Only draft purchase orders can be marked as ordered (current status: '{po.status}')",
        )

    po.status = "ordered"
    po.ordered_by = current_user.id
    po.ordered_at = datetime.utcnow()
    await session.commit()
    await session.refresh(po)

    items_result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po_id)
    )
    return _po_to_out(po, list(items_result.scalars().all()))


@router.post("/purchase-orders/{po_id}/receive", response_model=PurchaseOrderOut)
async def receive_purchase_order(
    po_id: uuid.UUID,
    body: ReceivePurchaseOrderRequest,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_inventory")),
) -> PurchaseOrderOut:
    """Receiving goods against a PO creates real StockBatch rows (one
    per receiving line, at the PO's agreed cost) and advances the PO's
    status -- this is the bridge between purchasing and the inventory
    system built earlier. Partial receiving (less than ordered) is
    fully supported, matching real supplier behavior.
    """
    po = await session.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Purchase order not found")
    if po.status not in ("ordered", "partially_received"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot receive against a purchase order with status '{po.status}' -- "
                "mark it as ordered first"
            ),
        )

    items_result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po_id)
    )
    po_items_by_id = {i.id: i for i in items_result.scalars().all()}

    for line in body.lines:
        po_item = po_items_by_id.get(line.purchase_order_item_id)
        if po_item is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order item {line.purchase_order_item_id} not found on this PO",
            )
        remaining = po_item.quantity_ordered - po_item.quantity_received
        if line.quantity_now > remaining:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot receive {line.quantity_now} for item {po_item.id}: "
                    f"only {remaining} remain unreceived"
                ),
            )

        batch = StockBatch(
            variant_id=po_item.variant_id,
            branch_id=po.branch_id,
            purchase_order_item_id=po_item.id,
            batch_number=line.batch_number,
            quantity_received=line.quantity_now,
            quantity_remaining=line.quantity_now,
            unit_cost_kobo=po_item.unit_cost_kobo,
            expiry_date=line.expiry_date,
        )
        session.add(batch)
        po_item.quantity_received += line.quantity_now

    # Recompute PO-level status from its items' receipt state.
    all_items = list(po_items_by_id.values())
    if all(i.quantity_received >= i.quantity_ordered for i in all_items):
        po.status = "received"
    else:
        po.status = "partially_received"

    await session.commit()
    await session.refresh(po)

    items_result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po_id)
    )
    return _po_to_out(po, list(items_result.scalars().all()))
