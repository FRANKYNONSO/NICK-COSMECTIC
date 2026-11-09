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
from sqlalchemy import func, select, text
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
_MANAGE = require_permission("manage_inventory")  # PATCHP2A-RESTOCK-MANAGE
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
        request_no=send.request_no,
        received_at=send.received_at,
    )


def _clean_note(value: str | None) -> str | None:
    """Per-item note for the supplier: trimmed, capped at 500 characters,
    and an empty note is stored as no note at all."""
    return (value or "").strip()[:500] or None


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
    # RESTOCK-STAYS-ON-LIST: a manually-added catalog entry (Inventory's
    # "Add to restock list") also leaves once it has really been restocked
    # above its reorder level, i.e. stock was received AFTER it was added.
    # An item added while already well stocked stays until removed.
    _manual_restocked: set = set()
    _manual_check = [
        e for e in catalog_entries
        if e.is_manual
        and getattr(e, "created_at", None) is not None
        and availability.get(e.product_id, 0) > e.product.reorder_level
    ]
    if _manual_check:
        from datetime import timezone as _tz

        def _utc(dt):
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=_tz.utc)

        _latest_rows = await session.execute(
            select(StockBatch.product_id, func.max(StockBatch.received_at))
            .where(
                StockBatch.branch_id == branch_id,
                StockBatch.product_id.in_([e.product_id for e in _manual_check]),
            )
            .group_by(StockBatch.product_id)
        )
        _latest = {pid: ts for pid, ts in _latest_rows.all() if ts is not None}
        for e in _manual_check:
            ts = _latest.get(e.product_id)
            if ts is not None and _utc(ts) >= _utc(e.created_at):
                _manual_restocked.add(e.id)
    for entry in list(catalog_entries):
        available = availability.get(entry.product_id, 0)
        if (not entry.is_manual and available > entry.product.reorder_level) or entry.id in _manual_restocked:
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
    if current_user.role in ("cashier", "store_assistant"):  # PATCHP2E-HIDDEN-ROLES
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
    # RESTOCK-NEVER-CARRIED-FIX (patch 25a): products that never had any
    # stock batch row at this branch are "Not carried at this branch" on
    # the Products screen (same rule as catalog.py
    # _load_stocked_product_ids), so they must not show here as an
    # automatic Out of stock / Low stock row. Manual entries stay.
    _carried_rows = await session.execute(
        select(StockBatch.product_id).where(StockBatch.branch_id == branch_id).distinct()
    )
    _carried_pids = {pid for (pid,) in _carried_rows.all()}
    rows: list[RestockListRowOut] = []
    for product in products_result.scalars().all():
        available = availability.get(product.id, 0)
        is_low = available <= product.reorder_level
        entry = entries_by_product.get(product.id)
        if product.id in _customer_order_pids and (entry is None or not entry.is_manual):
            continue
        if product.id not in _carried_pids and (entry is None or not entry.is_manual):
            continue  # never carried at this branch (patch 25a)
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
                note=entry.note if entry else None,
            )
        )
    rows.sort(key=lambda r: r.product_name.lower())

    # RESTOCK-TYPED-DUPLICATE-FIX (patch 23a): a typed row (name only, no
    # product link) must leave the list once a real product with the same
    # name has stock in this branch (shelf or warehouse). Otherwise the
    # product shows twice when it later runs low. A typed row whose customer
    # order is still pending is never removed.
    if freeform_entries:
        def _norm_name(s):
            return " ".join((s or "").lower().split())

        _prod_rows = await session.execute(
            select(Product.id, Product.name).where(Product.is_deleted.is_(False))
        )
        _wh_rows = await session.execute(
            text(
                "select product_id from warehouse_stock "
                "where branch_id = :b and quantity_pieces > 0"
            ),
            {"b": branch_id},
        )
        _wh_pids = {pid for (pid,) in _wh_rows.all()}
        _stocked_names = set()
        for _pid, _pname in _prod_rows.all():
            if availability.get(_pid, 0) > 0 or _pid in _wh_pids:
                _stocked_names.add(_norm_name(_pname))
        _waiting_rows = await session.execute(
            select(_CustReq.restock_list_entry_id).where(
                _CustReq.branch_id == branch_id,
                _CustReq.status == "pending",
                _CustReq.restock_list_entry_id.is_not(None),
            )
        )
        _waiting_entry_ids = {eid for (eid,) in _waiting_rows.all()}
        _dup_removed = False
        for _entry in list(freeform_entries):
            if _entry.id in _waiting_entry_ids:
                continue
            if _norm_name(_entry.custom_name) in _stocked_names:
                freeform_entries.remove(_entry)
                await session.delete(_entry)
                _dup_removed = True
        if _dup_removed:
            await session.commit()

    # RESTOCK-STAYS-ON-LIST: a typed row tied to a customer order leaves
    # only once that order is marked arrived (or collected) on the
    # Customer Orders page. Until then it stays, even after a download.
    if freeform_entries:
        _arrived_rows = await session.execute(
            select(_CustReq.restock_list_entry_id).where(
                _CustReq.restock_list_entry_id.in_([e.id for e in freeform_entries]),
                _CustReq.status.in_(("arrived", "collected")),
            )
        )
        _arrived_entry_ids = {eid for (eid,) in _arrived_rows.all()}
        if _arrived_entry_ids:
            for _entry in list(freeform_entries):
                if _entry.id in _arrived_entry_ids:
                    freeform_entries.remove(_entry)
                    await session.delete(_entry)
            await session.commit()

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
            note=entry.note,
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
            note=_clean_note(body.note),
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
            note=entry.note,
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
            note=_clean_note(body.note),
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
        if body.note is not None:
            entry.note = _clean_note(body.note)

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
        note=entry.note,
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
    if body.note is not None:
        entry.note = _clean_note(body.note)
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
            note=entry.note,
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
        note=entry.note,
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


