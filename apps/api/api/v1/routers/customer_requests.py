"""Customer requests -- deposit pre-orders tied to the Restock List.

A customer asks the shop to source an item that is not in stock and pays
a deposit. This module records who they are, what they paid and what they
asked for, and follows the order until the customer collects it:

    pending -> arrived -> collected        (or -> cancelled)

* The item is also put on the Restock List (so it can be sent to the
  supplier), but the customer record lives here because a Restock List
  entry is deleted when its PDF is downloaded.
* "arrived" is detected automatically: once a StockBatch for the item is
  received at the request's branch AFTER the request was made (that is
  what happens when an invoice item is pushed through to the shop
  floor), the request flips to arrived, a number of pieces is held back
  from the POS (sales.py::_get_open_booking_holds) and the dashboard
  bell shows "Customer orders waiting". Free-typed items are matched to
  an inventory product by exact name (case/space-insensitive); anything
  that cannot be matched safely is linked by hand with "Mark arrived".
* Collected / cancelled releases the hold. Cancelling records whether
  the deposit was refunded.

Mounted under /restock-list/customer-requests (see the include_router
call at the bottom of restock_list.py).
"""
import re
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission
from api.v1.schemas.restock_list import UNIT_LABELS
from infrastructure.db.models import (
    CustomerRequest,
    Product,
    RestockListEntry,
    StockBatch,
    User,
)

router = APIRouter(prefix="/customer-requests", tags=["customer-requests"])

_VIEW = require_permission("view_restock_list")
_MANAGE = require_permission("manage_restock_list")

# Units whose size in pieces is fixed. Cartons/bags/half cartons vary by
# product, so for those the staff member says how many pieces to hold.
_PIECES_PER_UNIT = {"pieces": 1, "dozens": 12, "half_dozen": 6}

_STATUS_ORDER = {"arrived": 0, "pending": 1, "collected": 2, "cancelled": 3}


# ----------------------------------------------------------------- helpers


def default_hold_pieces(quantity: int, unit_label: str) -> int:
    per_unit = _PIECES_PER_UNIT.get(unit_label)
    if not per_unit:
        return 0
    return max(int(quantity or 0), 0) * per_unit


