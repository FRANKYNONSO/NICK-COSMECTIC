"""Supplier purchase invoices -- a super admin/branch manager's record
of what was bought from a supplier, typed in and/or backed by a photo
of the paper invoice, filterable by month/year/branch. Deliberately
separate from Sales & Receipts (which is customer-facing money coming
IN) and from Refunds (which stays on the Invoice page per the
merchant's own final call -- "the invoice here should just have the
history of those refunds").
"""
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi import status as fastapi_status
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db_session, require_permission
from api.v1.routers.catalog import _PUBLIC_BASE_URL, _generate_sku
from api.v1.schemas.purchase_invoices import (
    PurchaseInvoiceCreate,
    PurchaseInvoiceLineItemOut,
    PurchaseInvoiceOut,
    PurchaseInvoiceUpdate,
    TotalInvestedOut,
)
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import Branch as ORMBranch
from infrastructure.db.models import PurchaseInvoice, PurchaseInvoiceLineItem, Product, StockAdjustment, Supplier, User
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/purchase-invoices", tags=["purchase-invoices"])

_IMAGE_DIR = Path("static/uploads/purchase_invoices")
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".mp4", ".mov", ".webm"}

# Who gets to file/edit supplier invoices and manage suppliers -- same
# tier as before. Separate from _VIEW_PERMISSION below (spec section
# 21: "Separate VIEW permission from MANAGE permission") so a cashier
# can be given read-only access to invoice history without also being
# able to add/edit/delete invoices or manage suppliers.
_PERMISSION = "manage_suppliers"
# Read-only: every role holds this by default (migration
# z1a2b3c4d5e6 added it to staff/branch_manager/super_admin), and a
# super_admin can still revoke it per-account like any other
# permission via denied_permissions.
_VIEW_PERMISSION = "view_purchase_invoices"

# September 2026 change -- per-invoice cost-per-piece calculator.
# Dozens/pieces always convert at a fixed rate; only cartons vary by
# supplier and need an admin-entered pieces_per_carton figure.
_DOZEN_PIECES = 12


def _unit_pieces(unit_label: str, count: int, pieces_per_carton: int | None) -> int | None:
    if unit_label == "cartons":
        return count * pieces_per_carton if pieces_per_carton else None
    if unit_label == "bags":
        # Oct 2026: bags convert like cartons -- size varies per product.
        return count * pieces_per_carton if pieces_per_carton else None
    if unit_label == "rolls":
        # PATCH5B-INVOICE-PACKS: rolls convert like cartons and bags.
        return count * pieces_per_carton if pieces_per_carton else None
    if unit_label == "dozens":
        return count * _DOZEN_PIECES
    if unit_label == "pieces":
        return count
    # A "half pack" partial from the invoice quantity popup -- no
    # separate quantity box for these (picking "half" IS the quantity),
    # so count is ignored and 1 is always what "half" means here.
    if unit_label == "half_dozen":
        return _DOZEN_PIECES // 2  # always 6, fixed like a whole dozen
    if unit_label == "half_carton":
        return pieces_per_carton // 2 if pieces_per_carton else None
    return None


_HALF_PACK_LABELS = {"half_carton", "half_dozen"}


def _compute_line_piece_calc(
    quantity: int,
    unit_label: str,
    pieces_per_carton: int | None,
    remainder_quantity: int | None,
    remainder_unit_label: str | None,
) -> int | None:
    primary = _unit_pieces(unit_label, quantity, pieces_per_carton)
    if primary is None:
        return None
    if not remainder_unit_label:
        return primary
    # A half-pack remainder ("half_carton"/"half_dozen") has no
    # quantity box on the invoice popup -- picking it IS the quantity --
    # so it must not be dropped just because remainder_quantity is
    # empty, unlike an ordinary dozens/pieces remainder.
    is_half_pack = remainder_unit_label in _HALF_PACK_LABELS
    if not is_half_pack and not remainder_quantity:
        return primary
    remainder = _unit_pieces(remainder_unit_label, remainder_quantity or 1, pieces_per_carton)
    return primary + remainder if remainder is not None else None


