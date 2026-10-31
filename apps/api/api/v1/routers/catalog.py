import os
import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import (
    get_current_user,
    get_current_user_optional,
    get_db_session,
    require_permission,
)
from api.v1.schemas.catalog import (
    BrandCreate,
    BrandOut,
    CategoryCreate,
    CategoryOut,
    ImportResult,
    InventoryDeletionReclassify,
    InventoryDeletionRecordOut,
    ProductCreate,
    ProductDeleteRequest,
    ProductImageOut,
    ProductOut,
    ProductUpdate,
)
from infrastructure.db.models import (
    Branch,
    BranchProductPrice,
    Brand,
    Category,
    InventoryDeletionRecord,
    Product,
    ProductCategory,
    ProductImage,
    StockBatch,
    User,
)
from infrastructure.db.audit import log_audit_event
from infrastructure.import_export.products import (
    build_product_import_template,
    export_products_workbook,
    import_products_workbook,
)
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/catalog", tags=["catalog"])

# ---------------------------------------------------------------------------
# PATCH9A-UNITS -- packaging units (carton / bag / roll / dozen / piece ...).
# Anyone signed in can read the list; changing it needs manage_products.
# Sizes: dozen is fixed (12); carton / bag / roll are per product (product form).
# ---------------------------------------------------------------------------
from pydantic import BaseModel as _UnitBase  # noqa: E402


class PackagingUnitOut(_UnitBase):
    id: uuid.UUID
    code: str
    label: str
    plural_label: str
    fixed_pieces: int | None = None
    include_in_breakdown: bool
    is_active: bool
    is_builtin: bool
    sort_order: int

    model_config = {"from_attributes": True}


class PackagingUnitCreate(_UnitBase):
    label: str
    plural_label: str | None = None
    fixed_pieces: int | None = None
    include_in_breakdown: bool = True


class PackagingUnitUpdate(_UnitBase):
    label: str | None = None
    plural_label: str | None = None
    fixed_pieces: int | None = None
    include_in_breakdown: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


def _unit_code_from_label(label: str) -> str:
    code = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return code or "unit"


