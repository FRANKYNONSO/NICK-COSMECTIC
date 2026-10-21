"""Excel export/import for sales records.

Import here is a data-migration tool for backfilling historical sales
(e.g. from a spreadsheet or a previous system) -- it does NOT run
these through the live checkout/FIFO stock-deduction flow. Each row
becomes one Sale + one SaleItem directly, with no effect on current
stock levels. This is deliberate: replaying old sales through the
live FIFO logic could fail (batches from that period may no longer
exist) or incorrectly deduct today's stock for a sale that happened
months ago. If you need to record a sale that SHOULD deduct current
stock, use the POS or storefront checkout instead of this importer.
"""
import io
import uuid
from datetime import datetime

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.db.models import Branch, Customer, Product, Sale, SaleItem
from nickcosmetics_core.domain.money import Money

SALES_COLUMNS = [
    "sale_date", "branch", "channel", "payment_method", "customer_email",
    "customer_name", "variant_sku", "quantity", "unit_price_naira",
    "line_total_naira", "sale_total_naira",
]


async def export_sales_workbook(session: AsyncSession) -> bytes:
    stmt = (
        select(Sale)
        .options(selectinload(Sale.items))
        .order_by(Sale.created_at.desc())
    )
    result = await session.execute(stmt)
    sales = list(result.scalars().all())

    branch_ids = {s.branch_id for s in sales}
    branches = {}
    if branch_ids:
        branch_result = await session.execute(select(Branch).where(Branch.id.in_(branch_ids)))
        branches = {b.id: b.name for b in branch_result.scalars().all()}

    customer_ids = {s.customer_id for s in sales if s.customer_id}
    customers = {}
    if customer_ids:
        customer_result = await session.execute(select(Customer).where(Customer.id.in_(customer_ids)))
        customers = {c.id: c for c in customer_result.scalars().all()}

    product_ids = {item.product_id for s in sales for item in s.items}
    variants = {}
    if product_ids:
        variant_result = await session.execute(
            select(Product).where(Product.id.in_(product_ids))
        )
        variants = {v.id: v.sku for v in variant_result.scalars().all()}

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(SALES_COLUMNS)

    for sale in sales:
        customer = customers.get(sale.customer_id) if sale.customer_id else None
        for item in sale.items:
            ws.append([
                sale.created_at.strftime("%Y-%m-%d") if sale.created_at else "",
                branches.get(sale.branch_id, ""),
                sale.channel,
                sale.payment_method or "",
                customer.email if customer else "",
                customer.full_name if customer else "",
                variants.get(item.product_id, ""),
                item.quantity,
                Money(item.unit_price_kobo).naira,
                Money(item.line_total_kobo).naira,
                Money(sale.total_kobo).naira,
            ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_sales_import_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(SALES_COLUMNS)
    ws.append([
        "2026-01-15", "Main Branch", "pos", "cash", "", "Walk-in customer",
        "SER-001-30ML", 1, 12500, 12500, 12500,
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def import_sales_workbook(session: AsyncSession, file_bytes: bytes) -> dict:
    """Each row becomes one historical Sale + one SaleItem, with no
    stock impact -- see the module docstring. One bad row is recorded
    in `errors` and skipped rather than aborting the whole file."""
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        return {"created": 0, "updated": 0, "errors": [f"Could not read file as .xlsx: {exc}"]}

    ws = wb.active
    header = [c.value for c in ws[1]]
    col_index = {name: idx for idx, name in enumerate(header)}
    required = ["sale_date", "branch", "variant_sku", "quantity", "unit_price_naira"]
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
    errors: list[str] = []
    branch_cache: dict = {}
    variant_cache: dict = {}

    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if row is None or all(v is None for v in row):
            continue
        try:
            branch_name = str(cell(row, "branch", "")).strip()
            variant_sku = str(cell(row, "variant_sku", "")).strip()
            quantity = cell(row, "quantity")
            unit_price = cell(row, "unit_price_naira")
            sale_date_raw = cell(row, "sale_date")

            if not branch_name or not variant_sku:
                errors.append(f"Row {i}: branch and variant_sku are required.")
                continue
            if quantity is None or unit_price is None:
                errors.append(f"Row {i}: quantity and unit_price_naira are required.")
                continue

            branch_key = branch_name.lower()
            if branch_key not in branch_cache:
                branch_result = await session.execute(select(Branch).where(Branch.name == branch_name))
                branch_cache[branch_key] = branch_result.scalar_one_or_none()
            branch = branch_cache[branch_key]
            if branch is None:
                errors.append(f"Row {i}: no branch named '{branch_name}' found.")
                continue

            if variant_sku not in variant_cache:
                variant_result = await session.execute(
                    select(Product).where(Product.sku == variant_sku)
                )
                variant_cache[variant_sku] = variant_result.scalar_one_or_none()
            variant = variant_cache[variant_sku]
            if variant is None:
                errors.append(f"Row {i}: no product variant with SKU '{variant_sku}' found.")
                continue

            customer_id = None
            customer_email = str(cell(row, "customer_email", "") or "").strip()
            if customer_email:
                cust_result = await session.execute(select(Customer).where(Customer.email == customer_email))
                customer = cust_result.scalar_one_or_none()
                if customer is None:
                    customer = Customer(
                        full_name=str(cell(row, "customer_name", "") or "") or None,
                        email=customer_email,
                    )
                    session.add(customer)
                    await session.flush()
                customer_id = customer.id

            if isinstance(sale_date_raw, datetime):
                created_at = sale_date_raw
            else:
                try:
                    created_at = datetime.strptime(str(sale_date_raw), "%Y-%m-%d")
                except ValueError:
                    errors.append(f"Row {i}: sale_date '{sale_date_raw}' isn't a valid date (expected YYYY-MM-DD).")
                    continue

            quantity = int(quantity)
            unit_price_kobo = Money.from_naira(float(unit_price)).kobo
            line_total_kobo = unit_price_kobo * quantity

            sale = Sale(
                branch_id=branch.id,
                customer_id=customer_id,
                channel=str(cell(row, "channel", "") or "pos"),
                status="completed",
                subtotal_kobo=line_total_kobo,
                discount_kobo=0,
                total_kobo=line_total_kobo,
                payment_method=str(cell(row, "payment_method", "") or None) or None,
                local_ref_id=f"import-{uuid.uuid4()}",
                created_at=created_at,
            )
            session.add(sale)
            await session.flush()

            sale_item = SaleItem(
                sale_id=sale.id,
                product_id=variant.id,
                quantity=quantity,
                unit_price_kobo=unit_price_kobo,
                line_total_kobo=line_total_kobo,
            )
            session.add(sale_item)
            created += 1
        except Exception as exc:
            errors.append(f"Row {i}: {exc}")

    await session.commit()
    return {"created": created, "updated": 0, "errors": errors}
