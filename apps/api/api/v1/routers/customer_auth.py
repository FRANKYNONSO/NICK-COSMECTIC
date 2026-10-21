from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_customer, get_db_session
from api.v1.schemas.auth import TokenResponse
from api.v1.schemas.customer_auth import CustomerLoginRequest, CustomerRegisterRequest
from api.v1.schemas.customers import CustomerAddressOut, CustomerOut
from infrastructure.db.models import Customer, CustomerAddress
from infrastructure.db.repositories import CustomerRepository
from infrastructure.security.jwt import create_access_token, create_refresh_token
from infrastructure.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/customer-auth", tags=["customer-auth"])


def _tokens_for(customer: Customer) -> TokenResponse:
    # branch_id is a staff-only concept -- customer tokens always carry
    # None there, same shape as staff tokens (create_access_token takes
    # the same three args either way) so decode_token/get_current_user
    # don't need a special case for the field being absent.
    return TokenResponse(
        access_token=create_access_token(str(customer.id), "customer", None),
        refresh_token=create_refresh_token(str(customer.id), "customer", None),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: CustomerRegisterRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    repo = CustomerRepository(session)
    existing = await repo.get_by_email(body.email)

    if existing is not None:
        if existing.password_hash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="An account with this email already exists. Please log in instead.",
            )
        # A guest checkout already created this customer record (see
        # POST /customers, used by the storefront checkout flow) --
        # registering now "claims" it by adding a password, rather than
        # creating a second, disconnected customer row that wouldn't
        # see their past orders.
        existing.full_name = body.full_name
        existing.password_hash = hash_password(body.password)
        if body.phone and not existing.phone:
            existing.phone = body.phone
        customer = await repo.save(existing)
    else:
        customer = Customer(
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
            password_hash=hash_password(body.password),
        )
        customer = await repo.create(customer)

    return _tokens_for(customer)


@router.post("/login", response_model=TokenResponse)
async def login(body: CustomerLoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    repo = CustomerRepository(session)
    customer = await repo.get_by_email(body.email)

    # Same "identical error either way" reasoning as staff auth.py --
    # don't let a login attempt reveal whether an email is registered.
    invalid_credentials = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if customer is None or not customer.password_hash or not verify_password(body.password, customer.password_hash):
        raise invalid_credentials

    return _tokens_for(customer)


@router.get("/me", response_model=CustomerOut)
async def me(
    current_customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_db_session),
) -> CustomerOut:
    addresses_result = await session.execute(
        select(CustomerAddress).where(CustomerAddress.customer_id == current_customer.id)
    )
    addresses = list(addresses_result.scalars().all())
    return CustomerOut(
        id=current_customer.id,
        full_name=current_customer.full_name,
        email=current_customer.email,
        phone=current_customer.phone,
        created_at=current_customer.created_at,
        addresses=[CustomerAddressOut.model_validate(a, from_attributes=True) for a in addresses],
    )
