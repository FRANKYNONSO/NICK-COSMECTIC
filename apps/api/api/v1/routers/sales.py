import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_permission, require_role
from infrastructure.db.audit import log_audit_event
from api.v1.schemas.catalog import ImportResult
from api.v1.schemas.sales import (
    AuditLogOut,
    BookingAddPaymentRequest,
    BookingCancelRequest,
    BookingChangeItemRequest,
    BookingCreateRequest,
    CheckoutRequest,
    PendingChangeCreate,
    PendingChangeMarkProcessed,
    PendingChangeOut,
    PendingChangeSettle,
    PendingChangeSummary,
    PendingRefundSummary,
    RefundCreate,
    RefundDetailOut,
    RefundItemOut,
    RefundListItem,
    RefundMarkProcessed,
    RefundOut,
    SaleItemOut,
    SaleOut,
    SalePaymentOut,
)
from api.v1.routers.catalog import _PUBLIC_BASE_URL
from infrastructure.db.checkout_adapter import (
    load_sale_repo,
    load_stock_batch_repo,
    persist_new_sale,
)
from infrastructure.db.models import AuditLog
from infrastructure.db.models import Branch as ORMBranch
from infrastructure.db.models import CreditEntry
from infrastructure.db.models import Customer
from infrastructure.db.models import PendingChange
from infrastructure.db.models import Product
from infrastructure.db.models import Refund as ORMRefund
from infrastructure.db.models import RefundItem
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import SalePayment
from infrastructure.db.models import Shift as ORMShift
from infrastructure.db.models import StockAdjustment as ORMStockAdjustment
from infrastructure.db.models import StockBatch as ORMStockBatch
from infrastructure.db.models import User
from infrastructure.db.repositories import CustomerRepository
from infrastructure.import_export.sales import (
    build_sales_import_template,
    export_sales_workbook,
    import_sales_workbook,
)
from nickcosmetics_core.domain.inventory.stock import FifoStockAllocator
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentMethod as DomainPaymentMethod
from nickcosmetics_core.domain.sales.sale import PaymentTender as DomainPaymentTender
from nickcosmetics_core.domain.sales.sale import SaleChannel
from nickcosmetics_core.domain.sales.sale import SaleStatus as DomainSaleStatus
from nickcosmetics_core.exceptions import InsufficientStockError

# SalesService itself, plus the CheckoutLine value object it expects --
# this router is the seam where the framework-agnostic core package
# gets wired into the live async API for the first time.
from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService

# Same rate as api/v1/routers/customers.py -- kept in sync manually
# since the two routers don't share a constants module yet.
POINTS_PER_NAIRA_SPENT = 1 / 100

router = APIRouter(prefix="/sales", tags=["sales"])

_REFUND_IMAGE_DIR = Path("static/uploads/refunds")
_ALLOWED_REFUND_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@router.post("/refund-photo")
async def upload_refund_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("process_sale")),
) -> dict:
    """Uploads a photo of a returned item ahead of POST /{sale_id}/refund
    -- the returned URL goes in RefundCreate.image_url. Same pattern
    as the warehouse intake-receipt photo upload.
    """
    original_name = file.filename or "refund"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_REFUND_IMAGE_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_REFUND_IMAGE_EXTENSIONS))}",
        )
    _REFUND_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _REFUND_IMAGE_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"url": f"{_PUBLIC_BASE_URL}/static/uploads/refunds/{new_filename}"}


_BOOKING_STORAGE_FEE_NAIRA_PER_DAY = 100
_BOOKING_STORAGE_FEE_KOBO_PER_DAY = _BOOKING_STORAGE_FEE_NAIRA_PER_DAY * 100


def _booking_storage_fee_kobo(orm_sale: ORMSale) -> int:
    """Flat â‚¦100/day storage-slash-security fee once a deposit-held
    booking has sat unpaid-in-full for more than a year (365 days).
    E.g. 30 days overdue = â‚¦3,000. Measured from the firm pickup_date
    if one was given, otherwise from when the booking was created.
    Zero for anything that isn't still an open booking.
    """
    if orm_sale.status != "booking":
        return 0
    anchor = orm_sale.pickup_date or orm_sale.created_at
    if anchor is None:
        return 0
    overdue_days = (datetime.utcnow() - anchor.replace(tzinfo=None)).days - 365
    if overdue_days <= 0:
        return 0
    return overdue_days * _BOOKING_STORAGE_FEE_KOBO_PER_DAY


def _refund_to_out(refund: "ORMRefund", sale_status: str) -> RefundOut:
    return RefundOut(
        id=refund.id,
        sale_id=refund.sale_id,
        processed_by=refund.processed_by,
        amount_naira=Money(refund.amount_kobo).naira,
        reason=refund.reason,
        image_url=refund.image_url,
        action=refund.action,
        bank_name=refund.bank_name,
        account_number=refund.account_number,
        account_name=refund.account_name,
        status=refund.status,
        exchange_product_id=refund.exchange_product_id,
        settlement=refund.settlement,
        settlement_amount_naira=(
            Money(refund.settlement_amount_kobo).naira if refund.settlement_amount_kobo is not None else None
        ),
        settlement_method=refund.settlement_method,
        created_at=refund.created_at,
        sale_status=sale_status,
    )


def _to_sale_out(
    orm_sale: ORMSale,
    orm_items: list[ORMSaleItem],
    cashier_names: dict[uuid.UUID, str] | None = None,
    product_names: dict[uuid.UUID, str] | None = None,
    paid_amounts_kobo: dict[uuid.UUID, int] | None = None,
    payments_by_sale: dict[uuid.UUID, list["SalePayment"]] | None = None,
    customer: "Customer | None" = None,
    refunds_by_sale: dict[uuid.UUID, list["ORMRefund"]] | None = None,
    pending_changes_by_sale: dict[uuid.UUID, list["PendingChange"]] | None = None,
) -> SaleOut:
    cashier_names = cashier_names or {}
    product_names = product_names or {}
    paid_amounts_kobo = paid_amounts_kobo or {}
    payments_by_sale = payments_by_sale or {}
    refunds_by_sale = refunds_by_sale or {}
    pending_changes_by_sale = pending_changes_by_sale or {}
    sale_payments = payments_by_sale.get(orm_sale.id, [])
    # cashier_names doubles as the staff-name lookup for both
    # "served_by" (who rang up the sale) and "recorded_by" (who took
    # each individual part-payment) -- callers that build this dict
    # for a set of sales fetch both sets of user ids up front.
    paid_naira = (
        Money(sum(p.amount_kobo for p in sale_payments)).naira
        if sale_payments
        else Money(paid_amounts_kobo.get(orm_sale.id, orm_sale.total_kobo if orm_sale.status == "completed" else 0)).naira
    )
    return SaleOut(
        id=orm_sale.id,
        branch_id=orm_sale.branch_id,
        customer_id=orm_sale.customer_id,
        served_by=orm_sale.served_by,
        cashier_name=cashier_names.get(orm_sale.served_by) if orm_sale.served_by else None,
        channel=orm_sale.channel,
        status=orm_sale.status,
        subtotal_naira=Money(orm_sale.subtotal_kobo).naira,
        discount_naira=Money(orm_sale.discount_kobo).naira,
        total_naira=Money(orm_sale.total_kobo).naira,
        payment_method=orm_sale.payment_method,
        local_ref_id=orm_sale.local_ref_id,
        notes=orm_sale.notes,
        pickup_date=orm_sale.pickup_date,
        paid_naira=paid_naira,
        storage_fee_naira=Money(_booking_storage_fee_kobo(orm_sale)).naira,
        payments=[
            SalePaymentOut(
                id=p.id,
                method=p.method,
                amount_naira=Money(p.amount_kobo).naira,
                recorded_by=p.recorded_by,
                recorded_by_name=cashier_names.get(p.recorded_by) if p.recorded_by else None,
                created_at=p.created_at,
            )
            for p in sale_payments
        ],
        created_at=orm_sale.created_at,
        items=[
            SaleItemOut(
                id=item.id,
                product_id=item.product_id,
                stock_batch_id=item.stock_batch_id,
                quantity=item.quantity,
                unit_price_naira=Money(item.unit_price_kobo).naira,
                line_total_naira=Money(item.line_total_kobo).naira,
                product_name=product_names.get(item.product_id),
                pack_units=[
                    {"code": p.code, "label": p.label, "plural_label": p.plural_label, "pieces": p.pieces}
                    for p in getattr(item, "_pack_units", [])
                ],
            )
            for item in orm_items
        ],
        customer_name=customer.full_name if customer else None,
        customer_phone=customer.phone if customer else None,
        refunds=[_refund_to_out(r, orm_sale.status) for r in refunds_by_sale.get(orm_sale.id, [])],
        item_swap_history=orm_sale.item_swap_history or [],
        # A sale can have more than one pending-change row -- change
        # owed can be split across settlement methods (part cash, part
        # airtime, etc.), each settled/approved independently -- so
        # every row for this sale is surfaced, not just the latest.
        pending_changes=[
            PendingChangeSummary(
                id=c.id,
                status=c.status,
                settlement_method=c.settlement_method,
                reference_code=c.reference_code,
                amount_naira=Money(c.amount_kobo).naira,
            )
            for c in pending_changes_by_sale.get(orm_sale.id, [])
        ],
    )


async def _resolve_product_names(
    session: AsyncSession, items: list[ORMSaleItem]
) -> dict[uuid.UUID, str]:
    """product_id -> product name for the receipt -- Sale/SaleItem only
    stores product_id, so this is a join back to catalog on every
    read. Small, targeted query (one IN clause), not worth caching
    given how infrequently a single sale is re-fetched.
    """
    product_ids = {item.product_id for item in items}
    if not product_ids:
        return {}
    result = await session.execute(select(Product).where(Product.id.in_(product_ids)))
    products = list(result.scalars().all())
    # PATCH9A-UNITS: hang each product's pack sizes on its sale items so
    # _to_sale_out can send them with the receipt.
    from infrastructure.units import load_unit_sizes, load_units, pack_units_for_product

    all_units = await load_units(session)
    sizes_by_product = await load_unit_sizes(session, [p.id for p in products])
    packs = {p.id: pack_units_for_product(all_units, p, sizes_by_product.get(p.id)) for p in products}
    for item in items:
        item._pack_units = packs.get(item.product_id, [])
    return {p.id: p.name for p in products}