@router.get("/packaging-units", response_model=list[PackagingUnitOut])
async def list_packaging_units(
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[PackagingUnitOut]:
    from infrastructure.db.models import PackagingUnit

    stmt = select(PackagingUnit).order_by(PackagingUnit.sort_order, PackagingUnit.label)
    if not include_inactive:
        stmt = stmt.where(PackagingUnit.is_active.is_(True))
    result = await session.execute(stmt)
    return [PackagingUnitOut.model_validate(u) for u in result.scalars().all()]


@router.post("/packaging-units", response_model=PackagingUnitOut, status_code=status.HTTP_201_CREATED)
async def create_packaging_unit(
    body: PackagingUnitCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> PackagingUnitOut:
    from infrastructure.db.models import PackagingUnit

    label = body.label.strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Give the unit a name.")
    if body.fixed_pieces is not None and body.fixed_pieces < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pieces in one unit must be 2 or more.")
    code = _unit_code_from_label(label)
    existing = await session.execute(select(PackagingUnit).where(PackagingUnit.code == code))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"A unit called '{label}' already exists.")
    max_sort = await session.execute(select(func.max(PackagingUnit.sort_order)))
    unit = PackagingUnit(
        code=code,
        label=label,
        plural_label=(body.plural_label or "").strip() or label + "s",
        fixed_pieces=body.fixed_pieces,
        include_in_breakdown=body.include_in_breakdown,
        is_active=True,
        is_builtin=False,
        sort_order=(max_sort.scalar() or 0) + 10,
    )
    session.add(unit)
    await session.commit()
    await session.refresh(unit)
    return PackagingUnitOut.model_validate(unit)


@router.patch("/packaging-units/{unit_id}", response_model=PackagingUnitOut)
async def update_packaging_unit(
    unit_id: uuid.UUID,
    body: PackagingUnitUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> PackagingUnitOut:
    from infrastructure.db.models import PackagingUnit

    unit = await session.get(PackagingUnit, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    data = body.model_dump(exclude_unset=True)
    if "label" in data:
        label = (data["label"] or "").strip()
        if not label:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Give the unit a name.")
        unit.label = label
    if "plural_label" in data:
        unit.plural_label = (data["plural_label"] or "").strip() or unit.label + "s"
    if "fixed_pieces" in data:
        # carton / bag / roll sizes live on each product, piece is always 1.
        if unit.code in ("carton", "bag", "roll", "piece"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="This unit's size is set per product on the product form, not here.",
            )
        if data["fixed_pieces"] is not None and data["fixed_pieces"] < 2:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pieces in one unit must be 2 or more.")
        unit.fixed_pieces = data["fixed_pieces"]
    if "include_in_breakdown" in data and data["include_in_breakdown"] is not None:
        unit.include_in_breakdown = data["include_in_breakdown"]
    if "is_active" in data and data["is_active"] is not None:
        if unit.code == "piece" and not data["is_active"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Piece is the base unit and stays on.")
        unit.is_active = data["is_active"]
    if "sort_order" in data and data["sort_order"] is not None:
        unit.sort_order = data["sort_order"]
    await session.commit()
    await session.refresh(unit)
    return PackagingUnitOut.model_validate(unit)

class ProductUnitSizesIn(_UnitBase):
    # unit id -> pieces in one unit of THIS product. null removes the product's own size.
    sizes: dict[uuid.UUID, int | None]


class ProductUnitSizeOut(_UnitBase):
    product_id: uuid.UUID
    unit_id: uuid.UUID
    pieces: int

    model_config = {"from_attributes": True}


@router.get("/product-unit-sizes", response_model=list[ProductUnitSizeOut])
async def list_product_unit_sizes(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[ProductUnitSizeOut]:
    from infrastructure.db.models import ProductUnitSize

    result = await session.execute(select(ProductUnitSize))
    return [ProductUnitSizeOut.model_validate(r) for r in result.scalars().all()]


@router.put("/products/{product_id}/unit-sizes", response_model=list[ProductUnitSizeOut])
async def set_product_unit_sizes(
    product_id: uuid.UUID,
    body: ProductUnitSizesIn,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> list[ProductUnitSizeOut]:
    from infrastructure.db.models import PackagingUnit, ProductUnitSize

    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")
    units = {u.id: u for u in (await session.execute(select(PackagingUnit))).scalars().all()}
    existing = {
        r.unit_id: r
        for r in (
            await session.execute(
                select(ProductUnitSize).where(ProductUnitSize.product_id == product_id)
            )
        ).scalars().all()
    }
    # check everything first, write after
    for unit_id, pieces in body.sizes.items():
        unit = units.get(unit_id)
        if unit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
        if unit.code in ("carton", "bag", "roll", "piece"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Carton, bag and roll sizes are set in the product's own pack boxes.",
            )
        if pieces is not None and pieces < 2:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pieces in one unit must be 2 or more.")
    for unit_id, pieces in body.sizes.items():
        row = existing.get(unit_id)
        if pieces is None:
            if row is not None:
                await session.delete(row)
        elif row is not None:
            row.pieces = pieces
        else:
            session.add(ProductUnitSize(product_id=product_id, unit_id=unit_id, pieces=pieces))
    await session.commit()
    result = await session.execute(
        select(ProductUnitSize).where(ProductUnitSize.product_id == product_id)
    )
    return [ProductUnitSizeOut.model_validate(r) for r in result.scalars().all()]


# Same default host used by the older upload_product_image.py CLI
# script -- override via API_PUBLIC_BASE_URL when the API is reachable
# at a different host/port than this process binds to (e.g. behind a
# reverse proxy, or when phones/desktop tills reach it over LAN by IP).
_PUBLIC_BASE_URL = os.getenv("API_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
_PRODUCT_IMAGE_DIR = Path("static/uploads/products")
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


async def _load_stocked_product_ids(
    session: AsyncSession, product_ids: list[uuid.UUID], branch_id: uuid.UUID | None
) -> set[uuid.UUID]:
    """Which of these products have ever had a StockBatch row at this
    branch at all -- regardless of remaining quantity or expiry. Lets
    the caller tell "carried here, currently depleted" (0, a real Out
    of Stock) apart from "never stocked at this branch" (None, so the
    UI can show "Not carried at this branch" instead of a misleading
    Out of Stock pill implying it used to sell here).
    """
    if not product_ids or branch_id is None:
        return set()
    stmt = (
        select(StockBatch.product_id)
        .where(StockBatch.product_id.in_(product_ids), StockBatch.branch_id == branch_id)
        .distinct()
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


def _product_to_out(product: Product, quantity_available: int | None = 0, override_price_kobo: int | None = None) -> ProductOut:
    primary = next((img for img in product.images if img.is_primary), None)
    if primary is None and product.images:
        primary = product.images[0]
    data = ProductOut.model_validate(product)
    data.product_id = product.id
    data.image_url = primary.url if primary else None
    data.images = [img.url for img in sorted(product.images, key=lambda i: i.sort_order)]
    data.category = product.category.name if product.category else None
    # The web-admin inventory table reads `category_name` specifically
    # (not `category`) -- kept as an addition alongside the existing
    # `category` field rather than renaming it.
    data.category_name = data.category
    # Item 5 (Add Product rework): the inventory table now shows a
    # Brand column, so the resolved brand name needs to travel with
    # the product the same way category name already does.
    data.brand_name = product.brand.name if product.brand else None
    data.extra_category_ids = [ec.category_id for ec in product.extra_categories]
    data.extra_category_names = [
        ec.category.name for ec in product.extra_categories if ec.category is not None
    ]

    effective_price_kobo = override_price_kobo if override_price_kobo is not None else product.selling_price_kobo
    price_naira = Money(effective_price_kobo).naira
    data.price = price_naira
    data.price_naira = price_naira
    data.cost_price_naira = Money(product.cost_price_kobo).naira
    data.selling_price_naira = price_naira
    # September 2026 change spec section 22 -- wholesale_min_quantity
    # already flows through model_validate() above (name matches the
    # ORM attribute exactly); the naira price needs the same kobo
    # conversion as cost/selling price above.
    data.wholesale_price_naira = (
        Money(product.wholesale_price_kobo).naira if product.wholesale_price_kobo is not None else None
    )
    data.product_id = product.id
    data.quantity_available = quantity_available
    return data


async def _load_stock_for_products(
    session: AsyncSession, product_ids: list[uuid.UUID], branch_id: uuid.UUID | None
) -> dict[uuid.UUID, int]:
    """Available quantity per product, summing non-expired batches with
    remaining stock -- mirrors the eligibility rule in
    GET /inventory/stock-levels so the number shown in the catalog
    matches what checkout can actually sell.

    When branch_id is omitted, sums across every branch (an "All
    branches" view of the catalog).
    """
    if not product_ids:
        return {}
    today = date.today()
    stmt = (
        select(StockBatch.product_id, func.sum(StockBatch.quantity_remaining).label("qty"))
        .where(
            StockBatch.product_id.in_(product_ids),
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
        .group_by(StockBatch.product_id)
    )
    if branch_id is not None:
        stmt = stmt.where(StockBatch.branch_id == branch_id)
    result = await session.execute(stmt)
    return {row.product_id: row.qty for row in result.all()}


async def _load_price_overrides(
    session: AsyncSession, product_ids: list[uuid.UUID], branch_id: uuid.UUID | None
) -> dict[uuid.UUID, int]:
    """Per-branch price override lookup, mirrors _load_stock_for_products.

    When branch_id is None (e.g. an "All branches" super_admin view, or
    an anonymous caller with no branch), no overrides apply -- every
    product resolves to its global selling_price_kobo, same as before
    this feature existed.
    """
    if not product_ids or branch_id is None:
        return {}
    stmt = select(BranchProductPrice.product_id, BranchProductPrice.selling_price_kobo).where(
        BranchProductPrice.product_id.in_(product_ids),
        BranchProductPrice.branch_id == branch_id,
    )
    result = await session.execute(stmt)
    return {row.product_id: row.selling_price_kobo for row in result.all()}


_PRODUCT_LOAD_OPTIONS = (
    selectinload(Product.images),
    selectinload(Product.category),
    selectinload(Product.extra_categories).selectinload(ProductCategory.category),
    selectinload(Product.brand),
)


async def _sync_extra_categories(
    session: AsyncSession, product: Product, category_ids: list[uuid.UUID] | None
) -> None:
    """Section 6: replace a product's extra-category assignments with
    exactly the given set. None means "leave untouched" (PATCH didn't
    mention the field); an empty list clears all extra categories.
    """
    if category_ids is None:
        return
    existing = await session.execute(
        select(ProductCategory).where(ProductCategory.product_id == product.id)
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()
    for category_id in dict.fromkeys(category_ids):  # de-dupe, keep order
        if category_id == product.category_id:
            continue  # already the primary category, no need to duplicate
        session.add(ProductCategory(product_id=product.id, category_id=category_id))


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
) -> list[Brand]:
    result = await session.execute(select(Brand).order_by(Brand.name))
    return list(result.scalars().all())


# --------------------------------------------------------- CATEGORIES
# Section 6: "Categories and Variants" is retired -- this is now plain
# "Product Categories" management, plus the ability to file one
# product under more than one category (see /products/{id} below and
# ProductCategory).

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
) -> list[CategoryOut]:
    result = await session.execute(select(Category).order_by(Category.name))
    categories = list(result.scalars().all())

    primary_counts = dict(
        (await session.execute(
            select(Product.category_id, func.count())
            .where(Product.is_deleted.is_(False), Product.category_id.is_not(None))
            .group_by(Product.category_id)
        )).all()
    )
    extra_counts = dict(
        (await session.execute(
            select(ProductCategory.category_id, func.count())
            .group_by(ProductCategory.category_id)
        )).all()
    )
    return [
        CategoryOut(
            id=c.id,
            name=c.name,
            parent_id=c.parent_id,
            product_count=primary_counts.get(c.id, 0) + extra_counts.get(c.id, 0),
        )
        for c in categories
    ]


@router.get("/categories/{category_id}/sizes", response_model=list[str])
async def list_category_sizes(
    category_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[str]:
    """Every distinct size used by a non-deleted product filed under
    this category (primary category only). Backs the Add Product size
    field's "used before in this category" quick-pick suggestions --
    e.g. once 500ml/600ml exist for a Drinks product, picking Drinks
    again on a new product offers those as one-click options instead
    of retyping them, while free-text/Big-Medium-Small entry still
    works exactly as before.
    """
    result = await session.execute(
        select(Product.size)
        .where(
            Product.category_id == category_id,
            Product.size.is_not(None),
            Product.is_deleted.is_(False),
        )
        .distinct()
    )
    return sorted({row[0] for row in result.all() if row[0]})


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def rename_category(
    category_id: uuid.UUID,
    body: CategoryCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> Category:
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")
    category.name = body.name
    if body.parent_id is not None:
        category.parent_id = body.parent_id
    await session.commit()
    await session.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> None:
    """Unconditional delete: a category can always be removed, even if
    products (including soft-deleted ones) still point to it or
    sub-categories sit under it. Rather than blocking on that -- which
    is what caused this to 500 with a raw FK IntegrityError when a
    soft-deleted product still referenced the category -- everything
    that points at this category is detached first:

    - Products with this as their primary category_id (deleted or not)
      have it cleared to NULL. This never touches product data/stock,
      it only unlinks the category.
    - Any ProductCategory "extra category" rows for this category are
      removed the same way _sync_extra_categories already removes them
      elsewhere in this file.
    - Child categories are promoted to top-level (parent_id -> NULL)
      instead of being deleted or blocking the parent's removal.
    """
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")

    # Detach primary-category references on every product, regardless
    # of is_deleted -- this is exactly the gap that let a soft-deleted
    # product hold onto the FK and crash the old count-based check.
    await session.execute(
        update(Product).where(Product.category_id == category_id).values(category_id=None)
    )

    # Detach any "extra category" links.
    extra_links = await session.execute(
        select(ProductCategory).where(ProductCategory.category_id == category_id)
    )
    for link in extra_links.scalars().all():
        await session.delete(link)

    # Promote any sub-categories instead of blocking the delete.
    await session.execute(
        update(Category).where(Category.parent_id == category_id).values(parent_id=None)
    )

    await session.flush()
    await session.delete(category)
    await session.commit()


# ----------------------------------------------------------- PRODUCTS

async def _generate_sku(session: AsyncSession, name: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-") or uuid.uuid4().hex[:8].upper()
    candidate = base
    suffix = 1
    while True:
        existing = await session.execute(select(Product).where(Product.sku == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
        suffix += 1
        candidate = f"{base}-{suffix}"


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_products")),
) -> ProductOut:
    data = body.model_dump(
        exclude={"sku", "extra_category_ids", "cost_price_naira", "selling_price_naira", "wholesale_price_naira", "status"}
    )
    data["status"] = body.status or "active"
    # Add Product -> Invoice Workflow follow-up (Sept 2026): mirrors
    # the same "brand-new, non-draft product" condition inventory.js
    # already uses to decide whether to open the invoice prompt.
    data["needs_invoice"] = data["status"] != "draft"
    data["wholesale_price_kobo"] = (
        Money.from_naira(body.wholesale_price_naira).kobo if body.wholesale_price_naira is not None else None
    )

    brand = None
    if body.brand_id is not None:
        brand = await session.get(Brand, body.brand_id)
        if brand is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Brand not found")

    # Section 1 used to say "Product name = Brand name + byproduct,
    # concatenated automatically", but that meant a name the user
    # typed by hand could get silently overwritten by this
    # concatenation if a brand+byproduct also happened to be present
    # -- the "new product name doesn't reliably register" bug (Update
    # Request item 5). An explicitly supplied name is now always
    # authoritative; brand+byproduct concatenation is only a fallback
    # for callers that never send a name (e.g. the older warehouse
    # quick-add flow, if it's still on brand+byproduct).
    if body.name:
        data["name"] = body.name.strip()
    elif brand is not None and body.byproduct:
        data["name"] = f"{brand.name} {body.byproduct}".strip()
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Provide either a brand + byproduct, or a product name.",
        )

    sku = body.sku
    if not sku:
        # Quick-add path (warehouse "+ Quick add product") only sends a
        # name -- derive a starter SKU from it so the product can still
        # be created; staff can rename it properly later from the full
        # product form. Falls back to a short uuid slug and disambiguates
        # on collision by appending -2, -3, ... .
        sku = await _generate_sku(session, data["name"])
    else:
        existing = await session.execute(select(Product).where(Product.sku == sku))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"SKU '{sku}' already exists")

    product = Product(
        **data,
        sku=sku,
        cost_price_kobo=Money.from_naira(body.cost_price_naira).kobo,
        selling_price_kobo=Money.from_naira(body.selling_price_naira).kobo,
    )
    session.add(product)
    await session.flush()
    await _sync_extra_categories(session, product, body.extra_category_ids)
    await session.commit()

    result = await session.execute(
        select(Product).where(Product.id == product.id).options(*_PRODUCT_LOAD_OPTIONS)
    )
    return _product_to_out(result.scalar_one())


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    brand_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    low_stock: bool = False,
    out_of_stock: bool = False,
    needs_invoice: bool = False,
    sort_by: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
) -> list[ProductOut]:
    stmt = select(Product).where(Product.is_deleted.is_(False)).options(*_PRODUCT_LOAD_OPTIONS)
    # Per-product staff visibility (feature spec 2) -- a *server-side*
    # exclusion, not a frontend filter, and applied here so every
    # caller of this endpoint (Inventory page, Warehouse page's staff
    # stock list, POS search) is covered by one check instead of each
    # screen re-implementing it. Only bites for an authenticated
    # staff-role caller; anonymous/customer/manager/admin callers are
    # unaffected.
    if current_user is not None and current_user.role == "staff":
        stmt = stmt.where(Product.hidden_from_staff.is_(False))
    if search:
        stmt = stmt.where(Product.name.ilike(f"%{search}%"))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if brand_id:
        stmt = stmt.where(Product.brand_id == brand_id)
    stmt = stmt.order_by(Product.name)

    result = await session.execute(stmt)
    products = list(result.scalars().all())

    # Force branch-scoped stock for anyone who isn't super_admin -- the
    # catalog itself is shared across branches, but stock status must
    # only ever reflect the viewer's own branch, never a blend or
    # another branch's numbers.
    effective_branch_id = branch_id
    if current_user is not None and current_user.role != "super_admin":
        effective_branch_id = current_user.branch_id

    stock_by_product = await _load_stock_for_products(
        session, [p.id for p in products], effective_branch_id
    )

    stocked_product_ids = await _load_stocked_product_ids(
        session, [p.id for p in products], effective_branch_id
    )

    def _resolve_quantity_available(pid: uuid.UUID) -> int | None:
        if pid in stock_by_product:
            return stock_by_product[pid]
        if effective_branch_id is None or pid in stocked_product_ids:
            return 0
        return None

    price_overrides = await _load_price_overrides(session, [p.id for p in products], effective_branch_id)

    out = [_product_to_out(p, _resolve_quantity_available(p.id), price_overrides.get(p.id)) for p in products]
    if low_stock:
        # "Low stock only" must show only items that still have stock
        # but are at/under the reorder threshold -- NOT items that are
        # already fully out of stock. Those are a separate bucket
        # (out_of_stock below), so a plain `<= reorder_level` here
        # (which also matches quantity 0) was pulling out-of-stock
        # items into the low-stock filter too.
        out = [p for p in out if p.quantity_available is not None and 0 < p.quantity_available <= p.reorder_level]
    if out_of_stock:
        out = [p for p in out if p.quantity_available is not None and p.quantity_available <= 0]
    if needs_invoice:
        out = [p for p in out if p.needs_invoice]
    if not low_stock and not out_of_stock:
        # Default "All categories" view: in-stock items list first (in
        # as much as they're still on stock, they should be there),
        # out-of-stock items drop to the bottom instead of being mixed
        # in among items staff can actually sell. Stable sort keeps
        # the existing name ordering within each group.
        out.sort(key=lambda p: True if p.quantity_available is None else p.quantity_available <= 0)

    if min_price is not None:
        out = [p for p in out if (p.price or 0) >= min_price]
    if max_price is not None:
        out = [p for p in out if (p.price or 0) <= max_price]

    # sort_by is additive and optional so existing callers (web_admin,
    # inventory screens) that never pass it keep the previous
    # name-ordered behaviour from the SQL query above.
    if sort_by == "price_asc":
        out.sort(key=lambda p: p.price if p.price is not None else float("inf"))
    elif sort_by == "price_desc":
        out.sort(key=lambda p: p.price if p.price is not None else float("-inf"), reverse=True)
    elif sort_by == "newest":
        out.sort(key=lambda p: p.created_at or p.id.int, reverse=True)
    elif sort_by == "name":
        out.sort(key=lambda p: p.name.lower())

    return out


# These three routes must be registered before /products/{product_id}
# below -- FastAPI matches routes in registration order, and
# "export"/"import-template"/"import" would otherwise be swallowed by
# the {product_id}: uuid.UUID path param and fail as a 422 instead of
# reaching these handlers.

@router.get("/products/export")
async def export_products(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> Response:
    content = await export_products_workbook(session)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=products.xlsx"},
    )


@router.get("/products/import-template")
async def download_product_import_template(
    _: User = Depends(require_permission("manage_products")),
) -> Response:
    content = build_product_import_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product-import-template.xlsx"},
    )


@router.post("/products/import", response_model=ImportResult)
async def import_products(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> ImportResult:
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please upload a .xlsx file.")
    file_bytes = await file.read()
    result = await import_products_workbook(session, file_bytes)
    return ImportResult(**result)


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: uuid.UUID,
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
) -> ProductOut:
    stmt = select(Product).where(Product.id == product_id).options(*_PRODUCT_LOAD_OPTIONS)
    result = await session.execute(stmt)
    product = result.scalar_one_or_none()
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    # Same per-product staff visibility rule as list_products -- a
    # direct GET by id must not leak a hidden product to a staff caller
    # either (e.g. via a stale link or an id guessed from another
    # screen). 404, not 403: existence of a hidden product shouldn't be
    # revealed to staff at all.
    if current_user is not None and current_user.role == "staff" and product.hidden_from_staff:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    effective_branch_id = branch_id
    if current_user is not None and current_user.role != "super_admin":
        effective_branch_id = current_user.branch_id

    stock_by_product = await _load_stock_for_products(session, [product.id], effective_branch_id)
    price_override_row = await _load_price_overrides(session, [product.id], effective_branch_id)
    return _product_to_out(product, stock_by_product.get(product.id, 0), price_override_row.get(product.id))


# PATCH10B-SAME-PRICE-NOOP: a save that sends the price the product (or this
# branch) already has changes nothing, so it writes no price audit event and
# does not reset or create branch price overrides.
async def _selling_price_unchanged(session, product, current_user, naira) -> bool:
    new_kobo = Money.from_naira(naira).kobo
    if current_user.role == "super_admin":
        return new_kobo == product.selling_price_kobo
    result = await session.execute(
        select(BranchProductPrice).where(
            BranchProductPrice.branch_id == current_user.branch_id,
            BranchProductPrice.product_id == product.id,
        )
    )
    row = result.scalar_one_or_none()
    current_kobo = row.selling_price_kobo if row is not None else product.selling_price_kobo
    return new_kobo == current_kobo


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_products")),
) -> ProductOut:
    result = await session.execute(
        select(Product).where(Product.id == product_id).options(*_PRODUCT_LOAD_OPTIONS)
    )
    product = result.scalar_one_or_none()
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    data = body.model_dump(
        exclude_unset=True,
        exclude={"extra_category_ids", "cost_price_naira", "selling_price_naira", "wholesale_price_naira"},
    )
    # hidden_from_staff is super-admin-only (feature spec 2) -- gated
    # here on top of the ordinary manage_products check above, since a
    # branch_manager also holds manage_products but shouldn't be able
    # to flip this particular field.
    if "hidden_from_staff" in data and current_user.role != "super_admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only a super admin can change a product's staff visibility.",
        )

    if "sku" in data and data["sku"] != product.sku:
        existing = await session.execute(select(Product).where(Product.sku == data["sku"]))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"SKU '{data['sku']}' already exists")

    for field, value in data.items():
        setattr(product, field, value)

    body_data = body.model_dump(exclude_unset=True)
    if "cost_price_naira" in body_data:
        product.cost_price_kobo = Money.from_naira(body_data["cost_price_naira"]).kobo
    if "selling_price_naira" in body_data and not await _selling_price_unchanged(  # PATCH10B-SAME-PRICE-NOOP
        session, product, current_user, body_data["selling_price_naira"]
    ):
        new_price_kobo = Money.from_naira(body_data["selling_price_naira"]).kobo
        if current_user.role == "super_admin":
            old_price_kobo = product.selling_price_kobo
            product.selling_price_kobo = new_price_kobo
            await session.execute(
                delete(BranchProductPrice).where(BranchProductPrice.product_id == product.id)
            )
            log_audit_event(
                session,
                user_id=current_user.id,
                action="global_price_changed",
                entity_type="product",
                entity_id=product.id,
                metadata={
                    "product_name": product.name,
                    "old_price_naira": round(old_price_kobo / 100, 2),
                    "new_price_naira": body_data["selling_price_naira"],
                },
            )
        else:
            branch = await session.get(Branch, current_user.branch_id)
            existing_override = await session.execute(
                select(BranchProductPrice).where(
                    BranchProductPrice.branch_id == current_user.branch_id,
                    BranchProductPrice.product_id == product.id,
                )
            )
            override_row = existing_override.scalar_one_or_none()
            old_price_kobo = override_row.selling_price_kobo if override_row is not None else product.selling_price_kobo
            if override_row is not None:
                override_row.selling_price_kobo = new_price_kobo
                override_row.set_by = current_user.id
            else:
                session.add(
                    BranchProductPrice(
                        branch_id=current_user.branch_id,
                        product_id=product.id,
                        selling_price_kobo=new_price_kobo,
                        set_by=current_user.id,
                    )
                )
            log_audit_event(
                session,
                user_id=current_user.id,
                action="branch_price_override_changed",
                entity_type="product",
                entity_id=product.id,
                metadata={
                    "product_name": product.name,
                    "branch_id": str(current_user.branch_id),
                    "branch_name": branch.name if branch is not None else None,
                    "old_price_naira": round(old_price_kobo / 100, 2),
                    "new_price_naira": body_data["selling_price_naira"],
                },
            )
    # September 2026 change spec section 22 -- wholesale_price_naira=
    # null explicitly clears the tier (kept separate from the ordinary
    # per-field loop above since it needs the same naira->kobo
    # conversion as cost/selling price).
    if "wholesale_price_naira" in body_data:
        product.wholesale_price_kobo = (
            Money.from_naira(body_data["wholesale_price_naira"]).kobo
            if body_data["wholesale_price_naira"] is not None
            else None
        )

    await _sync_extra_categories(session, product, body.extra_category_ids)

    await session.commit()
    result = await session.execute(
        select(Product).where(Product.id == product_id).options(*_PRODUCT_LOAD_OPTIONS)
    )
    saved_product = result.scalar_one()
    resp_branch_id = current_user.branch_id if current_user.role != "super_admin" else None
    resp_price_overrides = await _load_price_overrides(session, [saved_product.id], resp_branch_id)
    return _product_to_out(saved_product, override_price_kobo=resp_price_overrides.get(saved_product.id))


@router.post("/products/{product_id}/mute-out-of-stock-alert", response_model=ProductOut)
async def mute_out_of_stock_alert(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    # Same permission as seeing the alert in the first place
    # (view_low_stock, granted to staff too) -- whoever can see the
    # dashboard nag should be able to dismiss it, not just managers.
    _: User = Depends(require_permission("view_low_stock")),
) -> ProductOut:
    """Dismisses this product from the dashboard's out-of-stock alert
    count/dropdown -- doesn't touch the Low Stock or Inventory pages,
    which still show it. Auto-clears the next time this product
    actually has stock again (see the low-stock report endpoint).
    """
    result = await session.execute(
        select(Product).where(Product.id == product_id).options(*_PRODUCT_LOAD_OPTIONS)
    )
    product = result.scalar_one_or_none()
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    product.out_of_stock_alert_muted = True
    await session.commit()
    return _product_to_out(product)


@router.post("/products/{product_id}/unmute-out-of-stock-alert", response_model=ProductOut)
async def unmute_out_of_stock_alert(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("view_low_stock")),
) -> ProductOut:
    result = await session.execute(
        select(Product).where(Product.id == product_id).options(*_PRODUCT_LOAD_OPTIONS)
    )
    product = result.scalar_one_or_none()
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    product.out_of_stock_alert_muted = False
    await session.commit()
    return _product_to_out(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    body: ProductDeleteRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_products")),
) -> None:
    """Soft delete, same as before -- hard deletes still break offline
    sync reconciliation (docs/architecture Section 4.3). Spec sections
    8-10 add a required reason and a traceable audit record on top:
    deleting stock that was actually purchased affects inventory
    valuation and P&L, so it must never just vanish. The record
    snapshots product/category/sku/unit and the stock quantity being
    written off *before* the soft delete, so it reads correctly even
    after the product is gone from active use.
    """
    product = await session.get(Product, product_id, options=[selectinload(Product.category)])
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Sept 2026 change: no reason is collected from the person deleting
    # an item anymore -- the frontend always sends a generic reason, so
    # deletion is a plain confirm() with no follow-up questions. Still
    # recorded below in InventoryDeletionRecord for auditability.

    result = await session.execute(
        select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
            StockBatch.product_id == product.id
        )
    )
    quantity_affected = result.scalar_one()

    # incorrect_entry/duplicate are data-entry corrections, not actual
    # stock leaving the business -- everything else (damaged, expired,
    # lost, spoiled, and the catch-all "other") is a real loss for P&L
    # purposes (spec section 10).
    is_loss = body.reason not in ("incorrect_entry", "duplicate")

    session.add(InventoryDeletionRecord(
        product_id=product.id,
        product_name=product.name,
        category_name=product.category.name if product.category else None,
        sku=product.sku,
        unit_of_measure=product.unit_of_measure,
        branch_id=current_user.branch_id,
        quantity_affected=quantity_affected,
        reason=body.reason,
        reason_note=body.reason_note,
        is_loss=is_loss,
        deleted_by=current_user.id,
    ))

    product.is_deleted = True
    await session.commit()


@router.post("/products/{product_id}/restore", response_model=ProductOut)
async def restore_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_products")),
) -> ProductOut:
    """Undo a soft delete (September 2026 change spec section 8) --
    identity fields only come back (image, name, barcode, category,
    size), never quantity-in-stock or price. Deleting a product never
    touched its StockBatch rows (see delete_product above -- it only
    snapshots the quantity for the audit record and flips is_deleted),
    so a plain `is_deleted = False` here would silently bring back
    whatever stock/cost was left over from before the delete. Instead:
    every existing StockBatch for this product is zeroed out (not
    deleted -- that would break FIFO/cost-history for any sale that
    happened before the delete) and cost/selling price reset to 0, so
    the item comes back dormant at â‚¦0 / 0 in stock until someone
    manually restocks it (gated on real warehouse stock, section 7)
    and re-prices it.
    """
    product = await session.get(Product, product_id, options=list(_PRODUCT_LOAD_OPTIONS))
    if product is None or not product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Deleted product not found")

    await session.execute(
        update(StockBatch).where(StockBatch.product_id == product.id).values(quantity_remaining=0)
    )
    product.is_deleted = False
    product.cost_price_kobo = 0
    product.selling_price_kobo = 0
    await session.commit()

    # _product_to_out reads product.brand and product.extra_categories
    # too (not just category/images) -- session.refresh() alone
    # doesn't reload relationships, so this re-selects with the same
    # full _PRODUCT_LOAD_OPTIONS every other endpoint in this file
    # uses, the same way create_product/update_product do above.
    # Without this, accessing those two attributes below triggers a
    # lazy DB load outside the async context and raises
    # sqlalchemy.exc.MissingGreenlet instead of returning a response.
    result = await session.execute(
        select(Product).where(Product.id == product_id).options(*_PRODUCT_LOAD_OPTIONS)
    )
    return _product_to_out(result.scalar_one())


@router.post("/products/{product_id}/hard-delete", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_products")),
) -> None:
    """Permanently remove a product from the inventory/deletion-history
    list (September 2026 change). Only allowed on a product that has
    ALREADY been soft-deleted via the normal delete-with-reason flow
    above. This never touches StockBatch, SaleItem,
    PurchaseInvoiceLineItem, or any other historical reference -- those
    keep pointing at this product row exactly as before, so invoices,
    past sales, and "total invested in inventory" figures are entirely
    unaffected. It only sets hard_deleted=True, which the frontend uses
    to permanently hide the Restore action in Deletion History.
    Re-adding the same product later is an ordinary fresh Add Product
    -- it becomes a brand-new row with a new id.
    """
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    if product.hard_deleted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This product was already permanently deleted.")
    # Sept 2026 change: no longer requires the product to already be
    # soft-deleted -- if it's currently active (e.g. restored since its
    # last deletion), this soft-deletes it as part of the same action,
    # so a history row for an active product can still be permanently
    # cleared in one click instead of two separate steps.
    product.is_deleted = True
    product.hard_deleted = True
    await session.commit()


@router.get("/inventory-deletions", response_model=list[InventoryDeletionRecordOut])
async def list_inventory_deletions(
    branch_id: uuid.UUID | None = None,
    is_loss: bool | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("view_all_reports")),
) -> list[InventoryDeletionRecordOut]:
    """Inventory Deletion/Adjustment History (spec section 9) -- "the
    administrator should be able to review these records" and they
    should be "auditable and traceable". Same permission gate as the
    sales audit-log/reports.
    """
    stmt = select(InventoryDeletionRecord).order_by(InventoryDeletionRecord.created_at.desc()).limit(limit)
    if branch_id:
        stmt = stmt.where(InventoryDeletionRecord.branch_id == branch_id)
    if is_loss is not None:
        stmt = stmt.where(InventoryDeletionRecord.is_loss == is_loss)
    result = await session.execute(stmt)
    records = list(result.scalars().all())

    user_ids = {r.deleted_by for r in records if r.deleted_by}
    branch_ids = {r.branch_id for r in records if r.branch_id}
    users_by_id, branches_by_id = {}, {}
    if user_ids:
        u = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {row.id: row.full_name for row in u.scalars().all()}
    if branch_ids:
        b = await session.execute(select(Branch).where(Branch.id.in_(branch_ids)))
        branches_by_id = {row.id: row.name for row in b.scalars().all()}

    # September 2026 change spec section 8: only the product's CURRENT
    # is_deleted state makes Restore actionable -- a product can be
    # deleted, restored, and deleted again, leaving multiple history
    # rows for the same product_id, but at most one of them should
    # ever offer a working Restore button.
    product_ids = {r.product_id for r in records if r.product_id}
    deleted_product_ids: set[uuid.UUID] = set()
    hard_deleted_product_ids: set[uuid.UUID] = set()
    images_by_product_id: dict[uuid.UUID, str | None] = {}
    if product_ids:
        p = await session.execute(
            select(Product.id, Product.is_deleted, Product.hard_deleted).where(Product.id.in_(product_ids))
        )
        for row_id, row_is_deleted, row_hard_deleted in p.all():
            if row_is_deleted:
                deleted_product_ids.add(row_id)
            if row_hard_deleted:
                hard_deleted_product_ids.add(row_id)

        img = await session.execute(
            select(ProductImage.product_id, ProductImage.url).where(
                ProductImage.product_id.in_(product_ids),
                ProductImage.is_primary.is_(True),
            )
        )
        images_by_product_id = {row_product_id: row_url for row_product_id, row_url in img.all()}

    return [
        InventoryDeletionRecordOut(
            id=r.id,
            product_id=r.product_id,
            product_name=r.product_name,
            category_name=r.category_name,
            sku=r.sku,
            unit_of_measure=r.unit_of_measure,
            branch_id=r.branch_id,
            branch_name=branches_by_id.get(r.branch_id) if r.branch_id else None,
            quantity_affected=r.quantity_affected,
            reason=r.reason,
            reason_note=r.reason_note,
            is_loss=r.is_loss,
            deleted_by=r.deleted_by,
            deleted_by_name=users_by_id.get(r.deleted_by) if r.deleted_by else None,
            created_at=r.created_at,
            product_is_deleted=(r.product_id in deleted_product_ids) and (r.product_id not in hard_deleted_product_ids),
            product_hard_deleted=r.product_id in hard_deleted_product_ids,
            image_url=images_by_product_id.get(r.product_id) if r.product_id else None,
        )
        for r in records
        # Sept 2026 change: a permanently-deleted (hard_deleted) product's
        # history rows are excluded entirely, not just stripped of their
        # Restore button.
        if r.product_id is None or r.product_id not in hard_deleted_product_ids
    ]


@router.patch("/inventory-deletions/{record_id}", response_model=InventoryDeletionRecordOut)
async def reclassify_inventory_deletion(
    record_id: uuid.UUID,
    body: InventoryDeletionReclassify,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> InventoryDeletionRecordOut:
    """Lets a Super Admin correct the loss classification or add a
    note after review (spec section 10: "The Super Admin should have
    appropriate authority to review/classify these records").
    """
    if current_user.role != "super_admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Only a super admin can reclassify a deletion record."
        )
    record = await session.get(InventoryDeletionRecord, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Deletion record not found")

    if body.is_loss is not None:
        record.is_loss = body.is_loss
    if body.reason_note is not None:
        record.reason_note = body.reason_note
    await session.commit()
    await session.refresh(record)

    deleted_by_name = None
    if record.deleted_by:
        deleter = await session.get(User, record.deleted_by)
        deleted_by_name = deleter.full_name if deleter else None
    branch_name = None
    if record.branch_id:
        branch = await session.get(Branch, record.branch_id)
        branch_name = branch.name if branch else None

    return InventoryDeletionRecordOut(
        id=record.id,
        product_id=record.product_id,
        product_name=record.product_name,
        category_name=record.category_name,
        sku=record.sku,
        unit_of_measure=record.unit_of_measure,
        branch_id=record.branch_id,
        branch_name=branch_name,
        quantity_affected=record.quantity_affected,
        reason=record.reason,
        reason_note=record.reason_note,
        is_loss=record.is_loss,
        deleted_by=record.deleted_by,
        deleted_by_name=deleted_by_name,
        created_at=record.created_at,
    )


# --------------------------------------------------------------- IMAGES
# Product creation from the desktop POS needs somewhere to send the
# photo it just took/picked -- this is the HTTP equivalent of the
# older upload_product_image.py CLI script, so both paths (manual CLI
# upload and the in-app "Add Product" dialog) land in the same place
# and produce the same URL shape.

@router.post(
    "/products/{product_id}/images",
    response_model=ProductImageOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_product_image(
    product_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> ProductImage:
    product = await session.get(Product, product_id)
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}",
        )

    _PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _PRODUCT_IMAGE_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)

    # New upload becomes the primary image, matching how
    # upload_product_image.py behaves -- a product added via the POS
    # dialog normally only has the one photo taken at intake time.
    existing_result = await session.execute(
        select(ProductImage).where(ProductImage.product_id == product_id)
    )
    existing_images = list(existing_result.scalars().all())
    for img in existing_images:
        img.is_primary = False

    image = ProductImage(
        product_id=product_id,
        url=f"{_PUBLIC_BASE_URL}/static/uploads/products/{new_filename}",
        is_primary=True,
        sort_order=len(existing_images),
    )
    session.add(image)
    await session.commit()
    await session.refresh(image)
    return image


# ------------------------------------------------- MEASUREMENT UNITS
# Owner-managed measuring units for the Add Product "Size" field. The
# table is created (and the built-ins seeded) on first use, so no
# separate migration step is needed.
from pydantic import BaseModel as _UnitBase, Field as _UnitField
from sqlalchemy.exc import IntegrityError as _UnitIntegrityError
from infrastructure.db.models import MeasurementUnit


class UnitCreate(_UnitBase):
    name: str = _UnitField(min_length=1, max_length=12, pattern="^[A-Za-z]+$")


class UnitOut(_UnitBase):
    id: uuid.UUID
    name: str
    group_name: str
    is_builtin: bool

    model_config = {"from_attributes": True}


_BUILTIN_UNITS = [
    ("mg", "Weight"), ("g", "Weight"), ("kg", "Weight"),
    ("ml", "Volume"), ("cl", "Volume"), ("L", "Volume"),
    ("mm", "Length"), ("cm", "Length"), ("m", "Length"),
]
_units_ready = False


async def _ensure_units_table(session: AsyncSession) -> None:
    global _units_ready
    if _units_ready:
        return
    from sqlalchemy import inspect as _sa_inspect

    existed = await session.run_sync(
        lambda s: _sa_inspect(s.connection()).has_table("measurement_units")
    )
    if not existed:
        # First ever use: create the table and seed the starter units once.
        # After that the owner is free to remove any of them for good.
        await session.run_sync(
            lambda s: MeasurementUnit.__table__.create(s.connection(), checkfirst=True)
        )
        for name, group in _BUILTIN_UNITS:
            session.add(MeasurementUnit(name=name, group_name=group, is_builtin=True))
        try:
            await session.commit()
        except _UnitIntegrityError:
            await session.rollback()
    _units_ready = True


@router.get("/units", response_model=list[UnitOut])
async def list_units(session: AsyncSession = Depends(get_db_session)) -> list[MeasurementUnit]:
    await _ensure_units_table(session)
    result = await session.execute(
        select(MeasurementUnit).order_by(MeasurementUnit.is_builtin.desc(), MeasurementUnit.created_at)
    )
    return list(result.scalars().all())


@router.post("/units", response_model=UnitOut, status_code=status.HTTP_201_CREATED)
async def create_unit(
    body: UnitCreate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> MeasurementUnit:
    await _ensure_units_table(session)
    name = body.name.strip()
    dup = await session.execute(
        select(MeasurementUnit).where(func.lower(MeasurementUnit.name) == name.lower())
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Unit '{name}' already exists")
    unit = MeasurementUnit(name=name, group_name="Custom", is_builtin=False)
    session.add(unit)
    await session.commit()
    await session.refresh(unit)
    return unit


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    unit_id: uuid.UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("manage_products")),
) -> None:
    await _ensure_units_table(session)
    unit = await session.get(MeasurementUnit, unit_id)
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found")
    in_use = (
        await session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.size.op("~*")(r"\d\s*" + re.escape(unit.name) + r"\s*$"))
        )
    ).scalar_one()
    if in_use and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"{in_use} product(s) still use '{unit.name}'.",
        )
    await session.delete(unit)
    await session.commit()
