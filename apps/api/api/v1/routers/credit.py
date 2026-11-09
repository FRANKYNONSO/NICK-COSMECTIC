import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.credit import CreditCreate, CreditOut, CreditPaymentCreate, CreditPaymentOut
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import Branch as ORMBranch
from infrastructure.db.models import Category as ORMCategory
from infrastructure.db.models import CreditEntry, CreditPayment
from infrastructure.db.models import Customer as ORMCustomer
from infrastructure.db.models import Product as ORMProduct
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import SaleItem as ORMSaleItem
from infrastructure.db.models import SalePayment as ORMSalePayment
from infrastructure.db.models import StockBatch as ORMStockBatch
from infrastructure.db.models import User
from infrastructure.db.repositories import CustomerRepository
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/credit", tags=["credit"])


async def _deduct_stock_fifo(
    session: AsyncSession, product_id: uuid.UUID, branch_id: uuid.UUID, quantity: int
) -> None:
    """Deduct `quantity` units of a product's stock at one branch,
    oldest batch first -- same FIFO ordering the ordinary POS checkout
    uses (see infrastructure/db/checkout_adapter.py), but applied
    directly here instead of going through SalesService, since a
    credit is deliberately NOT a Sale row.

    Raises HTTPException(400) and touches nothing if the branch
    doesn't have enough stock -- the caller must not commit after
    catching this, so a credit is never created without successfully
    taking the item out of inventory (spec section 28: "the system
    must not create the credit successfully while failing to deduct
    inventory").
    """
    result = await session.execute(
        select(ORMStockBatch)
        .where(
            ORMStockBatch.product_id == product_id,
            ORMStockBatch.branch_id == branch_id,
            ORMStockBatch.quantity_remaining > 0,
        )
        .order_by(ORMStockBatch.received_at)
        .with_for_update()
    )
    batches = list(result.scalars().all())
    available = sum(b.quantity_remaining for b in batches)
    if available < quantity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock. Only {available} unit(s) are available.",
        )
    remaining_to_take = quantity
    for batch in batches:
        if remaining_to_take <= 0:
            break
        take = min(batch.quantity_remaining, remaining_to_take)
        batch.quantity_remaining -= take
        remaining_to_take -= take


def _serialize(entry: CreditEntry) -> CreditOut:
    paid_kobo = sum(p.amount_kobo for p in entry.payments)
    balance_kobo = entry.amount_kobo - paid_kobo
    return CreditOut(
        id=entry.id,
        branch_id=entry.branch_id,
        branch_name=entry.branch.name,
        customer_full_name=entry.customer_full_name,
        customer_phone=entry.customer_phone,
        customer_address=entry.customer_address,
        description=entry.description,
        category_id=entry.category_id,
        category_name=entry.category.name if entry.category else None,
        product_id=entry.product_id,
        product_name=entry.product.name if entry.product else None,
        name=entry.product.name if entry.product else None,
        quantity=entry.quantity,
        unit_price_naira=Money(entry.unit_price_kobo).naira if entry.unit_price_kobo is not None else None,
        amount_naira=Money(entry.amount_kobo).naira,
        amount_paid_naira=Money(paid_kobo).naira,
        balance_naira=Money(balance_kobo).naira,
        status=entry.status,
        sale_id=entry.sale_id,
        opened_by_name=entry.opener.full_name,
        opened_at=entry.opened_at,
        payments=[
            CreditPaymentOut(
                id=p.id,
                amount_naira=Money(p.amount_kobo).naira,
                method=p.method,
                received_by_name=p.receiver.full_name,
                paid_at=p.paid_at,
            )
            for p in entry.payments
        ],
    )


