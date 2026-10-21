import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------- BRANDS

class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class BrandOut(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


# --------------------------------------------------------- CATEGORIES

class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    product_count: int = 0

    model_config = {"from_attributes": True}


# ----------------------------------------------------------- PRODUCTS
# Update spec ("nick-cosmetics-update-spec-3.md", Sections 1-3 & 6):
# there is no more "variant" concept anywhere in this flow. A product
# is one sellable row -- name, category (plus optional extra
# categories), SKU, price, reorder level, optional size, optional
# expiration date. Prices are accepted/returned in Naira (human-
# friendly) at the API boundary and converted to/from integer kobo
# internally via nickcosmetics_core.domain.money.Money (see the
# router).

_SIZE_PATTERN = "^[A-Za-z0-9][A-Za-z0-9 /.'\\-]{0,39}$"


class ProductCreate(BaseModel):
    # Section 1: "Product name = Brand name + byproduct, concatenated
    # automatically". The router derives `name` from brand.name +
    # byproduct when both are supplied; `name` itself can still be
    # sent directly (and is required if brand_id/byproduct aren't
    # both given) so the warehouse "quick add product" flow, which
    # only ever prompts for a plain name, keeps working unchanged.
    name: str | None = Field(default=None, min_length=1, max_length=300)
    brand_id: uuid.UUID | None = None
    byproduct: str | None = Field(default=None, max_length=200)
    description: str | None = None

    category_id: uuid.UUID | None = None
    # Section 6: a product can additionally be filed under any number
    # of extra categories beyond the primary one above.
    extra_category_ids: list[uuid.UUID] = []

    # Optional so the warehouse "quick add product" flow (which only
    # prompts for a name) doesn't 422 on a missing SKU -- the router
    # auto-generates one from the product name when this is left blank.
    sku: str | None = Field(default=None, max_length=100)
    barcode: str | None = None
    unit_of_measure: str = "each"

    # Section 1: optional Big/Medium/Small size.
    size: str | None = Field(default=None, pattern=_SIZE_PATTERN)
    # Section 3: optional expiration date, always shown on the form
    # (never gated behind a checkbox).
    expiration_date: date | None = None

    # Cost stays an internal, optional reference figure -- not
    # something the Add Product form requires -- since the real cost
    # of what's in stock is captured per-batch when stock is actually
    # received (StockBatch.unit_cost_kobo).
    cost_price_naira: float = Field(default=0, ge=0)
    selling_price_naira: float = Field(default=0, ge=0)
    reorder_level: int = Field(default=10, ge=0)
    # PATCH5A-PACK-SIZES: optional default pieces per pack (empty = no default).
    default_pieces_per_carton: int | None = Field(default=None, ge=1)
    default_pieces_per_bag: int | None = Field(default=None, ge=1)
    default_pieces_per_roll: int | None = Field(default=None, ge=1)
    # September 2026 change spec section 22 -- both null (the default)
    # means no wholesale tier is configured; POS only applies the rule
    # once both are set.
    wholesale_price_naira: float | None = Field(default=None, ge=0)
    wholesale_min_quantity: int | None = Field(default=None, ge=1)
    # September 2026 change spec section 15: "add a product to the
    # catalog so it exists for later" -- draft stays dormant/non-live
    # until someone later opens it from Inventory and fills in price/
    # stock to make it sellable. Defaults to active for every other
    # existing caller (the ordinary Add Product flow, the warehouse
    # quick-add flow) that doesn't send this at all.
    status: str | None = Field(default=None, pattern="^(active|discontinued|draft)$")

    @field_validator("name")
    @classmethod
    def _blank_name_to_none(cls, v):
        if v is not None and not v.strip():
            return None
        return v


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    brand_id: uuid.UUID | None = None
    byproduct: str | None = Field(default=None, max_length=200)
    description: str | None = None
    category_id: uuid.UUID | None = None
    extra_category_ids: list[uuid.UUID] | None = None
    sku: str | None = Field(default=None, min_length=1, max_length=100)
    barcode: str | None = None
    size: str | None = Field(default=None, pattern=_SIZE_PATTERN)
    expiration_date: date | None = None
    cost_price_naira: float | None = Field(default=None, ge=0)
    selling_price_naira: float | None = Field(default=None, ge=0)
    reorder_level: int | None = Field(default=None, ge=0)
    # PATCH5A-PACK-SIZES: null clears a default.
    default_pieces_per_carton: int | None = Field(default=None, ge=1)
    default_pieces_per_bag: int | None = Field(default=None, ge=1)
    default_pieces_per_roll: int | None = Field(default=None, ge=1)
    # September 2026 change spec section 22 -- send both to configure
    # a wholesale tier, or both as null to remove one.
    wholesale_price_naira: float | None = Field(default=None, ge=0)
    wholesale_min_quantity: int | None = Field(default=None, ge=1)
    status: str | None = Field(default=None, pattern="^(active|discontinued|draft)$")
    out_of_stock_alert_muted: bool | None = None
    # Super-admin-only visibility toggle (feature spec 2) -- the router
    # gates setting this to a *different* value behind require_role
    # ("super_admin") on top of the usual manage_products check, since
    # a branch_manager can otherwise edit products but shouldn't be
    # able to flip this particular field.
    hidden_from_staff: bool | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    # Mirrors `id` -- kept so any existing caller that reads
    # `product_id` off a catalog row (e.g. POS checkout, which
    # identifies cart lines by product_id) keeps working now that a
    # product and what used to be "the variant" are the same row.
    product_id: uuid.UUID | None = None
    sku: str
    barcode: str | None
    name: str
    byproduct: str | None = None
    description: str | None
    brand_id: uuid.UUID | None
    brand_name: str | None = None
    category_id: uuid.UUID | None
    category: str | None = None
    category_name: str | None = None
    extra_category_ids: list[uuid.UUID] = []
    extra_category_names: list[str] = []
    size: str | None = None
    expiration_date: date | None = None
    status: str
    unit_of_measure: str
    hidden_from_staff: bool = False
    image_url: str | None = None
    images: list[str] = []
    cost_price_naira: float = 0
    selling_price_naira: float = 0
    # September 2026 change spec section 22.
    wholesale_price_naira: float | None = None
    wholesale_min_quantity: int | None = None
    price: float | None = None
    price_naira: float | None = None
    quantity_available: int = 0
    reorder_level: int = 0
    out_of_stock_alert_muted: bool = False
    needs_invoice: bool = False
    # PATCH5A-PACK-SIZES
    default_pieces_per_carton: int | None = None
    default_pieces_per_bag: int | None = None
    default_pieces_per_roll: int | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("category", mode="before")
    @classmethod
    def _category_to_name(cls, v):
        # `Product.category` (as loaded from the DB) is a SQLAlchemy
        # `Category` relationship object, not a string -- validating
        # it directly against this str field crashed with a
        # ValidationError before the router ever got a chance to
        # convert it. This coerces it to `.name` (or None) up front,
        # so model_validate(product) succeeds regardless of whether
        # the router already normalized it.
        if v is None or isinstance(v, str):
            return v
        return getattr(v, "name", None)

    @field_validator("images", mode="before")
    @classmethod
    def _images_to_urls(cls, v):
        # Same issue as `category` above: `Product.images` (as loaded
        # from the DB) is a list of `ProductImage` relationship
        # objects, not strings -- validating it directly against this
        # list[str] field crashed with a ValidationError before the
        # router's manual `data.images = [...]` reassignment ever ran.
        # This coerces each item to `.url` up front, so
        # model_validate(product) succeeds regardless of whether the
        # router already normalized it.
        if v and hasattr(v[0], "url"):
            return [img.url for img in v]
        return v


class ProductImageOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID | None = None
    url: str
    is_primary: bool
    sort_order: int

    model_config = {"from_attributes": True}


class ImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    errors: list[str] = []


# ---------------------------------------------- INVENTORY DELETION AUDIT
# Spec sections 8-10: a product must never simply disappear when
# deleted -- the user supplies a reason and the system keeps a
# traceable, loss-classified record of it.

_DELETION_REASONS = "damaged|expired|lost|spoiled|incorrect_entry|duplicate|other"


class ProductDeleteRequest(BaseModel):
    reason: str = Field(pattern=f"^({_DELETION_REASONS})$")
    # Required when reason == "other" -- enforced in the router, since
    # a pattern-only Field can't express "required if X" on its own.
    reason_note: str | None = Field(default=None, max_length=1000)


class InventoryDeletionRecordOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID | None = None
    product_name: str
    category_name: str | None = None
    sku: str | None = None
    unit_of_measure: str | None = None
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None
    quantity_affected: int
    reason: str
    reason_note: str | None = None
    is_loss: bool
    deleted_by: uuid.UUID | None = None
    deleted_by_name: str | None = None
    created_at: datetime
    # The product row survives a soft delete (only is_deleted flips),
    # so its image is still there to show in the history list -- staff
    # asked to be able to see (and zoom into) the item's photo here,
    # the same way they can on the main inventory table.
    image_url: str | None = None
    # September 2026 change spec section 8: "Restore" only makes sense
    # on the deletion event that's still in effect -- if the product
    # was since restored (or hard-deleted, or doesn't exist at all
    # anymore), this is False and the frontend hides the action rather
    # than offering a Restore button that 404s.
    product_is_deleted: bool = False
    # True once the product has additionally been permanently deleted
    # (see POST /catalog/products/{id}/hard-delete) -- distinct from
    # product_is_deleted above, which only means "soft-deleted /
    # restorable". Once hard-deleted, Restore should never be offered.
    product_hard_deleted: bool = False

    model_config = {"from_attributes": True}


class InventoryDeletionReclassify(BaseModel):
    """Super-admin-only correction after the fact (spec section 10:
    "The Super Admin should have appropriate authority to review/
    classify these records")."""

    is_loss: bool | None = None
    reason_note: str | None = Field(default=None, max_length=1000)