@router.get("/bookings/holds", response_model=dict)
async def get_booking_holds(
    branch_id: uuid.UUID = Query(...),
    product_id: list[uuid.UUID] = Query(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> dict:
    """Read-only lookup the POS calls when a product hits the cart
    (or the cart's product list changes) so it can warn the cashier
    live -- before checkout is even attempted -- that some on-hand
    quantity is tied up in open bookings. Mirrors the reservation math
    in _enforce_available_stock() but never blocks anything; the POS
    already has on_hand from the catalog/stock endpoint, so it just
    does available = on_hand - reserved locally and shows an inline
    note once the requested quantity would eat into the reserved units.
    """
    holds = await _get_open_booking_holds(session, product_id, branch_id)
    return {
        str(pid): {
            "reserved_quantity": hold["reserved"],
            "booking_ids": [str(bid) for bid in hold["booking_ids"]],
        }
        for pid, hold in holds.items()
    }


async def _get_open_booking_holds(
    session: AsyncSession, product_ids: list[uuid.UUID], branch_id: uuid.UUID
) -> dict[uuid.UUID, dict]:
    """How many units of each product are tied up in still-open
    bookings at this branch. September 2026 change spec section 9
    (reversed): an open booking now DOES reserve stock against other
    sales -- on-hand still shows the full total (nothing is deducted
    from stock until the booking is paid in full and fulfilled), but
    nobody else should be able to sell past on_hand - reserved while
    these bookings sit open.
    """
    holds: dict[uuid.UUID, dict] = {}
    if not product_ids:
        return holds
    result = await session.execute(
        select(ORMSaleItem.product_id, ORMSaleItem.sale_id, ORMSaleItem.quantity)
        .join(ORMSale, ORMSale.id == ORMSaleItem.sale_id)
        .where(
            ORMSale.status == "booking",
            ORMSale.branch_id == branch_id,
            ORMSaleItem.product_id.in_(product_ids),
        )
    )
    for product_id, sale_id, quantity in result.all():
        entry = holds.setdefault(product_id, {"reserved": 0, "booking_ids": set()})
        entry["reserved"] += quantity
        entry["booking_ids"].add(sale_id)
    # Customer pre-orders whose item has arrived: the shop holds this stock
    # for the customer until it is collected or the order is cancelled (see
    # customer_requests.py). Done in a savepoint so a problem here can never
    # take POS checkout down with it.
    try:
        from infrastructure.db.models import CustomerRequest as _CustomerRequest

        async with session.begin_nested():
            held_rows = await session.execute(
                select(_CustomerRequest.product_id, func.sum(_CustomerRequest.hold_pieces))
                .where(
                    _CustomerRequest.status == "arrived",
                    _CustomerRequest.branch_id == branch_id,
                    _CustomerRequest.product_id.in_(product_ids),
                    _CustomerRequest.hold_pieces > 0,
                )
                .group_by(_CustomerRequest.product_id)
            )
            for held_product_id, held_pieces in held_rows.all():
                held_entry = holds.setdefault(held_product_id, {"reserved": 0, "booking_ids": set()})
                held_entry["reserved"] += int(held_pieces or 0)
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).exception("customer order stock hold lookup failed")
    return holds


async def _enforce_available_stock(session, items, branch_id: uuid.UUID) -> None:
    """Raises 409 if any line would sell past on_hand - reserved,
    where "reserved" is everything sitting in a still-open booking at
    this branch (_get_open_booking_holds). on_hand comes straight from
    StockBatch.quantity_remaining -- the same source load_stock_batch_repo
    reads from -- so this reflects real, currently-in-stock quantity.
    """
    product_ids = [line.product_id for line in items]
    if not product_ids:
        return

    # Lock this branch's batch rows for these products first (held
    # until the request commits/rolls back), so the on_hand figure
    # below can't go stale between this check and the actual
    # deduction by a second cashier/booking running at the same time.
    # Same lock order as load_stock_batch_repo (received_at, id).
    await session.execute(
        select(ORMStockBatch.id)
        .where(
            ORMStockBatch.product_id.in_(product_ids),
            ORMStockBatch.branch_id == branch_id,
        )
        .order_by(ORMStockBatch.received_at, ORMStockBatch.id)
        .with_for_update()
    )

    on_hand_result = await session.execute(
        select(ORMStockBatch.product_id, func.sum(ORMStockBatch.quantity_remaining))
        .where(
            ORMStockBatch.product_id.in_(product_ids),
            ORMStockBatch.branch_id == branch_id,
        )
        .group_by(ORMStockBatch.product_id)
    )
    on_hand = {product_id: int(total or 0) for product_id, total in on_hand_result.all()}

    holds = await _get_open_booking_holds(session, product_ids, branch_id)

    product_names = None
    for line in items:
        hold = holds.get(line.product_id)
        if not hold or hold["reserved"] <= 0:
            continue
        available = on_hand.get(line.product_id, 0) - hold["reserved"]
        if line.quantity > available:
            if product_names is None:
                name_rows = await session.execute(
                    select(Product.id, Product.name).where(Product.id.in_(product_ids))
                )
                product_names = dict(name_rows.all())
            product_name = product_names.get(line.product_id, str(line.product_id))
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "STOCK_ON_OPEN_BOOKING",
                    "message": (
                        f"{hold['reserved']} unit(s) of {product_name} are on open "
                        f"booking or held for a customer order, awaiting payment/collection. Only "
                        f"{max(available, 0)} of {on_hand.get(line.product_id, 0)} in "
                        f"stock can be sold right now. Cancel the open booking or customer order to free "
                        f"up the rest."
                    ),
                    "conflicting_booking_ids": [str(bid) for bid in hold["booking_ids"]],
                },
            )


@router.post("/checkout", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
async def checkout(
    body: CheckoutRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> SaleOut:
    if body.channel == "pos":
        if body.shift_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="An open shift is required to check out a POS sale.",
            )
        shift = await session.get(ORMShift, body.shift_id)
        if shift is None or shift.status != "open" or shift.opened_by != current_user.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="shift_id must be your own currently-open shift.",
            )

    product_ids = [line.product_id for line in body.items]

    await _enforce_available_stock(session, body.items, body.branch_id)

    stock_repo = await load_stock_batch_repo(session, product_ids, body.branch_id)
    sale_repo = await load_sale_repo(session, body.local_ref_id)

    service = SalesService(stock_repo, sale_repo)
    checkout_lines = [
        CheckoutLine(
            variant_id=str(line.product_id),
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
        idempotent_items = list(items_result.scalars().all())
        idempotent_payments_result = await session.execute(
            select(SalePayment).where(SalePayment.sale_id == orm_sale.id).order_by(SalePayment.id)
        )
        idempotent_payments = list(idempotent_payments_result.scalars().all())
        return _to_sale_out(
            orm_sale,
            idempotent_items,
            {current_user.id: current_user.full_name},
            await _resolve_product_names(session, idempotent_items),
            payments_by_sale={orm_sale.id: idempotent_payments} if idempotent_payments else None,
        )

    orm_sale = persist_new_sale(session, domain_sale, body.payment_method)
    orm_sale.shift_id = body.shift_id

    if body.payments:
        tendered_kobo = sum(round(p.amount_naira * 100) for p in body.payments)
        # Small rounding slack (1 kobo) for float arithmetic on the client.
        # Payments are allowed to exceed the sale total -- a customer
        # handing over a bigger note than the total (e.g. paying â‚¦500
        # cash for a â‚¦250 item) is a completed sale plus change owed,
        # not a mismatched payment. The tendered amount is recorded as
        # given (so the till reflects the real cash received) and the
        # excess is surfaced to the client as change due; only a
        # shortfall (tendered < total) is rejected.
        if tendered_kobo < orm_sale.total_kobo - 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Payments total {Money(tendered_kobo).naira} but the sale "
                    f"total is {Money(orm_sale.total_kobo).naira}."
                ),
            )
        new_sale_payments: list[SalePayment] = []
        for tender in body.payments:
            payment_row = SalePayment(
                sale_id=orm_sale.id,
                method=tender.method,
                amount_kobo=round(tender.amount_naira * 100),
                recorded_by=current_user.id,
            )
            session.add(payment_row)
            new_sale_payments.append(payment_row)
        orm_sale.payment_method = body.payments[0].method if len(body.payments) == 1 else "split"
    else:
        new_sale_payments = []

    if body.discount_naira and body.discount_naira > 0:
        log_audit_event(
            session,
            user_id=current_user.id,
            action="discount_applied",
            entity_type="sale",
            entity_id=orm_sale.id,
            metadata={"discount_naira": body.discount_naira},
        )

    if orm_sale.customer_id is not None:
        # Award loyalty points on genuinely new sales only -- the
        # idempotent-retry branch above returns early and never
        # reaches here, so a retried sync can't double-award points.
        customer = await session.get(Customer, orm_sale.customer_id)
        if customer is not None:
            earned = int(Money(orm_sale.total_kobo).naira * POINTS_PER_NAIRA_SPENT)
            customer.loyalty_points += earned

    await session.commit()
    await session.refresh(orm_sale)

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == orm_sale.id))
    new_sale_items = list(items_result.scalars().all())

    # "Sold Below Original Price" as a checkout-time discount path was
    # removed (spec section 3 -- all discounts now flow through
    # super-admin-generated coupon codes only), so there is no longer
    # any per-line loss tracking here; SaleLoss/ORMSaleLoss has been
    # retired along with it.
    return _to_sale_out(
        orm_sale,
        new_sale_items,
        {current_user.id: current_user.full_name},
        await _resolve_product_names(session, new_sale_items),
        payments_by_sale={orm_sale.id: new_sale_payments} if new_sale_payments else None,
    )


# These three routes must be registered before /{sale_id} below --
# same reasoning as the equivalent block in catalog.py.

@router.get("/export")
async def export_sales(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("view_all_reports")),
) -> Response:
    content = await export_sales_workbook(session)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sales.xlsx"},
    )


@router.get("/import-template")
async def download_sales_import_template(
    _: User = Depends(require_permission("view_all_reports")),
) -> Response:
    content = build_sales_import_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sales-import-template.xlsx"},
    )


@router.post("/import", response_model=ImportResult)
async def import_sales(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("view_all_reports")),
) -> ImportResult:
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please upload a .xlsx file.")
    file_bytes = await file.read()
    result = await import_sales_workbook(session, file_bytes)
    return ImportResult(**result)


# --------------------------------------------------------------- BOOKINGS
# Registered before GET /{sale_id} below for the same reason as the
# export/import-template/import block above -- "/bookings" would
# otherwise be swallowed as an (invalid) sale_id by that catch-all.