def _serialize(inv: PurchaseInvoice) -> PurchaseInvoiceOut:
    amount_naira = Money(inv.amount_kobo).naira if inv.amount_kobo is not None else None
    amount_paid_naira = Money(inv.amount_paid_kobo).naira if inv.amount_paid_kobo is not None else None
    waybill_naira = Money(inv.waybill_cost_kobo).naira if inv.waybill_cost_kobo is not None else None
    transit_naira = Money(inv.transit_cost_kobo).naira if inv.transit_cost_kobo is not None else None
    handling_naira = Money(inv.handling_cost_kobo).naira if inv.handling_cost_kobo is not None else None
    return PurchaseInvoiceOut(
        id=inv.id,
        status=inv.status,
        branch_id=inv.branch_id,
        branch_name=inv.branch.name if inv.branch else None,
        supplier_id=inv.supplier_id,
        supplier_name=inv.supplier_name,
        invoice_date=inv.invoice_date,
        amount_naira=amount_naira,
        description=inv.description,
        image_url=inv.image_url,
        invoice_number=inv.invoice_number,
        source=inv.source,
        supplier_address=inv.supplier_address,
        supplier_phone=inv.supplier_phone,
        payment_method=inv.payment_method,
        amount_paid_naira=amount_paid_naira,
        # Balance is always derived from the current amount/paid figures
        # rather than stored, so it can never drift out of sync (spec
        # section 17: "the remaining balance should be calculated
        # automatically").
        balance_naira=(
            max(0.0, amount_naira - amount_paid_naira)
            if amount_naira is not None and amount_paid_naira is not None
            else None
        ),
        waybill_cost_naira=waybill_naira,
        transit_cost_naira=transit_naira,
        handling_cost_naira=handling_naira,
        # Spec section 19: "the system should calculate the total
        # automatically" -- goods amount + dispatch + transit + handling.
        total_landed_cost_naira=(
            (amount_naira or 0) + (waybill_naira or 0) + (transit_naira or 0) + (handling_naira or 0)
            if amount_naira is not None or waybill_naira is not None or transit_naira is not None or handling_naira is not None
            else None
        ),
        adjustment_amount_naira=(
            Money(inv.adjustment_amount_kobo).naira if inv.adjustment_amount_kobo is not None else None
        ),
        adjustment_status=inv.adjustment_status,
        adjustment_note=inv.adjustment_note,
        created_by_name=inv.creator.full_name,
        created_at=inv.created_at,
        items=[
            PurchaseInvoiceLineItemOut(
                id=item.id,
                item_name=item.item_name,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_label=item.unit_label,
                cost_price_naira=Money(item.cost_price_kobo).naira if item.cost_price_kobo is not None else None,
                rate_naira=Money(item.rate_kobo).naira if item.rate_kobo is not None else None,
                line_total_naira=Money(item.line_total_kobo).naira if item.line_total_kobo is not None else None,
                pieces_per_carton=item.pieces_per_carton,
                remainder_quantity=item.remainder_quantity,
                remainder_unit_label=item.remainder_unit_label,
                total_pieces=item.total_pieces,
                cost_price_per_piece_naira=(
                    Money(item.cost_price_per_piece_kobo).naira
                    if item.cost_price_per_piece_kobo is not None else None
                ),
            )
            for item in sorted(inv.line_items, key=lambda i: i.position)
        ],
    )


@router.post("/upload-image")
async def upload_invoice_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(_PERMISSION)),
) -> dict:
    original_name = file.filename or "invoice"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            fastapi_status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}",
        )
    _IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _IMAGE_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"url": f"{_PUBLIC_BASE_URL}/static/uploads/purchase_invoices/{new_filename}"}


