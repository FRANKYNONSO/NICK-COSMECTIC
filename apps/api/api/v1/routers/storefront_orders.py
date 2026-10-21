"""Website (storefront) orders.

Why this exists as its own router: POS checkout deducts stock from ONE
branch. A website order should be fillable from ALL branches (50 from
Branch A + 5 from Branch B for a 55-piece order). Rather than change POS
checkout, this router splits a website order into one ordinary Sale per
branch (each one running through the exact same SalesService /
FIFO / row-locking code POS uses) and links them together with
storefront_order_sales so staff see ONE merged order to dispatch.

Nothing here alters POS checkout, the sales table, or stock tables.
"""

import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission, require_role
from api.v1.routers._storefront_logic import allocate_across_branches, split_discount
from api.v1.routers.sales import POINTS_PER_NAIRA_SPENT
from infrastructure.db.audit import log_audit_event
from infrastructure.db.checkout_adapter import (
    PreloadedStockBatchRepository,
    load_sale_repo,
    persist_new_sale,
)
from infrastructure.db.models import Branch as ORMBranch
from infrastructure.db.models import Customer, CustomerAddress, Product
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import StockBatch as ORMStockBatch
from infrastructure.db.models import StorefrontCoupon, StorefrontOrder, StorefrontOrderSale, User
from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import SaleChannel
from nickcosmetics_core.exceptions import InsufficientStockError

router = APIRouter(prefix="/storefront", tags=["storefront"])

ORDER_STATUSES = {"new", "processing", "waybilled", "delivered", "cancelled"}
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


# ------------------------------------------------------------------ schemas


class StorefrontOrderLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(..., ge=1, le=10000)


class StorefrontCustomerIn(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=5, max_length=30)
    email: str | None = Field(None, max_length=200)


class StorefrontDeliveryIn(BaseModel):
    address_line: str = Field(..., min_length=3, max_length=500)
    city: str | None = Field(None, max_length=120)
    lga: str | None = Field(None, max_length=120)
    state: str = Field(..., min_length=2, max_length=100)


class StorefrontOrderCreate(BaseModel):
    reference: str = Field(..., min_length=8, max_length=100)
    customer: StorefrontCustomerIn
    delivery: StorefrontDeliveryIn
    items: list[StorefrontOrderLineIn]
    coupon_code: str | None = Field(None, max_length=50)
    shipping_fee_naira: float = Field(0, ge=0, le=10_000_000)
    shipping_note: str | None = Field(None, max_length=500)


class StockOut(BaseModel):
    product_id: uuid.UUID
    quantity_available: int
    is_low_stock: bool


class OrderSaleItemOut(BaseModel):
    product_id: uuid.UUID
    product_name: str
    quantity: int
    unit_price_naira: float
    line_total_naira: float


class OrderSaleOut(BaseModel):
    sale_id: uuid.UUID
    ticket: str
    branch_id: uuid.UUID
    branch_name: str
    status: str
    total_naira: float
    items: list[OrderSaleItemOut]


class PickBranchOut(BaseModel):
    branch_id: uuid.UUID
    branch_name: str
    quantity: int


class PickLineOut(BaseModel):
    product_id: uuid.UUID
    product_name: str
    total_quantity: int
    branches: list[PickBranchOut]


class StorefrontOrderOut(BaseModel):
    id: uuid.UUID
    reference: str
    status: str
    created_at: datetime
    customer_name: str
    customer_phone: str
    customer_email: str | None
    delivery_address: str
    delivery_city: str | None
    delivery_lga: str | None
    delivery_state: str
    shipping_fee_naira: float
    shipping_note: str | None
    subtotal_naira: float
    discount_naira: float
    total_naira: float
    payable_naira: float
    coupon_code: str | None
    waybill_ref: str | None
    admin_note: str | None
    status_updated_at: datetime | None
    payment_status: str
    payment_method: str | None
    amount_received_naira: float | None
    paid_at: datetime | None
    paid_recorded_by: uuid.UUID | None
    payment_note: str | None
    # Suggested place to gather the parts into one parcel: the branch
    # sending the most pieces. Staff can ignore it.
    merge_at_branch_id: uuid.UUID | None
    merge_at_branch_name: str | None
    sales: list[OrderSaleOut]
    pick_list: list[PickLineOut]


class StorefrontOrderUpdate(BaseModel):
    status: str | None = None
    waybill_ref: str | None = Field(None, max_length=100)
    admin_note: str | None = Field(None, max_length=2000)