@router.post("/bookings", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> SaleOut:
    """Part-payment booking against a deposit that can be less than
    the full price. The customer isn't held to an exact pickup
    date/time -- `pickup_note` is free text for a flexible window.

    September 2026 change spec section 9: an open booking does NOT
    reserve/deduct stock -- the customer hasn't paid in full, so the
    item is still physically in the shop and still counts as
    available stock elsewhere (on the shelf, in another sale, in
    another booking). Only completing the booking (paid in full, see
    add_booking_payment()) actually deducts. This is a deliberate,
    confirmed tradeoff: it's possible for stock to be oversold across
    several open bookings on the same limited item, but that's judged
    better than tying up real stock for a reservation the customer
    hasn't committed money to yet. Open Credit is different and
    unaffected by this -- see credit.py::create_credit(), which still
    deducts immediately, since a credit is already vetted/trusted by a
    manager the moment it's opened.
    """
    product_ids = [line.product_id for line in body.items]
    item_total_naira = sum(line.quantity * line.unit_price_naira for line in body.items)
    await _enforce_available_stock(session, body.items, body.branch_id)
    if body.discount_naira > item_total_naira:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Discount cannot exceed the item total."
        )
    discounted_total_naira = item_total_naira - body.discount_naira
    if body.deposit_amount_naira > discounted_total_naira:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Deposit cannot exceed the item total after discount."
        )

    # A booking must always be traceable to a real customer -- either
    # one already on file, or their full legal name + phone supplied
    # right now (spec: "it should be a must that you must take the
    # customer's full legal name and the phone number when trying to
    # book, open a booking"). No customer_id and no name+phone is a
    # hard 400, not silently allowed through as an anonymous booking.
    customer_repo = CustomerRepository(session)
    resolved_customer_id = body.customer_id
    if resolved_customer_id is None:
        name = (body.customer_full_name or "").strip()
        phone = (body.customer_phone or "").strip()
        if not name or not phone:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="A booking requires either an existing customer or the customer's full legal name and phone number.",
            )
        existing = await customer_repo.get_by_phone(phone)
        if existing is not None:
            resolved_customer_id = existing.id
            if not existing.full_name:
                existing.full_name = name
        else:
            new_customer = Customer(full_name=name, phone=phone)
            new_customer = await customer_repo.create(new_customer)
            resolved_customer_id = new_customer.id

    stock_repo = await load_stock_batch_repo(session, product_ids, body.branch_id)
    sale_repo = await load_sale_repo(session, body.local_ref_id)

    service = SalesService(stock_repo, sale_repo)
    checkout_lines = [
        CheckoutLine(
            variant_id=str(line.product_id),
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
            channel=SaleChannel.POS,
            local_ref_id=body.local_ref_id,
            lines=checkout_lines,
            customer_id=str(resolved_customer_id) if resolved_customer_id else None,
            payments=[
                DomainPaymentTender(
                    method=DomainPaymentMethod(body.deposit_method),
                    amount=Money.from_naira(body.deposit_amount_naira),
                )
            ],
            status=DomainSaleStatus.BOOKING,
            notes=body.pickup_note,
            discount=Money.from_naira(body.discount_naira),
        )
    except InsufficientStockError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if sale_repo.was_idempotent_hit:
        result = await session.execute(select(ORMSale).where(ORMSale.id == uuid.UUID(domain_sale.id)))
        orm_sale = result.scalar_one()
        items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == orm_sale.id))
        booking_items = list(items_result.scalars().all())
        payments_result = await session.execute(select(SalePayment).where(SalePayment.sale_id == orm_sale.id))
        existing_payments = list(payments_result.scalars().all())
        staff_ids = {current_user.id} | {p.recorded_by for p in existing_payments if p.recorded_by}
        staff_names = {current_user.id: current_user.full_name}
        if staff_ids - {current_user.id}:
            users_result = await session.execute(select(User).where(User.id.in_(staff_ids)))
            staff_names = {u.id: u.full_name for u in users_result.scalars().all()}
        return _to_sale_out(
            orm_sale,
            booking_items,
            staff_names,
            await _resolve_product_names(session, booking_items),
            payments_by_sale={orm_sale.id: existing_payments},
        )

    orm_sale = persist_new_sale(session, domain_sale, body.deposit_method)
    orm_sale.notes = body.pickup_note
    orm_sale.pickup_date = body.pickup_date

    # persist_new_sale only stages the Sale/SaleItem rows -- the deposit
    # tender passed to service.checkout() lives on the in-memory domain
    # object and still needs its own SalePayment row here, same as the
    # /checkout endpoint does for body.payments below.
    deposit_kobo = round(body.deposit_amount_naira * 100)
    deposit_payment = SalePayment(
        sale_id=orm_sale.id,
        method=body.deposit_method,
        amount_kobo=deposit_kobo,
        recorded_by=current_user.id,
    )
    session.add(deposit_payment)

    log_audit_event(
        session,
        user_id=current_user.id,
        action="booking_created",
        entity_type="sale",
        entity_id=orm_sale.id,
        metadata={
            "deposit_naira": body.deposit_amount_naira,
            "discount_naira": body.discount_naira,
            "total_naira": Money(orm_sale.total_kobo).naira,
            "pickup_note": body.pickup_note,
        },
    )

    await session.commit()
    await session.refresh(orm_sale)

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == orm_sale.id))
    new_booking_items = list(items_result.scalars().all())
    payments_result = await session.execute(select(SalePayment).where(SalePayment.sale_id == orm_sale.id))
    booking_payments = list(payments_result.scalars().all())
    return _to_sale_out(
        orm_sale,
        new_booking_items,
        {current_user.id: current_user.full_name},
        await _resolve_product_names(session, new_booking_items),
        payments_by_sale={orm_sale.id: booking_payments},
    )


@router.get("/bookings", response_model=list[SaleOut])
async def list_bookings(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_own_sales")),
) -> list[SaleOut]:
    """Open bookings only (status='booking') -- once completed or
    cancelled they show up in the ordinary sales list/history instead,
    same as a normal sale would.
    """
    # Open bookings are visible to every staff member at their OWN
    # branch regardless of who took the original deposit -- any cashier
    # on shift needs to be able to find a customer's booking and take
    # the next payment or hand over the item, so (unlike the ordinary
    # sales list) this is deliberately NOT restricted to the caller's
    # own served_by rows. It IS still hard-restricted to the caller's
    # own branch server-side for a cashier -- never trusted from the
    # client -- so a staff member can't see another branch's open
    # bookings just by omitting/changing the branch_id query param.
    stmt = select(ORMSale).where(ORMSale.status == "booking")
    if current_user.role != "super_admin":
        stmt = stmt.where(ORMSale.branch_id == current_user.branch_id)
    elif branch_id:
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

    all_items = [item for items in items_by_sale.values() for item in items]
    product_names = await _resolve_product_names(session, all_items)

    # Every part-payment on an open booking is visible to every role
    # (cashier, branch manager, super admin) -- so this fetches the
    # full row set, not just a summed total, and looks up the name of
    # whoever recorded each one alongside the sale's own cashier.
    payments_result = await session.execute(
        select(SalePayment).where(SalePayment.sale_id.in_(sale_ids)).order_by(SalePayment.id)
    )
    all_payments = list(payments_result.scalars().all())
    payments_by_sale: dict[uuid.UUID, list[SalePayment]] = {}
    for p in all_payments:
        payments_by_sale.setdefault(p.sale_id, []).append(p)

    staff_ids = {s.served_by for s in orm_sales if s.served_by is not None}
    staff_ids |= {p.recorded_by for p in all_payments if p.recorded_by is not None}
    staff_names: dict[uuid.UUID, str] = {}
    if staff_ids:
        users_result = await session.execute(select(User).where(User.id.in_(staff_ids)))
        staff_names = {u.id: u.full_name for u in users_result.scalars().all()}

    return [
        _to_sale_out(s, items_by_sale.get(s.id, []), staff_names, product_names, payments_by_sale=payments_by_sale)
        for s in orm_sales
    ]


# PATCH3A-BOOKING-DRIVES-ORDER
# When a booking that came from a customer order is completed or cancelled,
# the order moves by itself (Collected / Cancelled). The order update runs in
# its own savepoint and any failure is only logged, so it can never block
# completing or cancelling a booking.
async def _sync_customer_order_for_booking(session, booking_id, outcome, refund=None):
    import logging as _logging

    # Real problems with the booking itself must surface where they always
    # did, so flush them here, outside the guarded block below.
    await session.flush()
    try:
        from infrastructure.db.models import CustomerRequest as _CustomerRequest

        async with session.begin_nested():
            found = await session.execute(
                select(_CustomerRequest).where(_CustomerRequest.booking_id == booking_id)
            )
            order = found.scalars().first()
            if order is None or order.status != "arrived":
                return
            now = datetime.now(timezone.utc)
            order.hold_pieces = 0
            if outcome == "collected":
                order.status = "collected"
                order.collected_at = now
                order.resolution_note = None
            elif outcome == "cancelled":
                order.status = "cancelled"
                order.cancelled_at = now
                if refund is not None and refund.status == "completed":
                    order.deposit_refunded = True
                    order.resolution_note = None
                elif refund is not None:
                    order.resolution_note = "Refund pending (bank transfer)"
                else:
                    order.resolution_note = None
    except Exception:
        _logging.getLogger(__name__).exception(
            "could not update the customer order linked to booking %s", booking_id
        )


# PATCH3A2-REFUND-PROCESSED-UPDATES-ORDER (helper)
async def _sync_customer_order_for_refund_processed(session, sale_id):
    import logging as _logging

    # Flush the refund change first, outside the guarded block below.
    await session.flush()
    try:
        from infrastructure.db.models import CustomerRequest as _CustomerRequest

        async with session.begin_nested():
            found = await session.execute(
                select(_CustomerRequest).where(_CustomerRequest.booking_id == sale_id)
            )
            order = found.scalars().first()
            if order is None:
                return
            if order.status != "cancelled" or order.deposit_refunded:
                return
            if order.resolution_note != "Refund pending (bank transfer)":
                return
            order.deposit_refunded = True
            order.resolution_note = None
    except Exception:
        _logging.getLogger(__name__).exception(
            "could not update the customer order for processed refund of sale %s", sale_id
        )


