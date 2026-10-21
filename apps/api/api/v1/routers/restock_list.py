"""The Restock List -- a persistent "shopping list" for the store
manager, separate from the live/computed Low Stock & Out of Stock
alert on reports.py::low_stock_report(). See models.RestockListEntry
for the full design note on why entries are lazily created and
self-removing.

Feature request, restated and confirmed with the person before
building: items land here automatically when low/out of stock,
manually from the Inventory Catalog's checkbox-select flow, or typed
in directly as a brand-new item (name + optional photo) that isn't in
the catalog at all. Each gets a quantity + unit (pieces/dozens/
cartons/half dozen/half carton). Downloading a PDF for a chosen
supplier removes the downloaded items from the list entirely --
whatever wasn't selected stays put. The only other way an entry
leaves the list is an actual restock (stock rising back above reorder
level) or an explicit delete.
"""
import io
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db_session, require_permission
from api.v1.routers.catalog import _PUBLIC_BASE_URL
from api.v1.schemas.restock_list import (
    RestockDownloadRequest,
    RestockEntrySendOut,
    RestockItemAdd,
    RestockItemUpdate,
    RestockListRowOut,
    UNIT_LABELS,
)
from infrastructure.db.models import (
    Product,
    RestockListEntry,
    RestockListEntrySend,
    StockBatch,
    Supplier,
    User,
)

router = APIRouter(prefix="/restock-list", tags=["restock-list"])

_VIEW = require_permission("view_restock_list")
_MANAGE = require_permission("manage_restock_list")
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_IMAGE_DIR = Path("static/uploads/restock_list")


async def _availability_by_product(session: AsyncSession, branch_id: uuid.UUID) -> dict[uuid.UUID, int]:
    today = date.today()
    stmt = (
        select(StockBatch.product_id, func.sum(StockBatch.quantity_remaining).label("qty"))
        .where(
            StockBatch.branch_id == branch_id,
            StockBatch.quantity_remaining > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date > today),
        )
        .group_by(StockBatch.product_id)
    )
    result = await session.execute(stmt)
    return {row.product_id: row.qty for row in result.all()}


def _send_out(send: RestockListEntrySend) -> RestockEntrySendOut:
    return RestockEntrySendOut(
        id=send.id,
        supplier_name=send.supplier_name,
        quantity_sent=send.quantity_sent,
        unit_label_sent=send.unit_label_sent,
        sent_by_name=send.sent_by_user.full_name if getattr(send, "sent_by_user", None) else None,
        sent_at=send.sent_at,
    )