def _norm(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def decide_arrivals(pending, products_by_name, batches, already_held=None):
    """Pure decision step (no database), so it can be unit-tested.

    pending          requests still waiting (need .product_id, .item_name,
                     .branch_id, .created_at, .quantity, .unit_label)
    products_by_name {normalised product name: [product ids]}
    batches          [(product_id, branch_id, received_at, quantity_left)]
                     -- unexpired batches with stock left
    already_held     {(product_id, branch_id): pieces already held}

    Returns [(request, product_id, hold_pieces)] for requests whose item
    has reached the shelf since they were made.
    """
    held = dict(already_held or {})
    decided = []
    for req in pending:
        product_id = req.product_id
        if product_id is None:
            candidates = products_by_name.get(_norm(req.item_name), [])
            if len(candidates) != 1:
                continue  # no match, or ambiguous -- staff link it by hand
            product_id = candidates[0]
        created = _aware(req.created_at)
        fresh = False
        available = 0
        for b_product, b_branch, received_at, qty_left in batches:
            if b_product != product_id or b_branch != req.branch_id:
                continue
            available += int(qty_left or 0)
            if received_at is not None and created is not None and _aware(received_at) >= created:
                fresh = True
        if not fresh or available <= 0:
            continue
        key = (product_id, req.branch_id)
        wanted = default_hold_pieces(req.quantity, req.unit_label)
        free = max(available - held.get(key, 0), 0)
        hold = min(wanted, free) if wanted > 0 else 0
        held[key] = held.get(key, 0) + hold
        decided.append((req, product_id, hold))
    return decided


async def _flip_pending_arrivals(session: AsyncSession, branch_id: uuid.UUID | None = None) -> int:
    """Flip every pending request whose item has reached the shelf to
    'arrived' and set its stock hold. Cheap when nothing is pending."""
    stmt = select(CustomerRequest).where(CustomerRequest.status == "pending")
    if branch_id is not None:
        stmt = stmt.where(CustomerRequest.branch_id == branch_id)
    pending = list((await session.execute(stmt)).scalars().all())
    if not pending:
        return 0

    names = {_norm(r.item_name) for r in pending if r.product_id is None}
    by_name: dict[str, list[uuid.UUID]] = {}
    if names:
        product_rows = await session.execute(
            select(Product.id, Product.name).where(Product.is_deleted.is_(False))
        )
        for product_id, product_name in product_rows.all():
            key = _norm(product_name)
            if key in names:
                by_name.setdefault(key, []).append(product_id)

    wanted_ids = {r.product_id for r in pending if r.product_id is not None}
    for ids in by_name.values():
        wanted_ids.update(ids)
    if not wanted_ids:
        return 0
    wanted_ids = list(wanted_ids)

    today = date.today()
    batch_rows = await session.execute(
        select(
            StockBatch.product_id,
            StockBatch.branch_id,
            StockBatch.received_at,
            StockBatch.quantity_remaining,
        ).where(
            StockBatch.product_id.in_(wanted_ids),
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
    )
    batches = [tuple(row) for row in batch_rows.all()]
    if not batches:
        return 0

    held_rows = await session.execute(
        select(
            CustomerRequest.product_id,
            CustomerRequest.branch_id,
            func.sum(CustomerRequest.hold_pieces),
        )
        .where(CustomerRequest.status == "arrived", CustomerRequest.product_id.in_(wanted_ids))
        .group_by(CustomerRequest.product_id, CustomerRequest.branch_id)
    )
    already_held = {(p, b): int(total or 0) for p, b, total in held_rows.all()}

    decided = decide_arrivals(pending, by_name, batches, already_held)
    if not decided:
        return 0
    now = datetime.now(timezone.utc)
    for req, product_id, hold in decided:
        req.product_id = product_id
        req.status = "arrived"
        req.arrived_at = now
        req.hold_pieces = 0  # PATCH2A: stock is no longer held by arrival; a booking holds it
    await session.commit()
    return len(decided)


# PATCH2A-ORDER-TO-BOOKING
async def sync_arrivals(session: AsyncSession, branch_id: uuid.UUID | None = None) -> int:
    """Flip waiting orders to arrived, then turn every arrived order that
    has "Customer needs" filled in into a real open booking. A problem
    while booking is logged and never breaks the page or the bell."""
    flipped = await _flip_pending_arrivals(session, branch_id)
    try:
        await _convert_ready_orders(session, branch_id)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("customer order -> booking conversion failed")
        await session.rollback()
    return flipped


async def _convert_ready_orders(session: AsyncSession, branch_id: uuid.UUID | None = None) -> None:
    stmt = select(CustomerRequest.id).where(
        CustomerRequest.status == "arrived",
        CustomerRequest.customer_pieces.is_not(None),
        CustomerRequest.booking_id.is_(None),
        CustomerRequest.product_id.is_not(None),
    )
    if branch_id is not None:
        stmt = stmt.where(CustomerRequest.branch_id == branch_id)
    ids = list((await session.execute(stmt)).scalars().all())
    for req_id in ids:
        try:
            await _convert_one(session, req_id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("customer order %s: booking failed", req_id)
            await session.rollback()


async def _set_order_note(session: AsyncSession, req_id, current, text: str) -> None:
    """Save a short reason on the order and release the row lock."""
    if current != text:
        await session.execute(
            update(CustomerRequest).where(CustomerRequest.id == req_id).values(resolution_note=text)
        )
    await session.commit()


async def _free_stock_for_booking(session: AsyncSession, product_id, branch_id, req_id) -> int:
    """Shelf stock minus open bookings minus stock held for other orders."""
    from infrastructure.db.models import Sale as ORMSale
    from infrastructure.db.models import SaleItem as ORMSaleItem

    on_hand = await _on_hand(session, product_id, branch_id)
    reserved = await session.scalar(
        select(func.coalesce(func.sum(ORMSaleItem.quantity), 0))
        .join(ORMSale, ORMSale.id == ORMSaleItem.sale_id)
        .where(
            ORMSale.status == "booking",
            ORMSale.branch_id == branch_id,
            ORMSaleItem.product_id == product_id,
        )
    )
    others = await _held_by_others(session, product_id, branch_id, req_id)
    return max(on_hand - int(reserved or 0) - others, 0)


async def _convert_one(session: AsyncSession, req_id: uuid.UUID) -> None:
    """Create ONE open booking for an arrived order, in one transaction.
    The booking carries the name of the staff member who created the
    order (served_by = order.created_by). The fixed local_ref_id makes a
    second run return the same booking instead of creating another."""
    import logging

    from infrastructure.db.audit import log_audit_event
    from infrastructure.db.checkout_adapter import (
        load_sale_repo,
        load_stock_batch_repo,
        persist_new_sale,
    )
    from infrastructure.db.models import BranchProductPrice, Customer, SalePayment
    from infrastructure.db.repositories import CustomerRepository
    from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService
    from nickcosmetics_core.domain.money import Money
    from nickcosmetics_core.domain.sales.sale import PaymentMethod as DomainPaymentMethod
    from nickcosmetics_core.domain.sales.sale import PaymentTender as DomainPaymentTender
    from nickcosmetics_core.domain.sales.sale import SaleChannel
    from nickcosmetics_core.domain.sales.sale import SaleStatus as DomainSaleStatus
    from nickcosmetics_core.exceptions import InsufficientStockError

    log = logging.getLogger(__name__)

    # Lock this order's row. If another request is already booking it, skip.
    locked = (
        await session.execute(
            select(CustomerRequest)
            .where(CustomerRequest.id == req_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None:
        return
    if (
        locked.status != "arrived"
        or locked.booking_id is not None
        or not locked.customer_pieces
        or locked.product_id is None
    ):
        await session.commit()
        return

    branch_id = locked.branch_id
    product_id = locked.product_id
    wanted = int(locked.customer_pieces)
    deposit_kobo = int(locked.deposit_kobo or 0)
    method = (locked.deposit_method or "").strip().lower() or None
    created_by = locked.created_by
    created_at = locked.created_at
    customer_name = locked.customer_name
    phone = locked.customer_phone
    item_name = locked.item_name
    note_now = locked.resolution_note

    if created_by is None or await session.get(User, created_by) is None:
        await _set_order_note(session, req_id, note_now, "Arrived, not booked: this order has no staff name saved.")
        return

    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        await _set_order_note(session, req_id, note_now, "Arrived, not booked: the item is not in inventory.")
        return

    override = await session.scalar(
        select(BranchProductPrice.selling_price_kobo).where(
            BranchProductPrice.branch_id == branch_id,
            BranchProductPrice.product_id == product_id,
        )
    )
    price_kobo = int(override if override is not None else (product.selling_price_kobo or 0))
    if price_kobo <= 0:
        await _set_order_note(
            session, req_id, note_now,
            "Arrived, not booked yet: set the selling price of this item in Inventory.",
        )
        return

    free = await _free_stock_for_booking(session, product_id, branch_id, req_id)
    size = min(wanted, free)
    if size <= 0:
        await _set_order_note(session, req_id, note_now, "Arrived, not booked yet: no free stock to book.")
        return

    if deposit_kobo > 0 and method not in ("cash", "card", "transfer", "online"):
        await _set_order_note(
            session, req_id, note_now, "Arrived, not booked: how the deposit was paid is not saved."
        )
        return
    # PATCH3B-AUTO-COMPLETE
    if size < wanted and deposit_kobo >= price_kobo * size:
        await _set_order_note(
            session, req_id, note_now,
            "Arrived, not booked yet: only " + str(size) + " of " + str(wanted)
            + " pieces are on the shelf and the deposit already covers them. "
            + "It will be booked when all the pieces arrive.",
        )
        return

    local_ref = "customer-order-" + str(req_id)
    note_fail = None
    try:
        async with session.begin_nested():
            repo = CustomerRepository(session)
            existing = await repo.get_by_phone(phone)
            if existing is not None:
                customer_id = existing.id
                if not existing.full_name:
                    existing.full_name = customer_name
            else:
                # PATCH2A1-NEW-CUSTOMER: no commit inside the savepoint, flush only
                new_customer = Customer(full_name=customer_name, phone=phone)
                session.add(new_customer)
                await session.flush()
                customer_id = new_customer.id

            stock_repo = await load_stock_batch_repo(session, [product_id], branch_id)
            sale_repo = await load_sale_repo(session, local_ref)
            service = SalesService(stock_repo, sale_repo)
            domain_sale = service.checkout(
                sale_id=str(uuid.uuid4()),
                branch_id=str(branch_id),
                served_by=str(created_by),
                channel=SaleChannel.POS,
                local_ref_id=local_ref,
                lines=[
                    CheckoutLine(
                        variant_id=str(product_id),
                        quantity=size,
                        unit_price=Money(price_kobo),
                    )
                ],
                customer_id=str(customer_id),
                payments=(
                    [DomainPaymentTender(method=DomainPaymentMethod("transfer" if method == "online" else method), amount=Money(deposit_kobo))]
                    if deposit_kobo > 0
                    else None
                ),
                status=DomainSaleStatus.BOOKING,
                notes="Order: " + str(item_name) + " - " + str(customer_name),
                discount=Money(0),
            )
            if sale_repo.was_idempotent_hit:
                booking_id = uuid.UUID(domain_sale.id)
            else:
                orm_sale = persist_new_sale(session, domain_sale, method if deposit_kobo > 0 else None)
                # Back-date to the day the customer really ordered/paid, so
                # the 24-hour cash-refund rule counts from that day.
                orm_sale.created_at = created_at
                if deposit_kobo > 0:
                    session.add(
                        SalePayment(
                            sale_id=orm_sale.id,
                            method=method,
                            amount_kobo=deposit_kobo,
                            recorded_by=created_by,
                            created_at=created_at,
                        )
                    )
                await session.flush()
                booking_id = orm_sale.id
                log_audit_event(
                    session,
                    user_id=created_by,
                    action="booking_created_from_customer_order",
                    entity_type="sale",
                    entity_id=booking_id,
                    metadata={
                        "customer_order_id": str(req_id),
                        "pieces_booked": size,
                        "pieces_wanted": wanted,
                        "deposit_kobo": deposit_kobo,
                    },
                )
            # PATCH3B-AUTO-COMPLETE
            order_done = False
            if (not sale_repo.was_idempotent_hit) and deposit_kobo > 0 and deposit_kobo >= orm_sale.total_kobo:
                from api.v1.routers.sales import _generate_change_reference_code
                from infrastructure.db.models import PendingChange
                from infrastructure.db.models import SaleItem as ORMSaleItem
                from nickcosmetics_core.domain.inventory.stock import FifoStockAllocator

                done_items = list(
                    (await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == booking_id)))
                    .scalars()
                    .all()
                )
                # Copy of the FIFO block in add_booking_payment (sales.py).
                # If that block changes, change this one too.
                for item in done_items:
                    batches = stock_repo.get_batches_for_variant(str(item.product_id), str(branch_id))
                    allocation = FifoStockAllocator.allocate(batches, str(item.product_id), item.quantity)
                    first_batch, first_qty = allocation[0]
                    first_batch.quantity_remaining -= first_qty
                    stock_repo.save_batch(first_batch)
                    item.stock_batch_id = uuid.UUID(first_batch.id)
                    for extra_batch, extra_qty in allocation[1:]:
                        extra_batch.quantity_remaining -= extra_qty
                        stock_repo.save_batch(extra_batch)
                        session.add(
                            ORMSaleItem(
                                sale_id=booking_id,
                                product_id=item.product_id,
                                stock_batch_id=uuid.UUID(extra_batch.id),
                                quantity=extra_qty,
                                unit_price_kobo=item.unit_price_kobo,
                                line_total_kobo=round(item.unit_price_kobo * extra_qty),
                            )
                        )
                        item.quantity -= extra_qty
                        item.line_total_kobo = round(item.unit_price_kobo * item.quantity)
                now_dt = datetime.now(timezone.utc)
                orm_sale.status = "completed"
                orm_sale.created_at = now_dt
                extra_kobo = deposit_kobo - orm_sale.total_kobo
                if extra_kobo > 0:
                    session.add(
                        PendingChange(
                            sale_id=booking_id,
                            branch_id=branch_id,
                            customer_name=customer_name,
                            customer_phone=phone,
                            amount_kobo=extra_kobo,
                            notes="Deposit above the price of the item",
                            status="outstanding",
                            reference_code=_generate_change_reference_code(),
                            settlement_method=None,
                            created_by=created_by,
                        )
                    )
                await session.flush()
                log_audit_event(
                    session,
                    user_id=created_by,
                    action="booking_completed_from_customer_order",
                    entity_type="sale",
                    entity_id=booking_id,
                    metadata={
                        "customer_order_id": str(req_id),
                        "deposit_kobo": deposit_kobo,
                        "total_kobo": int(orm_sale.total_kobo),
                        "extra_kobo": max(extra_kobo, 0),
                    },
                )
                order_done = True
            locked.booking_id = booking_id
            locked.hold_pieces = 0
            locked.resolution_note = None
            if order_done:
                locked.status = "collected"
                locked.collected_at = now_dt
    except InsufficientStockError:
        note_fail = "Arrived, not booked yet: not enough stock on the shelf."
    except Exception:
        log.exception("customer order %s: could not create the booking", req_id)
        note_fail = "Arrived, not booked: the booking could not be created (see the server log)."

    if note_fail:
        await session.rollback()
        await _set_order_note(session, req_id, note_now, note_fail)
        return
    await session.commit()


async def count_waiting_customers(session: AsyncSession, branch_id: uuid.UUID | None = None) -> int:
    """Number of customers whose item has arrived and who have not
    collected yet -- the figure behind the dashboard bell."""
    await sync_arrivals(session, branch_id)
    stmt = select(func.count()).select_from(CustomerRequest).where(CustomerRequest.status == "arrived")
    if branch_id is not None:
        stmt = stmt.where(CustomerRequest.branch_id == branch_id)
    return int(await session.scalar(stmt) or 0)


async def mark_requests_ordered(session: AsyncSession, entry_ids) -> None:
    """Called by the Restock List download: the items just went to a
    supplier, so remember when (the entries themselves are deleted)."""
    ids = list(entry_ids)
    if not ids:
        return
    await session.execute(
        update(CustomerRequest)
        .where(
            CustomerRequest.restock_list_entry_id.in_(ids),
            CustomerRequest.status == "pending",
            CustomerRequest.ordered_at.is_(None),
        )
        .values(ordered_at=datetime.now(timezone.utc))
    )


async def _on_hand(session: AsyncSession, product_id: uuid.UUID, branch_id: uuid.UUID) -> int:
    today = date.today()
    total = await session.scalar(
        select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
            StockBatch.product_id == product_id,
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
    )
    return int(total or 0)


async def _held_by_others(
    session: AsyncSession, product_id: uuid.UUID, branch_id: uuid.UUID, exclude_id: uuid.UUID
) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(CustomerRequest.hold_pieces), 0)).where(
            CustomerRequest.status == "arrived",
            CustomerRequest.product_id == product_id,
            CustomerRequest.branch_id == branch_id,
            CustomerRequest.id != exclude_id,
        )
    )
    return int(total or 0)


async def _capped_hold(session: AsyncSession, req: CustomerRequest, wanted: int) -> int:
    """Never hold more than what is actually on the shelf and not already
    held for another customer, otherwise nobody could sell the item."""
    if req.product_id is None or wanted <= 0:
        return 0
    room = await _on_hand(session, req.product_id, req.branch_id)
    room -= await _held_by_others(session, req.product_id, req.branch_id, req.id)
    return max(min(wanted, room), 0)


async def _names(session: AsyncSession, user_ids) -> dict:
    ids = [i for i in set(user_ids) if i is not None]
    if not ids:
        return {}
    rows = await session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {uid: name for uid, name in rows.all()}


# ----------------------------------------------------------------- schemas


class CustomerRequestCreate(BaseModel):
    branch_id: uuid.UUID
    item_name: str = Field(min_length=1, max_length=200)
    item_image_url: str | None = None
    quantity: int = Field(default=1, ge=1)
    unit_label: str = "pieces"
    customer_name: str = Field(min_length=1, max_length=200)
    customer_phone: str = Field(min_length=1, max_length=40)
    customer_address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    deposit_naira: float = Field(default=0, ge=0)
    # PATCH1-CUSTOMER-ORDER-FIELDS
    order_pieces: int | None = Field(default=None, ge=1)
    customer_pieces: int | None = Field(default=None, ge=1)
    deposit_method: str | None = None
    # Optional: the customer wants something that already exists in the
    # catalog (e.g. an out-of-stock product) instead of a typed-in item.
    product_id: uuid.UUID | None = None
    add_to_restock_list: bool = True


class CustomerRequestOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    restock_list_entry_id: uuid.UUID | None
    product_id: uuid.UUID | None
    item_name: str
    item_image_url: str | None
    quantity: int
    unit_label: str
    customer_name: str
    customer_phone: str
    customer_address: str | None
    notes: str | None
    deposit_naira: float
    status: str
    hold_pieces: int
    needs_pieces: bool
    order_pieces: int | None = None
    customer_pieces: int | None = None
    deposit_method: str | None = None
    booking_id: uuid.UUID | None = None
    created_at: datetime
    ordered_at: datetime | None
    arrived_at: datetime | None
    collected_at: datetime | None
    cancelled_at: datetime | None
    deposit_refunded: bool
    resolution_note: str | None
    created_by_name: str | None


class ArriveBody(BaseModel):
    product_id: uuid.UUID | None = None
    hold_pieces: int | None = Field(default=None, ge=0)
    # PATCH2B-MARK-ARRIVED-STOCK
    add_to_shelf: bool = False
    pieces_arrived: int | None = Field(default=None, ge=1)
    selling_price_naira: float | None = Field(default=None, ge=0)
    cost_price_naira: float | None = Field(default=None, ge=0)


class CustomerRequestUpdate(BaseModel):
    hold_pieces: int | None = Field(default=None, ge=0)
    customer_phone: str | None = Field(default=None, max_length=40)
    customer_address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class CollectBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class CancelBody(BaseModel):
    deposit_refunded: bool = False
    note: str | None = Field(default=None, max_length=500)


class ProductPick(BaseModel):
    id: uuid.UUID
    name: str
    on_hand: int


def _out(req: CustomerRequest, created_by_name: str | None) -> CustomerRequestOut:
    return CustomerRequestOut(
        id=req.id,
        branch_id=req.branch_id,
        restock_list_entry_id=req.restock_list_entry_id,
        product_id=req.product_id,
        item_name=req.item_name,
        item_image_url=req.item_image_url,
        quantity=req.quantity,
        unit_label=req.unit_label,
        customer_name=req.customer_name,
        customer_phone=req.customer_phone,
        customer_address=req.customer_address,
        notes=req.notes,
        deposit_naira=round((req.deposit_kobo or 0) / 100, 2),
        status=req.status,
        hold_pieces=req.hold_pieces or 0,
        needs_pieces=(req.status == "arrived" and (req.hold_pieces or 0) == 0 and req.booking_id is None),
        order_pieces=req.order_pieces,
        customer_pieces=req.customer_pieces,
        deposit_method=req.deposit_method,
        booking_id=req.booking_id,
        created_at=req.created_at,
        ordered_at=req.ordered_at,
        arrived_at=req.arrived_at,
        collected_at=req.collected_at,
        cancelled_at=req.cancelled_at,
        deposit_refunded=bool(req.deposit_refunded),
        resolution_note=req.resolution_note,
        created_by_name=created_by_name,
    )


async def _load(session: AsyncSession, request_id: uuid.UUID) -> CustomerRequest:
    req = await session.get(CustomerRequest, request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Customer order not found")
    return req


async def _reply(session: AsyncSession, req: CustomerRequest) -> CustomerRequestOut:
    await session.refresh(req)
    names = await _names(session, [req.created_by])
    return _out(req, names.get(req.created_by))


def _clean_phone(raw: str) -> str:
    phone = (raw or "").strip()
    if len(re.sub(r"\D", "", phone)) < 7:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter a valid phone number for the customer."
        )
    return phone


# --------------------------------------------------------------- endpoints


@router.get("", response_model=list[CustomerRequestOut])
async def list_customer_requests(
    branch_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_VIEW),
) -> list[CustomerRequestOut]:
    """status: pending | arrived | collected | cancelled | open
    (open = pending + arrived). Omit for everything."""
    if current_user.role in ("branch_manager", "staff") and current_user.branch_id:
        branch_id = current_user.branch_id
    await sync_arrivals(session, branch_id)

    stmt = select(CustomerRequest)
    if branch_id is not None:
        stmt = stmt.where(CustomerRequest.branch_id == branch_id)
    if status_filter == "open":
        stmt = stmt.where(CustomerRequest.status.in_(["pending", "arrived"]))
    elif status_filter:
        stmt = stmt.where(CustomerRequest.status == status_filter)
    reqs = list((await session.execute(stmt)).scalars().all())
    # Newest first within each status; arrived (needs action) on top.
    reqs.sort(key=lambda r: _aware(r.created_at), reverse=True)
    reqs.sort(key=lambda r: _STATUS_ORDER.get(r.status, 9))
    names = await _names(session, [r.created_by for r in reqs])
    return [_out(r, names.get(r.created_by)) for r in reqs]