import re as _re
from datetime import datetime as _datetime

# Every item you tick on the Restock List is printed on the PDF -- nothing is
# hidden by name. (This pattern never matches; it is kept only so the code that
# uses it keeps working.)
_PLACEHOLDER_NAME_RE = _re.compile(r"(?!)")


def _unit_parts(unit_label: str, quantity: int, product=None) -> tuple[str, str]:
    """Returns (qty_text, unit_text), e.g. ("5", "cartons") or ("1", "piece").
    Singular when the quantity is exactly 1. Pack sizes (pieces per dozen,
    carton or bag) are deliberately NOT printed on the PDF."""
    qty = int(quantity or 0)
    one = qty == 1
    names = {
        "pieces": ("piece", "pieces"),
        "dozens": ("dozen", "dozens"),
        "cartons": ("carton", "cartons"),
        "bags": ("bag", "bags"),
    }
    if unit_label in names:
        return str(qty), names[unit_label][0 if one else 1]
    if unit_label == "half_dozen":
        return str(qty), "half dozen"
    if unit_label == "half_carton":
        return str(qty), "half carton"
    return str(qty), str(unit_label).replace("_", " ")


def _unit_display(unit_label: str, quantity: int) -> str:
    qty_text, unit_text = _unit_parts(unit_label, quantity)
    if unit_label in ("half_dozen", "half_carton"):
        return unit_text
    return f"{qty_text} {unit_text}"


def _pdf_text(value) -> str:
    # fpdf's built-in Helvetica only knows Latin-1, so anything else
    # (e.g. the naira sign) becomes "?" instead of crashing the download.
    return str("" if value is None else value).encode("latin-1", "replace").decode("latin-1")