@router.get("/total-invested", response_model=TotalInvestedOut)
async def total_invested(
    branch_id: uuid.UUID | None = None,
    # Dashboard redesign implementation prompt, step 1: the old
    # resettable "financial tracking period" concept (formerly
    # September 2026 change spec section 14, with its own
    # /financial-period endpoints in reports.py) is removed entirely.
    # The dashboard now always calls this with a far-past constant
    # (see js/dashboard.js's currentFinancialPeriodStartDate) so this
    # stays a true all-time total. date_from itself is left in place
    # as a generic optional filter -- it isn't reset-specific -- even
    # though nothing currently passes anything but that constant or
    # omits it entirely.
    date_from: date | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> TotalInvestedOut:
    """"Total Amount Invested in Inventory" -- every invoice's landed
    cost (goods + dispatch + transit + handling), summed cumulatively
    (never resets on its own, unlike the Losses dashboard figure in
    section 18.6 -- section 14 adds a manual, super-admin-triggered
    reset instead). September 2026 change spec section 1: this figure
    is never reduced by losses -- losses instead reduce Total Revenue
    only (see reports.py::sales_summary(), which already subtracts
    StockAdjustment losses from revenue per period). "Sold Below
    Original Price" (SaleLoss) has been retired entirely along with
    the checkout discount path it tracked (section 3), so it no longer
    factors in here either.

    total_losses_naira/net_invested_naira are still computed and
    returned (now purely StockAdjustment-based) in case another
    consumer wants a losses-adjusted view, but the KPI tile itself
    must read total_landed_cost_naira -- see dashboard.js.
    """
    effective_branch_id = current_user.branch_id if current_user.branch_id is not None else branch_id

    landed_cost_stmt = select(
        func.coalesce(
            func.sum(
                func.coalesce(PurchaseInvoice.amount_kobo, 0)
                + func.coalesce(PurchaseInvoice.waybill_cost_kobo, 0)
                + func.coalesce(PurchaseInvoice.transit_cost_kobo, 0)
                + func.coalesce(PurchaseInvoice.handling_cost_kobo, 0)
            ),
            0,
        )
    )
    if effective_branch_id is not None:
        landed_cost_stmt = landed_cost_stmt.where(PurchaseInvoice.branch_id == effective_branch_id)
    else:
        # XFER5: with no branch picked, a transfer invoice would count the same
        # goods twice (once on the supplier invoice, once on the transfer).
        landed_cost_stmt = landed_cost_stmt.where(PurchaseInvoice.source.is_distinct_from("branch_transfer"))
    if date_from is not None:
        landed_cost_stmt = landed_cost_stmt.where(PurchaseInvoice.invoice_date >= date_from)
    landed_cost_kobo = (await session.execute(landed_cost_stmt)).scalar_one()

    adjustment_loss_stmt = select(func.coalesce(func.sum(StockAdjustment.value_kobo), 0)).where(
        StockAdjustment.value_kobo.is_not(None), StockAdjustment.status == "completed"
    )
    if effective_branch_id is not None:
        adjustment_loss_stmt = adjustment_loss_stmt.where(StockAdjustment.branch_id == effective_branch_id)
    if date_from is not None:
        adjustment_loss_stmt = adjustment_loss_stmt.where(
            StockAdjustment.created_at >= datetime.combine(date_from, datetime.min.time())
        )
    adjustment_loss_kobo = (await session.execute(adjustment_loss_stmt)).scalar_one()

    total_losses_kobo = int(adjustment_loss_kobo or 0)

    return TotalInvestedOut(
        total_landed_cost_naira=Money(int(landed_cost_kobo or 0)).naira,
        total_losses_naira=Money(total_losses_kobo).naira,
        net_invested_naira=Money(int(landed_cost_kobo or 0) - total_losses_kobo).naira,
    )


@router.get("", response_model=list[PurchaseInvoiceOut])
async def list_purchase_invoices(
    branch_id: uuid.UUID | None = None,
    year: int | None = None,
    month: int | None = None,
    supplier_id: uuid.UUID | None = None,
    # Add Product -> Invoice Workflow spec (Sept 2026): lets the "add
    # this product to an existing draft" picker on Add Product ask for
    # only unfinished drafts, instead of every invoice ever filed.
    status: str | None = None,
    # XFER5: the receive and damage-complaint screens ask for exclude_transfers=true.
    exclude_transfers: bool = False,
    session: AsyncSession = Depends(get_db_session),
    # View-only permission -- a cashier with view_purchase_invoices
    # (granted by default) can see invoice history/details for their
    # own branch but cannot add/edit/delete (see create/upload below,
    # still gated on manage_suppliers).
    current_user: User = Depends(require_permission(_VIEW_PERMISSION)),
) -> list[PurchaseInvoiceOut]:
    conditions = []
    # A cashier/branch manager tied to a single branch only ever sees
    # that branch's invoices, enforced server-side regardless of the
    # branch_id query param (spec section 20: "view invoice history
    # for their current branch"). A super_admin (no fixed branch_id)
    # can filter by whichever branch they've picked on the dashboard.
    if current_user.branch_id is not None:
        conditions.append(PurchaseInvoice.branch_id == current_user.branch_id)
    elif branch_id:
        conditions.append(PurchaseInvoice.branch_id == branch_id)
    if year:
        conditions.append(extract("year", PurchaseInvoice.invoice_date) == year)
    if month:
        conditions.append(extract("month", PurchaseInvoice.invoice_date) == month)
    # Backs "Lodge a complaint": pick the supplier first, then see
    # their invoice history (recent first) to pick from, rather than
    # asking "when did this item come to store."
    if supplier_id:
        conditions.append(PurchaseInvoice.supplier_id == supplier_id)
    if status:
        conditions.append(PurchaseInvoice.status == status)
    if exclude_transfers:
        conditions.append(PurchaseInvoice.source.is_distinct_from("branch_transfer"))
    result = await session.execute(
        select(PurchaseInvoice)
        .options(
            selectinload(PurchaseInvoice.branch),
            selectinload(PurchaseInvoice.creator),
            selectinload(PurchaseInvoice.line_items),
        )
        .where(*conditions)
        .order_by(PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.created_at.desc())
    )
    return [_serialize(inv) for inv in result.scalars().all()]


@router.post("", response_model=PurchaseInvoiceOut, status_code=fastapi_status.HTTP_201_CREATED)
async def create_purchase_invoice(
    body: PurchaseInvoiceCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission(_PERMISSION)),
) -> PurchaseInvoiceOut:
    # Branch is never trusted from the client for anyone tied to a
    # single branch -- it always comes from the branch selected on the
    # Super Admin dashboard for a roaming super_admin, or the
    # branch_manager/cashier's own fixed branch otherwise (spec
    # section 13: "the branch field should be automatically populated
    # and read-only... do not hard-code branch names").
    branch_id = current_user.branch_id if current_user.branch_id is not None else body.branch_id
    if branch_id is not None:
        branch = await session.get(ORMBranch, branch_id)
        if branch is None:
            raise HTTPException(fastapi_status.HTTP_404_NOT_FOUND, detail="Branch not found")

    # Spec section 14: resolve to an existing supplier by exact name
    # match (case-insensitive) so repeat suppliers autofill next time
    # without the user re-picking from the dropdown; if genuinely new,
    # register it automatically -- same "register it centrally the
    # first time it's used" pattern as products (spec section 12).
    # Deliberately does NOT overwrite an existing supplier's stored
    # address/phone even if the typed values differ here -- that's a
    # separate, explicit confirmation the frontend asks for via
    # PATCH /suppliers/{id}, never implied by filing an invoice.
    # Add Product -> Invoice Workflow spec (Sept 2026): a bare draft
    # invoice can be created with no supplier at all -- skip the
    # lookup/auto-registration entirely rather than calling .strip()
    # on a None.
    supplier_id = body.supplier_id
    supplier_name_clean = body.supplier_name.strip() if body.supplier_name else None
    if supplier_id is None and supplier_name_clean:
        existing = await session.execute(
            select(Supplier).where(
                Supplier.is_deleted.is_(False),
                Supplier.name.ilike(supplier_name_clean),
            )
        )
        supplier = existing.scalar_one_or_none()
        if supplier is None:
            supplier = Supplier(
                name=supplier_name_clean,
                contact_phone=body.supplier_phone.strip() if body.supplier_phone else None,
                address=body.supplier_address.strip() if body.supplier_address else None,
            )
            session.add(supplier)
            await session.flush()
        supplier_id = supplier.id

    inv = PurchaseInvoice(
        branch_id=branch_id,
        supplier_id=supplier_id,
        supplier_name=supplier_name_clean,
        status=body.status,
        # A bare draft (no invoice_date typed in yet) still needs a
        # real date for sorting/filtering -- defaults to today, same
        # as every other auto-filed placeholder in this system.
        invoice_date=body.invoice_date or date.today(),
        amount_kobo=Money.from_naira(body.amount_naira).kobo if body.amount_naira is not None else None,
        description=body.description,
        image_url=body.image_url,
        supplier_address=body.supplier_address.strip() if body.supplier_address else None,
        supplier_phone=body.supplier_phone.strip() if body.supplier_phone else None,
        payment_method=body.payment_method,
        amount_paid_kobo=Money.from_naira(body.amount_paid_naira).kobo if body.amount_paid_naira is not None else None,
        waybill_cost_kobo=Money.from_naira(body.waybill_cost_naira).kobo if body.waybill_cost_naira is not None else None,
        transit_cost_kobo=Money.from_naira(body.transit_cost_naira).kobo if body.transit_cost_naira is not None else None,
        handling_cost_kobo=Money.from_naira(body.handling_cost_naira).kobo if body.handling_cost_naira is not None else None,
        adjustment_amount_kobo=(
            Money.from_naira(body.adjustment_amount_naira).kobo if body.adjustment_amount_naira is not None else None
        ),
        adjustment_status=body.adjustment_status,
        adjustment_note=body.adjustment_note,
        created_by=current_user.id,
    )
    for position, item in enumerate(body.items):
        rate_kobo = Money.from_naira(item.rate_naira).kobo if item.rate_naira is not None else None
        item_name = item.item_name.strip()
        product_id = item.product_id
        # Spec section 13: if the typed item name doesn't already
        # exist as an inventory item (and the line wasn't already
        # linked to one via the Item autocomplete), auto-create a
        # bare/draft product -- name only, no price/category/image/
        # stock -- so it's selectable next time and can be fleshed
        # out later from Inventory (ties into the draft-product
        # concept in section 15).
        if product_id is None and item_name:
            existing_product = await session.execute(
                select(Product).where(
                    Product.name.ilike(item_name),
                )
            )
            product = existing_product.scalars().first()
            if product is None:
                sku = await _generate_sku(session, item_name)
                product = Product(
                    name=item_name,
                    sku=sku,
                    status="draft",
                    cost_price_kobo=0,
                    selling_price_kobo=0,
                )
                session.add(product)
                await session.flush()
            product_id = product.id
        # A manually-typed total (for invoices with no per-unit rate)
        # always wins; otherwise it's quantity x rate when a rate is
        # given (Sept 2026 rate/total override, same pattern as
        # warehouse.js's quantity_pieces_override).
        if item.line_total_naira is not None:
            line_total_kobo = Money.from_naira(item.line_total_naira).kobo
        elif rate_kobo is not None:
            line_total_kobo = item.quantity * rate_kobo
        else:
            line_total_kobo = 0
        total_pieces = _compute_line_piece_calc(
            item.quantity, item.unit_label, item.pieces_per_carton,
            item.remainder_quantity, item.remainder_unit_label,
        )
        inv.line_items.append(
            PurchaseInvoiceLineItem(
                position=position,
                item_name=item_name,
                product_id=product_id,
                quantity=item.quantity,
                unit_label=item.unit_label,
                # Total is quantity x rate by default, or a manually
                # typed total when rate wasn't given.
                rate_kobo=rate_kobo if rate_kobo is not None else 0,
                line_total_kobo=line_total_kobo,
                pieces_per_carton=item.pieces_per_carton,
                remainder_quantity=item.remainder_quantity,
                remainder_unit_label=item.remainder_unit_label,
                total_pieces=total_pieces,
                cost_price_per_piece_kobo=(
                    round(line_total_kobo / total_pieces) if total_pieces else None
                ),
            )
        )
    # Sept 2026 follow-up: clear the "pending invoice" flag on any
    # product actually filed onto this invoice, so the Inventory
    # "Awaiting invoice" filter only ever shows products nobody has
    # decided about yet.
    linked_product_ids = {li.product_id for li in inv.line_items if li.product_id is not None}
    for pid in linked_product_ids:
        prod = await session.get(Product, pid)
        if prod is not None:
            prod.needs_invoice = False

    session.add(inv)
    await session.flush()
    log_audit_event(
        session,
        user_id=current_user.id,
        action="purchase_invoice.create",
        entity_type="purchase_invoice",
        entity_id=inv.id,
        metadata={"supplier_name": body.supplier_name, "amount_naira": body.amount_naira, "item_count": len(body.items)},
    )
    await session.commit()
    result = await session.execute(
        select(PurchaseInvoice)
        .options(
            selectinload(PurchaseInvoice.branch),
            selectinload(PurchaseInvoice.creator),
            selectinload(PurchaseInvoice.line_items),
        )
        .where(PurchaseInvoice.id == inv.id)
    )
    return _serialize(result.scalar_one())


