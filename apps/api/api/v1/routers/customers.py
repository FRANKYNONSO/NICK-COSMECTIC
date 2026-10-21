from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_customer, get_current_user, get_db_session
from api.v1.schemas.customers import CustomerCreate, CustomerOut
from api.v1.schemas.sales import SaleItemOut, SaleOut
from infrastructure.db.models import Customer, CustomerAddress, Sale as ORMSale, SaleItem as ORMSaleItem, User

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> CustomerOut:
    """Creates a guest customer + delivery address, or reuses an existing
    guest customer record if one already exists with the same email or
    phone (both are unique columns) -- repeat guest checkouts from the
    same shopper attach a new address to their existing record instead
    of crashing on a duplicate-key error.
    """
    customer = None
    if body.email:
        result = await session.execute(select(Customer).where(Customer.email == body.email))
        customer = result.scalar_one_or_none()
    if customer is None and body.phone:
        result = await session.execute(select(Customer).where(Customer.phone == body.phone))
        customer = result.scalar_one_or_none()

    if customer is None:
        customer = Customer(
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
        )
        session.add(customer)
        await session.flush()
    else:
        customer.full_name = body.full_name

    address = CustomerAddress(
        customer_id=customer.id,
        label=body.address.label,
        address_line=body.address.address_line,
        city=body.address.city,
        state=body.address.state,
        country=body.address.country,
        is_default=True,
    )
    session.add(address)
    await session.commit()
    await session.refresh(customer)
    await session.refresh(address)

    addresses_result = await session.execute(
        select(CustomerAddress).where(CustomerAddress.customer_id == customer.id)
    )
    addresses = list(addresses_result.scalars().all())

    return CustomerOut(
        id=customer.id,
        full_name=customer.full_name,
        email=customer.email,
        phone=customer.phone,
        created_at=customer.created_at,
        addresses=[
            {
                "id": a.id,
                "address_line": a.address_line,
                "city": a.city,
                "state": a.state,
                "country": a.country,
                "label": a.label,
                "is_default": a.is_default,
            }
            for a in addresses
        ],
    )

@router.get("/me/orders", response_model=list[SaleOut])
async def my_orders(
    current_customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_db_session),
) -> list[SaleOut]:
    """Order history for the logged-in customer. Separate from
    GET /sales (staff-only, permission-gated) -- this is scoped to
    exactly the caller's own customer_id, taken from their verified
    token rather than a query param, so there's no way to pass someone
    else's customer_id and see their orders.
    """
    from nickcosmetics_core.domain.money import Money

    result = await session.execute(
        select(ORMSale)
        .where(ORMSale.customer_id == current_customer.id)
        .order_by(ORMSale.created_at.desc())
    )
    sales = list(result.scalars().all())
    if not sales:
        return []

    sale_ids = [s.id for s in sales]
    items_result = await session.execute(select(ORMSaleItem).where(ORMSaleItem.sale_id.in_(sale_ids)))
    items_by_sale: dict = {}
    for item in items_result.scalars().all():
        items_by_sale.setdefault(item.sale_id, []).append(item)

    return [
        SaleOut(
            id=s.id,
            branch_id=s.branch_id,
            customer_id=s.customer_id,
            served_by=s.served_by,
            cashier_name=None,
            channel=s.channel,
            status=s.status,
            subtotal_naira=Money(s.subtotal_kobo).naira,
            discount_naira=Money(s.discount_kobo).naira,
            total_naira=Money(s.total_kobo).naira,
            payment_method=s.payment_method,
            local_ref_id=s.local_ref_id,
            created_at=s.created_at,
            items=[
                SaleItemOut(
                    id=item.id,
                    product_id=item.product_id,
                    stock_batch_id=item.stock_batch_id,
                    quantity=item.quantity,
                    unit_price_naira=Money(item.unit_price_kobo).naira,
                    line_total_naira=Money(item.line_total_kobo).naira,
                )
                for item in items_by_sale.get(s.id, [])
            ],
        )
        for s in sales
    ]