def _wrap_lines(pdf, text: str, max_w: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in str(text).split():
        trial = f"{cur} {word}".strip()
        if pdf.get_string_width(trial) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while pdf.get_string_width(word) > max_w and len(word) > 1:
            k = len(word)
            while k > 1 and pdf.get_string_width(word[:k]) > max_w:
                k -= 1
            lines.append(word[:k])
            word = word[k:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit_image(path: str, box: float):
    """Size (w, h) in mm that fits the image inside a box x box square
    WITHOUT stretching it, or None if the file can't be read."""
    from pathlib import Path as _P
    if not path or not _P(path).is_file():
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            iw, ih = im.size
    except Exception:
        return None
    if not iw or not ih:
        return None
    scale = min(box / iw, box / ih)
    return iw * scale, ih * scale


def _render_restock_pdf(
    rows: list[dict],
    supplier_display_name: str | None,
    supplier_contact: list[str] | None = None,
    request_no: str | None = None,
    business: dict | None = None,
) -> bytes:
    # fpdf2 -- same small pure-Python PDF dependency statements.py
    # already uses, so no new system libraries are needed to deploy this.
    from fpdf import FPDF

    business = business or {}

    class _RestockPDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="C")
            self.set_text_color(0, 0, 0)

    left = 12.0
    usable = 186.0
    bottom = 280.0  # content never goes below this line (footer lives under it)
    box = 14.0      # item image box, mm

    pdf = _RestockPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left, 12, left)
    pdf.set_auto_page_break(auto=False)  # we break pages ourselves, row by row
    pdf.alias_nb_pages()
    pdf.add_page()

    # ---- header: logo + business details on the left, request info right ----
    top = 12.0
    logo_box = 22.0
    right_w = 62.0
    import pdf_branding as _brand  # Prestige / Nick Cosmetics logo
    logo_path = _brand.mark_path() or business.get("logo_path")
    logo_size = _fit_image(logo_path, logo_box)
    logo_drawn = False
    if logo_size:
        lw, lh = logo_size
        try:
            pdf.image(logo_path, x=left + (logo_box - lw) / 2, y=top + (logo_box - lh) / 2, w=lw, h=lh)
            logo_drawn = True
        except Exception:
            logo_drawn = False
    text_x = left + (logo_box + 4 if logo_drawn else 0)
    left_w = usable - right_w - (text_x - left) - 4

    ly = top
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_xy(text_x, ly)
    pdf.cell(left_w, 7, "PRESTIGE")
    ly += 7
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.set_xy(text_x, ly)
    pdf.cell(left_w, 5, "NICK COSMETICS")
    pdf.set_text_color(0, 0, 0)
    ly += 6
    info = []
    if business.get("branch_name"):
        info.append("Branch: " + _pdf_text(business["branch_name"]))
    if business.get("branch_address"):
        info.append(_pdf_text(business["branch_address"]))
    if business.get("support_line"):
        info.append("Support line: " + _pdf_text(business["support_line"]))
    pdf.set_font("Helvetica", "", 9.5)
    for text in info:
        for ln in _wrap_lines(pdf, text, left_w):
            pdf.set_xy(text_x, ly)
            pdf.cell(left_w, 5, ln)
            ly += 5

    rx = left + usable - right_w
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_xy(rx, top)
    pdf.cell(right_w, 7, "Restock Request", align="R")
    ry = top + 8
    if request_no:
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_xy(rx, ry)
        pdf.cell(right_w, 5, _pdf_text(f"No: {request_no}"), align="R")
        ry += 5
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_xy(rx, ry)
    pdf.cell(right_w, 5, "Date: " + date.today().strftime("%d %b %Y"), align="R")
    ry += 5

    y = max(ly, ry, top + (logo_box if logo_drawn else 0)) + 3
    pdf.set_draw_color(170, 170, 170)
    pdf.line(left, y, left + usable, y)
    y += 5

    def kv(label: str, value: str, y0: float) -> float:
        pdf.set_xy(left, y0)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.cell(20, 5.5, label)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_xy(left + 20, y0)
        pdf.cell(usable - 20, 5.5, _pdf_text(value))
        return y0 + 5.5

    # Supplier is optional -- this PDF is useful even before you've decided
    # who it's going to (print it, then hand-address it).
    y = kv("Supplier:", supplier_display_name or "____________________", y)
    for extra in (supplier_contact or []):
        y = kv("", extra, y)
    y += 4

    # ---- table setup ------------------------------------------------------
    use_images = any(_fit_image(r.get("local_image_path"), box) for r in rows)
    w_num, w_qty, w_unit = 0.0, 16.0, 48.0  # no "#" column
    w_item = usable - (w_num + w_qty + w_unit)
    img_pad = (box + 3) if use_images else 0.0
    cols = [("Item", w_item, "L"), ("Qty", w_qty, "C"), ("Unit", w_unit, "L")]

    def draw_header(y0: float) -> float:
        pdf.set_fill_color(55, 55, 55)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 9)
        x = left
        for title, w, al in cols:
            pdf.set_xy(x, y0)
            pdf.cell(w, 7, title, align=al, fill=True)
            x += w
        pdf.set_text_color(0, 0, 0)
        return y0 + 7

    y = draw_header(y)
    line_h = 4.4
    note_h = 3.9

    # ---- rows -------------------------------------------------------------
    for idx, row in enumerate(rows, start=1):
        name = _pdf_text(row.get("product_name"))
        note = _pdf_text(row.get("notes") or "").strip()
        qty_text = _pdf_text(row.get("qty_text") or "")
        unit_text = _pdf_text(row.get("unit_text") or row.get("quantity_display") or "")

        text_w = w_item - img_pad - 3
        pdf.set_font("Helvetica", "B", 9.5)
        name_lines = _wrap_lines(pdf, name, text_w)
        pdf.set_font("Helvetica", "I", 8)
        note_lines = _wrap_lines(pdf, note, text_w) if note else []
        pdf.set_font("Helvetica", "", 9)
        unit_lines = _wrap_lines(pdf, unit_text, w_unit - 3)

        item_h = len(name_lines) * line_h + len(note_lines) * note_h
        text_h = max(item_h, len(unit_lines) * line_h)
        row_h = max(text_h + 4, (box + 4) if use_images else 9.0)

        if y + row_h > bottom:  # whole row (image included) moves to the next page
            pdf.add_page()
            y = draw_header(12.0)

        def put(lines: list[str], x: float, w: float, align: str, bold: bool = False):
            pdf.set_font("Helvetica", "B" if bold else "", 9.5 if bold else 9)
            start = y + (row_h - len(lines) * line_h) / 2
            for i, ln in enumerate(lines):
                pdf.set_xy(x + 1.5, start + i * line_h)
                pdf.cell(w - 3, line_h, ln, align=align)

        x = left

        # item cell: image, name, and (only when typed) the item's note
        start = y + (row_h - item_h) / 2
        pdf.set_font("Helvetica", "B", 9.5)
        for i, ln in enumerate(name_lines):
            pdf.set_xy(x + img_pad + 1.5, start + i * line_h)
            pdf.cell(text_w, line_h, ln)
        if note_lines:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(110, 110, 110)
            base = start + len(name_lines) * line_h
            for i, ln in enumerate(note_lines):
                pdf.set_xy(x + img_pad + 1.5, base + i * note_h)
                pdf.cell(text_w, note_h, ln)
            pdf.set_text_color(0, 0, 0)
        if use_images:
            size = _fit_image(row.get("local_image_path"), box)
            ix, iy = x + 1.5, y + (row_h - box) / 2
            drawn = False
            if size:
                iw, ih = size
                try:
                    pdf.image(row["local_image_path"], x=ix + (box - iw) / 2, y=iy + (box - ih) / 2, w=iw, h=ih)
                    drawn = True
                except Exception:
                    drawn = False
            if not drawn:  # placeholder so every row looks the same
                pdf.set_fill_color(238, 238, 238)
                pdf.rect(ix, iy, box, box, style="F")
                pdf.set_font("Helvetica", "I", 6)
                pdf.set_text_color(130, 130, 130)
                pdf.set_xy(ix, iy + box / 2 - 1.5)
                pdf.cell(box, 3, "No image", align="C")
                pdf.set_text_color(0, 0, 0)
        x += w_item
        put([qty_text], x, w_qty, "C", bold=True)
        x += w_qty
        put(unit_lines, x, w_unit, "L")

        pdf.set_draw_color(215, 215, 215)
        pdf.line(left, y + row_h, left + usable, y + row_h)
        y += row_h

    if not rows:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_xy(left, y + 3)
        pdf.cell(0, 8, "No items selected.")
        y += 11

    # ---- total (kept on the same page as the last row when possible) --------
    if y + 14 > bottom:
        pdf.add_page()
        y = 12.0
    y += 4
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(left, y)
    pdf.cell(usable, 6, f"Total items: {len(rows)}")

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
    supplier_contact: list[str] = []
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
        supplier_contact = [
            c for c in (supplier.contact_phone, supplier.contact_email, supplier.address) if c
        ]

    # Customer deposit orders that are still waiting are real work, so an
    # entry tied to one is never treated as a test item.
    from infrastructure.db.models import Branch as _DlBranch
    from infrastructure.db.models import CustomerRequest as _DlCustReq
    from infrastructure.db.models import StoreSetting as _DlStoreSetting

    _linked_rows = await session.execute(
        select(_DlCustReq.restock_list_entry_id).where(
            _DlCustReq.restock_list_entry_id.in_([e.id for e in entries]),
            _DlCustReq.status == "pending",
        )
    )
    _customer_linked_ids = {eid for (eid,) in _linked_rows.all()}

    pdf_rows = []
    skipped_placeholders = 0
    for entry in entries:
        product = entry.product
        if product is not None:
            primary_image = next((img for img in product.images if img.is_primary), None) if product.images else None
            image_url = primary_image.url if primary_image else (product.images[0].url if product.images else None)
            item_name = product.name
            size = (getattr(product, "size", None) or "").strip()
            if size and size.lower() not in item_name.lower():
                item_name = f"{item_name} - {size}"
        else:
            # Freeform item -- its own custom_image_url stands in for
            # a catalog product's photo.
            image_url = entry.custom_image_url
            item_name = entry.custom_name or "(unnamed item)"
        if _PLACEHOLDER_NAME_RE.match(item_name) and entry.id not in _customer_linked_ids:
            skipped_placeholders += 1
            continue
        local_path = None
        if image_url and image_url.startswith(_PUBLIC_BASE_URL):
            local_path = image_url.replace(f"{_PUBLIC_BASE_URL}/", "", 1)
        qty_text, unit_text = _unit_parts(entry.unit_label, entry.quantity, product)
        pdf_rows.append({
            "product_name": item_name,
            "qty_text": qty_text,
            "unit_text": unit_text,
            # the per-item note typed on the Restock List (empty until that exists)
            "notes": (getattr(entry, "note", None) or "").strip(),
            "local_image_path": local_path,
        })

    if not pdf_rows:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Every selected item looks like a test or placeholder item, so there is nothing to put on the request.",
        )
    pdf_rows.sort(key=lambda r: r["product_name"].lower())

    # Business details printed at the top: the store name + logo saved in
    # Settings, and the branch the list belongs to (name, address, support line).
    _setting_rows = await session.execute(
        select(_DlStoreSetting).where(_DlStoreSetting.key.in_(("store_name", "logo_url")))
    )
    _store = {r.key: r.value for r in _setting_rows.scalars().all()}
    _logo_url = _store.get("logo_url")
    _logo_path = None
    if _logo_url and _logo_url.startswith(_PUBLIC_BASE_URL):
        _logo_path = _logo_url.replace(f"{_PUBLIC_BASE_URL}/", "", 1)
    _branch_ids = {e.branch_id for e in entries}
    _branch = await session.get(_DlBranch, next(iter(_branch_ids))) if len(_branch_ids) == 1 else None
    business = {
        "name": _store.get("store_name") or "Nick Cosmetics",
        "logo_path": _logo_path,
        "branch_name": _branch.name if _branch is not None else None,
        "branch_address": _branch.address if _branch is not None else None,
        "support_line": _branch.support_line if _branch is not None else None,
    }

    import json as _json
    from infrastructure.db.models import RestockRequest as _RestockRequest

    # Numbers run in order through the year: RS-2026-001, RS-2026-002, ...
    _year = _datetime.now().year
    _numbers = (
        await session.execute(
            select(_RestockRequest.request_no).where(_RestockRequest.request_no.like(f"RS-{_year}-%"))
        )
    ).scalars().all()
    # Next number = highest number used this year + 1 (so deleting an older request never clashes)
    _last = max([int(n.rsplit("-", 1)[1]) for n in _numbers if n.rsplit("-", 1)[1].isdigit()] or [0])
    request_no = f"RS-{_year}-{_last + 1:03d}"

    pdf_bytes = _render_restock_pdf(pdf_rows, supplier_display_name, supplier_contact, request_no, business)
    # RESTOCK-STAYS-ON-LIST: downloading the PDF does NOT take items off
    # the Restock List any more. An item only leaves when
    #   - it is a catalog item that has been restocked above its reorder
    #     level (cleanup on read in list_restock_list), or
    #   - it is a typed item whose customer order is marked arrived
    #     (cleanup on read in list_restock_list), or
    #   - someone presses Remove.
    # Customer orders attached to these entries are just stamped with when
    # they were sent to a supplier.
    from api.v1.routers.customer_requests import mark_requests_ordered

    await mark_requests_ordered(session, [e.id for e in entries])

    # Save this request under its number and mark every printed item as
    # "on order", so the Restock List shows it and warns before it is
    # ordered a second time.
    _request = _RestockRequest(
        request_no=request_no,
        branch_id=entries[0].branch_id,
        supplier_name=typed_name or None,
        created_by=current_user.id,
        pdf_data=pdf_bytes,
        items_json=_json.dumps(
            [
                {"name": r["product_name"], "qty": r["qty_text"], "unit": r["unit_text"], "note": r["notes"]}
                for r in pdf_rows
            ]
        ),
    )
    session.add(_request)
    await session.flush()
    for _entry in entries:
        session.add(
            RestockListEntrySend(
                restock_list_entry_id=_entry.id,
                supplier_name=typed_name or "No supplier named",
                quantity_sent=_entry.quantity,
                unit_label_sent=_entry.unit_label,
                sent_by=current_user.id,
                request_id=_request.id,
                request_no=request_no,
            )
        )
    await session.commit()

    slug = typed_name.replace(" ", "-").lower() if typed_name else "list"
    filename = f"{request_no}-{slug}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/requests")
