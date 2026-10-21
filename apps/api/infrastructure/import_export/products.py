"""Excel export/import for the product catalog.

Kept out of catalog.py so that file stays focused on CRUD; this module
owns the .xlsx <-> Product mapping used by the web_admin Import/Export
page.

Update spec ("nick-cosmetics-update-spec-3.md", Sections 1-3 & 6)
retired the variants concept: one row is one Product, matched on
import by `sku` (unique in the DB) -- a matching SKU updates that
product, a new SKU creates one.
"""
import io

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.db.models import Brand, Category, Product
from nickcosmetics_core.domain.money import Money

PRODUCT_COLUMNS = [
    "sku", "product_name", "byproduct", "description", "category", "brand",
    "size", "status", "unit_of_measure", "barcode",
    "cost_price_naira", "selling_price_naira", "reorder_level", "expiration_date",
]


async def export_products_workbook(session: AsyncSession) -> bytes:
    stmt = (
        select(Product)
        .where(Product.is_deleted.is_(False))
        .options(selectinload(Product.category), selectinload(Product.brand))
        .order_by(Product.name)
    )
    result = await session.execute(stmt)
    products = list(result.scalars().all())

    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(PRODUCT_COLUMNS)

    for product in products:
        ws.append([
            product.sku,
            product.name,
            product.byproduct or "",
            product.description or "",
            product.category.name if product.category else "",
            product.brand.name if product.brand else "",
            product.size or "",
            product.status,
            product.unit_of_measure,
            product.barcode or "",
            Money(product.cost_price_kobo).naira,
            Money(product.selling_price_kobo).naira,
            product.reorder_level,
            product.expiration_date.isoformat() if product.expiration_date else "",
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_product_import_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(PRODUCT_COLUMNS)
    ws.append([
        "SER-001", "Nick Cosmetics Radiance Vitamin C Serum (30ml)", "Radiance Vitamin C Serum (30ml)",
        "Brightening daily serum", "Skincare", "Nick Cosmetics", "Medium",
        "active", "each", "", 4000, 12500, 5, "",
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _get_or_create_category(session: AsyncSession, name: str, cache: dict) -> "Category | None":
    if not name:
        return None
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    result = await session.execute(select(Category).where(Category.name == name.strip()))
    category = result.scalar_one_or_none()
    if category is None:
        category = Category(name=name.strip())
        session.add(category)
        await session.flush()
    cache[key] = category
    return category


async def _get_or_create_brand(session: AsyncSession, name: str, cache: dict) -> "Brand | None":
    if not name:
        return None
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    result = await session.execute(select(Brand).where(Brand.name == name.strip()))
    brand = result.scalar_one_or_none()
    if brand is None:
        brand = Brand(name=name.strip())
        session.add(brand)
        await session.flush()
    cache[key] = brand
    return brand


async def import_products_workbook(session: AsyncSession, file_bytes: bytes) -> dict:
    """Returns {"created": int, "updated": int, "errors": list[str]}.

    Each row is handled independently -- one bad row is recorded in
    `errors` and skipped, it doesn't abort the rest of the file. Rows
    are 1-indexed as shown in Excel (row 1 is the header), so error
    messages reference the same row numbers the person sees when they
    open the file.
    """
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        return {"created": 0, "updated": 0, "errors": [f"Could not read file as .xlsx: {exc}"]}

    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    header = [c.value for c in ws[1]]
    col_index = {name: idx for idx, name in enumerate(header)}
    required = ["sku", "product_name", "cost_price_naira", "selling_price_naira"]
    missing_cols = [c for c in required if c not in col_index]
    if missing_cols:
        return {
            "created": 0, "updated": 0,
            "errors": [f"Missing required column(s): {', '.join(missing_cols)}"],
        }

    def cell(row, name, default=None):
        idx = col_index.get(name)
        if idx is None or idx >= len(row):
            return default
        val = row[idx]
        return val if val is not None else default

    created = 0
    updated = 0
    errors: list[str] = []
    category_cache: dict = {}
    brand_cache: dict = {}

    for i, row in enumerate(rows, start=2):
        if row is None or all(v is None for v in row):
            continue
        try:
            sku = str(cell(row, "sku", "")).strip()
            product_name = str(cell(row, "product_name", "")).strip()
            cost_naira = cell(row, "cost_price_naira")
            selling_naira = cell(row, "selling_price_naira")

            if not sku or not product_name:
                errors.append(f"Row {i}: sku and product_name are both required.")
                continue
            if cost_naira is None or selling_naira is None:
                errors.append(f"Row {i}: cost_price_naira and selling_price_naira are required.")
                continue

            category_name = str(cell(row, "category", "") or "").strip()
            brand_name = str(cell(row, "brand", "") or "").strip()
            category = await _get_or_create_category(session, category_name, category_cache)
            brand = await _get_or_create_brand(session, brand_name, brand_cache)

            size = str(cell(row, "size", "") or "").strip() or None
            if size not in ("Big", "Medium", "Small", None):
                errors.append(f"Row {i}: size must be Big, Medium, or Small (or left blank).")
                continue

            expiration_raw = cell(row, "expiration_date", "")
            expiration_date = None
            if expiration_raw:
                if hasattr(expiration_raw, "date"):
                    expiration_date = expiration_raw.date()
                else:
                    from datetime import date as _date
                    expiration_date = _date.fromisoformat(str(expiration_raw).strip())

            result = await session.execute(select(Product).where(Product.sku == sku))
            product = result.scalar_one_or_none()

            if product is not None:
                product.name = product_name
                product.byproduct = str(cell(row, "byproduct", "") or "") or product.byproduct
                product.description = str(cell(row, "description", "") or "") or product.description
                product.status = str(cell(row, "status", "") or product.status)
                product.unit_of_measure = str(cell(row, "unit_of_measure", "") or product.unit_of_measure)
                product.barcode = str(cell(row, "barcode", "") or "") or product.barcode
                product.size = size or product.size
                product.expiration_date = expiration_date or product.expiration_date
                if category is not None:
                    product.category_id = category.id
                if brand is not None:
                    product.brand_id = brand.id
                product.cost_price_kobo = Money.from_naira(float(cost_naira)).kobo
                product.selling_price_kobo = Money.from_naira(float(selling_naira)).kobo
                reorder = cell(row, "reorder_level")
                if reorder is not None:
                    product.reorder_level = int(reorder)
                updated += 1
            else:
                product = Product(
                    sku=sku,
                    name=product_name,
                    byproduct=str(cell(row, "byproduct", "") or "") or None,
                    description=str(cell(row, "description", "") or "") or None,
                    status=str(cell(row, "status", "") or "active"),
                    unit_of_measure=str(cell(row, "unit_of_measure", "") or "each"),
                    barcode=str(cell(row, "barcode", "") or "") or None,
                    size=size,
                    expiration_date=expiration_date,
                    category_id=category.id if category else None,
                    brand_id=brand.id if brand else None,
                    cost_price_kobo=Money.from_naira(float(cost_naira)).kobo,
                    selling_price_kobo=Money.from_naira(float(selling_naira)).kobo,
                    reorder_level=int(cell(row, "reorder_level", 5) or 5),
                )
                session.add(product)
                created += 1
        except Exception as exc:
            errors.append(f"Row {i}: {exc}")

    await session.commit()
    return {"created": created, "updated": updated, "errors": errors}