async def _load(session: AsyncSession, credit_id: uuid.UUID) -> CreditEntry:
    result = await session.execute(
        select(CreditEntry)
        .options(
            selectinload(CreditEntry.branch),
            selectinload(CreditEntry.opener),
            selectinload(CreditEntry.category),
            selectinload(CreditEntry.product),
            selectinload(CreditEntry.payments).selectinload(CreditPayment.receiver),
        )
        .where(CreditEntry.id == credit_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Credit entry not found")
    return entry


@router.get("", response_model=list[CreditOut])
async def list_credit(
    branch_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    # Deliberately no permission gate beyond being a logged-in worker --
    # spec: "visible to every person working in the store", and to
    # whatever role the super admin designates as auditor. Every role
    # already passes get_current_user, so an "auditor" role created via
    # the Roles page automatically sees this list without any special
    # casing here.
    current_user: User = Depends(get_current_user),
) -> list[CreditOut]:
    conditions = []
    # Platform update spec section 4 (confirmed bug): a staff member
    # must never see another branch's credit list just by omitting or
    # changing the branch_id query param -- same hard server-side
    # scoping as list_sales/list_bookings. Only a super_admin/
    # branch_manager may freely pick any branch via the query param
    # (branch_manager's own branch_id still wins if set, same as
    # POST /credit above).
    if current_user.role == "cashier":
        conditions.append(CreditEntry.branch_id == current_user.branch_id)
    elif current_user.branch_id:
        conditions.append(CreditEntry.branch_id == current_user.branch_id)
    elif branch_id:
        conditions.append(CreditEntry.branch_id == branch_id)
    if status_filter:
        conditions.append(CreditEntry.status == status_filter)
    result = await session.execute(
        select(CreditEntry)
        .options(
            selectinload(CreditEntry.branch),
            selectinload(CreditEntry.opener),
            selectinload(CreditEntry.category),
            selectinload(CreditEntry.product),
            selectinload(CreditEntry.payments).selectinload(CreditPayment.receiver),
        )
        .where(*conditions)
        .order_by(CreditEntry.opened_at.desc())
    )
    return [_serialize(e) for e in result.scalars().all()]


@router.get("/{credit_id}", response_model=CreditOut)
async def get_credit(
    credit_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> CreditOut:
    entry = await _load(session, credit_id)
    return _serialize(entry)


@router.post("", response_model=CreditOut, status_code=status.HTTP_201_CREATED)
async def create_credit(
    body: CreditCreate,
    session: AsyncSession = Depends(get_db_session),
    # Gated the same as completing a sale (process_sale) -- opening
    # credit is a checkout-time decision, not an admin-only one, since
    # any cashier on the floor needs to be able to extend it.
    current_user: User = Depends(require_permission("process_sale")),
) -> CreditOut:
    # Branch is never trusted from the client for anyone tied to a
    # single branch (staff/branch_manager) -- it always comes from
    # their own account, matching spec section 14 ("the backend must
    # enforce this as well"). A super_admin has no fixed branch_id
    # (they roam every branch via the dashboard's branch selector), so
    # for them alone the client-supplied branch_id is honoured.
    branch_id = current_user.branch_id if current_user.branch_id else body.branch_id
    branch = await session.get(ORMBranch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")

    # What they're buying must actually be real, current inventory --
    # spec: "what are they buying should be in direct reflection of
    # what is on the inventory" -- not just any UUID the client sends.
    if body.category_id is not None:
        category = await session.get(ORMCategory, body.category_id)
        if category is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")
    product = await session.get(ORMProduct, body.product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    if body.category_id is not None and product.category_id != body.category_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Product does not belong to the selected category")

    # Server-side price/total calculation -- spec: "the user should not
    # manually calculate the balance... backend must validate the
    # calculation." The product's current selling price times quantity
    # is the source of truth; a client-supplied amount_naira that
    # doesn't match (a knowingly negotiated price) is accepted but
    # flagged in the audit trail rather than silently trusted.
    unit_price_kobo = product.selling_price_kobo
    computed_total_kobo = unit_price_kobo * body.quantity
    client_total_kobo = Money.from_naira(body.amount_naira).kobo
    price_was_overridden = client_total_kobo != computed_total_kobo
    amount_kobo = client_total_kobo if price_was_overridden else computed_total_kobo

    discount_kobo = Money.from_naira(body.discount_naira).kobo
    if discount_kobo > amount_kobo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Discount cannot exceed this line's amount."
        )
    net_amount_kobo = amount_kobo - discount_kobo

    deposit_kobo = Money.from_naira(body.amount_paid_naira).kobo
    if deposit_kobo > net_amount_kobo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Amount paid cannot exceed the total amount owed after discount."
        )

    # Inventory leaves the building the moment the customer takes it,
    # regardless of how much (if anything) they've paid -- spec
    # section 8/9: "Open Credit must deduct inventory immediately."
    # This must succeed before the credit is created; if it fails
    # (insufficient stock), nothing below runs and nothing is
    # committed, so the credit is never created without the deduction
    # actually happening (spec section 28, database transaction
    # safety).
    await _deduct_stock_fifo(session, body.product_id, branch_id, body.quantity)

    # A credit purchase is still a real, completed transaction from an
    # inventory/accounting standpoint -- the item has already left the
    # building (spec section 8/9 above). Creating an actual Sale +
    # SaleItem for it (spec section 12: "same level of detail as a
    # normal sale receipt") is what lets the Credit List's "View" show
    # a proper receipt and lets a credit-sale go through the exact
    # same refund flow as any other sale (sections 7-9), rather than
    # needing a separate parallel implementation of either.
    customer_repo = CustomerRepository(session)
    customer = await customer_repo.get_by_phone(body.customer_phone.strip())
    if customer is None:
        customer = ORMCustomer(full_name=body.customer_full_name.strip(), phone=body.customer_phone.strip())
        session.add(customer)
        await session.flush()

    sale = ORMSale(
        branch_id=branch_id,
        customer_id=customer.id,
        served_by=current_user.id,
        channel="pos",
        status="completed",
        subtotal_kobo=amount_kobo,
        discount_kobo=discount_kobo,
        total_kobo=net_amount_kobo,
        # Reflects how the deposit (if any) was actually paid -- 'cash'
        # is a neutral default for a credit opened with no deposit at
        # all, since Sale.payment_method has no dedicated 'credit'
        # option of its own; the credit ledger itself (CreditEntry/
        # CreditPayment) is what actually tracks what's outstanding.
        payment_method=body.payment_method if deposit_kobo > 0 else "cash",
        notes="Credit sale",
    )
    session.add(sale)
    await session.flush()
    session.add(ORMSaleItem(
        sale_id=sale.id,
        product_id=body.product_id,
        quantity=body.quantity,
        unit_price_kobo=unit_price_kobo,
        line_total_kobo=amount_kobo,
    ))
    if deposit_kobo > 0:
        session.add(ORMSalePayment(
            sale_id=sale.id,
            method=body.payment_method,
            amount_kobo=deposit_kobo,
            recorded_by=current_user.id,
        ))

    entry = CreditEntry(
        branch_id=branch_id,
        customer_full_name=body.customer_full_name.strip(),
        customer_phone=body.customer_phone.strip(),
        customer_address=(body.customer_address or "").strip() or None,
        category_id=body.category_id,
        product_id=body.product_id,
        quantity=body.quantity,
        description=body.description,
        unit_price_kobo=unit_price_kobo,
        amount_kobo=net_amount_kobo,
        stock_deducted=True,
        sale_id=sale.id,
        opened_by=current_user.id,
        # opened_at is set here server-side (now), never trusted from
        # the client, so it always reflects the actual moment the
        # credit was opened.
        opened_at=datetime.now(timezone.utc),
    )
    session.add(entry)
    await session.flush()

    if deposit_kobo > 0:
        session.add(
            CreditPayment(
                credit_entry_id=entry.id,
                amount_kobo=deposit_kobo,
                method=body.payment_method,
                received_by=current_user.id,
                paid_at=entry.opened_at,
            )
        )
        entry.status = "paid" if deposit_kobo >= net_amount_kobo else "partially_paid"

    log_audit_event(
        session,
        user_id=current_user.id,
        action="credit.open",
        entity_type="credit_entry",
        entity_id=entry.id,
        metadata={
            "amount_naira": Money(amount_kobo).naira,
            "discount_naira": body.discount_naira,
            "net_amount_naira": Money(net_amount_kobo).naira,
            "unit_price_naira": Money(unit_price_kobo).naira,
            "amount_paid_naira": body.amount_paid_naira,
            "customer": body.customer_full_name,
            "product": product.name,
            "quantity": body.quantity,
            "branch": branch.name,
            "price_overridden": price_was_overridden,
            "inventory_deducted": True,
        },
    )
    await session.commit()
    return await get_credit(entry.id, session=session, current_user=current_user)


@router.post("/{credit_id}/payments", response_model=CreditOut)
async def add_credit_payment(
    credit_id: uuid.UUID,
    body: CreditPaymentCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("process_sale")),
) -> CreditOut:
    entry = await _load(session, credit_id)
    if entry.status == "paid":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This credit is already fully paid")

    paid_so_far = sum(p.amount_kobo for p in entry.payments)
    amount_kobo = Money.from_naira(body.amount_naira).kobo
    if paid_so_far + amount_kobo > entry.amount_kobo:
        remaining = Money(entry.amount_kobo - paid_so_far).naira
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Payment exceeds the outstanding balance (₦{remaining:,.2f} left)",
        )

    payment = CreditPayment(
        credit_entry_id=entry.id,
        amount_kobo=amount_kobo,
        method=body.method,
        received_by=current_user.id,
        paid_at=datetime.now(timezone.utc),
    )
    session.add(payment)

    # Mirror the payment onto the linked Sale so its receipt reflects
    # it. Previously only CreditPayment/CreditEntry were updated here,
    # so the Sale (and therefore the receipt built from it in
    # sales.py::_to_sale_out) never learned that new money came in --
    # it stayed frozen at whatever SalePayment rows existed when the
    # credit was first opened.
    session.add(ORMSalePayment(
        sale_id=entry.sale_id,
        method=body.method,
        amount_kobo=amount_kobo,
        recorded_by=current_user.id,
    ))

    new_total_paid = paid_so_far + amount_kobo
    entry.status = "paid" if new_total_paid >= entry.amount_kobo else "partially_paid"

    log_audit_event(
        session,
        user_id=current_user.id,
        action="credit.payment",
        entity_type="credit_entry",
        entity_id=entry.id,
        metadata={"amount_naira": body.amount_naira, "method": body.method},
    )
    await session.commit()
    return await get_credit(entry.id, session=session, current_user=current_user)