@router.post("/bookings/{booking_id}/add-payment", response_model=SaleOut)
async def add_booking_payment(
    booking_id: uuid.UUID,
    body: BookingAddPaymentRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> SaleOut:
    """Record one part-payment top-up against an open booking. Any
    staff member can do this, whether or not they created the
    booking -- the amount can be less than the remaining balance (the
    customer just topped up, they'll be back for the rest later) or
    exactly the remaining balance (which completes the sale).

    September 2026 change spec section 9: stock is NOT deducted at
    booking time anymore (see create_booking()) -- it's deducted right
    here, the moment the booking actually completes, against whatever
    stock genuinely remains at that point.
    """
    orm_sale = await session.get(ORMSale, booking_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if orm_sale.status != "booking":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot add a payment to a sale with status '{orm_sale.status}'"
        )

    payments_result = await session.execute(
        select(func.sum(SalePayment.amount_kobo)).where(SalePayment.sale_id == booking_id)
    )
    already_paid_kobo = int(payments_result.scalar() or 0)
    storage_fee_kobo = _booking_storage_fee_kobo(orm_sale)
    balance_kobo = orm_sale.total_kobo + storage_fee_kobo - already_paid_kobo

    amount_kobo = round(body.amount_naira * 100)
    if amount_kobo > balance_kobo + 1:  # 1 kobo rounding slack
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"That's more than the remaining balance ({Money(max(balance_kobo, 0)).naira}).",
        )

    session.add(SalePayment(
        sale_id=booking_id,
        method=body.payment_method,
        amount_kobo=amount_kobo,
        recorded_by=current_user.id,
    ))
    if body.pickup_date is not None:
        orm_sale.pickup_date = body.pickup_date

    now_paid_kobo = already_paid_kobo + amount_kobo
    now_complete = now_paid_kobo >= orm_sale.total_kobo + storage_fee_kobo - 1
    if now_complete:
        # September 2026 change spec section 9: this is the point
        # stock actually leaves the shelf -- an open booking never
        # deducted (see create_booking() / SalesService.checkout()).
        # Re-run FIFO fresh against whatever stock genuinely remains
        # *right now* (not whatever was recorded at booking time,
        # which could since have been sold to someone else -- the
        # accepted "overselling tradeoff") and update each item's
        # stock_batch_id to reflect the batch actually drawn from.
        items_for_deduction = await session.execute(
            select(ORMSaleItem).where(ORMSaleItem.sale_id == booking_id)
        )
        items_to_fulfill = list(items_for_deduction.scalars().all())
        product_ids = [item.product_id for item in items_to_fulfill]
        stock_repo = await load_stock_batch_repo(session, product_ids, orm_sale.branch_id)
        for item in items_to_fulfill:
            try:
                batches = stock_repo.get_batches_for_variant(str(item.product_id), str(orm_sale.branch_id))
                allocation = FifoStockAllocator.allocate(batches, str(item.product_id), item.quantity)
            except InsufficientStockError as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Not enough stock to complete this pickup -- {exc}. "
                        "The item reserved by this booking has since been sold elsewhere; "
                        "change the item or cancel the booking instead."
                    ),
                )
            # A single sale item can now split across more than one
            # batch if no single batch covers the full quantity --
            # update this row for the first (largest) portion and add
            # extra rows for the remainder, mirroring how a fresh
            # checkout's SalesService.checkout() would lay it out.
            first_batch, first_qty = allocation[0]
            first_batch.quantity_remaining -= first_qty
            stock_repo.save_batch(first_batch)
            item.stock_batch_id = uuid.UUID(first_batch.id)
            for extra_batch, extra_qty in allocation[1:]:
                extra_batch.quantity_remaining -= extra_qty
                stock_repo.save_batch(extra_batch)
                session.add(ORMSaleItem(
                    sale_id=booking_id,
                    product_id=item.product_id,
                    stock_batch_id=uuid.UUID(extra_batch.id),
                    quantity=extra_qty,
                    unit_price_kobo=item.unit_price_kobo,
                    line_total_kobo=round(item.unit_price_kobo * extra_qty),
                ))
                item.quantity -= extra_qty
                item.line_total_kobo = round(item.unit_price_kobo * item.quantity)

        orm_sale.status = "completed"
        # "split" once more than one tender has gone into this sale
        # (deposit + at least one top-up, or several top-ups) -- a
        # single deposit that exactly covered the total stays as that
        # one payment method instead of being mislabelled "split".
        payment_count_result = await session.execute(
            select(func.count()).select_from(SalePayment).where(SalePayment.sale_id == booking_id)
        )
        payment_count = int(payment_count_result.scalar() or 0) + 1  # +1 for the row just added
        orm_sale.payment_method = "split" if payment_count > 1 else body.payment_method
        # PATCH3A-BOOKING-DRIVES-ORDER
        await _sync_customer_order_for_booking(session, orm_sale.id, "collected")

    log_audit_event(
        session,
        user_id=current_user.id,
        action="booking_completed" if now_complete else "booking_payment_added",
        entity_type="sale",
        entity_id=orm_sale.id,
        metadata={"amount_naira": body.amount_naira, "method": body.payment_method},
    )

    await session.commit()
    await session.refresh(orm_sale)

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == booking_id))
    booking_items = list(items_result.scalars().all())
    payments_result = await session.execute(
        select(SalePayment).where(SalePayment.sale_id == booking_id).order_by(SalePayment.id)
    )
    all_payments = list(payments_result.scalars().all())
    staff_ids = {orm_sale.served_by, current_user.id} | {p.recorded_by for p in all_payments if p.recorded_by}
    staff_ids.discard(None)
    users_result = await session.execute(select(User).where(User.id.in_(staff_ids)))
    staff_names = {u.id: u.full_name for u in users_result.scalars().all()}
    return _to_sale_out(
        orm_sale,
        booking_items,
        staff_names,
        await _resolve_product_names(session, booking_items),
        payments_by_sale={booking_id: all_payments},
    )


@router.post("/bookings/{booking_id}/change-item", response_model=SaleOut)
async def change_booking_item(
    booking_id: uuid.UUID,
    body: BookingChangeItemRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> SaleOut:
    """Swap the product on an open booking (spec section 1).
    Recalculates the total/balance while keeping the deposit already
    paid untouched, and leaves a full OLD -> NEW audit trail (both in
    the audit log and in orm_sale.item_swap_history, which the
    receipt/booking detail view can render) rather than overwriting
    the original line silently.

    September 2026 change spec section 9: since an open booking
    doesn't deduct/reserve stock at all, this doesn't restock the old
    item or deduct the new one either -- it only validates the new
    item has enough stock to be a sane swap. If the new item costs
    LESS than what's already been paid, the difference is refunded
    same-day-in-cash (if that's how the deposit was originally paid,
    within 24 hours) or routed to a pending bank-transfer refund
    otherwise -- see the refund_method handling below, which mirrors
    section 10's cancellation refund logic.
    """
    orm_sale = await session.get(ORMSale, booking_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if orm_sale.status != "booking":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot change the item on a sale with status '{orm_sale.status}'"
        )

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == booking_id))
    existing_items = list(items_result.scalars().all())
    if len(existing_items) != 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Change Item is only supported for a booking with a single product line.",
        )
    old_item = existing_items[0]

    new_product = await session.get(Product, body.product_id)
    if new_product is None or new_product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    old_product = await session.get(Product, old_item.product_id)

    old_price_kobo = old_item.unit_price_kobo
    old_qty = old_item.quantity
    old_total_kobo = orm_sale.total_kobo

    new_unit_price_kobo = (
        Money.from_naira(body.unit_price_naira).kobo
        if body.unit_price_naira is not None
        else new_product.selling_price_kobo
    )
    new_line_total_kobo = new_unit_price_kobo * body.quantity
    new_total_kobo = new_line_total_kobo - orm_sale.discount_kobo

    paid_result = await session.execute(
        select(func.sum(SalePayment.amount_kobo)).where(SalePayment.sale_id == booking_id)
    )
    deposit_paid_kobo = int(paid_result.scalar_one_or_none() or 0)

    # September 2026 change spec section 9: when the new item costs
    # LESS than what's already been paid, the difference is owed back
    # to the customer -- same-day-cash vs. everything-else routing
    # mirrors section 10's cancellation refund logic exactly, rather
    # than blocking the swap outright and making the cashier go
    # process a separate refund first.
    change_refund = None
    if deposit_paid_kobo > new_total_kobo:
        difference_kobo = deposit_paid_kobo - new_total_kobo
        first_payment_result = await session.execute(
            select(SalePayment).where(SalePayment.sale_id == booking_id).order_by(SalePayment.created_at).limit(1)
        )
        first_payment = first_payment_result.scalar_one_or_none()
        same_day = (datetime.now(timezone.utc) - orm_sale.created_at) <= timedelta(hours=24)
        paid_in_cash = first_payment is not None and first_payment.method == "cash"

        if same_day and paid_in_cash:
            if body.refund_method != "cash":
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"The new item is {Money(difference_kobo).naira:,.2f} cheaper. "
                        "This was paid in cash today, so confirm handing that difference back in cash "
                        "to proceed with the swap."
                    ),
                )
            change_refund = ORMRefund(
                sale_id=orm_sale.id,
                processed_by=current_user.id,
                amount_kobo=difference_kobo,
                reason="Booking item change -- price difference refunded in cash on the spot.",
                action="cash",
                status="completed",
            )
        else:
            if body.refund_method != "transfer" or not (body.refund_account_number or "").strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"The new item is {Money(difference_kobo).naira:,.2f} cheaper, and this can no longer be "
                        "refunded in cash on the spot (either it wasn't paid in cash originally, or it's been "
                        "more than 24 hours). Provide the customer's bank account details -- account number, "
                        "bank name, and account name -- to proceed with the swap."
                    ),
                )
            change_refund = ORMRefund(
                sale_id=orm_sale.id,
                processed_by=current_user.id,
                amount_kobo=difference_kobo,
                reason="Booking item change -- price difference owed to the customer via bank transfer.",
                action="transfer",
                status="pending",
                bank_name=body.refund_bank_name,
                account_number=body.refund_account_number,
                account_name=body.refund_account_name,
            )
        session.add(change_refund)

    # September 2026 change spec section 9: an open booking never
    # deducted stock in the first place (see create_booking()), so
    # there's no old-item restock needed here, and the new item isn't
    # deducted either -- it's still just a reservation until the
    # booking is actually paid in full (add_booking_payment() does the
    # real FIFO deduction at that point, against whatever stock
    # genuinely remains then). This still validates that the new item
    # has enough stock to be a sane swap, and records which batch it
    # would draw from for cost/traceability continuity, but doesn't
    # touch quantity_remaining either direction.
    new_stock_batch_id = await _pick_batch_for_booking_change(session, body.product_id, orm_sale.branch_id, body.quantity)

    old_item.product_id = body.product_id
    old_item.quantity = body.quantity
    old_item.unit_price_kobo = new_unit_price_kobo
    old_item.line_total_kobo = new_line_total_kobo
    old_item.stock_batch_id = new_stock_batch_id

    orm_sale.subtotal_kobo = new_line_total_kobo
    orm_sale.total_kobo = new_total_kobo

    new_balance_kobo = orm_sale.total_kobo - deposit_paid_kobo

    # September 2026 change spec section 9: append-only trail of every
    # item swap on this booking, since old_item above is overwritten
    # in place -- "the system must record and display the full chain,"
    # not just the final state.
    swap_entry = {
        "from_product_name": old_product.name if old_product else None,
        "from_quantity": old_qty,
        "from_unit_price_naira": Money(old_price_kobo).naira,
        "to_product_name": new_product.name,
        "to_quantity": body.quantity,
        "to_unit_price_naira": Money(new_unit_price_kobo).naira,
        "previous_balance_naira": Money(old_total_kobo - deposit_paid_kobo).naira,
        "new_balance_naira": Money(new_balance_kobo).naira,
        "refund_naira": Money(change_refund.amount_kobo).naira if change_refund else None,
        "refund_method": change_refund.action if change_refund else None,
        "changed_by": str(current_user.id),
        "changed_by_name": current_user.full_name,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }
    orm_sale.item_swap_history = [*(orm_sale.item_swap_history or []), swap_entry]

    log_audit_event(
        session,
        user_id=current_user.id,
        action="booking_item_changed",
        entity_type="sale",
        entity_id=orm_sale.id,
        metadata={
            "old_product": old_product.name if old_product else None,
            "old_quantity": old_qty,
            "old_unit_price_naira": Money(old_price_kobo).naira,
            "old_total_naira": Money(old_total_kobo).naira,
            "new_product": new_product.name,
            "new_quantity": body.quantity,
            "new_unit_price_naira": Money(new_unit_price_kobo).naira,
            "new_total_naira": Money(orm_sale.total_kobo).naira,
            "deposit_paid_naira": Money(deposit_paid_kobo).naira,
            "previous_balance_naira": Money(old_total_kobo - deposit_paid_kobo).naira,
            "new_balance_naira": Money(new_balance_kobo).naira,
            "refund_naira": swap_entry["refund_naira"],
            "refund_method": swap_entry["refund_method"],
        },
    )

    await session.commit()
    await session.refresh(orm_sale)
    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == booking_id))
    refreshed_items = list(items_result.scalars().all())
    payments_result = await session.execute(select(SalePayment).where(SalePayment.sale_id == booking_id))
    all_payments = list(payments_result.scalars().all())
    staff_ids = {current_user.id} | {p.recorded_by for p in all_payments if p.recorded_by}
    staff_names = {current_user.id: current_user.full_name}
    if staff_ids - {current_user.id}:
        users_result = await session.execute(select(User).where(User.id.in_(staff_ids)))
        staff_names = {u.id: u.full_name for u in users_result.scalars().all()}
    return _to_sale_out(
        orm_sale,
        refreshed_items,
        staff_names,
        await _resolve_product_names(session, refreshed_items),
        payments_by_sale={booking_id: all_payments},
    )