class StorefrontOrderPaymentIn(BaseModel):
    method: str = Field(..., pattern="^(bank_transfer|cash_on_delivery)$")
    amount_naira: float = Field(..., gt=0, le=100000000)
    note: str | None = Field(None, max_length=500)


class StorefrontOrderUndoPaymentIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class CouponGenerate(BaseModel):
    discount_amount_naira: float = Field(..., gt=0, le=10_000_000)
    expires_in_hours: int = Field(24, ge=1, le=24 * 90)
    count: int = Field(1, ge=1, le=50)
    note: str | None = Field(None, max_length=200)


class CouponOut(BaseModel):
    id: uuid.UUID
    code: str
    discount_amount_naira: float
    status: str
    expires_at: datetime | None
    started_at: datetime | None
    valid_hours: int
    note: str | None
    created_at: datetime
    redeemed_at: datetime | None
    redeemed_order_id: uuid.UUID | None


class CouponReveal(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)


class CouponCheckOut(BaseModel):
    valid: bool
    discount_amount_naira: float
    message: str


# ------------------------------------------------------------------ helpers


def _naira(kobo: int | None) -> float:
    return round((kobo or 0) / 100, 2)


async def _open_booking_holds(
    session: AsyncSession, product_ids: list[uuid.UUID] | None
) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    """Units tied up in still-open bookings, per (product, branch). Same
    rule POS uses (sales.py::_get_open_booking_holds): an open booking
    reserves stock against other sales."""
    stmt = (
        select(ORMSaleItem.product_id, ORMSale.branch_id, func.sum(ORMSaleItem.quantity))
        .join(ORMSale, ORMSale.id == ORMSaleItem.sale_id)
        .where(ORMSale.status == "booking")
    )
    if product_ids is not None:
        stmt = stmt.where(ORMSaleItem.product_id.in_(product_ids))
    stmt = stmt.group_by(ORMSaleItem.product_id, ORMSale.branch_id)
    rows = (await session.execute(stmt)).all()
    return {(pid, bid): int(total or 0) for pid, bid, total in rows}


def _unit_price_kobo(product: Product, total_quantity: int) -> int:
    """Same rule as POS: once the quantity reaches the product's wholesale
    minimum, the whole line is priced at the wholesale price."""
    if (
        product.wholesale_price_kobo is not None
        and product.wholesale_min_quantity is not None
        and total_quantity >= product.wholesale_min_quantity
    ):
        return int(product.wholesale_price_kobo)
    return int(product.selling_price_kobo)


async def _find_order_by_reference(session: AsyncSession, reference: str) -> StorefrontOrder | None:
    result = await session.execute(select(StorefrontOrder).where(StorefrontOrder.reference == reference))
    return result.scalar_one_or_none()


