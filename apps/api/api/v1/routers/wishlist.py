import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_customer, get_db_session
from api.v1.schemas.wishlist import WishlistAddRequest, WishlistItemOut
from infrastructure.db.models import Customer, Product, ProductImage, WishlistItem

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


async def _serialize(session: AsyncSession, item: WishlistItem) -> WishlistItemOut:
    from nickcosmetics_core.domain.money import Money

    product = (
        await session.execute(select(Product).where(Product.id == item.product_id))
    ).scalar_one_or_none()

    image_url = None
    price_naira = 0.0
    in_stock = False
    if product is not None:
        img = (
            await session.execute(
                select(ProductImage)
                .where(ProductImage.product_id == product.id)
                .order_by(ProductImage.sort_order)
                .limit(1)
            )
        ).scalar_one_or_none()
        image_url = img.url if img else None

        price_naira = Money(product.selling_price_kobo).naira
        in_stock = product.status == "active"

    return WishlistItemOut(
        id=item.id,
        product_id=item.product_id,
        product_name=product.name if product else "Product no longer available",
        product_image_url=image_url,
        price_naira=price_naira,
        in_stock=in_stock,
        created_at=item.created_at,
    )


@router.get("", response_model=list[WishlistItemOut])
async def list_wishlist(
    current_customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_db_session),
) -> list[WishlistItemOut]:
    result = await session.execute(
        select(WishlistItem)
        .where(WishlistItem.customer_id == current_customer.id)
        .order_by(WishlistItem.created_at.desc())
    )
    items = list(result.scalars().all())
    return [await _serialize(session, item) for item in items]


@router.post("", response_model=WishlistItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_wishlist(
    body: WishlistAddRequest,
    current_customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_db_session),
) -> WishlistItemOut:
    existing = (
        await session.execute(
            select(WishlistItem).where(
                WishlistItem.customer_id == current_customer.id,
                WishlistItem.product_id == body.product_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await _serialize(session, existing)

    product = (await session.execute(select(Product).where(Product.id == body.product_id))).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    item = WishlistItem(customer_id=current_customer.id, product_id=body.product_id)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return await _serialize(session, item)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_wishlist(
    product_id: uuid.UUID,
    current_customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    item = (
        await session.execute(
            select(WishlistItem).where(
                WishlistItem.customer_id == current_customer.id,
                WishlistItem.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not in wishlist")

    await session.delete(item)
    await session.commit()