@router.get("", response_model=list[RestockListRowOut])
async def get_restock_list(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_VIEW),
) -> list[RestockListRowOut]:
    availability = await _availability_by_product(session, branch_id)

    entries_result = await session.execute(
        select(RestockListEntry)
        .options(
            selectinload(RestockListEntry.sends).selectinload(RestockListEntrySend.sent_by_user),
            selectinload(RestockListEntry.product),
        )
        .where(RestockListEntry.branch_id == branch_id)
    )
    entries = list(entries_result.scalars().all())
    # Freeform entries (product_id is None) can't share the
    # product_id-keyed dict below -- there could be several of them.
    catalog_entries = [e for e in entries if e.product_id is not None]
    freeform_entries = [e for e in entries if e.product_id is None]
    entries_by_product = {e.product_id: e for e in catalog_entries}

    # Self-healing cleanup, same pattern as the out_of_stock_alert_muted
    # auto-clear in reports.py::low_stock_report(): an auto-sourced
    # entry (is_manual=False) whose product has actually been
    # restocked above its reorder level no longer belongs on the
    # list, so it's deleted right here on read rather than needing a
    # background job to keep the list in sync with real stock.
    # Freeform entries never go through this -- there's no stock level
    # to auto-remove them by; only an explicit delete takes them off.
    removed_any = False
    for entry in list(catalog_entries):
        available = availability.get(entry.product_id, 0)
        if not entry.is_manual and available > entry.product.reorder_level:
            catalog_entries.remove(entry)
            del entries_by_product[entry.product_id]
            await session.delete(entry)
            removed_any = True
    if removed_any:
        await session.commit()

    # Every product that's currently low/out of stock, plus every
    # product with a manual entry (even if well-stocked).
    product_ids = set(entries_by_product.keys())
    low_stock_conditions = [Product.is_deleted.is_(False)]
    # Same hidden_from_staff exclusion as GET /catalog/products and the
    # Low Stock report -- a staff caller's Restock List must never
    # surface a product a super admin deliberately hid from staff.
    if current_user.role == "staff":
        low_stock_conditions.append(Product.hidden_from_staff.is_(False))
    products_result = await session.execute(
        select(Product)
        .options(selectinload(Product.category), selectinload(Product.images))
        .where(*low_stock_conditions)
    )
    # CUSTOMER-ORDER-DUPLICATE-FIX: a draft product made for a waiting
    # customer deposit order already has its own typed row (with the
    # customer badge), so it must not also appear as an automatic
    # out-of-stock row.
    from infrastructure.db.models import CustomerRequest as _CustReq
    _cust_rows = await session.execute(
        select(_CustReq.product_id).where(
            _CustReq.status == "pending",
            _CustReq.branch_id == branch_id,
            _CustReq.product_id.is_not(None),
        )
    )
    _customer_order_pids = {pid for (pid,) in _cust_rows.all()}
    rows: list[RestockListRowOut] = []
    for product in products_result.scalars().all():
        available = availability.get(product.id, 0)
        is_low = available <= product.reorder_level
        entry = entries_by_product.get(product.id)
        if product.id in _customer_order_pids and (entry is None or not entry.is_manual):
            continue
        if not is_low and entry is None:
            continue  # not low/out of stock and never manually added -- not on the list
        reason = "out_of_stock" if available <= 0 else ("low_stock" if is_low else "manual")
        primary_image = next((img for img in product.images if img.is_primary), None)
        image_url = primary_image.url if primary_image else (product.images[0].url if product.images else None)
        rows.append(
            RestockListRowOut(
                entry_id=entry.id if entry else None,
                product_id=product.id,
                product_name=product.name,
                category_name=product.category.name if product.category else None,
                image_url=image_url,
                quantity_available=available,
                reorder_level=product.reorder_level,
                reason=reason,
                quantity=entry.quantity if entry else 1,
                unit_label=entry.unit_label if entry else "pieces",
                sends=[_send_out(s) for s in entry.sends] if entry else [],
            )
        )
    rows.sort(key=lambda r: r.product_name.lower())

    # Freeform items -- typed straight onto the list, no Product to
    # merge against, so they're appended as-is (still alphabetically
    # folded in relative to the catalog rows above).
    freeform_rows = [
        RestockListRowOut(
            entry_id=entry.id,
            product_id=None,
            product_name=entry.custom_name or "(unnamed item)",
            category_name=None,
            image_url=entry.custom_image_url,
            quantity_available=None,
            reorder_level=None,
            reason="custom",
            quantity=entry.quantity,
            unit_label=entry.unit_label,
            sends=[_send_out(s) for s in entry.sends],
        )
        for entry in freeform_entries
    ]
    rows = sorted(rows + freeform_rows, key=lambda r: r.product_name.lower())
    return rows