async def _build_order_outs(
    session: AsyncSession, orders: list[StorefrontOrder]
) -> list[StorefrontOrderOut]:
    if not orders:
        return []
    order_ids = [o.id for o in orders]

    links = (
        (await session.execute(select(StorefrontOrderSale).where(StorefrontOrderSale.order_id.in_(order_ids))))
        .scalars()
        .all()
    )
    sale_ids = [link.sale_id for link in links]
    sales_by_id: dict[uuid.UUID, ORMSale] = {}
    items: list[ORMSaleItem] = []
    if sale_ids:
        sales_by_id = {
            s.id: s
            for s in (await session.execute(select(ORMSale).where(ORMSale.id.in_(sale_ids)))).scalars().all()
        }
        items = list(
            (await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id.in_(sale_ids)))).scalars().all()
        )

    product_ids = {i.product_id for i in items}
    product_names: dict[uuid.UUID, str] = {}
    if product_ids:
        product_names = dict(
            (await session.execute(select(Product.id, Product.name).where(Product.id.in_(product_ids)))).all()
        )
    branch_names = dict((await session.execute(select(ORMBranch.id, ORMBranch.name))).all())

    # A sale can hold several SaleItem rows for one product (FIFO batches);
    # fold them into one line per product.
    folded: dict[uuid.UUID, dict[uuid.UUID, dict]] = defaultdict(dict)
    for item in items:
        line = folded[item.sale_id].setdefault(
            item.product_id, {"quantity": 0, "total": 0, "unit": item.unit_price_kobo}
        )
        line["quantity"] += item.quantity
        line["total"] += item.line_total_kobo

    links_by_order: dict[uuid.UUID, list[StorefrontOrderSale]] = defaultdict(list)
    for link in links:
        links_by_order[link.order_id].append(link)

    outs: list[StorefrontOrderOut] = []
    for order in orders:
        order_sales: list[OrderSaleOut] = []
        pick: dict[uuid.UUID, dict] = {}
        branch_totals: dict[uuid.UUID, int] = defaultdict(int)

        for link in sorted(
            links_by_order.get(order.id, []), key=lambda l: (branch_names.get(l.branch_id, ""), str(l.sale_id))
        ):
            sale = sales_by_id.get(link.sale_id)
            if sale is None:
                continue
            sale_items: list[OrderSaleItemOut] = []
            for pid, line in folded.get(link.sale_id, {}).items():
                name = product_names.get(pid, str(pid))
                sale_items.append(
                    OrderSaleItemOut(
                        product_id=pid,
                        product_name=name,
                        quantity=line["quantity"],
                        unit_price_naira=_naira(line["unit"]),
                        line_total_naira=_naira(line["total"]),
                    )
                )
                entry = pick.setdefault(pid, {"name": name, "total": 0, "branches": []})
                entry["total"] += line["quantity"]
                entry["branches"].append(
                    PickBranchOut(
                        branch_id=link.branch_id,
                        branch_name=branch_names.get(link.branch_id, str(link.branch_id)),
                        quantity=line["quantity"],
                    )
                )
                branch_totals[link.branch_id] += line["quantity"]
            order_sales.append(
                OrderSaleOut(
                    sale_id=sale.id,
                    ticket=str(sale.id)[:8].upper(),
                    branch_id=link.branch_id,
                    branch_name=branch_names.get(link.branch_id, str(link.branch_id)),
                    status=sale.status,
                    total_naira=_naira(sale.total_kobo),
                    items=sale_items,
                )
            )

        merge_branch = None
        if branch_totals:
            merge_branch = max(branch_totals, key=lambda b: (branch_totals[b], branch_names.get(b, "")))

        outs.append(
            StorefrontOrderOut(
                id=order.id,
                reference=order.reference,
                status=order.status,
                created_at=order.created_at,
                customer_name=order.customer_name,
                customer_phone=order.customer_phone,
                customer_email=order.customer_email,
                delivery_address=order.delivery_address,
                delivery_city=order.delivery_city,
                delivery_lga=order.delivery_lga,
                delivery_state=order.delivery_state,
                shipping_fee_naira=_naira(order.shipping_fee_kobo),
                shipping_note=order.shipping_note,
                subtotal_naira=_naira(order.subtotal_kobo),
                discount_naira=_naira(order.discount_kobo),
                total_naira=_naira(order.total_kobo),
                payable_naira=_naira(order.total_kobo + order.shipping_fee_kobo),
                coupon_code=order.coupon_code,
                waybill_ref=order.waybill_ref,
                admin_note=order.admin_note,
                status_updated_at=order.status_updated_at,
                payment_status=order.payment_status,
                payment_method=order.payment_method,
                amount_received_naira=(
                    None if order.amount_received_kobo is None else _naira(order.amount_received_kobo)
                ),
                paid_at=order.paid_at,
                paid_recorded_by=order.paid_recorded_by,
                payment_note=order.payment_note,
                merge_at_branch_id=merge_branch,
                merge_at_branch_name=branch_names.get(merge_branch) if merge_branch else None,
                sales=order_sales,
                pick_list=[
                    PickLineOut(product_id=pid, product_name=e["name"], total_quantity=e["total"], branches=e["branches"])
                    for pid, e in pick.items()
                ],
            )
        )
    return outs


def _coupon_out(coupon: StorefrontCoupon) -> CouponOut:
    now = datetime.now(timezone.utc)
    shown_status = coupon.status
    if coupon.status == "active" and coupon.expires_at is not None and coupon.expires_at <= now:
        shown_status = "expired"
    return CouponOut(
        id=coupon.id,
        code=_MASKED_CODE if _coupon_hidden(coupon) else coupon.code,
        discount_amount_naira=_naira(coupon.discount_amount_kobo),
        status=shown_status,
        expires_at=coupon.expires_at,
        started_at=coupon.started_at,
        valid_hours=int(coupon.valid_hours or 24),
        note=coupon.note,
        created_at=coupon.created_at,
        redeemed_at=coupon.redeemed_at,
        redeemed_order_id=coupon.redeemed_order_id,
    )


def _new_coupon_code() -> str:
    part = lambda: "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))  # noqa: E731
    return f"WEB-{part()}-{part()}"


