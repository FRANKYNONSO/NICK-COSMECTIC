import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --------------------------------------------------------- WAYBILL COMPLAINTS
# (Was "intake receipts" -- renamed at the API-response level per spec:
# "log delivery receipt" should read "log a waybill complaint." The DB
# table/model keep the old name (intake_receipts / IntakeReceipt) to
# avoid an unnecessary rename migration; this is purely the public
# shape.)

class IntakeReceiptOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    invoice_line_item_id: uuid.UUID | None = None
    # Denormalized straight onto the response (rather than making every
    # caller separately fetch the invoice) since the whole point of
    # picking an invoice is to show its date/supplier alongside the
    # complaint.
    invoice_date: str | None = None
    line_item_name: str | None = None
    image_url: str
    supplier_name: str | None
    item_count: int | None
    damaged_count: int | None
    missing_count: int | None
    notes: str | None
    created_by: uuid.UUID | None
    created_by_name: str | None = None
    received_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------- STOCK TRANSFERS

class StockTransferCreate(BaseModel):
    product_id: uuid.UUID
    from_branch_id: uuid.UUID
    to_branch_id: uuid.UUID
    # Was a strict piece/carton/roll enum; relaxed to a free label so the
    # warehouse UI's unit-label dropdown (carton, cartons, rolls, pieces)
    # plus its "quick-add unit label" option can send an arbitrary
    # store-defined label without a matching backend change each time.
    unit_label: str = Field(min_length=1, max_length=40)
    unit_count: int = Field(gt=0)
    pieces_per_unit: int = Field(default=1, gt=0)
    receipt_id: uuid.UUID | None = None
    notes: str | None = None
    # September 2026 change spec section 19: how the receiving branch
    # settles this transfer with the sending branch -- required for a
    # branch_to_branch transfer (this endpoint's only transfer_type),
    # unlike the warehouse-internal move types.
    payment_method: str = Field(pattern="^(cash|transfer)$")


class StockTransferOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    from_branch_id: uuid.UUID
    to_branch_id: uuid.UUID
    transfer_type: str
    unit_label: str
    unit_count: int
    pieces_per_unit: int
    is_half_partial: bool
    remainder_quantity: int | None = None
    remainder_unit_label: str | None = None
    cartons_count: int | None = None
    cartons_pieces_per_unit: int | None = None
    cartons_half: bool = False
    dozens_count: int | None = None
    dozens_pieces_per_unit: int | None = None
    dozens_half: bool = False
    bags_count: int | None = None
    bags_pieces_per_unit: int | None = None
    bags_half: bool = False
    pieces_count: int | None = None
    quantity_pieces_override: int | None
    quantity_pieces: int
    receipt_id: uuid.UUID | None
    invoice_line_item_id: uuid.UUID | None = None
    notes: str | None
    # September 2026 change spec section 19.
    total_cost_naira: float | None = None
    payment_method: str | None = None
    created_by: uuid.UUID | None
    created_by_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------- WAREHOUSE STOCK LEDGER
# A branch's own backstock -- separate from what's sellable on the
# shop floor (StockBatch). See models.WarehouseStock for why this is
# a distinct ledger rather than a flag on StockBatch.

class WarehouseMoveCreate(BaseModel):
    """Shared shape for pull-to-store (this branch's warehouse -> its
    own shop) and transfer-to-branch (this branch's warehouse -> a
    different branch's shop). Product/category/variant are picked
    from what already exists in the catalog (spec: "they don't
    actually have to be fillable... a drop-down from what is existing
    in the inventory itself") -- only product_id is actually required
    here since category/product are just how the frontend narrows
    down to it.
    """

    product_id: uuid.UUID
    unit_label: str = Field(min_length=1, max_length=40)
    unit_count: int = Field(gt=0)
    pieces_per_unit: int = Field(default=1, gt=0)
    notes: str | None = None