async def list_restock_requests(
    branch_id: uuid.UUID,
    q: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_VIEW),
) -> list[dict]:
    """The saved Restock Requests for a branch, newest first. `q` matches the
    request number (RS-2026-001), the supplier name, or any item name."""
    import json as _json

    from infrastructure.db.models import RestockRequest as _RR

    stmt = (
        select(
            _RR.id,
            _RR.request_no,
            _RR.supplier_name,
            _RR.created_at,
            _RR.items_json,
            (_RR.pdf_data.isnot(None)).label("has_pdf"),
        )
        .where(_RR.branch_id == branch_id)
        .order_by(_RR.created_at.desc())
        .limit(300)
    )
    found = (await session.execute(stmt)).all()
    needle = (q or "").strip().lower()
    out: list[dict] = []
    for row in found:
        try:
            items = _json.loads(row.items_json) if row.items_json else []
        except ValueError:
            items = []
        if needle:
            haystack = " ".join(
                [row.request_no or "", row.supplier_name or ""] + [str(i.get("name", "")) for i in items]
            ).lower()
            if needle not in haystack:
                continue
        out.append(
            {
                "id": str(row.id),
                "request_no": row.request_no,
                "supplier_name": row.supplier_name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "items": items,
                "item_count": len(items),
                "has_pdf": bool(row.has_pdf),
            }
        )
    return out