_MASKED_CODE = "WEB-****-****"


def _coupon_hidden(coupon: StorefrontCoupon) -> bool:
    """True while the code has not been revealed yet: the server then never
    sends the real code. Revealing starts the countdown (started_at/expires_at)."""
    return coupon.status == "active" and coupon.started_at is None and coupon.expires_at is None


def _coupon_usable(coupon: StorefrontCoupon, now: datetime) -> bool:
    """Active and not past its expiry. A coupon whose clock has not started
    yet (expires_at is NULL) is usable."""
    if coupon.status != "active":
        return False
    return coupon.expires_at is None or coupon.expires_at > now


def _start_coupon_clock(coupon: StorefrontCoupon, now: datetime) -> bool:
    """Start the countdown ONCE. Returns True only if it started now.
    Already started (started_at or expires_at set), redeemed, voided or
    expired coupons are left exactly as they are."""
    if coupon.status != "active" or coupon.started_at is not None or coupon.expires_at is not None:
        return False
    coupon.started_at = now
    coupon.expires_at = now + timedelta(hours=int(coupon.valid_hours or 24))
    return True


# ------------------------------------------------------------ combined stock


@router.get("/stock", response_model=list[StockOut])
async def combined_stock(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> list[StockOut]:
    """What the website may sell: stock from ALL branches added together,
    minus units reserved by open bookings at each branch, ignoring expired
    batches. A branch's own back-of-house warehouse is not counted (it is
    not sellable stock in this system)."""
    today = date.today()
    on_hand_rows = (
        await session.execute(
            select(
                ORMStockBatch.product_id,
                ORMStockBatch.branch_id,
                func.sum(ORMStockBatch.quantity_remaining),
            )
            .where(
                ORMStockBatch.product_id.is_not(None),
                ORMStockBatch.quantity_remaining > 0,
                or_(ORMStockBatch.expiry_date.is_(None), ORMStockBatch.expiry_date > today),
            )
            .group_by(ORMStockBatch.product_id, ORMStockBatch.branch_id)
        )
    ).all()
    holds = await _open_booking_holds(session, None)

    totals: dict[uuid.UUID, int] = defaultdict(int)
    for pid, bid, qty in on_hand_rows:
        totals[pid] += max(int(qty or 0) - holds.get((pid, bid), 0), 0)

    if not totals:
        return []
    reorder = dict(
        (await session.execute(select(Product.id, Product.reorder_level).where(Product.id.in_(list(totals))))).all()
    )
    return [
        StockOut(
            product_id=pid,
            quantity_available=qty,
            is_low_stock=qty > 0 and qty <= (reorder.get(pid) or 0),
        )
        for pid, qty in totals.items()
        if qty > 0
    ]


# -------------------------------------------------------------- create order


@router.post("/orders", response_model=StorefrontOrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: StorefrontOrderCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> StorefrontOrderOut:
    if not body.items:
        raise HTTPException(422, detail="Your cart is empty.")

    # Retried/double-submitted order: return the one already created.
    existing = await _find_order_by_reference(session, body.reference)
    if existing is not None:
        return (await _build_order_outs(session, [existing]))[0]

    try:
        return await _create_order(body, session, current_user)
    except IntegrityError:
        # Two identical submissions raced; the other one won.
        await session.rollback()
        existing = await _find_order_by_reference(session, body.reference)
        if existing is not None:
            return (await _build_order_outs(session, [existing]))[0]
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="We couldn't place your order. Please try again."
        )
    except InsufficientStockError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)[:200])