@router.post("/items", response_model=RestockListRowOut, status_code=status.HTTP_201_CREATED)
async def add_or_touch_restock_item(
    body: RestockItemAdd,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_MANAGE),
) -> RestockListRowOut:
    if body.unit_label not in UNIT_LABELS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown unit_label '{body.unit_label}'")
    if not body.product_id and not (body.custom_name and body.custom_name.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Give either product_id (from the catalog) or custom_name (a freeform item).",
        )

    if not body.product_id:
        # Freeform item -- typed straight onto the list, no matching
        # Product. Always a fresh row (no upsert-by-name -- two
        # different real items could coincidentally share a typed
        # name), always manual (there's no stock level to auto-remove
        # it by).
        entry = RestockListEntry(
            product_id=None,
            custom_name=body.custom_name.strip(),
            custom_image_url=body.custom_image_url,
            branch_id=body.branch_id,
            is_manual=True,
            quantity=body.quantity,
            unit_label=body.unit_label,
            added_by=current_user.id,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return RestockListRowOut(
            entry_id=entry.id,
            product_id=None,
            product_name=entry.custom_name,
            category_name=None,
            image_url=entry.custom_image_url,
            quantity_available=None,
            reorder_level=None,
            reason="custom",
            quantity=entry.quantity,
            unit_label=entry.unit_label,
            sends=[],
        )

    product_result = await session.execute(
        select(Product).options(selectinload(Product.category)).where(Product.id == body.product_id)
    )
    product = product_result.scalar_one_or_none()
    if product is None or product.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    existing = await session.execute(
        select(RestockListEntry).where(
            RestockListEntry.product_id == body.product_id,
            RestockListEntry.branch_id == body.branch_id,
        )
    )
    entry = existing.scalar_one_or_none()
    if entry is None:
        entry = RestockListEntry(
            product_id=body.product_id,
            branch_id=body.branch_id,
            is_manual=body.is_manual,
            quantity=body.quantity,
            unit_label=body.unit_label,
            added_by=current_user.id,
        )
        session.add(entry)
    else:
        # Never downgrade an entry from manual back to auto here --
        # only DELETE removes a manually-added item.
        if body.is_manual:
            entry.is_manual = True
        entry.quantity = body.quantity
        entry.unit_label = body.unit_label

    await session.commit()
    await session.refresh(entry)

    availability = await _availability_by_product(session, body.branch_id)
    available = availability.get(product.id, 0)
    is_low = available <= product.reorder_level
    reason = "out_of_stock" if available <= 0 else ("low_stock" if is_low else "manual")
    return RestockListRowOut(
        entry_id=entry.id,
        product_id=product.id,
        product_name=product.name,
        category_name=product.category.name if product.category else None,
        image_url=None,  # frontend already has the image from the catalog/list fetch
        quantity_available=available,
        reorder_level=product.reorder_level,
        reason=reason,
        quantity=entry.quantity,
        unit_label=entry.unit_label,
        sends=[],
    )


@router.patch("/items/{entry_id}", response_model=RestockListRowOut)
async def update_restock_item(
    entry_id: uuid.UUID,
    body: RestockItemUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> RestockListRowOut:
    result = await session.execute(
        select(RestockListEntry)
        .options(
            selectinload(RestockListEntry.sends).selectinload(RestockListEntrySend.sent_by_user),
            selectinload(RestockListEntry.product).selectinload(Product.category),
        )
        .where(RestockListEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Restock list entry not found")
    if body.unit_label is not None:
        if body.unit_label not in UNIT_LABELS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown unit_label '{body.unit_label}'")
        entry.unit_label = body.unit_label
    if body.quantity is not None:
        entry.quantity = body.quantity
    await session.commit()

    if entry.product_id is None:
        # Freeform item -- no stock/reorder concept at all.
        return RestockListRowOut(
            entry_id=entry.id,
            product_id=None,
            product_name=entry.custom_name or "(unnamed item)",
            category_name=None,
            image_url=entry.custom_image_url,
            quantity_available=None,
            reorder_level=None,
            reason="custom",
            quantity=entry.quantity,
            unit_label=entry.unit_label,
            sends=[_send_out(s) for s in entry.sends],
        )

    availability = await _availability_by_product(session, entry.branch_id)
    available = availability.get(entry.product_id, 0)
    product = entry.product
    is_low = available <= product.reorder_level
    reason = "out_of_stock" if available <= 0 else ("low_stock" if is_low else "manual")
    return RestockListRowOut(
        entry_id=entry.id,
        product_id=product.id,
        product_name=product.name,
        category_name=product.category.name if product.category else None,
        image_url=None,
        quantity_available=available,
        reorder_level=product.reorder_level,
        reason=reason,
        quantity=entry.quantity,
        unit_label=entry.unit_label,
        sends=[_send_out(s) for s in entry.sends],
    )


@router.delete("/items/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_restock_item(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> None:
    entry = await session.get(RestockListEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Restock list entry not found")
    await session.delete(entry)
    await session.commit()


def _unit_display(unit_label: str, quantity: int) -> str:
    labels = {
        "pieces": "pieces", "dozens": "dozens", "cartons": "cartons", "bags": "bags",
        "half_dozen": "half dozen", "half_carton": "half carton",
    }
    if unit_label in ("half_dozen", "half_carton"):
        return labels[unit_label]
    return f"{quantity} {labels.get(unit_label, unit_label)}"


def _render_restock_pdf(rows: list[dict], supplier_display_name: str | None) -> bytes:
    # fpdf2 -- same small pure-Python PDF dependency statements.py
    # already uses, so no new system libraries are needed to deploy
    # this.
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Restock Request", ln=1)
    pdf.set_font("Helvetica", "", 10)
    # Supplier is optional -- per the person's correction, this PDF is
    # useful even before you've decided who it's going to (e.g. you
    # print it, then hand-address it to whichever supplier has stock).
    if supplier_display_name:
        pdf.cell(0, 6, f"To: {supplier_display_name}", ln=1)
    pdf.cell(0, 6, f"Date: {date.today().strftime('%d %b %Y')}", ln=1)
    pdf.ln(4)

    row_h = 18
    for row in rows:
        y = pdf.get_y()
        if y + row_h > 280:
            pdf.add_page()
            y = pdf.get_y()
        if row.get("local_image_path"):
            try:
                pdf.image(row["local_image_path"], x=12, y=y + 1, w=16, h=16)
            except Exception:
                pass
        pdf.set_xy(32, y)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, row["product_name"], ln=1)
        pdf.set_x(32)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.cell(0, 6, f"{row['category_name'] or ''}  ·  {row['quantity_display']}", ln=1)
        pdf.set_draw_color(225, 225, 225)
        pdf.line(12, y + row_h - 3, 198, y + row_h - 3)
        pdf.set_y(y + row_h)

    if not rows:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 8, "No items selected.", ln=1)

    return bytes(pdf.output())


@router.post("/download")
async def download_restock_list(
    body: RestockDownloadRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_MANAGE),
) -> StreamingResponse:
    if not body.entry_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No items selected")
    result = await session.execute(
        select(RestockListEntry)
        .options(selectinload(RestockListEntry.product).selectinload(Product.images))
        .where(RestockListEntry.id.in_(body.entry_ids))
    )
    entries = list(result.scalars().all())
    found_ids = {e.id for e in entries}
    missing = set(body.entry_ids) - found_ids
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Entries not found: {sorted(str(m) for m in missing)}")

    # Supplier is optional (person's explicit correction: don't force
    # a name before you can download). When one IS typed and it
    # doesn't match an existing supplier (case-insensitively), create
    # it -- this is the shared `suppliers` table purchasing.py's
    # /suppliers endpoints and the Invoice page's supplier picker
    # already read from, so a brand-new name typed here shows up
    # there too, not just inside this feature.
    typed_name = (body.supplier_name or "").strip()
    supplier_display_name: str | None = None
    if typed_name:
        existing_supplier = await session.execute(
            select(Supplier).where(
                func.lower(Supplier.name) == typed_name.lower(),
                Supplier.is_deleted.is_(False),
            )
        )
        supplier = existing_supplier.scalar_one_or_none()
        if supplier is None:
            supplier = Supplier(name=typed_name)
            session.add(supplier)
            await session.flush()
        supplier_display_name = supplier.name

    pdf_rows = []
    for entry in entries:
        product = entry.product
        if product is not None:
            primary_image = next((img for img in product.images if img.is_primary), None) if product.images else None
            image_url = primary_image.url if primary_image else (product.images[0].url if product.images else None)
            item_name = product.name
        else:
            # Freeform item -- its own custom_image_url stands in for
            # a catalog product's photo.
            image_url = entry.custom_image_url
            item_name = entry.custom_name or "(unnamed item)"
        local_path = None
        if image_url and image_url.startswith(_PUBLIC_BASE_URL):
            local_path = image_url.replace(f"{_PUBLIC_BASE_URL}/", "", 1)
        pdf_rows.append({
            "product_name": item_name,
            "category_name": None,
            "quantity_display": _unit_display(entry.unit_label, entry.quantity),
            "local_image_path": local_path,
        })

    pdf_bytes = _render_restock_pdf(pdf_rows, supplier_display_name)

    # Per the person's explicit correction: downloading takes the
    # selected items OFF the Restock List entirely (not just tagging
    # them "sent") -- whatever wasn't ticked stays exactly where it
    # was. If the same item legitimately needs to go to a second
    # supplier later, re-add it from the Inventory Catalog's checkbox
    # flow -- that's simpler than tracking a "sent, but still active"
    # state on the list itself.
    # Customer orders attached to these entries survive the delete below
    # (their FK is ON DELETE SET NULL); just stamp when they went out.
    from api.v1.routers.customer_requests import mark_requests_ordered

    await mark_requests_ordered(session, [e.id for e in entries])
    for entry in entries:
        await session.delete(entry)
    await session.commit()

    slug = typed_name.replace(" ", "-").lower() if typed_name else "list"
    filename = f"restock-{slug}-{date.today().isoformat()}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/upload-image")
async def upload_restock_item_image(
    file: UploadFile = File(...),
    _: User = Depends(_MANAGE),
) -> dict:
    """For the "type in a brand-new item" flow -- a photo for a
    freeform item that has no catalog Product (and so no product
    image gallery) to attach a photo to. Same pattern as
    purchase_invoices.py's upload-image endpoint.
    """
    original_name = file.filename or "restock-item"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}",
        )
    _IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _IMAGE_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"url": f"{_PUBLIC_BASE_URL}/static/uploads/restock_list/{new_filename}"}
# Customer pre-orders (deposits) are served under /restock-list/customer-requests.
from api.v1.routers.customer_requests import router as _customer_requests_router  # noqa: E402

router.include_router(_customer_requests_router)