@router.delete("/requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_restock_request(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_MANAGE),
) -> None:
    """Deletes a saved Restock Request: its saved PDF goes, and its items stop
    showing as on order under that number."""
    from sqlalchemy import delete as _delete

    from infrastructure.db.models import RestockRequest as _RR

    exists = (await session.execute(select(_RR.id).where(_RR.id == request_id))).first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Restock request not found")
    await session.execute(_delete(RestockListEntrySend).where(RestockListEntrySend.request_id == request_id))
    await session.execute(_delete(_RR).where(_RR.id == request_id))
    await session.commit()


@router.get("/requests/{request_id}/pdf")
async def download_saved_restock_request(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_VIEW),
) -> StreamingResponse:
    """Downloads again the exact PDF that was saved when the request was made."""
    from infrastructure.db.models import RestockRequest as _RR

    row = (
        await session.execute(select(_RR.request_no, _RR.supplier_name, _RR.pdf_data).where(_RR.id == request_id))
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Restock request not found")
    if not row.pdf_data:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="This request was saved before PDFs were kept, so it cannot be downloaded again.",
        )
    slug = (row.supplier_name or "").strip().replace(" ", "-").lower() or "list"
    filename = f"{row.request_no}-{slug}.pdf"
    return StreamingResponse(
        io.BytesIO(row.pdf_data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sends/{send_id}/received")
async def mark_restock_send_received(
    send_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> dict:
    """Marks ONE order line (one request to one supplier) as received, so an
    item ordered from two suppliers can be cleared one supplier at a time."""
    from datetime import timezone as _tz

    send = await session.get(RestockListEntrySend, send_id)
    if send is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order line not found")
    if send.received_at is None:
        send.received_at = _datetime.now(_tz.utc)
    await session.commit()
    return {"ok": True}


@router.post("/items/{entry_id}/received")
async def mark_restock_item_received(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> dict:
    """Clears the "on order" tag on an item: its open order records get a
    received date. The item itself stays on the list until its stock is back
    above the low-stock level, exactly as before."""
    from datetime import timezone as _tz

    result = await session.execute(
        select(RestockListEntrySend).where(
            RestockListEntrySend.restock_list_entry_id == entry_id,
            RestockListEntrySend.received_at.is_(None),
        )
    )
    open_sends = list(result.scalars().all())
    now = _datetime.now(_tz.utc)
    for s in open_sends:
        s.received_at = now
    await session.commit()
    return {"closed": len(open_sends)}


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