async def _create_order(
    body: StorefrontOrderCreate, session: AsyncSession, current_user: User
) -> StorefrontOrderOut:
    # ---- 1. merge duplicate lines, validate products ----------------------
    wanted: dict[uuid.UUID, int] = defaultdict(int)
    for line in body.items:
        wanted[line.product_id] += line.quantity
    product_ids = list(wanted)

    products = {
        p.id: p
        for p in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
    }
    for pid in product_ids:
        product = products.get(pid)
        if (
            product is None
            or product.is_deleted
            or product.status != "active"
            or (product.selling_price_kobo or 0) <= 0
        ):
            label = product.name if product is not None else "An item in your cart"
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{label} is no longer available.")

    # ---- 2. lock stock across ALL branches (same order POS locks in) ------
    batches = list(
        (
            await session.execute(
                select(ORMStockBatch)
                .where(ORMStockBatch.product_id.in_(product_ids), ORMStockBatch.quantity_remaining > 0)
                .order_by(ORMStockBatch.received_at, ORMStockBatch.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    today = date.today()
    on_hand: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
    for b in batches:
        if b.expiry_date is None or b.expiry_date > today:
            on_hand[(b.product_id, b.branch_id)] += b.quantity_remaining
    holds = await _open_booking_holds(session, product_ids)
    branch_names = dict((await session.execute(select(ORMBranch.id, ORMBranch.name))).all())
    branch_rank = {bid: (name or "").lower() for bid, name in branch_names.items()}

    # ---- 3. allocate every product across branches ------------------------
    # branch_id -> product_id -> quantity
    allocation: dict[uuid.UUID, dict[uuid.UUID, int]] = defaultdict(dict)
    for pid, qty in wanted.items():
        available = {
            bid: max(on_hand.get((pid, bid), 0) - holds.get((pid, bid), 0), 0) for bid in branch_names
        }
        plan = allocate_across_branches(qty, available, branch_rank)
        if plan is None:
            have = sum(available.values())
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Only {have} of {products[pid].name} available right now.",
            )
        for bid, take in plan.items():
            allocation[bid][pid] = take

    # ---- 4. price on the server (wholesale rule applies to the TOTAL qty) -
    unit_kobo = {pid: _unit_price_kobo(products[pid], wanted[pid]) for pid in product_ids}
    branch_order = sorted(allocation, key=lambda b: (branch_rank.get(b, ""), str(b)))
    branch_subtotals = [
        sum(qty * unit_kobo[pid] for pid, qty in allocation[bid].items()) for bid in branch_order
    ]
    subtotal_kobo = sum(branch_subtotals)

    # ---- 5. coupon (redeemed in the same transaction as the order) --------
    now = datetime.now(timezone.utc)
    coupon: StorefrontCoupon | None = None
    discount_kobo = 0
    code = (body.coupon_code or "").strip().upper()
    if code:
        coupon = (
            await session.execute(
                select(StorefrontCoupon).where(StorefrontCoupon.code == code).with_for_update()
            )
        ).scalar_one_or_none()
        if coupon is None or not _coupon_usable(coupon, now):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Coupon '{code}' is invalid, expired, or already used.",
            )
        discount_kobo = min(int(coupon.discount_amount_kobo), subtotal_kobo)
    discount_shares = split_discount(discount_kobo, branch_subtotals)

    # ---- 6. find or create the customer ----------------------------------
    phone = body.customer.phone.strip()
    email = (body.customer.email or "").strip().lower() or None
    customer = (await session.execute(select(Customer).where(Customer.phone == phone))).scalar_one_or_none()
    if customer is None and email:
        customer = (
            await session.execute(select(Customer).where(func.lower(Customer.email) == email))
        ).scalar_one_or_none()
    if customer is None:
        customer = Customer(full_name=body.customer.full_name.strip(), phone=phone, email=email)
        session.add(customer)
        await session.flush()
    elif not customer.full_name:
        customer.full_name = body.customer.full_name.strip()

    address_line = body.delivery.address_line.strip()
    known_address = (
        await session.execute(
            select(CustomerAddress.id)
            .where(
                CustomerAddress.customer_id == customer.id,
                func.lower(CustomerAddress.address_line) == address_line.lower(),
            )
            .limit(1)
        )
    ).first()
    if known_address is None:
        session.add(
            CustomerAddress(
                customer_id=customer.id,
                label="Website order",
                address_line=address_line,
                city=(body.delivery.city or body.delivery.lga or None),
                state=body.delivery.state.strip(),
                is_default=False,
            )
        )

    # ---- 7. one ordinary Sale per branch, via the same service POS uses ---
    created: list[tuple[uuid.UUID, uuid.UUID]] = []  # (sale_id, branch_id)
    for index, bid in enumerate(branch_order):
        local_ref = f"{body.reference}:{bid}"
        stock_repo = PreloadedStockBatchRepository([b for b in batches if b.branch_id == bid])
        sale_repo = await load_sale_repo(session, local_ref)
        service = SalesService(stock_repo, sale_repo)
        lines = [
            CheckoutLine(variant_id=str(pid), quantity=qty, unit_price=Money(unit_kobo[pid]))
            for pid, qty in allocation[bid].items()
        ]
        domain_sale = service.checkout(
            sale_id=str(uuid.uuid4()),
            branch_id=str(bid),
            served_by=str(current_user.id),
            channel=SaleChannel("website"),
            local_ref_id=local_ref,
            lines=lines,
            customer_id=str(customer.id),
            discount=Money(discount_shares[index]),
        )
        orm_sale = persist_new_sale(session, domain_sale, "online")
        customer.loyalty_points += int(Money(orm_sale.total_kobo).naira * POINTS_PER_NAIRA_SPENT)
        created.append((orm_sale.id, bid))

    # ---- 8. the merged order record --------------------------------------
    order = StorefrontOrder(
        id=uuid.uuid4(),
        reference=body.reference,
        customer_id=customer.id,
        customer_name=body.customer.full_name.strip(),
        customer_phone=phone,
        customer_email=email,
        delivery_address=address_line,
        delivery_city=(body.delivery.city or None),
        delivery_lga=(body.delivery.lga or None),
        delivery_state=body.delivery.state.strip(),
        shipping_fee_kobo=round(body.shipping_fee_naira * 100),
        shipping_note=body.shipping_note,
        subtotal_kobo=subtotal_kobo,
        discount_kobo=discount_kobo,
        total_kobo=subtotal_kobo - discount_kobo,
        coupon_code=coupon.code if coupon is not None else None,
        status="new",
    )
    session.add(order)
    await session.flush()  # order (and the sales) must exist before the link rows
    for sale_id, bid in created:
        session.add(StorefrontOrderSale(order_id=order.id, sale_id=sale_id, branch_id=bid))

    if coupon is not None:
        coupon.status = "redeemed"
        coupon.redeemed_at = now
        coupon.redeemed_order_id = order.id
        log_audit_event(
            session,
            user_id=current_user.id,
            action="discount_applied",
            entity_type="sale",
            entity_id=created[0][0],
            metadata={"discount_naira": _naira(discount_kobo), "storefront_coupon": coupon.code},
        )

    await session.commit()
    await session.refresh(order)
    return (await _build_order_outs(session, [order]))[0]


# ------------------------------------------------------ admin: order history


def _scope_to_branch(stmt, user: User, branch_id: uuid.UUID | None):
    """Branch managers only see orders that include a share from their own
    branch (the whole merged order is shown so they can see who else is
    sending parts). Super admins can optionally filter by branch."""
    effective = user.branch_id if user.role == "branch_manager" else branch_id
    if effective is not None:
        stmt = stmt.where(
            StorefrontOrder.id.in_(
                select(StorefrontOrderSale.order_id).where(StorefrontOrderSale.branch_id == effective)
            )
        )
    return stmt


@router.get("/orders", response_model=list[StorefrontOrderOut])
async def list_orders(
    status_filter: str | None = Query(None, alias="status"),
    branch_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=300),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> list[StorefrontOrderOut]:
    stmt = select(StorefrontOrder)
    if status_filter:
        if status_filter not in ORDER_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown status.")
        stmt = stmt.where(StorefrontOrder.status == status_filter)
    stmt = _scope_to_branch(stmt, current_user, branch_id)
    stmt = stmt.order_by(StorefrontOrder.created_at.desc(), StorefrontOrder.id).limit(limit).offset(offset)
    orders = list((await session.execute(stmt)).scalars().all())
    return await _build_order_outs(session, orders)


@router.get("/orders/{order_id}", response_model=StorefrontOrderOut)
async def get_order(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> StorefrontOrderOut:
    stmt = _scope_to_branch(select(StorefrontOrder).where(StorefrontOrder.id == order_id), current_user, None)
    order = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found.")
    return (await _build_order_outs(session, [order]))[0]


# PATCH11A-WEBSITE-CANCEL: cancelling a website order really cancels its
# sale(s): stock goes back to the batches it came from, loyalty points are
# taken back, and an audit event is written. Nothing is changed when any
# linked sale is not "completed" (for example a refund was already made).
async def _cancel_order_sales(session: AsyncSession, order: StorefrontOrder, current_user: User) -> None:
    sale_ids = list(
        (await session.execute(select(StorefrontOrderSale.sale_id).where(StorefrontOrderSale.order_id == order.id)))
        .scalars()
        .all()
    )
    if not sale_ids:
        return
    sales = list(
        (
            await session.execute(
                select(ORMSale)
                .where(ORMSale.id.in_(sale_ids))
                .order_by(ORMSale.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if any(s.status != "completed" for s in sales):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="A refund was already made on this order's sale. Cancel is not possible.",
        )
    items = list(
        (await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id.in_(sale_ids)))).scalars().all()
    )
    back: dict[uuid.UUID, int] = defaultdict(int)
    for item in items:
        if item.stock_batch_id is not None:
            back[item.stock_batch_id] += item.quantity
    if back:
        batches = list(
            (
                await session.execute(
                    select(ORMStockBatch)
                    .where(ORMStockBatch.id.in_(list(back)))
                    .order_by(ORMStockBatch.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        for batch in batches:
            batch.quantity_remaining += back[batch.id]
    points_back = 0
    for s in sales:
        points_back += int(Money(s.total_kobo).naira * POINTS_PER_NAIRA_SPENT)
    customer = None
    if order.customer_id is not None:
        customer = await session.get(Customer, order.customer_id, populate_existing=True)
    if customer is not None and points_back:
        customer.loyalty_points = max(0, int(customer.loyalty_points or 0) - points_back)
    for s in sales:
        s.status = "cancelled"
        s.notes = f"{s.notes or ''}\nCancelled: website order cancelled".strip()
    log_audit_event(
        session,
        user_id=current_user.id,
        action="website_order_cancelled",
        entity_type="storefront_order",
        entity_id=order.id,
        metadata={
            "order_reference": order.reference,
            "sale_ids": [str(x) for x in sale_ids],
            "stock_returned_pieces": int(sum(back.values())),
            "loyalty_points_taken_back": int(points_back),
        },
    )


@router.patch("/orders/{order_id}", response_model=StorefrontOrderOut)
async def update_order(
    order_id: uuid.UUID,
    body: StorefrontOrderUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> StorefrontOrderOut:
    stmt = _scope_to_branch(select(StorefrontOrder).where(StorefrontOrder.id == order_id), current_user, None)
    order = (await session.execute(stmt.with_for_update())).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found.")

    if body.status is not None:
        if body.status not in ORDER_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown status.")
        # PATCH11A-WEBSITE-CANCEL
        if order.status == "cancelled" and body.status != "cancelled":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A cancelled order cannot be changed back. Ask the customer to place a new order.",
            )
        if body.status == "cancelled" and order.status != "cancelled":
            if order.status == "delivered":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="A delivered order cannot be cancelled here. Process a refund from the sale's receipt instead.",
                )
            await _cancel_order_sales(session, order, current_user)
        order.status = body.status
    if body.waybill_ref is not None:
        order.waybill_ref = body.waybill_ref.strip() or None
    if body.admin_note is not None:
        order.admin_note = body.admin_note.strip() or None
    order.status_updated_by = current_user.id
    order.status_updated_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(order)
    return (await _build_order_outs(session, [order]))[0]


@router.post("/orders/{order_id}/payment", response_model=StorefrontOrderOut)
async def record_order_payment(
    order_id: uuid.UUID,
    body: StorefrontOrderPaymentIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> StorefrontOrderOut:
    # PATCH19B-PAYMENT-FIELDS
    # Records that the customer paid. It changes only the payment fields:
    # no sale, stock, points or revenue figure is touched.
    stmt = _scope_to_branch(select(StorefrontOrder).where(StorefrontOrder.id == order_id), current_user, None)
    order = (await session.execute(stmt.with_for_update())).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if order.status == "cancelled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="A cancelled order cannot be marked as paid.",
        )
    if order.payment_status == "paid":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This order is already marked as paid.",
        )
    amount_kobo = round(body.amount_naira * 100)
    if amount_kobo <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The amount must be more than 0.",
        )
    order.payment_status = "paid"
    order.payment_method = body.method
    order.amount_received_kobo = amount_kobo
    order.paid_at = datetime.now(timezone.utc)
    order.paid_recorded_by = current_user.id
    order.payment_note = (body.note or "").strip() or None
    log_audit_event(
        session,
        user_id=current_user.id,
        action="website_order_payment_recorded",
        entity_type="storefront_order",
        entity_id=order.id,
        metadata={
            "order_reference": order.reference,
            "method": body.method,
            "amount_kobo": int(amount_kobo),
        },
    )
    await session.commit()
    await session.refresh(order)
    return (await _build_order_outs(session, [order]))[0]


@router.post("/orders/{order_id}/payment/undo", response_model=StorefrontOrderOut)
async def undo_order_payment(
    order_id: uuid.UUID,
    body: StorefrontOrderUndoPaymentIn,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> StorefrontOrderOut:
    # PATCH19D-UNDO-PAYMENT
    # Puts the payment fields back to unpaid. No sale, stock, points or
    # revenue figure is touched. The old values are kept in the audit log.
    stmt = _scope_to_branch(select(StorefrontOrder).where(StorefrontOrder.id == order_id), current_user, None)
    order = (await session.execute(stmt.with_for_update())).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if order.status == "cancelled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="A cancelled order's payment cannot be changed.",
        )
    if order.payment_status != "paid":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This order is not marked as paid.",
        )
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Write why the payment is being undone.",
        )
    old_method = order.payment_method
    old_kobo = int(order.amount_received_kobo or 0)
    order.payment_status = "unpaid"
    order.payment_method = None
    order.amount_received_kobo = None
    order.paid_at = None
    order.paid_recorded_by = None
    order.payment_note = None
    log_audit_event(
        session,
        user_id=current_user.id,
        action="website_order_payment_undone",
        entity_type="storefront_order",
        entity_id=order.id,
        metadata={
            "order_reference": order.reference,
            "method": old_method,
            "amount_kobo": old_kobo,
            "reason": reason,
        },
    )
    await session.commit()
    await session.refresh(order)
    return (await _build_order_outs(session, [order]))[0]


# --------------------------------------------------------- website coupons


@router.post("/coupons", response_model=list[CouponOut], status_code=status.HTTP_201_CREATED)
async def generate_coupons(
    body: CouponGenerate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> list[CouponOut]:
    amount_kobo = round(body.discount_amount_naira * 100)
    made: list[StorefrontCoupon] = []
    for _ in range(body.count):
        for _attempt in range(10):
            code = _new_coupon_code()
            taken = (
                await session.execute(select(StorefrontCoupon.id).where(StorefrontCoupon.code == code))
            ).first()
            if taken is None:
                break
        else:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not generate a unique code.")
        coupon = StorefrontCoupon(
            code=code,
            discount_amount_kobo=amount_kobo,
            status="active",
            expires_at=None,  # the clock starts when an admin reveals the code
            started_at=None,
            valid_hours=body.expires_in_hours,
            note=(body.note or None),
            issued_by=current_user.id,
        )
        session.add(coupon)
        await session.flush()
        made.append(coupon)
    await session.commit()
    for coupon in made:
        await session.refresh(coupon)
    return [_coupon_out(c) for c in made]


@router.get("/coupons", response_model=list[CouponOut])
async def list_coupons(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> list[CouponOut]:
    rows = (
        (await session.execute(select(StorefrontCoupon).order_by(StorefrontCoupon.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [_coupon_out(c) for c in rows]


@router.post("/coupons/{coupon_id}/void", response_model=CouponOut)
async def void_coupon(
    coupon_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> CouponOut:
    coupon = (
        await session.execute(select(StorefrontCoupon).where(StorefrontCoupon.id == coupon_id).with_for_update())
    ).scalar_one_or_none()
    if coupon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Coupon not found.")
    if coupon.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Only an active coupon can be voided.")
    coupon.status = "voided"
    await session.commit()
    await session.refresh(coupon)
    return _coupon_out(coupon)


@router.post("/coupons/reveal", response_model=list[CouponOut])
async def reveal_coupons(
    body: CouponReveal,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> list[CouponOut]:
    """The ONLY way to see the code of a new coupon. Starts the countdown once
    (revealing again never restarts it) and returns the real code(s)."""
    ids = list(dict.fromkeys(body.ids))
    rows = (
        (
            await session.execute(
                select(StorefrontCoupon)
                .where(StorefrontCoupon.id.in_(ids))
                .order_by(StorefrontCoupon.created_at.desc(), StorefrontCoupon.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    for coupon in rows:
        _start_coupon_clock(coupon, now)
    await session.commit()
    for coupon in rows:
        await session.refresh(coupon)
    return [_coupon_out(c) for c in rows]


@router.get("/coupons/check", response_model=CouponCheckOut)
async def check_coupon(
    code: str = Query(..., min_length=1, max_length=50),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> CouponCheckOut:
    """Lets the storefront show the discount before the customer pays.
    Does NOT redeem anything -- redemption happens inside create_order."""
    clean = code.strip().upper()
    coupon = (
        await session.execute(select(StorefrontCoupon).where(StorefrontCoupon.code == clean))
    ).scalar_one_or_none()
    if coupon is None or not _coupon_usable(coupon, datetime.now(timezone.utc)):
        return CouponCheckOut(valid=False, discount_amount_naira=0, message="Coupon is invalid, expired, or already used.")
    return CouponCheckOut(
        valid=True, discount_amount_naira=_naira(coupon.discount_amount_kobo), message="Coupon applied."
    )