@router.patch("/{invoice_id}", response_model=PurchaseInvoiceOut)
async def update_purchase_invoice(
    invoice_id: uuid.UUID,
    body: PurchaseInvoiceUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission(_PERMISSION)),
) -> PurchaseInvoiceOut:
    """Edit an existing invoice after the fact -- fill in extra landed
    costs once they come in, correct a typed field, or add more items
    without losing what the invoice already has (spec: invoices should
    be editable, not locked the moment they're filed).
    """
    result = await session.execute(
        select(PurchaseInvoice)
        .options(selectinload(PurchaseInvoice.line_items))
        .where(PurchaseInvoice.id == invoice_id)
    )
    inv = result.scalar_one_or_none()
    if inv is None:
        raise HTTPException(fastapi_status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if inv.source == "branch_transfer":
        raise HTTPException(
            fastapi_status.HTTP_400_BAD_REQUEST,
            detail="This invoice was made by a branch transfer. It is read-only and cannot be edited.",
        )
    updates = body.model_dump(exclude_unset=True, exclude={"add_items", "update_items", "remove_item_ids"})
    money_fields = {
        "amount_naira": "amount_kobo",
        "amount_paid_naira": "amount_paid_kobo",
        "waybill_cost_naira": "waybill_cost_kobo",
        "transit_cost_naira": "transit_cost_kobo",
        "handling_cost_naira": "handling_cost_kobo",
        "adjustment_amount_naira": "adjustment_amount_kobo",
    }
    for field, value in updates.items():
        if field in money_fields:
            setattr(inv, money_fields[field], Money.from_naira(value).kobo if value is not None else None)
        elif isinstance(value, str):
            setattr(inv, field, value.strip())
        else:
            setattr(inv, field, value)

    # Sept 2026 fix: there was previously no way to delete a line item
    # already saved to an invoice. IDs not found on this invoice are
    # skipped rather than raising, since the invoice may have changed
    # since the person opened the edit screen.
    if body.remove_item_ids:
        remove_ids = set(body.remove_item_ids)
        for line_item in [li for li in inv.line_items if li.id in remove_ids]:
            inv.line_items.remove(line_item)
            await session.delete(line_item)

    next_position = len(inv.line_items)
    for offset, item in enumerate(body.add_items):
        rate_kobo = Money.from_naira(item.rate_naira).kobo if item.rate_naira is not None else None
        item_name = item.item_name.strip()
        product_id = item.product_id
        if product_id is None and item_name:
            existing_product = await session.execute(select(Product).where(Product.name.ilike(item_name)))
            product = existing_product.scalars().first()
            if product is None:
                sku = await _generate_sku(session, item_name)
                product = Product(name=item_name, sku=sku, status="draft", cost_price_kobo=0, selling_price_kobo=0)
                session.add(product)
                await session.flush()
            product_id = product.id
        # A manually-typed total (for invoices with no per-unit rate)
        # always wins; otherwise it's quantity x rate when a rate is
        # given (Sept 2026 rate/total override, same pattern as
        # warehouse.js's quantity_pieces_override).
        if item.line_total_naira is not None:
            line_total_kobo = Money.from_naira(item.line_total_naira).kobo
        elif rate_kobo is not None:
            line_total_kobo = item.quantity * rate_kobo
        else:
            line_total_kobo = 0
        total_pieces = _compute_line_piece_calc(
            item.quantity, item.unit_label, item.pieces_per_carton,
            item.remainder_quantity, item.remainder_unit_label,
        )
        inv.line_items.append(
            PurchaseInvoiceLineItem(
                position=next_position + offset,
                item_name=item_name,
                product_id=product_id,
                quantity=item.quantity,
                unit_label=item.unit_label,
                rate_kobo=rate_kobo if rate_kobo is not None else 0,
                line_total_kobo=line_total_kobo,
                pieces_per_carton=item.pieces_per_carton,
                remainder_quantity=item.remainder_quantity,
                remainder_unit_label=item.remainder_unit_label,
                total_pieces=total_pieces,
                cost_price_per_piece_kobo=(
                    round(line_total_kobo / total_pieces) if total_pieces else None
                ),
            )
        )

    # Sept 2026 fix: correcting a quantity/rate/name on a line the
    # invoice already had was never possible before -- add_items above
    # only ever appended brand-new rows. update_items edits an
    # existing row in place by id; any field left out of a given
    # update_items entry keeps its current value.
    # Sept 2026 follow-up: clear the "pending invoice" flag on any
    # product just added to this existing draft.
    added_product_ids = {li.product_id for li in inv.line_items[next_position:] if li.product_id is not None}
    for pid in added_product_ids:
        prod = await session.get(Product, pid)
        if prod is not None:
            prod.needs_invoice = False

    items_by_id = {li.id: li for li in inv.line_items}
    for item_update in body.update_items:
        line_item = items_by_id.get(item_update.id)
        if line_item is None:
            raise HTTPException(
                fastapi_status.HTTP_404_NOT_FOUND,
                detail=f"Line item {item_update.id} not found on this invoice",
            )
        if item_update.item_name is not None:
            line_item.item_name = item_update.item_name.strip()
        if item_update.product_id is not None:
            line_item.product_id = item_update.product_id
        if item_update.quantity is not None:
            line_item.quantity = item_update.quantity
        if item_update.unit_label is not None:
            line_item.unit_label = item_update.unit_label
        if item_update.rate_naira is not None:
            line_item.rate_kobo = Money.from_naira(item_update.rate_naira).kobo
        if item_update.pieces_per_carton is not None:
            line_item.pieces_per_carton = item_update.pieces_per_carton
        if item_update.remainder_quantity is not None:
            line_item.remainder_quantity = item_update.remainder_quantity
        if item_update.remainder_unit_label is not None:
            line_item.remainder_unit_label = item_update.remainder_unit_label
        # A manually-typed total always wins; otherwise it's quantity
        # x rate whenever a rate is on file, same override pattern as
        # warehouse.js's quantity_pieces_override (Sept 2026 rate/
        # total override).
        if item_update.line_total_naira is not None:
            line_item.line_total_kobo = Money.from_naira(item_update.line_total_naira).kobo
        elif line_item.rate_kobo:
            line_item.line_total_kobo = line_item.quantity * line_item.rate_kobo
        line_item.total_pieces = _compute_line_piece_calc(
            line_item.quantity, line_item.unit_label, line_item.pieces_per_carton,
            line_item.remainder_quantity, line_item.remainder_unit_label,
        )
        line_item.cost_price_per_piece_kobo = (
            round(line_item.line_total_kobo / line_item.total_pieces) if line_item.total_pieces else None
        )

    log_audit_event(
        session,
        user_id=current_user.id,
        action="purchase_invoice.update",
        entity_type="purchase_invoice",
        entity_id=inv.id,
        metadata={
            "fields_changed": list(updates.keys()),
            "items_added": len(body.add_items),
            "items_updated": len(body.update_items),
        },
    )
    await session.commit()

    result = await session.execute(
        select(PurchaseInvoice)
        .options(
            selectinload(PurchaseInvoice.branch),
            selectinload(PurchaseInvoice.creator),
            selectinload(PurchaseInvoice.line_items),
        )
        .where(PurchaseInvoice.id == inv.id)
    )
    return _serialize(result.scalar_one())