async def _pick_batch_for_booking_change(
    session: AsyncSession, product_id: uuid.UUID, branch_id: uuid.UUID, quantity: int
) -> uuid.UUID | None:
    """Validates the new item on a booking Change Item has enough
    stock to be a sane swap, and returns the oldest eligible batch id
    for traceability -- but (September 2026 change spec section 9)
    does NOT deduct quantity_remaining, since an open booking doesn't
    reserve/deduct stock at all until it's actually paid in full and
    completed. Raises 400 if the branch doesn't have enough stock.
    """
    result = await session.execute(
        select(ORMStockBatch)
        .where(
            ORMStockBatch.product_id == product_id,
            ORMStockBatch.branch_id == branch_id,
            ORMStockBatch.quantity_remaining > 0,
        )
        .order_by(ORMStockBatch.received_at)
    )
    batches = list(result.scalars().all())
    available = sum(b.quantity_remaining for b in batches)
    if available < quantity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock for the new product. Only {available} unit(s) are available.",
        )
    return batches[0].id if batches else None


@router.post("/bookings/{booking_id}/cancel", response_model=SaleOut)
async def cancel_booking(
    booking_id: uuid.UUID,
    body: BookingCancelRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> SaleOut:
    """Customer never came back for the balance. September 2026 change
    spec section 9: an open booking never deducted stock in the first
    place (see create_booking()), so there's nothing to restock here
    anymore -- the item was never actually taken off the shelf, it's
    been sellable the whole time the booking sat open. body.restock is
    kept on the request schema for backward compatibility with any
    existing caller, but is now a no-op.

    Section 10: if a deposit was paid, cancelling now actively
    triggers the refund (same-day-cash if that's how it was paid and
    it's still the same day; a pending bank-transfer refund
    otherwise) rather than just recording that one is owed and leaving
    it to a separate manual action -- see change_booking_item() above,
    which this mirrors exactly.
    """
    orm_sale = await session.get(ORMSale, booking_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if orm_sale.status != "booking":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Cannot cancel a sale with status '{orm_sale.status}'"
        )
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="A cancellation reason is required."
        )

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == booking_id))
    cancel_items = list(items_result.scalars().all())

    paid_result = await session.execute(
        select(func.sum(SalePayment.amount_kobo)).where(SalePayment.sale_id == booking_id)
    )
    deposit_paid_kobo = int(paid_result.scalar_one_or_none() or 0)

    cancel_refund = None
    if deposit_paid_kobo > 0:
        first_payment_result = await session.execute(
            select(SalePayment).where(SalePayment.sale_id == booking_id).order_by(SalePayment.created_at).limit(1)
        )
        first_payment = first_payment_result.scalar_one_or_none()
        same_day = (datetime.now(timezone.utc) - orm_sale.created_at) <= timedelta(hours=24)
        paid_in_cash = first_payment is not None and first_payment.method == "cash"

        if same_day and paid_in_cash:
            if body.refund_method != "cash":
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"This booking has a {Money(deposit_paid_kobo).naira:,.2f} deposit paid in cash today. "
                        "Confirm handing that back in cash to cancel."
                    ),
                )
            cancel_refund = ORMRefund(
                sale_id=orm_sale.id,
                processed_by=current_user.id,
                amount_kobo=deposit_paid_kobo,
                reason=f"Booking cancelled: {reason}",
                action="cash",
                status="completed",
            )
        else:
            if body.refund_method != "transfer" or not (body.refund_account_number or "").strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"This booking has a {Money(deposit_paid_kobo).naira:,.2f} deposit paid that can no "
                        "longer be refunded in cash on the spot (either it wasn't paid in cash originally, or "
                        "it's been more than 24 hours). Provide the customer's bank account details -- account "
                        "number, bank name, and account name -- to cancel."
                    ),
                )
            cancel_refund = ORMRefund(
                sale_id=orm_sale.id,
                processed_by=current_user.id,
                amount_kobo=deposit_paid_kobo,
                reason=f"Booking cancelled: {reason}",
                action="transfer",
                status="pending",
                bank_name=body.refund_bank_name,
                account_number=body.refund_account_number,
                account_name=body.refund_account_name,
            )
        session.add(cancel_refund)

    orm_sale.status = "cancelled"
    orm_sale.notes = f"{orm_sale.notes or ''}\nCancelled: {reason}".strip()
    # PATCH3A-BOOKING-DRIVES-ORDER
    await _sync_customer_order_for_booking(session, orm_sale.id, "cancelled", cancel_refund)

    log_audit_event(
        session,
        user_id=current_user.id,
        action="booking_cancelled",
        entity_type="sale",
        entity_id=orm_sale.id,
        metadata={
            "reason": reason,
            "original_total_naira": Money(orm_sale.total_kobo).naira,
            "deposit_paid_naira": Money(deposit_paid_kobo).naira,
            # Section 10: the refund is now actually triggered above
            # (a completed cash Refund, or a pending transfer Refund
            # visible to the super admin/branch manager in Refund
            # History) -- this audit metadata just mirrors what
            # happened, it isn't the only record of it anymore.
            "refund_amount_naira": Money(deposit_paid_kobo).naira if cancel_refund else None,
            "refund_method": cancel_refund.action if cancel_refund else None,
        },
    )

    await session.commit()
    await session.refresh(orm_sale)

    return _to_sale_out(
        orm_sale,
        cancel_items,
        {current_user.id: current_user.full_name},
        await _resolve_product_names(session, cancel_items),
    )