async def _find_or_create_draft_product(
    session: AsyncSession, item_name: str, image_url: str | None
) -> Product:
    """Make sure a typed customer-order item exists as an inventory product.

    Same pattern purchase_invoices.py uses for a typed invoice line: reuse
    the product with that name if there is one, otherwise create a bare
    DRAFT product (name only, no price/stock) so the order has something to
    hold stock on when the item later arrives. Prices etc. are filled in
    from Inventory afterwards.
    """
    found = await session.execute(
        select(Product).where(
            func.lower(Product.name) == item_name.lower(),
            Product.is_deleted.is_(False),
        )
    )
    product = found.scalars().first()
    if product is not None:
        return product
    from api.v1.routers.catalog import _generate_sku  # same helper the invoices use

    sku = await _generate_sku(session, item_name)
    product = Product(
        name=item_name,
        sku=sku,
        status="draft",
        cost_price_kobo=0,
        selling_price_kobo=0,
    )
    session.add(product)
    await session.flush()
    # CUSTOMER-ORDER-IMAGE-FIX: product pictures live in product_images
    # (Product has no image_url column), so save the order picture there.
    if image_url:
        from infrastructure.db.models import ProductImage
        session.add(
            ProductImage(product_id=product.id, url=image_url, is_primary=True, sort_order=0)
        )
        await session.flush()
    return product