class PushToWarehouseCreate(BaseModel):
    """Shop floor -> this branch's own warehouse. Deliberately separate
    from WarehouseMoveCreate: this is pure documentation ("28 cartons
    of product is already on the warehouse, available, not in use") --
    it never touches sellable shop-floor stock. Normally it references
    an actual line on a purchase invoice on file (spec: "if the item
    is not on the invoice... it should not be pushed to warehouse"),
    but invoice_line_item_id is optional to also cover an invoice that
    was only ever saved as a photo (no typed line items) -- in that
    case there's nothing to cap against, so it's pushed straight
    against product_id, same as WarehouseMoveCreate.
    """

    product_id: uuid.UUID
    invoice_line_item_id: uuid.UUID | None = None
    # Component shape (Sept 2026, second revision): cartons, dozens and
    # pieces are each counted independently, each with its own
    # pieces-per-unit. total = cartons_count*cartons_pieces_per_unit
    # (+ half of it if cartons_half) + same for dozens + pieces_count.
    cartons_count: int | None = Field(default=None, ge=0)
    cartons_pieces_per_unit: int | None = Field(default=None, gt=0)
    cartons_half: bool = False
    dozens_count: int | None = Field(default=None, ge=0)
    dozens_pieces_per_unit: int | None = Field(default=None, gt=0)
    dozens_half: bool = False
    bags_count: int | None = Field(default=None, ge=0)
    bags_pieces_per_unit: int | None = Field(default=None, gt=0)
    bags_half: bool = False
    pieces_count: int | None = Field(default=None, ge=0)
    # Legacy primary+remainder shape -- still accepted when none of the
    # component fields above are sent. The router enforces "one shape
    # or the other".
    unit_label: str | None = Field(default=None, min_length=1, max_length=40)
    unit_count: int | None = Field(default=None, gt=0)
    pieces_per_unit: int = Field(default=1, gt=0)
    # Deprecated (Sept 2026): superseded by remainder_quantity/
    # remainder_unit_label below, which can express any leftover unit
    # combination the invoice itself supports, not just a fixed half.
    # Left in place only so an un-migrated caller doesn't break; the
    # router now prefers the remainder pair whenever it's set.
    is_half_partial: bool = False
    # September 2026 change: mirrors the invoice's own
    # PurchaseInvoiceLineItemIn.remainder_quantity/remainder_unit_label
    # exactly -- an optional smaller-unit leftover on top of
    # unit_count (e.g. "20 cartons + 3 dozens"), or one of the two
    # fixed half-pack labels ("half_carton"/"half_dozen", which need
    # no quantity -- picking the label IS the quantity).
    remainder_quantity: int | None = Field(default=None, ge=1)
    remainder_unit_label: str | None = Field(default=None, min_length=1, max_length=40)
    # Total pieces actually pushed, typed directly -- overrides the
    # calculated total when set, so the warehouse figure can be
    # traced without opening the carton.
    quantity_pieces_override: int | None = Field(default=None, ge=0)
    notes: str | None = None


class PushableInvoiceLineItemOut(BaseModel):
    """One purchase-invoice line item that's eligible to be pushed to
    the warehouse -- i.e. linked to a catalog variant, on an invoice
    for this branch (or a store-wide invoice), with some quantity not
    yet pushed. `already_pushed` / `remaining` are computed from prior
    store_to_warehouse StockTransfer rows against this line, not
    stored anywhere.
    """

    invoice_line_item_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_date: str
    supplier_id: uuid.UUID | None = None
    supplier_name: str
    item_name: str
    # Null when this line couldn't be resolved to a catalog product --
    # no product_id link from invoice entry, and no exact name match
    # either (e.g. invoice says "VIVA SOAP", catalog has "viva"). Still
    # returned (not hidden) so it shows in the push-to-warehouse picker,
    # flagged via is_unmatched, so the person can fix the link instead
    # of the item silently disappearing.
    product_id: uuid.UUID | None = None
    is_unmatched: bool = False
    unit_label: str
    quantity: int
    already_pushed: int
    remaining: int
    # September 2026 change: the invoice line's own compound-unit
    # breakdown, previously dropped entirely once an item reached
    # this picker -- see PurchaseInvoiceLineItem for what each means.
    pieces_per_carton: int | None = None
    remainder_quantity: int | None = None
    remainder_unit_label: str | None = None
    total_pieces: int | None = None
    # Component view of the line (Sept 2026, second revision). Cartons
    # and dozens are in HALVES (1 half = a half carton/dozen) so a
    # half-pack is exact; pieces are plain. "remaining" = what is left
    # after every earlier push against this line.
    cartons_total_halves: int = 0
    cartons_remaining_halves: int = 0
    dozens_total_halves: int = 0
    dozens_remaining_halves: int = 0
    bags_total_halves: int = 0
    bags_remaining_halves: int = 0
    pieces_total: int = 0
    pieces_remaining: int = 0
    # PATCH5C-WAREHOUSE-PACK-PREFILL: the product's own pack-size defaults (may be null).
    default_pieces_per_carton: int | None = None
    default_pieces_per_bag: int | None = None
    default_pieces_per_roll: int | None = None
    # PATCH6C-LEFT-FIGURE: pieces of this line already received in Receive Goods, and pieces
    # still outstanding. Both stay null unless the line has a known total AND something
    # was received, so lines without receipts look exactly as before.
    received_pieces: int | None = None
    outstanding_pieces: int | None = None


class LinkInvoiceLineItemProductIn(BaseModel):
    """Body for permanently linking an invoice line item to a catalog
    product, when it wasn't linked at invoice-entry time and its name
    doesn't exactly match any catalog product name either.
    """

    product_id: uuid.UUID

    model_config = {"from_attributes": True}


class WarehouseTransferToBranchCreate(WarehouseMoveCreate):
    to_branch_id: uuid.UUID


class WarehouseStockUnitBreakdown(BaseModel):
    unit_label: str
    unit_count: int


class WarehouseStockOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None = None
    name: str | None = None
    category_name: str | None = None
    quantity_pieces: int
    unit_breakdown: list[WarehouseStockUnitBreakdown] = []
    # Server-built line describing what was actually pushed, e.g.
    # "2½ cartons + 1 dozen + 6 pieces".
    breakdown_text: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}
