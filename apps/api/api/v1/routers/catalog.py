import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.catalog import (
    BrandCreate,
    BrandOut,
    CategoryCreate,
    CategoryOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    ProductVariantCreate,
    ProductVariantOut,
)
from infrastructure.db.models import Brand, Category, Product, ProductVariant, User
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/catalog", tags=["catalog"])


# ------------------------------------------------------------- BRANDS

@router.post("/brands", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    body: BrandCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> Brand:
    existing = await session.execute(select(Brand).where(Brand.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Brand '{body.name}' already exists")

    brand = Brand(name=body.name)
    session.add(brand)
    await session.commit()
    await session.refresh(brand)
    return brand


@router.get("/brands", response_model=list[BrandOut])
async def list_brands(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[Brand]:
    result = await session.execute(select(Brand).order_by(Brand.name))
    return list(result.scalars().all())


# --------------------------------------------------------- CATEGORIES

@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> Category:
    if body.parent_id is not None:
        parent = await session.get(Category, body.parent_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Parent category not found")

    category = Category(name=body.name, parent_id=body.parent_id)
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[Category]:
    result = await session.execute(select(Category).order_by(Category.name))
    return list(result.scalars().all())


# ----------------------------------------------------------- PRODUCTS

@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> Product:
    existing = await session.execute(select(Product).where(Product.sku == body.sku))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"SKU '{body.sku}' already exists")

    product = Product(**body.model_dump())
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    brand_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[Product]:
    stmt = select(Product).where(Product.is_deleted.is_(False))
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if brand_id:
        stmt = stmt.where(Product.brand_id == brand_id)
    stmt = stmt.order_by(Product.name)

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> Product:
    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> Product:
    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    await session.commit()
    await session.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> None:
    # Soft delete only -- hard deletes break offline sync reconciliation
    # (a device offline for a week wouldn't know the product was
    # removed and could recreate it). See docs/architecture Section 4.3.
    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    product.is_deleted = True
    await session.commit()


# ------------------------------------------------------------ VARIANTS

def _variant_to_out(variant: ProductVariant) -> ProductVariantOut:
    return ProductVariantOut(
        id=variant.id,
        product_id=variant.product_id,
        variant_name=variant.variant_name,
        sku=variant.sku,
        barcode=variant.barcode,
        cost_price_naira=Money(variant.cost_price_kobo).naira,
        selling_price_naira=Money(variant.selling_price_kobo).naira,
        reorder_level=variant.reorder_level,
    )


@router.post(
    "/products/{product_id}/variants",
    response_model=ProductVariantOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_variant(
    product_id: uuid.UUID,
    body: ProductVariantCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> ProductVariantOut:
    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    existing = await session.execute(select(ProductVariant).where(ProductVariant.sku == body.sku))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Variant SKU '{body.sku}' already exists")

    variant = ProductVariant(
        product_id=product_id,
        variant_name=body.variant_name,
        sku=body.sku,
        barcode=body.barcode,
        cost_price_kobo=Money.from_naira(body.cost_price_naira).kobo,
        selling_price_kobo=Money.from_naira(body.selling_price_naira).kobo,
        reorder_level=body.reorder_level,
    )
    session.add(variant)
    await session.commit()
    await session.refresh(variant)
    return _variant_to_out(variant)


@router.get("/products/{product_id}/variants", response_model=list[ProductVariantOut])
async def list_variants(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[ProductVariantOut]:
    result = await session.execute(
        select(ProductVariant).where(
            ProductVariant.product_id == product_id, ProductVariant.is_deleted.is_(False)
        )
    )
    return [_variant_to_out(v) for v in result.scalars().all()]