@router.post("", response_model=CustomerRequestOut, status_code=status.HTTP_201_CREATED)
async def create_customer_request(
    body: CustomerRequestCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_MANAGE),
) -> CustomerRequestOut:
    if body.unit_label not in UNIT_LABELS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown unit_label '{body.unit_label}'")
    # PATCH1-CUSTOMER-ORDER-FIELDS
    deposit_method = (body.deposit_method or "").strip().lower() or None
    if body.deposit_naira > 0:
        if deposit_method not in ("cash", "card", "transfer", "online"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Choose how the deposit was paid (cash, card, transfer or online).",
            )
    else:
        deposit_method = None
    order_pieces = body.order_pieces
    if order_pieces is None:
        if body.unit_label == "pieces":
            order_pieces = body.quantity
        elif body.unit_label == "dozens":
            order_pieces = body.quantity * 12
    # PATCH3-TOTAL-OPTIONAL: the total pieces is optional (the carton size is
    # often unknown). Only "customer needs" must be known; when the total is
    # missing it stays empty and the arrival step asks how many pieces came.
    customer_pieces = body.customer_pieces if body.customer_pieces is not None else order_pieces
    if customer_pieces is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter how many pieces the customer needs.",
        )
    if order_pieces is not None and customer_pieces > order_pieces:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The customer cannot need more pieces than the whole order.",
        )
    phone = _clean_phone(body.customer_phone)
    item_name = body.item_name.strip()
    customer_name = body.customer_name.strip()
    if not item_name or not customer_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Item name and customer name are required.")

    product = None
    if body.product_id is not None:
        product = await session.get(Product, body.product_id)
        if product is None or product.is_deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
        item_name = product.name
    else:
        # A brand-new typed item: register it in inventory right away (as a
        # draft) so the order is tied to a real product from day one.
        product = await _find_or_create_draft_product(session, item_name, body.item_image_url)
        item_name = product.name

    entry = None
    if body.add_to_restock_list:
        if body.product_id is None:
            entry = RestockListEntry(
                product_id=None,
                custom_name=item_name,
                custom_image_url=body.item_image_url,
                branch_id=body.branch_id,
                is_manual=True,
                quantity=body.quantity,
                unit_label=body.unit_label,
                added_by=current_user.id,
            )
            session.add(entry)
        else:
            existing = await session.execute(
                select(RestockListEntry).where(
                    RestockListEntry.product_id == product.id,
                    RestockListEntry.branch_id == body.branch_id,
                )
            )
            entry = existing.scalar_one_or_none()
            if entry is None:
                entry = RestockListEntry(
                    product_id=product.id,
                    branch_id=body.branch_id,
                    is_manual=True,
                    quantity=body.quantity,
                    unit_label=body.unit_label,
                    added_by=current_user.id,
                )
                session.add(entry)
            else:
                entry.is_manual = True
                entry.quantity = max(entry.quantity or 1, body.quantity)
        await session.flush()

    req = CustomerRequest(
        branch_id=body.branch_id,
        restock_list_entry_id=entry.id if entry is not None else None,
        product_id=product.id if product is not None else None,
        item_name=item_name,
        item_image_url=body.item_image_url,
        quantity=body.quantity,
        unit_label=body.unit_label,
        customer_name=customer_name,
        customer_phone=phone,
        customer_address=(body.customer_address or "").strip() or None,
        notes=(body.notes or "").strip() or None,
        deposit_kobo=int(round(body.deposit_naira * 100)),
        order_pieces=order_pieces,
        customer_pieces=customer_pieces,
        deposit_method=deposit_method,
        status="pending",
        hold_pieces=0,
        created_by=current_user.id,
    )
    session.add(req)
    await session.commit()
    return await _reply(session, req)