@router.get("/refunds", response_model=list[RefundListItem])
async def list_refunds(
    branch_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(completed|pending)$"),
    session: AsyncSession = Depends(get_db_session),
    # Refunds get their own section/list (spec: "it shouldn't be
    # inside a view receipt. It should have its own section in the
    # sales and receipt"), gated the same as viewing sales generally.
    current_user: User = Depends(require_permission("process_sale")),
) -> list[RefundListItem]:
    """Unified Refund History (spec section 14): every refund is
    already keyed off `sale_id` regardless of whether that Sale came
    from a normal POS checkout, a credit-sale (CreditEntry.sale_id
    links to one -- see credit.py), or an Open Booking (Sale.status=
    'booking'/'refunded' after the fact) -- so this one endpoint,
    unfiltered by sale source, already *is* the unified history.
    `source` on each row just labels which of the three it was, for
    display/filtering, rather than gating what's included.
    """
    conditions = []
    if current_user.role != "super_admin":
        # Same branch hard-scoping as list_sales / list_bookings -- a
        # cashier or branch manager must never see another branch's
        # refunds just by omitting/changing the branch_id query param.
        conditions.append(ORMSale.branch_id == current_user.branch_id)
    elif branch_id:
        conditions.append(ORMSale.branch_id == branch_id)
    if status_filter:
        conditions.append(ORMRefund.status == status_filter)
    result = await session.execute(
        select(ORMRefund, ORMSale)
        .join(ORMSale, ORMRefund.sale_id == ORMSale.id)
        .where(*conditions)
        .order_by(ORMRefund.created_at.desc())
    )
    rows = result.all()

    user_ids = (
        {r.processed_by for r, _ in rows if r.processed_by}
        | {r.approved_by for r, _ in rows if r.approved_by}
        | {s.served_by for _, s in rows if s.served_by}
    )
    users_by_id: dict[uuid.UUID, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {u.id: u.full_name for u in users_result.scalars().all()}

    sale_ids = {sale.id for _, sale in rows}
    credit_sale_ids: set[uuid.UUID] = set()
    if sale_ids:
        credit_result = await session.execute(
            select(CreditEntry.sale_id).where(CreditEntry.sale_id.in_(sale_ids))
        )
        credit_sale_ids = {row[0] for row in credit_result.all()}

    def _source(sale) -> str:
        if sale.id in credit_sale_ids:
            return "credit"
        if sale.status == "booking" or sale.notes and "booking" in (sale.notes or "").lower():
            return "booking"
        return "sale"

    return [
        RefundListItem(
            id=refund.id,
            sale_id=sale.id,
            receipt_number=sale.local_ref_id,
            original_sale_at=sale.created_at,
            original_cashier_name=users_by_id.get(sale.served_by),
            processed_by=refund.processed_by,
            processed_by_name=users_by_id.get(refund.processed_by) if refund.processed_by else None,
            amount_naira=Money(refund.amount_kobo).naira,
            reason=refund.reason,
            image_url=refund.image_url,
            action=refund.action,
            bank_name=refund.bank_name,
            account_number=refund.account_number,
            account_name=refund.account_name,
            status=refund.status,
            approved_by_name=users_by_id.get(refund.approved_by) if refund.approved_by else None,
            processed_at=refund.processed_at,
            settlement=refund.settlement,
            settlement_amount_naira=(
                Money(refund.settlement_amount_kobo).naira
                if refund.settlement_amount_kobo is not None
                else None
            ),
            settlement_method=refund.settlement_method,
            created_at=refund.created_at,
            source=_source(sale),
        )
        for refund, sale in rows
    ]


@router.get("/refunds/pending-count", response_model=PendingRefundSummary)
async def pending_refund_count(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    # Same tier as viewing low-stock/expiry alerts -- the pending-
    # refund dashboard badge (spec section 10) is Super Admin/Branch
    # Manager territory, since only they can act on it.
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> PendingRefundSummary:
    conditions = [ORMRefund.status == "pending"]
    if current_user.role == "branch_manager" and current_user.branch_id:
        conditions.append(ORMSale.branch_id == current_user.branch_id)
    elif branch_id:
        conditions.append(ORMSale.branch_id == branch_id)
    count = await session.scalar(
        select(func.count())
        .select_from(ORMRefund)
        .join(ORMSale, ORMRefund.sale_id == ORMSale.id)
        .where(*conditions)
    )
    return PendingRefundSummary(count=count or 0)


@router.post("/refunds/{refund_id}/mark-processed", response_model=RefundOut)
async def mark_refund_processed(
    refund_id: uuid.UUID,
    body: RefundMarkProcessed,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> RefundOut:
    """Super Admin / Branch Manager confirming a pending transfer
    refund has actually been sent (spec section 10) -- clears the
    dashboard's pending-refund badge for this one refund.
    """
    refund = await session.get(ORMRefund, refund_id)
    if refund is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Refund not found")
    if refund.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This refund is not pending.")

    refund.status = "completed"
    refund.approved_by = current_user.id
    refund.processed_at = datetime.now(timezone.utc)
    await _sync_customer_order_for_refund_processed(session, refund.sale_id)  # PATCH3A2-REFUND-PROCESSED-UPDATES-ORDER

    log_audit_event(
        session,
        user_id=current_user.id,
        action="refund_marked_processed",
        entity_type="refund",
        entity_id=refund.id,
        metadata={"note": body.note} if body.note else None,
    )
    await session.commit()
    await session.refresh(refund)

    orm_sale = await session.get(ORMSale, refund.sale_id)
    return _refund_to_out(refund, orm_sale.status if orm_sale else "completed")


@router.get("/refunds/{refund_id}", response_model=RefundDetailOut)
async def get_refund_detail(
    refund_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> RefundDetailOut:
    """Full refund record for the printable refund receipt (spec:
    Refund â†’ Details must open a proper receipt, pulled from the
    actual refund/sale record -- not a plain-text summary and not a
    disconnected fake layout).
    """
    refund = await session.get(ORMRefund, refund_id)
    if refund is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Refund not found")

    orm_sale = await session.get(ORMSale, refund.sale_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Original sale not found")

    if current_user.role == "staff" and orm_sale.branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="You can only view refunds for your own branch"
        )

    items_result = await session.execute(
        select(RefundItem).where(RefundItem.refund_id == refund_id).order_by(RefundItem.id)
    )
    refund_items = list(items_result.scalars().all())

    user_ids = {refund.processed_by, refund.approved_by, orm_sale.served_by}
    user_ids.discard(None)
    users_by_id: dict[uuid.UUID, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {u.id: u.full_name for u in users_result.scalars().all()}

    customer_name = None
    customer_phone = None
    if orm_sale.customer_id:
        customer = await session.get(Customer, orm_sale.customer_id)
        if customer:
            customer_name = customer.full_name
            customer_phone = customer.phone

    return RefundDetailOut(
        id=refund.id,
        sale_id=orm_sale.id,
        receipt_number=orm_sale.local_ref_id,
        branch_id=orm_sale.branch_id,
        original_sale_at=orm_sale.created_at,
        original_cashier_name=users_by_id.get(orm_sale.served_by) if orm_sale.served_by else None,
        processed_by=refund.processed_by,
        processed_by_name=users_by_id.get(refund.processed_by) if refund.processed_by else None,
        customer_name=customer_name,
        customer_phone=customer_phone,
        items=[
            RefundItemOut(
                product_name=ri.product_name,
                unit_label=ri.unit_label,
                quantity=ri.quantity,
                unit_price_naira=Money(ri.unit_price_kobo).naira,
                line_total_naira=Money(ri.line_total_kobo).naira,
                restocked=ri.restocked,
                spoiled=ri.spoiled,
            )
            for ri in refund_items
        ],
        amount_naira=Money(refund.amount_kobo).naira,
        reason=refund.reason,
        image_url=refund.image_url,
        action=refund.action,
        bank_name=refund.bank_name,
        account_number=refund.account_number,
        account_name=refund.account_name,
        status=refund.status,
        approved_by_name=users_by_id.get(refund.approved_by) if refund.approved_by else None,
        processed_at=refund.processed_at,
        exchange_product_id=refund.exchange_product_id,
        settlement=refund.settlement,
        settlement_amount_naira=(
            Money(refund.settlement_amount_kobo).naira
            if refund.settlement_amount_kobo is not None
            else None
        ),
        settlement_method=refund.settlement_method,
        original_payment_method=orm_sale.payment_method,
        created_at=refund.created_at,
    )


@router.get("/audit-log", response_model=list[AuditLogOut])
async def list_audit_log(
    entity_type: str | None = None,
    branch_id: uuid.UUID | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> list[AuditLogOut]:
    """Accountability trail for who voided/refunded a sale or applied
    a discount, and when. Same permission gate as the reports
    endpoints -- this is manager-level visibility, not for every
    cashier to browse.

    AuditLog has no branch_id column of its own, so a non-super-admin
    is branch-scoped by joining through entity_id back to Sale (every
    booking/sale-related event -- deposit taken, payment added,
    completed, item changed, cancelled, discount applied -- has
    entity_type='sale' and entity_id=Sale.id). Any non-sale audit
    entry (e.g. a refund mark-processed event) has no branch it can be
    safely attributed to, so it's excluded entirely for a
    non-super-admin rather than shown unscoped -- same "exclude if we
    can't prove it's safe" rule the rest of the branch-scoping fixes
    follow.
    """
    stmt = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.created_at.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if current_user.role != "super_admin":
        stmt = stmt.join(ORMSale, ORMSale.id == AuditLog.entity_id).where(
            AuditLog.entity_type == "sale",
            ORMSale.branch_id == current_user.branch_id,
        )
    elif branch_id:
        stmt = stmt.join(ORMSale, ORMSale.id == AuditLog.entity_id).where(
            AuditLog.entity_type == "sale",
            ORMSale.branch_id == branch_id,
        )
    stmt = stmt.limit(min(limit, 500))

    result = await session.execute(stmt)
    entries = list(result.scalars().all())

    return [
        AuditLogOut(
            id=e.id,
            user_id=e.user_id,
            user_name=e.user.full_name if e.user else None,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            metadata=e.log_metadata,
            created_at=e.created_at,
        )
        for e in entries
    ]


async def _pending_change_to_out(session: AsyncSession, change: PendingChange) -> PendingChangeOut:
    user_ids = {uid for uid in (change.created_by, change.settled_by, change.approved_by) if uid}
    user_names: dict[uuid.UUID, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        user_names = {u.id: u.full_name for u in users_result.scalars().all()}
    branch_name = None
    if change.branch_id:
        branch = await session.get(ORMBranch, change.branch_id)
        branch_name = branch.name if branch else None
    return PendingChangeOut(
        id=change.id,
        sale_id=change.sale_id,
        branch_id=change.branch_id,
        branch_name=branch_name,
        customer_name=change.customer_name,
        customer_phone=change.customer_phone,
        amount_naira=Money(change.amount_kobo).naira,
        notes=change.notes,
        status=change.status,
        reference_code=change.reference_code,
        settlement_method=change.settlement_method,
        bank_name=change.bank_name,
        account_number=change.account_number,
        account_name=change.account_name,
        airtime_phone_number=change.airtime_phone_number,
        airtime_network=change.airtime_network,
        created_by=change.created_by,
        created_by_name=user_names.get(change.created_by) if change.created_by else None,
        created_at=change.created_at,
        settled_by=change.settled_by,
        settled_by_name=user_names.get(change.settled_by) if change.settled_by else None,
        settled_at=change.settled_at,
        approved_by=change.approved_by,
        approved_by_name=user_names.get(change.approved_by) if change.approved_by else None,
        processed_at=change.processed_at,
    )


# Nigerian mobile network prefixes for airtime auto-detect (spec 2.3:
# "auto-detect network... or let cashier confirm it"). Not exhaustive
# of every prefix ever allocated -- covers the common/current ranges
# for the four networks the spec names; anything unrecognized just
# leaves the field for the cashier to fill in manually rather than
# guessing wrong.
_NIGERIAN_NETWORK_PREFIXES: dict[str, str] = {
    **{p: "MTN" for p in ("0803", "0806", "0703", "0706", "0813", "0816", "0810", "0814", "0903", "0906", "0913", "0916")},
    **{p: "Airtel" for p in ("0802", "0808", "0708", "0812", "0701", "0902", "0901", "0904", "0907", "0912")},
    **{p: "Glo" for p in ("0805", "0807", "0705", "0815", "0811", "0905", "0915")},
    **{p: "9mobile" for p in ("0809", "0817", "0818", "0908", "0909")},
}


def _detect_nigerian_network(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    # Normalize +234/234 international prefixes back to the local 0-prefixed form.
    if digits.startswith("234") and len(digits) >= 13:
        digits = "0" + digits[3:]
    return _NIGERIAN_NETWORK_PREFIXES.get(digits[:4])


def _generate_change_reference_code() -> str:
    """Short, easy-to-read-back-over-the-till code (spec 2.2) -- not a
    raw UUID, which is unusable to repeat over a phone call. Collision
    odds are negligible for this app's volume; the DB's unique
    constraint is the real backstop.
    """
    return f"CHG-{uuid.uuid4().hex[:6].upper()}"


# Platform update spec section 2 -- these literal-path routes
# (/pending-changes, /pending-changes/pending-count, /pending-changes/
# {id}/settle, /pending-changes/{id}/mark-processed) MUST be
# registered before GET /{sale_id} below: FastAPI matches routes in
# registration order, and /{sale_id} would otherwise greedily match
# "pending-changes" as a sale_id and shadow these entirely.
@router.post("/pending-changes", response_model=PendingChangeOut, status_code=status.HTTP_201_CREATED)
async def create_pending_change(
    body: PendingChangeCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> PendingChangeOut:
    """Platform update spec section 2: a completed sale that can't be
    given its full change back on the spot. Tied to the Sale/receipt
    itself, not a bare name string -- two different customers can
    share a full name, and the receipt already sidesteps that
    collision problem entirely. A reference code (spec 2.2) is always
    generated regardless of settlement method.

    Section 2.3 routing: 'cash' starts 'outstanding' (no admin
    approval needed -- the money never left the till, the cashier just
    hands it over whenever the customer returns and quotes the code).
    'transfer'/'airtime' start 'pending' -- they route into the same
    Super Admin/Branch Manager approval queue refunds use (section
    2.4) since real money has to move.
    """
    orm_sale = await session.get(ORMSale, body.sale_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sale not found")

    if body.settlement_method == "transfer":
        if not (body.bank_name and body.account_number and body.account_name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="bank_name, account_number, and account_name are required for a transfer settlement.",
            )
    elif body.settlement_method == "airtime":
        if not body.airtime_phone_number:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="airtime_phone_number is required for an airtime settlement.",
            )

    initial_status = "outstanding" if body.settlement_method == "cash" else "pending"
    network = body.airtime_network or _detect_nigerian_network(body.airtime_phone_number)

    change = PendingChange(
        sale_id=body.sale_id,
        branch_id=orm_sale.branch_id,
        customer_name=body.customer_name.strip(),
        customer_phone=(body.customer_phone or "").strip() or None,
        amount_kobo=Money.from_naira(body.amount_naira).kobo,
        notes=body.notes,
        status=initial_status,
        reference_code=_generate_change_reference_code(),
        settlement_method=body.settlement_method,
        bank_name=body.bank_name,
        account_number=body.account_number,
        account_name=body.account_name,
        airtime_phone_number=body.airtime_phone_number,
        airtime_network=network,
        created_by=current_user.id,
    )
    session.add(change)
    await session.commit()
    await session.refresh(change)
    return await _pending_change_to_out(session, change)


@router.get("/pending-changes/pending-count", response_model=PendingRefundSummary)
async def pending_change_count(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    # Same tier as the refund pending-count badge (spec 2.4) -- only
    # someone who can actually act on it needs to see the count.
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> PendingRefundSummary:
    conditions = [PendingChange.status.in_(["pending", "outstanding"])]
    if current_user.role == "branch_manager" and current_user.branch_id:
        conditions.append(PendingChange.branch_id == current_user.branch_id)
    elif branch_id:
        conditions.append(PendingChange.branch_id == branch_id)
    count = await session.scalar(select(func.count()).select_from(PendingChange).where(*conditions))
    return PendingRefundSummary(count=count or 0)


@router.get("/pending-changes", response_model=list[PendingChangeOut])
async def list_pending_changes(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(outstanding|pending|settled)$"),
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> list[PendingChangeOut]:
    effective_branch_id = current_user.branch_id if current_user.branch_id is not None else branch_id
    stmt = select(PendingChange)
    if status_filter:
        stmt = stmt.where(PendingChange.status == status_filter)
    if effective_branch_id is not None:
        stmt = stmt.where(PendingChange.branch_id == effective_branch_id)
    stmt = stmt.order_by(PendingChange.created_at.desc())
    result = await session.execute(stmt)
    changes = list(result.scalars().all())
    return [await _pending_change_to_out(session, c) for c in changes]


@router.post("/pending-changes/{change_id}/mark-processed", response_model=PendingChangeOut)
async def mark_pending_change_processed(
    change_id: uuid.UUID,
    body: PendingChangeMarkProcessed,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin", "branch_manager")),
) -> PendingChangeOut:
    """Super Admin / Branch Manager confirming a pending transfer/
    airtime change settlement has actually been sent (spec 2.4) --
    mirrors POST /sales/refunds/{id}/mark-processed exactly. Only
    valid for a 'pending' (transfer/airtime) record; a plain cash
    'outstanding' record goes through /settle below instead, since no
    admin approval is needed for change that never left the till.
    """
    change = await session.get(PendingChange, change_id)
    if change is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pending change not found")
    if change.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This change settlement is not pending approval.")

    change.status = "settled"
    change.approved_by = current_user.id
    change.processed_at = datetime.now(timezone.utc)
    change.settled_by = current_user.id
    change.settled_at = change.processed_at

    log_audit_event(
        session,
        user_id=current_user.id,
        action="pending_change_marked_processed",
        entity_type="pending_change",
        entity_id=change.id,
        metadata={"note": body.note} if body.note else None,
    )
    await session.commit()
    await session.refresh(change)
    return await _pending_change_to_out(session, change)


@router.post("/pending-changes/{change_id}/settle", response_model=PendingChangeOut)
async def settle_pending_change(
    change_id: uuid.UUID,
    body: PendingChangeSettle,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> PendingChangeOut:
    """The cashier confirms exactly how an *outstanding* (cash-tracked)
    change record was actually handed back when the customer returned
    for it. Amount and method are reconfirmed here rather than assumed
    from how the record was first logged, since the customer may ask
    for a different method today (e.g. was recorded as cash-owed, but
    they'd rather have a transfer now).

    Choosing 'cash' settles immediately, same as before -- money that
    was always sitting in the till. Choosing 'transfer'/'airtime' here
    instead routes the record into the same Pending Approval queue a
    transfer/airtime request gets at creation (spec 2.4): it's real
    money moving now, so a Branch Manager/Super Admin still has to
    confirm it was actually sent before this counts as settled, even
    though it started life as a cash record.

    A 'pending' record must still go through .../mark-processed
    instead, by a Super Admin/Branch Manager -- this endpoint only
    ever starts from 'outstanding'. No un-settle -- if it was marked
    settled by mistake, that's a correction made by creating a fresh
    record, not reopening this one.
    """
    change = await session.get(PendingChange, change_id)
    if change is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pending change not found")
    if change.status == "pending":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A transfer/airtime change settlement must be approved via mark-processed, not settle.",
        )
    if change.status == "settled":
        return await _pending_change_to_out(session, change)

    # PATCH3C-COUPON-SERVER: a live discount code was made from this credit
    from infrastructure.db.models import Coupon as _CouponRow

    live_coupon = await session.scalar(
        select(_CouponRow.id)
        .where(
            _CouponRow.pending_change_id == change.id,
            _CouponRow.status == "active",
            _CouponRow.expires_at > datetime.now(timezone.utc),
        )
        .limit(1)
    )
    if live_coupon is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A discount code was made from this credit and is still valid. Wait until it expires.",
        )

    given_kobo = Money.from_naira(body.amount_naira).kobo
    if abs(given_kobo - change.amount_kobo) > 50:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Amount given back must match the outstanding balance of {Money(change.amount_kobo).naira}.",
        )

    if body.settlement_method == "transfer":
        if not (body.bank_name and body.account_number and body.account_name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="bank_name, account_number, and account_name are required for a transfer settlement.",
            )
    elif body.settlement_method == "airtime":
        if not body.airtime_phone_number:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="airtime_phone_number is required for an airtime settlement.",
            )

    change.settlement_method = body.settlement_method
    change.bank_name = body.bank_name
    change.account_number = body.account_number
    change.account_name = body.account_name
    change.airtime_phone_number = body.airtime_phone_number
    change.airtime_network = body.airtime_network or _detect_nigerian_network(body.airtime_phone_number)

    if body.settlement_method == "cash":
        change.status = "settled"
        change.settled_by = current_user.id
        change.settled_at = datetime.now(timezone.utc)
    else:
        # Real money now has to move -- same approval gate a
        # transfer/airtime request gets at creation (spec 2.4).
        change.status = "pending"

    await session.commit()
    await session.refresh(change)
    return await _pending_change_to_out(session, change)


@router.get("/{sale_id}", response_model=SaleOut)
async def get_sale(
    sale_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_own_sales")),
) -> SaleOut:
    orm_sale = await session.get(ORMSale, sale_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sale not found")

    # Viewing/printing a receipt is branch-based, same as the sales
    # list above -- a cashier can open any receipt from their own
    # branch (spec: "A customer may return later when a different
    # cashier is on duty... that cashier may need to view/print the
    # original receipt"), not only sales they personally rang up.
    # This is a VIEW permission only -- it does not let them edit,
    # delete, or cancel anything belonging to another cashier (spec
    # section 26: don't confuse visibility with edit permission).
    if current_user.role == "staff" and orm_sale.branch_id != current_user.branch_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="You can only view receipts for your own branch"
        )

    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id == sale_id))
    single_sale_items = list(items_result.scalars().all())
    payments_result = await session.execute(
        select(SalePayment).where(SalePayment.sale_id == sale_id).order_by(SalePayment.id)
    )
    sale_payments = list(payments_result.scalars().all())
    staff_ids = {orm_sale.served_by} | {p.recorded_by for p in sale_payments if p.recorded_by}
    staff_ids.discard(None)
    cashier_names = {}
    if staff_ids:
        users_result = await session.execute(select(User).where(User.id.in_(staff_ids)))
        cashier_names = {u.id: u.full_name for u in users_result.scalars().all()}
    customer = await session.get(Customer, orm_sale.customer_id) if orm_sale.customer_id else None
    # September 2026 change spec section 12: the sale's own receipt
    # must show every refund processed against it in place, so the
    # "View" receipt is always the single evolving source of truth.
    refunds_result = await session.execute(
        select(ORMRefund).where(ORMRefund.sale_id == sale_id).order_by(ORMRefund.created_at)
    )
    refunds_by_sale = {sale_id: list(refunds_result.scalars().all())}
    # Platform update spec section 2.5: the receipt/detail view must
    # show this sale's change-owed status same as the list does below.
    # A sale can have more than one row (change owed split across
    # settlement methods), so every row is fetched, not just the most
    # recent.
    change_result = await session.execute(
        select(PendingChange).where(PendingChange.sale_id == sale_id).order_by(PendingChange.created_at)
    )
    change_rows = list(change_result.scalars().all())
    pending_changes_by_sale = {sale_id: change_rows} if change_rows else {}
    return _to_sale_out(
        orm_sale,
        single_sale_items,
        cashier_names,
        await _resolve_product_names(session, single_sale_items),
        payments_by_sale={sale_id: sale_payments},
        customer=customer,
        refunds_by_sale=refunds_by_sale,
        pending_changes_by_sale=pending_changes_by_sale,
    )


@router.get("", response_model=list[SaleOut])
async def list_sales(
    branch_id: uuid.UUID | None = None,
    cashier_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_own_sales")),
) -> list[SaleOut]:
    # Sales & Receipts is branch-based, not cashier-based (spec
    # section 16): a cashier ("staff") sees every sale rung up at
    # their OWN branch, regardless of who served it, so they can help
    # a customer who bought from a colleague. They are still hard-
    # restricted to their own branch server-side -- never trusted from
    # the client -- so they cannot see another branch's sales just by
    # changing the branch_id query param.
    stmt = select(ORMSale)
    # September 2026 change spec section 9: an open booking must not
    # appear in Sales & Receipts at all until its payment is completed
    # -- it belongs only in the Open Bookings tab (see
    # list_open_bookings below) until then.
    stmt = stmt.where(ORMSale.status != "booking")
    if current_user.role != "super_admin":
        stmt = stmt.where(ORMSale.branch_id == current_user.branch_id)
        if cashier_id:
            # A cashier/branch manager can still narrow their own
            # branch's list down to one colleague via the filter
            # dropdown -- this only ever narrows within the branch
            # restriction above, it can never widen past it.
            stmt = stmt.where(ORMSale.served_by == cashier_id)
    else:
        # Only a super admin can freely pick any branch/cashier
        # combination.
        if cashier_id:
            stmt = stmt.where(ORMSale.served_by == cashier_id)
        if branch_id:
            stmt = stmt.where(ORMSale.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(ORMSale.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        # Inclusive of the whole day -- date_to is a plain "YYYY-MM-DD",
        # so push to the end of that day rather than midnight at its start.
        stmt = stmt.where(ORMSale.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
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

    all_items = [item for items in items_by_sale.values() for item in items]
    product_names = await _resolve_product_names(session, all_items)

    payments_result = await session.execute(
        select(SalePayment).where(SalePayment.sale_id.in_(sale_ids)).order_by(SalePayment.id)
    )
    all_payments = list(payments_result.scalars().all())
    payments_by_sale: dict[uuid.UUID, list[SalePayment]] = {}
    for p in all_payments:
        payments_by_sale.setdefault(p.sale_id, []).append(p)

    server_ids = {s.served_by for s in orm_sales if s.served_by is not None}
    cashier_names: dict[uuid.UUID, str] = {}
    if server_ids:
        users_result = await session.execute(select(User).where(User.id.in_(server_ids)))
        cashier_names = {u.id: u.full_name for u in users_result.scalars().all()}

    # Platform update spec section 2.5: "A completed sale that has a
    # pending or resolved change component must show that status in
    # the Sales/Transaction History" -- one bulk fetch for the whole
    # page, same pattern as items/payments above.
    changes_result = await session.execute(
        select(PendingChange).where(PendingChange.sale_id.in_(sale_ids)).order_by(PendingChange.created_at)
    )
    # A sale can carry more than one row now (change owed split across
    # settlement methods) -- collect all of them per sale rather than
    # keeping only the latest.
    pending_changes_by_sale: dict[uuid.UUID, list[PendingChange]] = {}
    for c in changes_result.scalars().all():
        pending_changes_by_sale.setdefault(c.sale_id, []).append(c)

    return [
        _to_sale_out(
            s, items_by_sale.get(s.id, []), cashier_names, product_names,
            payments_by_sale=payments_by_sale, pending_changes_by_sale=pending_changes_by_sale,
        )
        for s in orm_sales
    ]


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
    damaged/opened items that can't be resold. `spoiled=True` implies
    `restock=False` and additionally logs the returned item as its own
    received-then-written-off StockBatch at the warehouse branch, so
    it shows up in that product's warehouse stock history as "pushed
    to the warehouse" rather than just disappearing.

    body.action='exchange' additionally swaps the customer into a
    different product (exchange_product_id/exchange_quantity): stock
    for the new item is FIFO-deducted from this sale's branch, and a
    new completed Sale row is created for it (so it shows up in this
    cashier's dashboard sales figures same as any other sale). If the
    exchange item costs less than the refunded amount, `settlement`
    comes back 'store_paid_customer' (hand back the difference in
    cash); if it costs more, 'customer_topped_up' (collect the
    difference via exchange_payment_method); equal cost is 'even'.

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
    if orm_sale.channel == "transfer":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "A branch transfer cannot be refunded as a customer sale. "
                "Use Request back or Return items on the Warehouse page."
            ),
        )
    # 'booking' included (spec section 13: Open Booking gets the same
    # refund flow as a normal sale) alongside the pre-existing
    # completed/partially_refunded sale statuses.
    if orm_sale.status not in ("completed", "partially_refunded", "booking"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot refund a sale with status '{orm_sale.status}'",
        )

    # September 2026 change spec section 10: "All refunds, system-wide,
    # must go through bank transfer -- remove cash as a refund method
    # entirely," except for the same-day booking-cancellation/change
    # case, which has its own dedicated cash path in cancel_booking()/
    # change_booking_item() above and never comes through this generic
    # refund endpoint. A plain refund or an exchange's cash payout
    # therefore can no longer be 'cash' here, regardless of how the
    # original sale was paid or how recently.
    # PATCH11A: a plain cash refund is allowed again (owner decision).
    # It is saved as 'completed' below and needs no bank details.
    if body.action == "exchange" and body.settlement_method == "cash":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cash payouts are no longer available for a swap that owes the customer money -- "
                "use a bank transfer instead."
            ),
        )
    if body.action == "transfer" and not (body.bank_name and body.account_number and body.account_name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Bank name, account number, and account name are required for a transfer refund.",
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

        if line.spoiled:
            # Spoiled goods never rejoin sellable stock -- push a
            # traceable receive-then-writeoff pair onto this branch's
            # own warehouse record instead of restocking on the floor.
            # Every branch is its own store with its own warehouse
            # area -- there's no separate central "warehouse branch"
            # to route to.
            spoiled_batch = ORMStockBatch(
                product_id=sale_item.product_id,
                branch_id=orm_sale.branch_id,
                batch_number=f"refund-{sale_id}-spoiled",
                quantity_received=line.quantity,
                quantity_remaining=line.quantity,
                unit_cost_kobo=sale_item.unit_price_kobo,
            )
            session.add(spoiled_batch)
            await session.flush()
            session.add(ORMStockAdjustment(
                stock_batch_id=spoiled_batch.id,
                branch_id=orm_sale.branch_id,
                value_kobo=line.quantity * sale_item.unit_price_kobo,
                status="pending_review",
                adjusted_by=current_user.id,
                quantity_delta=-line.quantity,
                reason="damaged",
                notes=f"Spoiled item returned in refund on sale {sale_id}"
                + (f": {body.reason}" if body.reason else ""),
            ))
            spoiled_batch.quantity_remaining = 0
        elif line.restock and sale_item.stock_batch_id is not None:
            # Locked read-modify-write: a sale drawing from this same
            # batch at the same moment can't overwrite this add-back
            # (or the other way round).
            batch = (
                await session.execute(
                    select(ORMStockBatch)
                    .where(ORMStockBatch.id == sale_item.stock_batch_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if batch is not None:
                batch.quantity_remaining += line.quantity

    # ------------------------------------------------ exchange handling
    settlement: str | None = None
    settlement_amount_kobo: int | None = None
    if body.action == "exchange":
        if body.exchange_product_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="exchange_product_id is required when swapping items."
            )
        exchange_variant = await session.get(Product, body.exchange_product_id)
        if exchange_variant is None or exchange_variant.is_deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Swap item not found")

        exchange_total_kobo = exchange_variant.selling_price_kobo * body.exchange_quantity
        if exchange_total_kobo < amount_kobo:
            settlement = "store_paid_customer"
            settlement_amount_kobo = amount_kobo - exchange_total_kobo
            if body.settlement_method == "transfer" and not (
                body.bank_name and body.account_number and body.account_name
            ):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="Bank name, account number, and account name are required to pay the difference back by transfer.",
                )
        elif exchange_total_kobo > amount_kobo:
            settlement = "customer_topped_up"
            settlement_amount_kobo = exchange_total_kobo - amount_kobo
        else:
            settlement = "even"
            settlement_amount_kobo = 0

        # FIFO-deduct the exchange item from the same branch the
        # original sale happened at.
        batches_result = await session.execute(
            select(ORMStockBatch)
            .where(
                ORMStockBatch.product_id == exchange_variant.id,
                ORMStockBatch.branch_id == orm_sale.branch_id,
                ORMStockBatch.quantity_remaining > 0,
            )
            .order_by(ORMStockBatch.received_at, ORMStockBatch.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        exchange_batches = list(batches_result.scalars().all())
        available = sum(b.quantity_remaining for b in exchange_batches)
        if available < body.exchange_quantity:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Not enough stock of the swap item -- only {available} available.",
            )
        remaining_to_deduct = body.exchange_quantity
        chosen_batch_id = None
        for batch in exchange_batches:
            if remaining_to_deduct <= 0:
                break
            take = min(batch.quantity_remaining, remaining_to_deduct)
            batch.quantity_remaining -= take
            if chosen_batch_id is None:
                chosen_batch_id = batch.id
            remaining_to_deduct -= take

        exchange_sale = ORMSale(
            branch_id=orm_sale.branch_id,
            customer_id=orm_sale.customer_id,
            served_by=current_user.id,
            channel="pos",
            status="completed",
            subtotal_kobo=exchange_total_kobo,
            discount_kobo=0,
            total_kobo=exchange_total_kobo,
            payment_method=body.exchange_payment_method if settlement == "customer_topped_up" else "cash",
            notes=f"Swap Item(s) against refund on sale {sale_id}",
        )
        session.add(exchange_sale)
        await session.flush()
        session.add(ORMSaleItem(
            sale_id=exchange_sale.id,
            product_id=exchange_variant.id,
            stock_batch_id=chosen_batch_id,
            quantity=body.exchange_quantity,
            unit_price_kobo=exchange_variant.selling_price_kobo,
            line_total_kobo=exchange_total_kobo,
        ))
        if settlement == "customer_topped_up":
            session.add(SalePayment(
                sale_id=exchange_sale.id,
                method=body.exchange_payment_method,
                amount_kobo=settlement_amount_kobo,
                recorded_by=current_user.id,
            ))

    # Cash is handed over on the spot -- 'completed' immediately.
    # A transfer refund, or an exchange settlement paid back by
    # transfer, waits on management actually sending the money (spec
    # sections 8 & 10), so it starts 'pending'.
    is_transfer_payout = body.action == "transfer" or (
        settlement == "store_paid_customer" and body.settlement_method == "transfer"
    )
    refund = ORMRefund(
        sale_id=sale_id,
        processed_by=current_user.id,
        amount_kobo=amount_kobo,
        reason=body.reason,
        image_url=body.image_url,
        action=body.action,
        bank_name=body.bank_name if (body.action == "transfer" or is_transfer_payout) else None,
        account_number=body.account_number if (body.action == "transfer" or is_transfer_payout) else None,
        account_name=body.account_name if (body.action == "transfer" or is_transfer_payout) else None,
        status="pending" if is_transfer_payout else "completed",
        exchange_product_id=body.exchange_product_id,
        settlement=settlement,
        settlement_amount_kobo=settlement_amount_kobo,
        settlement_method=body.settlement_method if settlement == "store_paid_customer" else None,
    )
    session.add(refund)
    await session.flush()  # assigns refund.id, needed by the refund_items below

    # Itemized breakdown for the refund receipt (spec: Refund â†’ Details
    # must show refunded item(s)/quantity/unit/rate/total, not just an
    # aggregate amount). Snapshots product name, unit, and price at
    # refund time -- same rationale as SaleItem's own snapshot fields.
    product_ids = {sale_items_by_id[line.sale_item_id].product_id for line in body.lines}
    product_info: dict[uuid.UUID, tuple[str, str | None]] = {}
    if product_ids:
        product_rows = await session.execute(
            select(Product.id, Product.name, Product.unit_of_measure)
            .where(Product.id.in_(product_ids))
        )
        product_info = {
            product_id: (name, unit_of_measure)
            for product_id, name, unit_of_measure in product_rows.all()
        }
    for line in body.lines:
        sale_item = sale_items_by_id[line.sale_item_id]
        product_name, unit_label = product_info.get(sale_item.product_id, (None, None))
        session.add(RefundItem(
            refund_id=refund.id,
            sale_item_id=sale_item.id,
            product_name=product_name,
            unit_label=unit_label,
            quantity=line.quantity,
            unit_price_kobo=sale_item.unit_price_kobo,
            line_total_kobo=line.quantity * sale_item.unit_price_kobo,
            restocked=bool(line.restock and not line.spoiled),
            spoiled=line.spoiled,
        ))

    log_audit_event(
        session,
        user_id=current_user.id,
        action="refund_processed",
        entity_type="sale",
        entity_id=sale_id,
        metadata={
            "amount_naira": Money(amount_kobo).naira,
            "reason": body.reason,
            "refund_action": body.action,
            "settlement": settlement,
            "lines": [
                {
                    "sale_item_id": str(line.sale_item_id),
                    "quantity": line.quantity,
                    "restock": line.restock,
                    "spoiled": line.spoiled,
                }
                for line in body.lines
            ],
        },
    )

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

    return _refund_to_out(refund, orm_sale.status)
# PATCH3E-SWAP-LABELS