@router.get("/product-search", response_model=list[ProductPick])
async def search_products_for_link(
    q: str = "",
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_VIEW),
) -> list[ProductPick]:
    """Small picker for "Mark arrived": find the inventory product that
    matches a customer's item."""
    term = q.strip()
    stmt = select(Product.id, Product.name).where(Product.is_deleted.is_(False))
    if term:
        stmt = stmt.where(Product.name.ilike(f"%{term}%"))
    stmt = stmt.order_by(Product.name).limit(15)
    rows = (await session.execute(stmt)).all()
    picks = []
    for product_id, name in rows:
        on_hand = await _on_hand(session, product_id, branch_id) if branch_id else 0
        picks.append(ProductPick(id=product_id, name=name, on_hand=on_hand))
    return picks


# PATCH2B-MARK-ARRIVED-STOCK
async def _add_arrived_stock(session, req, body, current_user) -> None:
    """Put the pieces that arrived on the shelf (one shelf batch plus one
    receipt_to_shelf movement, like Receive Goods), make sure the product
    has a selling price, and flag the product so Inventory shows
    "Add to invoice" until a supplier invoice is filed."""
    from api.v1.routers.catalog import _generate_sku
    from infrastructure.db.models import BranchProductPrice, StockTransfer
    from nickcosmetics_core.domain.money import Money

    if req.booking_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This order already has a booking. Add the stock in Inventory instead.",
        )

    pieces = body.pieces_arrived
    if pieces is None:
        pieces = int(req.order_pieces or default_hold_pieces(req.quantity, req.unit_label) or 0)
    if pieces <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter how many pieces arrived.")

    product = None
    if req.product_id is not None:
        product = await session.get(Product, req.product_id)
        if product is not None and product.is_deleted:
            product = None
    if product is None:
        name = (req.item_name or "").strip()
        found = await session.execute(
            select(Product).where(Product.is_deleted.is_(False), func.lower(Product.name) == name.lower())
        )
        product = found.scalars().first()
        if product is None:
            product = Product(
                name=name,
                sku=await _generate_sku(session, name),
                status="active",
                cost_price_kobo=0,
                selling_price_kobo=0,
            )
            session.add(product)
            await session.flush()

    override = await session.scalar(
        select(BranchProductPrice.selling_price_kobo).where(
            BranchProductPrice.branch_id == req.branch_id,
            BranchProductPrice.product_id == product.id,
        )
    )
    current_price = int(override if override is not None else (product.selling_price_kobo or 0))
    if current_price <= 0:
        typed = int(Money.from_naira(body.selling_price_naira).kobo) if body.selling_price_naira else 0
        if typed <= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Type the selling price per piece. This item has no price yet.",
            )
        product.selling_price_kobo = typed

    cost_kobo = int(Money.from_naira(body.cost_price_naira).kobo) if body.cost_price_naira else 0
    if cost_kobo <= 0:
        cost_kobo = int(product.cost_price_kobo or 0)
    elif not product.cost_price_kobo:
        product.cost_price_kobo = cost_kobo

    # PATCH2B-1-DRAFT-TO-ACTIVE
    # A typed order item starts as a draft product. Once its stock is on
    # the shelf it is a real product, so make it active. Products that are
    # already active (or discontinued) are left as they are.
    if product.status == "draft":
        product.status = "active"
    product.needs_invoice = True
    now = datetime.now(timezone.utc)
    short_id = str(req.id)[:8]
    session.add(
        StockBatch(
            product_id=product.id,
            branch_id=req.branch_id,
            batch_number="order-" + short_id,
            quantity_received=pieces,
            quantity_remaining=pieces,
            unit_cost_kobo=cost_kobo,
            expiry_date=product.expiration_date,
            received_at=now,
        )
    )
    session.add(
        StockTransfer(
            product_id=product.id,
            from_branch_id=req.branch_id,
            to_branch_id=req.branch_id,
            transfer_type="receipt_to_shelf",
            unit_label="pieces",
            unit_count=pieces,
            pieces_per_unit=1,
            quantity_pieces=pieces,
            notes="Arrived for customer order " + short_id,
            created_by=current_user.id,
        )
    )
    req.product_id = product.id
    await session.flush()


@router.post("/{request_id}/arrived", response_model=CustomerRequestOut)
async def mark_arrived(
    request_id: uuid.UUID,
    body: ArriveBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_MANAGE),
) -> CustomerRequestOut:
    """Manual "the item is here" -- for when the automatic name match
    could not link it. Optionally links an inventory product and sets
    how many pieces to hold back for the customer."""
    req = await _load(session, request_id)
    if req.status not in ("pending", "arrived"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"This order is already {req.status}.")
    if body.product_id is not None:
        product = await session.get(Product, body.product_id)
        if product is None or product.is_deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
        req.product_id = product.id
    # PATCH2B-MARK-ARRIVED-STOCK
    if body.add_to_shelf:
        await _add_arrived_stock(session, req, body, current_user)
        body.hold_pieces = 0
    wanted = body.hold_pieces if body.hold_pieces is not None else default_hold_pieces(req.quantity, req.unit_label)
    req.hold_pieces = await _capped_hold(session, req, wanted)
    if req.status == "pending":
        req.status = "arrived"
        req.arrived_at = datetime.now(timezone.utc)
    await session.commit()
    return await _reply(session, req)


@router.patch("/{request_id}", response_model=CustomerRequestOut)
async def update_customer_request(
    request_id: uuid.UUID,
    body: CustomerRequestUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> CustomerRequestOut:
    req = await _load(session, request_id)
    if req.status in ("collected", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"This order is already {req.status}.")
    if body.customer_phone is not None:
        req.customer_phone = _clean_phone(body.customer_phone)
    if body.customer_address is not None:
        req.customer_address = body.customer_address.strip() or None
    if body.notes is not None:
        req.notes = body.notes.strip() or None
    if body.hold_pieces is not None:
        if req.status != "arrived":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Stock can only be held once the item has arrived.")
        req.hold_pieces = await _capped_hold(session, req, body.hold_pieces)
    await session.commit()
    return await _reply(session, req)


@router.post("/{request_id}/collect", response_model=CustomerRequestOut)
async def mark_collected(
    request_id: uuid.UUID,
    body: CollectBody,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> CustomerRequestOut:
    """The customer is collecting. This releases the stock hold so the
    sale can be rung up at POS (the deposit is deducted by hand there --
    the reply carries deposit_naira for that)."""
    req = await _load(session, request_id)
    if req.status != "arrived":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Only an order whose item has arrived can be collected. Use 'Mark arrived' first.",
        )
    req.status = "collected"
    req.collected_at = datetime.now(timezone.utc)
    req.resolution_note = (body.note or "").strip() or None
    await session.commit()
    return await _reply(session, req)


@router.post("/{request_id}/cancel", response_model=CustomerRequestOut)
async def cancel_customer_request(
    request_id: uuid.UUID,
    body: CancelBody,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> CustomerRequestOut:
    """The customer no longer wants it. Releases any stock hold and
    records whether their deposit was given back."""
    req = await _load(session, request_id)
    if req.status not in ("pending", "arrived"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"This order is already {req.status}.")
    req.status = "cancelled"
    req.cancelled_at = datetime.now(timezone.utc)
    req.deposit_refunded = bool(body.deposit_refunded)
    req.resolution_note = (body.note or "").strip() or None
    await session.commit()
    return await _reply(session, req)
