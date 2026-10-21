"""SQLAlchemy ORM models — infrastructure layer only.

Maps to the schema in docs/architecture/phase1-architecture.md Section 4.
Domain logic never imports from here directly — repository
implementations (infrastructure/db/repositories.py, added later)
translate between these ORM rows and the framework-agnostic entities
in nickcosmetics_core.domain.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _utcnow() -> datetime:
    # The old `datetime.utcnow()` used everywhere below (until this fix)
    # returns a
    # *naive* datetime -- no tzinfo attached. Depending on the DB driver's
    # round-trip, that naive value can come back out and get serialized to
    # JSON with no UTC offset/'Z' suffix, and the browser's `new Date(...)`
    # then reads an offset-less datetime string as *local browser time*,
    # not UTC. On a Nigeria-based machine (WAT, UTC+1) that silently shifts
    # every timestamp by an hour -- exactly the "POS clock is an hour off"
    # bug reported. Using an explicit tz-aware UTC value here guarantees
    # the offset survives serialization so the frontend's Africa/Lagos
    # formatting (see js/api.js formatDateTime) converts correctly.
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- USERS/AUTH

class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Address-first, per spec ("input the address... After inputting the
    # address... naming that particular branch should actually be
    # there"). `name` stays required rather than optional -- it's used
    # as the display label everywhere in the app (branch badge, POS,
    # dropdowns, reports), and making it nullable would mean adding a
    # fallback-to-address everywhere that currently assumes a name
    # exists. `phone` and `is_warehouse` are dropped outright: phone
    # was unused anywhere except this form, and is_warehouse was
    # already dead (see the removed "Warehouse" comment this used to
    # sit next to) -- the spec is explicit that "a branch is a branch,
    # and a branch also has its own warehouse [implicitly]", not a
    # flag some branches carry and others don't.
    # Nullable at the DB level (existing branches created before this
    # change may not have one) but required going forward via
    # BranchCreate -- see api/v1/schemas/branches.py.
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Shift-closing grace period (spec section 19): if set, a manual
    # close after this Lagos-local time + grace_minutes on the shift's
    # own opening day is recorded as a Late Close rather than an
    # ordinary Manual Close (still not a compliance violation on its
    # own -- only a genuine auto-close is). All nullable/optional --
    # branches that never configure this simply never get "Late Close"
    # shifts, only "Manually Closed" or "Auto-Closed".
    shift_expected_close_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shift_expected_close_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shift_grace_minutes: Mapped[int] = mapped_column(Integer, default=10)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # 'role' was previously a fixed CheckConstraint enum
        # (super_admin/branch_manager/staff) -- replaced by this FK to
        # the roles table (migration o1p2q3r4s5t6) so a super_admin can
        # define further roles (e.g. "assistant_branch_manager") beyond
        # the three built-in ones. See the Role class below.
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(
        String,
        ForeignKey("roles.name", use_alter=True, name="fk_users_role_roles_name"),
        nullable=False,
    )
    # Human-facing employee code ("EMP-0001"), assigned once by an
    # admin at staff-application acceptance time (see
    # StaffApplication / api/v1/routers/staff_applications.py). Null
    # for accounts created directly via POST /users (no application
    # flow) and for applicants still under review.
    employee_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    date_joined: Mapped[date | None] = mapped_column(Date, nullable=True)

    # User-level profile photo -- lets ANY account customize a photo,
    # including one created directly via POST /users (no staff
    # application). Previously photo upload only wrote to
    # StaffApplication.profile_photo_url, so admin-created accounts got
    # "This account has no staff application profile to attach a photo
    # to" and could never set one. GET /me/profile now prefers this
    # column and falls back to the application's photo (for accounts
    # that already had one there) -- see profile.py's _to_profile_out.
    profile_photo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Same reasoning as profile_photo_url above -- lets any account set a
    # display name, not only ones with a staff application.
    preferred_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # -- Forgot-password (spec Section 22) --
    # Only the hash is stored, same reasoning as password_hash itself --
    # see infrastructure/security/tokens.py. Single-use: reset_password()
    # clears both columns immediately after a successful reset.
    password_reset_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- Email change with verification (spec Section 17) --
    # The verified `email` column above is left untouched until the new
    # address is confirmed; these three hold the pending state in the
    # meantime. pending_email_token_hash follows the same hash-only
    # storage as the password-reset token.
    pending_email: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_email_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_email_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Permission names revoked from this specific account on top of
    # whatever their role would normally grant -- lets a super_admin
    # dial back an individual branch_manager (or staff) without
    # touching the role-wide table in nickcosmetics_core. Empty/absent
    # means "no restrictions beyond their role". See
    # api/v1/routers/users.py's permissions endpoint and
    # nickcosmetics_core.domain.users.user.User.can().
    denied_permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # The additive counterpart to denied_permissions: extra permission
    # names granted to this one account on top of whatever their role
    # normally allows, without upgrading their role -- spec: "I have a
    # particular staff I trust so much... I should be able to give that
    # particular staff a particular editable role... it shouldn't be
    # that they upgrade his role, I just come and edit it". A permission
    # present in both denied_permissions and granted_permissions is
    # denied -- api/dependencies.py checks denial first regardless of
    # role or grants, same as it always has, so a grant can never be
    # used to bypass an explicit denial on the same account.
    granted_permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # PATCH25B-PASSWORD-CHANGED-AT: when the password last changed. A token issued
    # before this moment is refused (see infrastructure/security/jwt.py). Empty = no limit.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Role(Base):
    """A named permission set a User's `role` column points to (FK on
    role.name). Super_admin, branch_manager, and staff are seeded as
    system rows (is_system=True, can't be deleted or renamed) by
    migration o1p2q3r4s5t6 -- everything from that point on, including
    those three, is just a row here rather than a hardcoded enum, so a
    super_admin can define further roles (e.g.
    "assistant_branch_manager") with their own permission set.

    Editing `permissions` on an existing row changes what everyone
    currently holding that role can do immediately -- there's no
    per-user copy to go out of sync, since api/dependencies.py's
    require_permission() reads this table directly on every request
    rather than a fixed permission table. A User's own
    `denied_permissions` (see above) still applies on top of whatever
    this grants, for restricting one specific account below their
    role's default.
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class StaffApplication(Base):
    """A person's staff employment application -- created together
    with their (login-only) User row at self-registration time, per
    docs upgrade spec Section 3/30. Deliberately one row per applicant,
    not a duplicate "employee" table: once accepted, the same row
    keeps serving as the profile record staff/admins read from (see
    api/v1/routers/staff_applications.py and users.py's profile
    endpoints), and its `status` also carries the post-acceptance
    suspended state so there's a single source of truth for "can this
    person use protected business features right now" (checked in
    api/dependencies.py's require_role/require_permission).

    A User with no StaffApplication row at all (e.g. created directly
    by an admin via POST /users) is never gated by this table --
    that's the pre-existing account-creation path and stays exactly
    as it was.
    """

    __tablename__ = "staff_applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('under_review','accepted','rejected','suspended')",
            name="ck_staff_application_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="under_review")
    # What the applicant asked for -- the admin still has the final say
    # and can pick a different role at accept time (spec Section 10);
    # this is just their stated preference, shown to the reviewer as a
    # starting point. Deliberately excludes super_admin -- self-applying
    # for the highest access tier isn't offered on the registration form.
    role_applied_for: Mapped[str] = mapped_column(String, nullable=False, default="staff")
    # Which branch they're applying to work at, chosen by the applicant
    # themselves at registration (not assigned later by the reviewer --
    # see StaffApplicationAccept.branch_id, which now just carries this
    # value forward by default but a reviewer can still override it at
    # accept time if needed). Nullable only because applications
    # submitted before this column existed have nothing to backfill it
    # with; every new submission is required to set it.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)

    # -- Basic information (spec Section 3) --
    full_legal_name: Mapped[str] = mapped_column(String, nullable=False)
    preferred_name: Mapped[str] = mapped_column(String, nullable=False)
    profile_photo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    # Current residential address (where they live now).
    residential_address: Mapped[str | None] = mapped_column(String, nullable=True)
    # Origin -- distinct from current residence: state/LGA/hometown/
    # address "back home". state_of_residence is kept as a legacy
    # column (a handful of applications submitted before this change
    # populated it) but is no longer written to by the registration
    # form.
    state_of_residence: Mapped[str | None] = mapped_column(String, nullable=True)
    state_of_origin: Mapped[str | None] = mapped_column(String, nullable=True)
    lga_of_origin: Mapped[str | None] = mapped_column(String, nullable=True)
    hometown: Mapped[str | None] = mapped_column(String, nullable=True)
    home_address_of_origin: Mapped[str | None] = mapped_column(String, nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    emergency_contact_relationship: Mapped[str | None] = mapped_column(String, nullable=True)

    # -- Identification information (spec Section 3) --
    id_type: Mapped[str | None] = mapped_column(String, nullable=True)
    id_number: Mapped[str | None] = mapped_column(String, nullable=True)
    # Path on private disk storage, NEVER a publicly-servable URL --
    # see PRIVATE_UPLOAD_ROOT in api/v1/routers/staff_applications.py
    # and its authenticated-only download endpoint.
    id_document_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # No longer collected on the registration form (kept as legacy,
    # nullable columns for any applications submitted before this
    # change).
    id_issued_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    id_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # -- Review trail --
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])


# ------------------------------------------------------------------ CATALOG

class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class MeasurementUnit(Base):
    """Measuring units for a product's size (mg, g, ml ... plus any the
    owner adds himself). Built-ins are seeded and can't be removed."""

    __tablename__ = "measurement_units"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    group_name: Mapped[str] = mapped_column(String, nullable=False, default="Custom")
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"), nullable=True)

    # NOTE: the old per-category VariantType/VariantOption configuration
    # (variant-category-system-spec.md) was retired by update spec 3,
    # Section 6 -- "Categories and Variants" is now just "Product
    # Categories", plain category management plus the ability to file
    # one product under several categories (see ProductCategory).


class ProductTemplate(Base):
    """Update Request item 8: "Product Categories" was reworked from a
    page that only assigns *existing* Product rows to categories, into
    a lightweight catalog of product-name templates that don't need to
    exist in live inventory yet -- e.g. filing "Nivea Lotion" here with
    its usual category and sizes once, so Add Product can offer it as
    a starting point later without anyone having typed it in before.

    Deliberately NOT a variant system -- the per-category
    VariantType/VariantOption setup mentioned on Product above was
    retired for a reason (Section 6 of the prior update spec). This is
    just a name plus a set of remembered (category, sizes,
    byproduct-variant) combinations; it never creates or constrains
    an actual Product row. A real Product created from a template is
    an entirely ordinary Product afterward, no link back to the
    template it came from.
    """

    __tablename__ = "product_templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    categories: Mapped[list["ProductTemplateCategory"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class ProductTemplateCategory(Base):
    """One category a ProductTemplate has been configured under, plus
    which of the four fixed sizes (Big/Medium/Small/Standard) and
    which free-text byproduct variants apply for that combination.
    A template can be attached to several categories (unique per
    template+category pair) -- e.g. "Vaseline" as both "Body Care"
    and "Baby Care" with different size sets for each.
    """

    __tablename__ = "product_template_categories"
    __table_args__ = (UniqueConstraint("template_id", "category_id", name="uq_template_category"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_templates.id"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"), nullable=False)
    sizes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    byproduct_variants: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    template: Mapped["ProductTemplate"] = relationship(back_populates="categories")
    category: Mapped["Category"] = relationship()


class Product(Base):
    """A sellable catalog item.

    Update spec ("nick-cosmetics-update-spec-3.md", Sections 1-3 & 6)
    retires the Product/VariantType/VariantOption system that
    used to sit underneath this row: there is no more "variant"
    concept anywhere in the product-creation flow or API surface.
    Every field that used to live on Product (sku, barcode,
    cost/selling price, reorder level, out_of_stock_alert_muted) now
    lives directly on Product -- one row is one sellable unit.

    `name` is auto-derived server-side as "<brand.name> <byproduct>"
    (Section 1) and stored (not computed at read time) so it can be
    indexed/searched/sorted like any other product name and so
    historical sales/receipts keep showing the name as it was at sale
    time even if the brand is later renamed.

    `size` is a free-text optional attribute -- originally fixed to
    Big/Medium/Small only, but Update Request item 5 asked for a
    "quick add size" so more than those three can be entered on the
    fly, so the DB-level CheckConstraint pinning it to those three
    values was dropped (see alembic aa11bb22cc33). It's still
    typically one of Big/Medium/Small/Standard (the four fixed choices
    the Product Categories template configurator offers -- see
    ProductTemplateCategory), just no longer enforced as a closed set
    at the database layer.
    """

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("status IN ('active','discontinued','draft')", name="ck_product_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sku: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("brands.id"), nullable=True)
    # The byproduct half of the name (e.g. "Cocoa Butter") -- kept as
    # its own column (rather than only baked into `name`) so the add-
    # product form can re-derive/re-render "<brand> <byproduct>" if the
    # brand is changed, and so it can be searched/edited independently.
    byproduct: Mapped[str | None] = mapped_column(String, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    unit_of_measure: Mapped[str] = mapped_column(String, default="each")
    # Section 1: optional Big/Medium/Small size attribute.
    size: Mapped[str | None] = mapped_column(String, nullable=True)
    # Section 3: optional expiration date, always shown on the form.
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Merged in from the retired Product model -- see class
    # docstring above.
    cost_price_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    selling_price_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # September 2026 change spec section 22: a standing quantity-
    # threshold rule on the product itself (e.g. "10+ pieces ->
    # 4500/unit instead of 5000"), applied automatically at POS once a
    # cart line for this product reaches wholesale_min_quantity --
    # supersedes the earlier idea of generated coupon codes for
    # wholesale customers, which was explicitly dropped in favor of
    # this. Both null (the common case) means no wholesale tier is
    # configured for this product; either can be set without the
    # other being filled in yet, but POS only ever applies the rule
    # once both are present.
    wholesale_price_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    wholesale_min_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reorder_level: Mapped[int] = mapped_column(Integer, default=10)
    # PATCH5A-PACK-SIZES: optional default pieces in one pack of this product
    # (a dozen is always 12). Empty means no default. Screens prefill
    # from these; a number typed on an invoice line always wins.
    default_pieces_per_carton: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_pieces_per_bag: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_pieces_per_roll: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Dismisses this product from the dashboard's out-of-stock alert
    # (not the ordinary Low Stock/Inventory pages) when someone
    # doesn't intend to restock it soon. Auto-cleared on the next real
    # restock -- see POST /catalog/products/{id}/restock.
    out_of_stock_alert_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Super-admin-only per-product visibility toggle (staff warehouse/
    # inventory spec, feature spec 2) -- distinct from the role-based
    # ROLE_PERMISSIONS system in web_admin/js/permissions.js. When true,
    # a request made by a `staff`-role user must never see this product
    # via GET /catalog/products or /catalog/products/{id} -- enforced
    # server-side in api/v1/routers/catalog.py, not just hidden in the
    # frontend. Naming follows the existing `is_deleted` boolean-flag
    # convention on this same model.
    hidden_from_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    # September 2026 change: a soft-deleted product can additionally be
    # "permanently deleted" from the Inventory Deletion History view --
    # this never touches StockBatch/SaleItem/PurchaseInvoiceLineItem/etc,
    # it only flags the row as no longer restorable/re-offerable. See
    # POST /catalog/products/{id}/hard-delete.
    hard_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Sept 2026: tracks whether a brand-new, non-draft product still
    # has an unresolved invoice-prompt decision (see the Add Product ->
    # Invoice Workflow router logic) -- cleared to False the moment
    # it's filed onto any purchase invoice draft, new or existing.
    # Lets Inventory surface a persistent "Awaiting invoice" filter
    # instead of losing the decision when the prompt is skipped or the
    # page reloads. See migration i1j2k3l4m5n6.
    needs_invoice: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    images: Mapped[list["ProductImage"]] = relationship(back_populates="product")
    category: Mapped["Category | None"] = relationship()
    brand: Mapped["Brand | None"] = relationship()
    # Section 6: a product can additionally be filed under any number
    # of extra categories beyond the primary `category_id` above --
    # managed from the renamed "Product Categories" page.
    extra_categories: Mapped[list["ProductCategory"]] = relationship(back_populates="product")


class ProductCategory(Base):
    """One additional category a product has been filed under, beyond
    its primary `Product.category_id` (Section 6: "assign multiple
    categories to a single product"). The primary category is kept as
    a plain FK on Product (still "selected normally" at creation, per
    Section 1, and still what every existing category_id-based query
    elsewhere in the app reads); this table is purely additive.
    """

    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("product_id", "category_id", name="uq_product_category"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    product: Mapped["Product"] = relationship(back_populates="extra_categories")
    category: Mapped["Category"] = relationship()


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="images")


# --------------------------------------------------------- SUPPLIERS/PURCHASING

class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','ordered','partially_received','received','cancelled')",
            name="ck_po_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft")
    ordered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="purchase_order")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity_ordered: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_received: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)

    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="items")


# ---------------------------------------------------------- STOCK (FIFO BATCHES)

class StockBatch(Base):
    __tablename__ = "stock_batches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Nullable now: a NULL product_id plus custom_name is a freeform
    # item the person typed in directly (not in the catalog at all --
    # e.g. a brand-new thing to order that hasn't been added as a
    # Product yet). The check constraint below enforces that every row
    # is one or the other, never neither.
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    custom_name: Mapped[str | None] = mapped_column(String, nullable=True)
    custom_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    purchase_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_order_items.id"), nullable=True
    )
    batch_number: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity_received: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    product: Mapped["Product"] = relationship()


class StockAdjustment(Base):
    """A change to a stock batch's remaining quantity outside the
    normal sell-through path -- almost always a *loss*: expired stock
    pulled off the shelf, damaged/defective goods, a recount that
    turns up missing units, or stock sent back to the supplier.
    (`recount` can also go positive, e.g. a physical count finds more
    on the shelf than the system expected -- that's the one reason
    that isn't a loss.)

    Spec section 1 (Super Admin dashboard Losses): every reason below
    that represents stock the business will never sell must be
    capturable here with its branch and its value at the time it was
    lost, so a dashboard total/breakdown can be computed without
    re-deriving historical cost from whatever the batch's cost happens
    to be *now*.
    """

    __tablename__ = "stock_adjustments"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('damaged','expired','theft','recount','other',"
            "'missing','defective','supplier_return')",
            name="ck_adjustment_reason",
        ),
        CheckConstraint(
            "damage_caused_by IN ('customer','staff') OR damage_caused_by IS NULL",
            name="ck_adjustment_damage_caused_by",
        ),
        CheckConstraint(
            "theft_caused_by IN ('customer','staff') OR theft_caused_by IS NULL",
            name="ck_adjustment_theft_caused_by",
        ),
        CheckConstraint(
            "status IN ('completed','pending_confirmation')",
            name="ck_adjustment_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    stock_batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_batches.id"), nullable=False)
    # Denormalized from stock_batch.branch_id at creation time so
    # branch-isolated loss queries/reports don't need a join, and so a
    # branch's loss history stays intact even if the batch itself is
    # later deleted. Nullable only for rows created before this column
    # existed; every new row sets it.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    adjusted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    # Monetary value of this adjustment, captured from the batch's
    # unit_cost_kobo x |quantity_delta| at the moment it was recorded
    # -- so the dashboard's "Total Losses" figure reflects what the
    # stock actually cost the business, not today's cost if it's
    # since changed. Null for a positive (non-loss) recount, and for
    # rows created before this column existed.
    value_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Optional link back to the waybill/damaged-item complaint (see
    # IntakeReceipt) this loss was recorded from, e.g. "20 cartons
    # damaged on this delivery" -> a supplier_return or damaged loss
    # entry referencing that same complaint -- so the two records
    # (what was reported vs. what was actually written off) stay
    # traceable to each other instead of being two disconnected trails.
    intake_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("intake_receipts.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Update Request item 11: accountability fields for the two
    # "unretrievable" reasons that most need someone to be answerable
    # for what happened. Both nullable and only ever populated for
    # their one matching reason -- damaged/theft each ignore the
    # other's columns entirely.
    #   damaged  -> damage_caused_by ('customer'|'staff') and, if
    #               'staff', damage_staff_name identifies who.
    #   theft    -> theft_staff_on_duty lists everyone on shift at the
    #               branch that day (names, not a hard FK -- rosters
    #               aren't tracked per-shift elsewhere), and
    #               theft_noticed_at is the approximate time the item
    #               was noticed missing (spec: "optionally").
    damage_caused_by: Mapped[str | None] = mapped_column(String, nullable=True)
    damage_staff_name: Mapped[str | None] = mapped_column(String, nullable=True)
    theft_staff_on_duty: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    theft_noticed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # September 2026 change spec section 4 additions below.
    #   theft_caused_by / theft_by_staff_name -- mirrors damaged's own
    #   caused_by/staff_name pair, but for WHO did it (as opposed to
    #   theft_staff_on_duty above, which is WHO WAS ON SHIFT -- a
    #   theft can be pinned on a specific staff member who wasn't even
    #   working that day, e.g. found out later).
    #   image_url -- required at the API layer (not the DB) for
    #   damaged/theft/expired: "all three require image or video
    #   evidence upload".
    #   status -- 'completed' immediately for every reason except
    #   theft, which starts 'pending_confirmation' (quantity is NOT
    #   yet deducted -- see create_stock_adjustment) until a super
    #   admin confirms it via confirm_theft_adjustment, matching spec:
    #   "Once confirmed, deduct from inventory and record as loss; a
    #   completed stolen-item report shouldn't remain open as a live
    #   complaint."
    #   reviewed_by/reviewed_at -- generic "an admin has seen this"
    #   marker, backing the dashboard notification for damaged/expired
    #   (theft's own confirmation sets these at the same time as
    #   confirming, rather than needing a separate acknowledgement).
    theft_caused_by: Mapped[str | None] = mapped_column(String, nullable=True)
    theft_by_staff_name: Mapped[str | None] = mapped_column(String, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    stock_batch: Mapped["StockBatch"] = relationship()


# ------------------------------------------------------- WAREHOUSE / INTAKE
# "Warehouse" is just a Branch with is_warehouse=True (see Branch above)
# -- no separate warehouse entity needed. These two tables cover the two
# things the till floor asked for: (1) a snapshot of the paper receipt
# that comes in with a wholesale delivery, and (2) a record of how much
# of that delivery got walked back to the warehouse branch afterward,
# in whatever unit it was actually counted in (carton/roll/piece).

class IntakeReceipt(Base):
    """A waybill complaint -- damaged or missing items found in a
    supplier delivery, logged against the purchase invoice that
    delivery was on (see PurchaseInvoice), with a photo or video of
    the damage as evidence. Was originally just a free-standing
    "delivery receipt" photo log with a typed-in supplier name; now
    tied to an actual invoice so the supplier/date come from a real
    record instead of being retyped, and the complaint is traceable
    back to what was actually ordered.
    """

    __tablename__ = "intake_receipts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    # Which delivery this complaint is about -- selected by the
    # reporter from a period-filtered list of purchase invoices (spec:
    # "a menu that comes up that asks when this item came into the
    # store... a drop-down of the invoice"). Nullable for rows filed
    # before this link existed, or filed without a matching invoice on
    # record.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_invoices.id"), nullable=True)
    # Which specific line on that invoice this complaint is about --
    # spec: "you should be able to select that particular item that
    # is from that particular invoice... this is the product that you
    # gave me 30 cartons. You select that particular one." Nullable:
    # not every complaint necessarily maps to one typed-in line (older
    # invoices filed before line items existed, or a complaint that's
    # about the delivery as a whole rather than one product).
    invoice_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_invoice_line_items.id"), nullable=True
    )
    # Photo OR video evidence of the damaged/missing items (spec:
    # "upload the image or the video") -- same column, content-type
    # agnostic; see _ALLOWED_MEDIA_EXTENSIONS in warehouse.py.
    image_url: Mapped[str] = mapped_column(String, nullable=False)
    # Auto-filled from the selected invoice's supplier when invoice_id
    # is set (see the router) -- kept as a plain column rather than
    # always joining through invoice_id, so a complaint filed without
    # a matching invoice still shows a supplier name if one was given.
    supplier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    damaged_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-typed product name for a complaint that isn't tied to one
    # invoice_line_item_id -- either the invoice was only ever a photo
    # (no typed lines to pick from), or the reporter typed the name
    # themselves. Only ever set when invoice_line_item_id is None.
    manual_item_name: Mapped[str | None] = mapped_column(String, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WarehouseStock(Base):
    """Stock physically held in a branch's own back-of-house warehouse
    -- separate from what's on the shop floor and sellable (that's
    still StockBatch/branch_id as everywhere else). Spec: "a shop...
    can only contain just two cartons... the remaining [cartons] are
    now going to be stored somewhere that whenever they need it, they
    will go there, pick it, and bring it to the shop to restock."
    Deliberately its own ledger rather than a location flag on
    StockBatch, so none of the existing sale/POS/low-stock/inventory
    math (which reads StockBatch per branch) needs to change or risk
    silently counting backstock as sellable. A branch's warehouse is
    not a separate movable entity -- it's implicitly "this branch's
    own warehouse", keyed by branch_id like everything else.
    """

    __tablename__ = "warehouse_stock"
    __table_args__ = (
        UniqueConstraint("branch_id", "product_id", name="uq_warehouse_stock_branch_product"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity_pieces: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class StockTransfer(Base):
    """A stock movement logged in whatever unit the person counted it
    in (`unit_label`/`unit_count`/`pieces_per_unit` preserve that;
    `quantity_pieces` is the sale-unit total actually moved).
    `transfer_type` distinguishes what's actually moving:
      - 'branch_to_branch': shop-floor stock moved directly from one
        branch's sellable inventory to another's (no warehouse
        involved) -- the original/legacy behavior of this table.
      - 'store_to_warehouse': shop floor -> this branch's own
        warehouse (excess stock that doesn't fit on the floor).
      - 'warehouse_to_store': this branch's own warehouse -> its shop
        floor (restocking from backstock).
      - 'warehouse_to_branch': this branch's warehouse -> a
        *different* branch's shop floor directly.
    For the warehouse_* types, WarehouseStock (not StockBatch) is the
    source/destination on the warehouse side -- see warehouse.py.
    """

    __tablename__ = "stock_transfers"
    __table_args__ = (
        CheckConstraint(
            "transfer_type IN ('branch_to_branch','store_to_warehouse','warehouse_to_store','warehouse_to_branch','receipt_to_warehouse','receipt_to_shelf')",
            name="ck_stock_transfer_type",
        ),
        CheckConstraint(
            "payment_method IN ('cash','transfer') OR payment_method IS NULL",
            name="ck_stock_transfer_payment_method",
        ),
    )
    # unit_label was originally restricted to piece/carton/roll via a CHECK
    # constraint; dropped in migration m9n0o1p2q3r4 so the warehouse UI's
    # unit-label dropdown (carton, cartons, rolls, pieces, plus any
    # store-added "quick-add unit label") can save without a matching
    # migration each time a new label is added.

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    from_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    to_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    transfer_type: Mapped[str] = mapped_column(String, nullable=False, default="branch_to_branch")
    unit_label: Mapped[str] = mapped_column(String, nullable=False, default="piece")
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pieces_per_unit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quantity_pieces: Mapped[int] = mapped_column(Integer, nullable=False)
    # "5 cartons and a half" on a store_to_warehouse push -- an extra
    # half of whatever unit_label/pieces_per_unit already says, added
    # on top of unit_count. Only meaningful for store_to_warehouse;
    # always False for every other transfer_type. Superseded by
    # remainder_quantity/remainder_unit_label below (Sept 2026 change
    # -- push-to-warehouse now mirrors the invoice's own arbitrary
    # remainder, not just a fixed half); kept so older rows written
    # before this change still read correctly.
    is_half_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # September 2026 change: mirrors PurchaseInvoiceLineItem's own
    # remainder_quantity/remainder_unit_label exactly -- an optional
    # smaller-unit leftover on top of unit_count (e.g. "20 cartons +
    # 3 dozens"), or one of the two fixed half-pack labels
    # ("half_carton"/"half_dozen", which need no quantity -- picking
    # the label IS the quantity, same convention as the invoice side).
    # Only ever set for transfer_type='store_to_warehouse'.
    remainder_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remainder_unit_label: Mapped[str | None] = mapped_column(String, nullable=True)
    # Lets the total actually pushed be typed directly instead of
    # trusting unit_count * pieces_per_unit -- null means "use the
    # calculated figure" (see push_to_warehouse in warehouse.py).
    quantity_pieces_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intake_receipts.id"), nullable=True)
    # September 2026 change spec section 19: a branch_to_branch
    # transfer is priced at the item's original invoice/supplier cost
    # (the same quantity-weighted average FIFO cost basis already
    # computed for the destination batch, see _fifo_deduct in
    # warehouse.py), never the retail selling price -- this is an
    # internal accountability transfer between branches of the same
    # business, not a retail sale. Only set for transfer_type=
    # 'branch_to_branch'/'warehouse_to_branch'; null for the
    # warehouse-internal move types, which aren't a cross-branch
    # settlement.
    total_cost_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    # Only set for transfer_type='store_to_warehouse' -- the specific
    # purchase invoice line this push is documenting delivery against.
    # A push is only ever allowed for an item that's actually on an
    # invoice, and is capped at what that line says was bought (see
    # warehouse.py push_to_warehouse) -- unlike every other move type,
    # this one deliberately does NOT touch shop-floor StockBatch stock
    # at all; it's pure documentation of what's sitting in the back.
    invoice_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_invoice_line_items.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Component-shape fields (migration v5w6x7y8z9a0) -- populated only
    # when unit_label == "mixed"; see _wh_transfer_components/
    # _wh_component_total_pieces in warehouse.py. Each pair is
    # independent (cartons counted separately from dozens), plus a
    # standalone pieces_count with no "half" concept of its own.
    cartons_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cartons_pieces_per_unit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cartons_half: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dozens_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dozens_pieces_per_unit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dozens_half: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bags_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bags_pieces_per_unit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bags_half: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pieces_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set only on movements written by posting a goods receipt
    # (receipt_to_warehouse / receipt_to_shelf) so every unit that
    # entered through a receipt is traceable back to it.
    goods_receipt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goods_receipts.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------- PHONE-AS-SCANNER PAIRING

class ScanSession(Base):
    """Short-lived pairing between a desktop-till screen and a phone's
    camera, so a phone can stand in for a USB barcode scanner. The
    till creates one (shows `code` as text/QR), the phone page posts
    scanned barcodes against that code, and the till polls for the
    latest one. Deliberately not tied to a user session/auth beyond
    the random code -- same trust model as e.g. a Zoom meeting ID --
    since it never carries anything more sensitive than a barcode
    string and expires almost immediately.
    """

    __tablename__ = "scan_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    last_barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ------------------------------------------------------------------- SALES

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    addresses: Mapped[list["CustomerAddress"]] = relationship(back_populates="customer")


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    address_line: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str] = mapped_column(String, default="Nigeria")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    customer: Mapped["Customer"] = relationship(back_populates="addresses")


class Shift(Base):
    """A till session: opened with a counted starting float, closed
    with a counted ending amount, so cash can be reconciled per
    session. Unlike desktop's shift feature (browser/device-local
    SQLite, per docs/architecture and local_db/shifts.py), this is a
    real backend record -- deliberately, since web POS staff may use
    different devices/browsers, and a branch_manager needs to see
    who has a shift open right now from anywhere, not just from the
    till that opened it.
    """

    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("status IN ('open','closed')", name="ck_shift_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    opened_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    opening_float_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closing_counted_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # True when the system closed this shift automatically at end of day
    # (11:59pm WAT) because the staff never hit "close shift" -- see
    # _auto_close_stale_shifts in api/v1/routers/shifts.py. An
    # auto-closed shift always has closing_counted_kobo = NULL at first,
    # since nobody actually counted the drawer at close time; the owner
    # is forced to confirm the real counted cash the next time they try
    # to open a new shift (PATCH .../close is reused for that
    # confirmation, even though the shift is already status="closed").
    auto_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Admin override: when True, this auto-closed shift was determined
    # NOT to be the staff member's fault (server downtime, a sync
    # failure, an admin closing it on their behalf, etc. -- spec
    # section 18, "Staff Auto-Closure" vs "System/Administrative
    # Auto-Closure") and is excluded from their compliance history.
    violation_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    violation_exempt_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set on a genuine manual close that happened after the branch's
    # configured expected-close time + grace period (spec section 19).
    # Distinct from auto_closed -- a late close is still the staff
    # member closing it themselves, just past the deadline, so it is
    # NOT a compliance violation on its own; it's shown to admins as a
    # softer signal (spec section 3/16: "A single mistake should
    # normally produce a reminder... do not penalize honest mistakes
    # too aggressively").
    late_close: Mapped[bool] = mapped_column(Boolean, default=False)


class ShiftComplianceEvent(Base):
    """One shift-closing compliance violation, created automatically
    when a shift auto-closes (see _record_compliance_event in
    api/v1/routers/shifts.py) -- never hand-entered. Always 1:1 with
    the Shift that triggered it (unique constraint below), so a shift
    that later gets marked violation_exempt has its event deleted
    rather than merely hidden, keeping "calculated from historical
    shift records" (spec section 5) honest -- there's no orphaned
    violation left counting against the staff member.

    occurrence_number is this user's Nth non-exempt auto-close ever
    (not per-branch, not per-month -- spec doesn't scope it, and a
    staff member who transfers branches should still carry their
    record). level is derived from occurrence_number via
    LEVEL_FOR_OCCURRENCE in the router (1: reminder, 2: formal
    warning, 3: management review, 4+: final warning), capped at 4 so
    a 10th violation doesn't invent a "Level 10".
    """

    __tablename__ = "shift_compliance_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    occurrence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # NULL until the staff member taps "Acknowledge" / "Acknowledge
    # Warning" -- spec sections 6/7/8/11. The staff dashboard keeps
    # showing this event (spec: "Do not allow the staff member to
    # simply ignore it indefinitely") until it's set.
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Update Request item 15: a *separate* admin-side "I've informed
    # this staff member" acknowledgment, distinct from the staff
    # member's own self-ack above -- an admin/branch_manager can mark
    # an incident as followed-up-on (optionally with a corrective
    # note, e.g. "won't happen again") straight from the Shift report,
    # without needing the staff member to act first, and without
    # touching the staff-facing acknowledged_at flow at all.
    admin_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("channel IN ('pos','website','mobile')", name="ck_sale_channel"),
        CheckConstraint(
            "status IN ('completed','refunded','partially_refunded','cancelled','booking')",
            name="ck_sale_status",
        ),
        CheckConstraint(
            "payment_method IN ('cash','card','transfer','online')",
            name="ck_sale_payment_method",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    served_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Which till session this sale was rung up under. Nullable so
    # existing/legacy sales and any channel that doesn't use shifts
    # (website, mobile) are unaffected -- see Shift below.
    shift_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shifts.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="completed")
    subtotal_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_kobo: Mapped[int] = mapped_column(BigInteger, default=0)
    total_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    # Client-generated idempotency key — makes desktop POS sync safe
    # against retries/duplicates. See docs/architecture Section 7.
    local_ref_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free text -- currently only used to hold a booking's flexible
    # pickup window (e.g. "later this week"); null on an ordinary sale.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional firm pickup date for a booking/deposit. Distinct from
    # `notes` (which stays free text for a rough window) -- this is
    # the date the 1%/day storage-fee calculation is measured against
    # once a booking passes the 1-year threshold.
    pickup_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # September 2026 change spec section 9: "when a booked item is
    # swapped for another, the system must record and display the
    # full chain -- original item + price, the swap, and the new item
    # + price/topup/balance -- so the transaction is traceable, not
    # just the final state." A booking's own SaleItem row is
    # overwritten in place on a swap (same reasoning as everywhere
    # else in this app -- one current-state row per line), so this is
    # the append-only trail of every swap that ever happened on this
    # sale. Each entry: from_product_name/from_quantity/
    # from_unit_price_naira, to_product_name/to_quantity/
    # to_unit_price_naira, previous_balance_naira/new_balance_naira,
    # changed_by (uuid str)/changed_by_name, changed_at (ISO string).
    # Empty/null for a sale that's never had an item changed.
    item_swap_history: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    items: Mapped[list["SaleItem"]] = relationship(back_populates="sale")


class SalePayment(Base):
    """One tender on a sale. Most sales have exactly one row here
    (single payment method); a split-tender sale (e.g. part cash,
    part transfer) has several. Sale.payment_method is kept as-is for
    backward compatibility -- it holds the single method for a
    single-tender sale, or the literal "split" when this table has
    more than one row for that sale.
    """

    __tablename__ = "sale_payments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    stock_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_batches.id"), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)

    sale: Mapped["Sale"] = relationship(back_populates="items")


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(
            "action IN ('cash','transfer','exchange') OR action IS NULL", name="ck_refund_action"
        ),
        CheckConstraint(
            "status IN ('completed','pending') OR status IS NULL", name="ck_refund_status"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), nullable=False)
    processed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Photo of the returned item, supplied by whoever processes the
    # refund at the till.
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # What the customer wants (spec section 7 -- the question the
    # cashier asks is "Refund or Exchange?", not "what are you doing").
    # 'cash' / 'transfer' are the two ways a plain Refund can be paid
    # out (spec section 7-8) -- defaulted from the original sale's
    # payment method, switchable by the cashier when they don't have
    # cash on hand. 'exchange' is its own path entirely --
    # exchange_product_id/settlement below describe that swap, which
    # can itself owe the customer money back via settlement_method.
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    # Bank transfer details (spec section 8: "account number, bank
    # name, account name for confirmation") -- populated when
    # action='transfer', or when an exchange's settlement is paid out
    # by transfer (settlement_method='transfer').
    bank_name: Mapped[str | None] = mapped_column(String, nullable=True)
    account_number: Mapped[str | None] = mapped_column(String, nullable=True)
    account_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # 'pending' for any refund paid out by transfer (cash refunds are
    # handed over on the spot and are 'completed' immediately) -- spec
    # section 10: "the number of refunds that are awaiting payment
    # processing". Cleared to 'completed' by
    # POST /sales/refunds/{id}/mark-processed once management has
    # actually sent the money, within the 24-hour window (spec
    # section 8).
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exchange_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    # Which side settled the price difference on an exchange:
    # 'store_paid_customer' (new item cheaper, store owes the
    # customer), 'customer_topped_up' (new item pricier, customer paid
    # the difference), or 'even' (same price, nothing changed hands).
    settlement: Mapped[str | None] = mapped_column(String, nullable=True)
    settlement_amount_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # How a 'store_paid_customer' settlement was actually paid out --
    # 'cash' handed over on the spot, or 'transfer' (bank_name/
    # account_number/account_name above apply, and status starts
    # 'pending' same as a plain transfer refund). Null for
    # 'customer_topped_up'/'even', where the store isn't paying
    # anything out.
    settlement_method: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    items: Mapped[list["RefundItem"]] = relationship(back_populates="refund")


class RefundItem(Base):
    """Per-line breakdown of a refund event, for the itemized refund
    receipt (see alembic f9a1b2c3d4e5). Product/unit name and price
    are snapshotted at refund time, same pattern as SaleItem, so the
    receipt stays accurate even if the product is later renamed,
    repriced, or deleted.
    """

    __tablename__ = "refund_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    refund_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("refunds.id"), nullable=False)
    sale_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sale_items.id"), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_label: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    restocked: Mapped[bool] = mapped_column(Boolean, default=False)
    spoiled: Mapped[bool] = mapped_column(Boolean, default=False)

    refund: Mapped["Refund"] = relationship(back_populates="items")


# "Sold Below Original Price" (formerly tracked here via SaleLoss) was
# retired as a checkout discount path (spec section 3) -- all
# discounts now flow through super-admin-generated coupon codes only,
# so the SaleLoss model and its sale_losses table are gone. See
# migration <TIMESTAMP>_drop_sale_losses_table.py for the table drop.

# -------------------------------------------------------------------- CHAT

class ChatMessage(Base):
    """One team-wide group channel (recipient_id IS NULL) plus DMs
    between any two accounts -- covers the "chat with themselves on
    the group... or send an individual message" request, e.g. a super
    admin generating a coupon and sending it straight to the cashier
    who asked for it. Polling-based (no websocket infra in this app
    yet), see api/v1/routers/chat.py.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # NULL = posted to the single group channel; set = a DM to that
    # specific user.
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Image or voice-note attachment -- body can be empty when one of
    # these is set (an image/voice message doesn't require a caption).
    attachment_url: Mapped[str | None] = mapped_column(String, nullable=True)
    attachment_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # Section 17: "a universal Announcements channel where the Super
    # Admin can broadcast a message... visible to all staff in all
    # branches". Reuses this same table/group-channel mechanism
    # (recipient_id IS NULL) rather than a parallel one -- this flag is
    # what distinguishes a one-way broadcast (only a super_admin may
    # post one, enforced in the router) from the ordinary all-staff
    # group chat, which anyone can post to. Read-tracking reuses
    # ChatReadState too, under conversation_key="announcements".
    is_announcement: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ChatReadState(Base):
    """Per-user, per-conversation "read up to" marker, driving the
    unread badge. `conversation_key` is "group" for the group channel,
    or the DM peer's user id (as text) for a DM -- a plain nullable
    peer_id column can't carry a reliable UNIQUE(user_id, peer_id)
    constraint in Postgres, since NULL is never equal to NULL, so this
    sidesteps that instead of relying on app-level dedup.
    """

    __tablename__ = "chat_read_state"
    __table_args__ = (
        UniqueConstraint("user_id", "conversation_key", name="uq_chat_read_state_user_conversation"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_key: Mapped[str] = mapped_column(String, nullable=False)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# -------------------------------------------------------------- AUDIT/SYNC

class InventoryDeletionRecord(Base):
    """Audit trail for deleted products (see alembic c3d4e5f6a7b8, spec
    sections 8-10). Product deletion itself stays a soft delete
    (Product.is_deleted) so nothing referencing it historically
    breaks; this table is what makes the deletion traceable: why it
    happened, how much stock it represented, who did it, and whether
    it should be counted as an inventory loss for P&L.
    """

    __tablename__ = "inventory_deletion_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    category_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_of_measure: Mapped[str | None] = mapped_column(String, nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    quantity_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    reason_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_loss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    log_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    user: Mapped["User | None"] = relationship()


class BranchProductPrice(Base):
    """A branch_manager's per-branch price override for a product.

    Product.selling_price_kobo remains the single global/default price,
    editable only by super_admin -- see catalog.py::update_product().
    A row here means this specific branch sells the product at a
    different price than the global default; deleted (not merely set
    to the global value) the moment super_admin changes the global
    price for that product, so every branch reverts to following the
    new global price until a branch_manager overrides it again.
    """
    __tablename__ = "branch_product_prices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    selling_price_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SyncLog(Base):
    __tablename__ = "sync_log"
    __table_args__ = (
        CheckConstraint("operation IN ('insert','update','delete')", name="ck_sync_operation"),
        CheckConstraint("status IN ('pending','applied','conflict','failed')", name="ck_sync_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------------------ COUPONS

class Coupon(Base):
    __tablename__ = "coupons"
    __table_args__ = (
        CheckConstraint("status IN ('active','redeemed','voided','expired')", name="ck_coupon_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    discount_amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    redeemed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    redeemed_sale_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales.id"), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # PATCH3C-COUPON-SERVER: set when the coupon was made from a store credit
    pending_change_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pending_changes.id", ondelete="SET NULL"), nullable=True
    )

class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_wishlist_customer_product"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ------------------------------------------------------------------ CREDIT LIST
#
# "Credit" here means store credit extended to a trusted customer at
# checkout time -- they take the goods now and settle the balance
# later. Deliberately store-wide visible (every logged-in worker, plus
# whatever role the super admin marks as "auditor" via the Roles page)
# rather than gated behind a manage_* permission, per spec: everyone on
# the floor needs to be able to see who owes what so any cashier can
# take a repayment, not just whoever opened the credit.

class CreditEntry(Base):
    __tablename__ = "credit_entries"
    __table_args__ = (
        CheckConstraint("status IN ('outstanding','partially_paid','paid')", name="ck_credit_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    # Required identity fields (spec: "must document their full legal
    # name and their phone numbers"). Address is explicitly optional.
    customer_full_name: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str] = mapped_column(String, nullable=False)
    customer_address: Mapped[str | None] = mapped_column(String, nullable=True)
    # What they bought and owe -- amount_kobo is the total granted; the
    # running balance is amount_kobo - sum(payments.amount_kobo),
    # computed at read time rather than duplicated here so a payment
    # can never drift out of sync with the ledger.
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # What they're buying, straight out of the catalog -- required by
    # the UI going forward (category -> product -> variant, same data
    # POS sells from) rather than the free-text `description` above,
    # which is kept only so pre-existing rows still render. Nullable
    # here so old rows opened before this column existed still load.
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Server-computed unit price at the moment the credit was opened
    # (product.selling_price_kobo, or an explicit override) -- kept so
    # amount_kobo (= unit_price_kobo * quantity) is always traceable
    # back to a per-unit price rather than only ever a lump sum.
    # Nullable: rows opened before this column existed have no
    # per-unit breakdown, only the historical total.
    unit_price_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Whether opening this credit already deducted the item from
    # inventory. True for every row created from the fix onward (the
    # deduction happens atomically in the same DB transaction as the
    # insert); pre-existing rows are backfilled to True by migration
    # z1a2b3c4d5e6 so the app never tries to deduct their stock a
    # second time.
    stock_deducted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="outstanding")
    sale_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales.id"), nullable=True)
    opened_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # opened_at is the moment the credit was granted -- date AND time,
    # per spec -- captured server-side (not client-supplied) so it
    # can't be backdated.
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    branch: Mapped["Branch"] = relationship()
    opener: Mapped["User"] = relationship(foreign_keys=[opened_by])
    category: Mapped["Category | None"] = relationship()
    product: Mapped["Product | None"] = relationship()
    payments: Mapped[list["CreditPayment"]] = relationship(
        back_populates="credit_entry", order_by="CreditPayment.paid_at"
    )


class CreditPayment(Base):
    __tablename__ = "credit_payments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    credit_entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_entries.id"), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False, default="cash")
    received_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Date and time this specific instalment was collected -- so the
    # credit list shows a full timeline, not just a single balance.
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    credit_entry: Mapped["CreditEntry"] = relationship(back_populates="payments")
    receiver: Mapped["User"] = relationship()


class PurchaseInvoiceLineItem(Base):
    """One line of a supplier purchase invoice, typed in alongside the
    optional photo -- "if there was 30 cartons of product on the
    invoice, there should also be able to write it down... quantity,
    name, cost price, rate, and total". `position` preserves the
    order rows were entered in (the UI adds rows one at a time).

    Redesigned per spec section 3/5/6: the invoice items table is now
    S/N | Quantity | Item | Rate | Total, with Quantity split into a
    numeric count plus a unit (cartons/rolls/pieces), Item optionally
    linked back to the catalog via product_id (autocomplete), and
    Total always server-computed as quantity x rate -- cost_price_kobo
    is no longer collected on new lines (kept only so older invoices
    filed before this change still display their cost price).
    """

    __tablename__ = "purchase_invoice_line_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_invoices.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_name: Mapped[str] = mapped_column(String, nullable=False)
    # Optional link back to the catalog product this line was
    # selected from via the Item autocomplete. Nullable -- a line can
    # still be typed freehand for a product not (yet) in the catalog.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Cartons / Rolls / Pieces -- how the product was purchased, per
    # the Nigerian-market purchasing pattern the invoice is modelled
    # on. Free string (like StockTransfer.unit_label) rather than a
    # CHECK constraint, so a new unit can be added without a migration.
    unit_label: Mapped[str] = mapped_column(String, nullable=False, default="pieces")
    # Deprecated: no longer collected on new lines (spec: "Remove Cost
    # Price from the invoice entry"). Left in place so invoices filed
    # before this change keep their historical cost-price data.
    cost_price_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Per-unit price. Total = quantity x rate, always computed
    # server-side -- never a client-supplied figure.
    rate_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    line_total_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # September 2026 change -- per-invoice cost-per-piece calculator.
    # Cartons vary in size by supplier/product, so pieces_per_carton is
    # always admin-entered per line (never remembered from a previous
    # invoice); dozens/pieces convert at the fixed 12/1 rate below and
    # never need an input. Only ever populated when unit_label ==
    # "cartons" for either the primary quantity or the remainder.
    pieces_per_carton: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # An optional smaller-unit leftover on top of the primary quantity
    # -- e.g. "4 cartons + 3 pieces" (quantity=4, unit_label="cartons",
    # remainder_quantity=3, remainder_unit_label="pieces"). Always a
    # smaller unit than the primary; null when there is no leftover.
    remainder_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remainder_unit_label: Mapped[str | None] = mapped_column(String, nullable=True)
    # Snapshot of the total-pieces conversion at the moment this line
    # was saved -- part of the permanent historical record, so it
    # never silently recalculates if pieces_per_carton conventions
    # change later.
    total_pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # line_total_kobo / total_pieces, stored alongside it for the same
    # reason. This is what feeds the master cost-price table later.
    cost_price_per_piece_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    invoice: Mapped["PurchaseInvoice"] = relationship(back_populates="line_items")


class PurchaseInvoice(Base):
    """A supplier purchase invoice on file -- typed fields plus an
    optional photo of the paper invoice, so a super admin/branch
    manager can look up what was bought from whom, when, filtered by
    month or year. Distinct from `refunds` (customer-facing) and
    `purchasing.py`'s PurchaseOrder (a stock-receiving workflow) --
    this is purely a record-keeping cabinet for supplier paperwork.
    """

    __tablename__ = "purchase_invoices"
    __table_args__ = (
        CheckConstraint(
            "adjustment_status IS NULL OR adjustment_status IN "
            "('pending','balanced','replaced','refunded')",
            name="ck_purchase_invoice_adjustment_status",
        ),
        CheckConstraint(
            "status IN ('draft', 'completed')",
            name="ck_purchase_invoice_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    # Add Product -> Invoice Workflow spec (Sept 2026): now optional so
    # a bare draft invoice -- created from Add Product with just an
    # item and nothing else -- can exist before anyone has typed in a
    # supplier. status distinguishes that unfinished state ('draft')
    # from a normally-filed, fully-typed-in invoice ('completed'),
    # which is what every invoice on file before this change already
    # is (see migration 1f2e3d4c5b6a).
    supplier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    # Optional link to the shared Supplier registry (spec section 14:
    # autofill a previously-registered supplier's address/phone by
    # name). Nullable so older invoices typed in before this existed,
    # or a genuinely one-off supplier, still work with plain free text
    # in supplier_name/supplier_address/supplier_phone below.
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Nigerian-invoice-style extras (spec: reference photos of physical
    # invoice/receipt booklets) -- all optional, matching what's
    # printed on the booklet's letterhead and stub. Nothing here is
    # ever server-generated; if the paper invoice has a number on it,
    # that's what goes here, typed in as-is.
    # invoice_number kept only for invoices filed before spec section 15
    # ("the supplier invoice workflow does not need a Receipt Number
    # field -- remove it"). No longer settable via the create/edit form;
    # the column stays so old records don't lose data.
    invoice_number: Mapped[str | None] = mapped_column(String, nullable=True)
    supplier_address: Mapped[str | None] = mapped_column(String, nullable=True)
    supplier_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Spec section 16: trimmed down from a broad payment-method list to
    # just these three. Column stays a free string (not a CHECK
    # constraint) so older invoices filed under a since-removed option
    # (e.g. "card") keep displaying correctly; only new writes are
    # constrained, at the schema layer.
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    # Spec section 17: partial payment support. amount_kobo above stays
    # "the total owed on this invoice"; amount_paid_kobo is how much of
    # that has actually been paid so far. The balance (amount_kobo -
    # amount_paid_kobo) is always derived, never stored, so it can't
    # drift out of sync with either figure.
    amount_paid_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Spec section 19: the real landed cost of getting goods into the
    # store, beyond what was paid to the supplier for the goods
    # themselves -- three legs: (1) dispatch, getting goods out of the
    # supplier's own shop/market to the departure park (waybill_cost_kobo,
    # despite the name -- kept as-is so existing invoices don't lose
    # data); (2) transit_cost_kobo, the actual inter-town waybill cost
    # from the departure park to the park nearest this business; (3)
    # handling_cost_kobo, last-mile cost from that arrival park into
    # the actual shop.
    waybill_cost_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transit_cost_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    handling_cost_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Spec section 18: "Check and Balance" -- documenting an adjustment
    # when some of what was received was damaged/returned to the
    # supplier, so the invoice doesn't just sit there implying the full
    # original amount is payable. adjustment_amount_kobo is the value
    # being balanced off; adjustment_status records what's actually
    # happening about it (a return in transit is a different state from
    # a confirmed refund/replacement); adjustment_note is free text for
    # whatever doesn't fit the predefined status.
    adjustment_amount_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    adjustment_status: Mapped[str | None] = mapped_column(String, nullable=True)
    adjustment_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    branch: Mapped["Branch | None"] = relationship()
    supplier: Mapped["Supplier | None"] = relationship()
    creator: Mapped["User"] = relationship()
    line_items: Mapped[list["PurchaseInvoiceLineItem"]] = relationship(
        back_populates="invoice",
        order_by="PurchaseInvoiceLineItem.position",
        cascade="all, delete-orphan",
    )


class StoreSetting(Base):
    """A single-row-per-key settings table for printed-receipt
    branding (store name, contact email, logo URL, invoice base URL
    for the receipt QR code). Previously only lived in browser
    localStorage -- per device, never synced -- so a super admin had
    no real way to set the store's own logo everywhere at once.
    """

    __tablename__ = "store_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ExpiryAlertDismissal(Base):
    """September 2026 change spec section 6: expiry alerts must be a
    real push notification (to the super admin, the branch manager,
    and every staff member at that branch) rather than only a number
    on the dashboard that nobody sees unless they happen to load it.
    There's no background scheduler in this stack (see
    _auto_close_stale_shifts in api/v1/routers/shifts.py for the same
    pattern elsewhere) -- so rather than a generated Notification row
    per recipient, this is the inverse: the near-expiry StockBatch set
    is computed fresh on every request (reports.py's existing
    expiry_report() query), and this table just records which of
    those batches a given user has already dismissed, so the same
    alert doesn't keep reappearing for them once seen. One row per
    (user, batch) pair -- unique constraint below.
    """

    __tablename__ = "expiry_alert_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "stock_batch_id", name="uq_expiry_dismissal_user_batch"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    stock_batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_batches.id"), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PendingChange(Base):
    """September 2026 change spec section 11 -- "Customer is owed
    change" tracking (new feature, no prior mechanism existed). When a
    completed sale can't be given its full change back on the spot,
    this records the shortfall against the SALE (not a bare name
    string -- two different customers can share a full name, and the
    receipt is already a unique key, so "customer X is owed Y" is
    really "receipt #12345 has an unclaimed Y balance," sidestepping
    the collision problem entirely). customer_name is still captured
    for the till/staff to recognize who to hand it to; customer_phone
    is optional, purely a secondary lookup aid, never required for
    uniqueness. Settling it (handing over the change) just flips
    status/settled_by/settled_at -- it drops off the outstanding list
    from that point on.
    """

    __tablename__ = "pending_changes"
    __table_args__ = (
        # Platform update spec section 2: 'outstanding' (cash owed,
        # waiting for the customer to return -- no admin approval
        # needed, it never left the till), 'pending' (transfer/airtime
        # submitted, awaiting Super Admin/Branch Manager approval --
        # spec 2.4, same tier as a Refund's 'pending' status), 'settled'
        # (done, either way).
        CheckConstraint("status IN ('outstanding','pending','settled')", name="ck_pending_change_status"),
        CheckConstraint(
            "settlement_method IN ('cash','transfer','airtime','coupon') OR settlement_method IS NULL",
            name="ck_pending_change_settlement_method",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="outstanding")
    # Platform update spec section 2.2: the actual unique identifier
    # used to match a returning customer to their pending balance --
    # system-generated (not the customer's name, which can collide),
    # short enough to read/repeat over the till. Assigned once at
    # creation for every settlement method, not just cash, so it's
    # still available as a fallback lookup if a transfer/airtime
    # attempt ever needs to be traced back to this record.
    reference_code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    # Which way the change is being settled (spec 2.3). Null only for
    # pre-existing rows created before this column existed.
    settlement_method: Mapped[str | None] = mapped_column(String, nullable=True)
    # Bank transfer detail -- same three fields/pattern as
    # Refund.bank_name/account_number/account_name.
    bank_name: Mapped[str | None] = mapped_column(String, nullable=True)
    account_number: Mapped[str | None] = mapped_column(String, nullable=True)
    account_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Airtime top-up detail -- network is auto-detected server-side
    # from the Nigerian prefix when not supplied, then confirmable/
    # overridable by the cashier (spec 2.3).
    airtime_phone_number: Mapped[str | None] = mapped_column(String, nullable=True)
    airtime_network: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    settled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Super Admin/Branch Manager approval trail for a 'pending'
    # transfer/airtime request (spec 2.4) -- mirrors
    # Refund.approved_by/processed_at exactly. Null for a plain cash
    # record, which never goes through admin approval.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MetricSnapshot(Base):
    """Dashboard redesign implementation prompt, step 4 (Performance
    Records). Replaces FinancialTrackingPeriod (September 2026 change
    spec section 14) entirely -- resetting zeroed out permanent
    history, which is the one thing this table is designed never to
    do: nothing here is ever overwritten in a way that loses a past
    day's number, only inserted or upserted for a day that hasn't been
    finalized yet (see the daily rollup job in main.py).

    Only `day` granularity is ever stored -- weekly/monthly/yearly/
    all-time are computed with SUM(value) GROUP BY date_trunc(...)
    over this table at read time (see
    api/v1/routers/reports.py::performance_records()), so there's
    never a separate weekly/monthly/yearly row that could drift out of
    sync with the daily rows underneath it.

    branch_id is nullable (null = all branches) to leave room for a
    future store-wide rollup row without a schema change, though the
    daily job today only ever writes one row per real branch.
    """

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        CheckConstraint(
            "metric_type IN ('revenue','inventory_investment','sales','losses')",
            name="ck_metric_snapshot_type",
        ),
        UniqueConstraint("branch_id", "metric_type", "period_start", name="uq_metric_snapshot_branch_type_period"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    metric_type: Mapped[str] = mapped_column(String, nullable=False)
    # Always a day boundary -- see docstring above.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --------------------------------------------------------------- RESTOCK LIST

class RestockListEntry(Base):
    """One product currently sitting on a branch's Restock List --
    the persistent "shopping list" a store manager builds up to send
    to suppliers, distinct from the live/computed Low Stock & Out of
    Stock alert (see reports.py::low_stock_report()).

    A row here can exist for two different reasons, tracked by
    `is_manual`:
      - False: the product is (or recently was) at/under its reorder
        level. These rows are created lazily the first time someone
        edits a quantity/unit on an alerted item, and are deleted
        automatically -- same self-healing-on-read pattern as
        Product.out_of_stock_alert_muted in reports.py -- the moment
        the product's stock rises back above its reorder level. This
        is what makes "restocking removes it from the list" true
        without any background job.
      - True: a manager explicitly added this product from the
        Inventory Catalog, regardless of its current stock level.
        These rows are never auto-removed -- only an explicit DELETE
        takes them off the list.

    Downloading a list for a supplier (see restock_list.py's
    download_restock_list) removes the downloaded entries entirely --
    per the person's explicit correction, "sent" no longer means
    "stays on the list tagged as sent", it means gone. Only an actual
    restock (stock rising back above reorder level) or this download
    action removes an entry; nothing else does.
    """

    __tablename__ = "restock_list_entries"
    __table_args__ = (
        UniqueConstraint("product_id", "branch_id", name="uq_restock_entry_product_branch"),
        CheckConstraint(
            "unit_label IN ('pieces','dozens','cartons','bags','half_dozen','half_carton')",
            name="ck_restock_entry_unit_label",
        ),
        # Every row is either a catalog product OR a freeform item
        # typed straight onto the list (name + optional photo, no
        # matching Product) -- never neither. Freeform rows exist
        # because the person explicitly asked for a way to order
        # something that isn't in inventory at all yet; they behave
        # like a manual entry always (is_manual is meaningless for
        # them -- there's no stock level to ever auto-remove them by),
        # and are only ever taken off the list by an explicit delete.
        CheckConstraint(
            "(product_id IS NOT NULL) OR (custom_name IS NOT NULL)",
            name="ck_restock_entry_has_product_or_custom_name",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_label: Mapped[str] = mapped_column(String, default="pieces")
    # Freeform item fields -- set together, and only when product_id
    # is null (see the check constraint above).
    custom_name: Mapped[str | None] = mapped_column(String, nullable=True)
    custom_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    product: Mapped["Product | None"] = relationship()
    sends: Mapped[list["RestockListEntrySend"]] = relationship(
        back_populates="entry",
        order_by="RestockListEntrySend.sent_at.desc()",
        cascade="all, delete-orphan",
    )


class RestockListEntrySend(Base):
    """One "sent to supplier" record for a RestockListEntry -- an
    append-only history, not a status flag, because the same item can
    legitimately be sent to more than one supplier (e.g. splitting an
    order, or re-ordering after a supplier fell through). The supplier
    is stored as free text, deliberately not a foreign key to
    Supplier: the person asked for a plain "type the name and send"
    flow here, not the fuller supplier-record tracking that
    purchasing.py already does for formal purchase orders.
    """

    __tablename__ = "restock_list_entry_sends"

    id: Mapped[uuid.UUID] = _uuid_pk()
    restock_list_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restock_list_entries.id"), nullable=False
    )
    supplier_name: Mapped[str] = mapped_column(String, nullable=False)
    quantity_sent: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_label_sent: Mapped[str] = mapped_column(String, nullable=False)
    sent_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    entry: Mapped["RestockListEntry"] = relationship(back_populates="sends")
    sent_by_user: Mapped["User | None"] = relationship()


# ------------------------------------------------------- STOREFRONT ORDERS
# Website orders. One customer order can be fulfilled from several
# branches; each branch's share is an ordinary Sale (channel='website')
# deducting that branch's own stock batches. storefront_order_sales links
# the order to those sales so staff see ONE merged order to dispatch.
# Nothing in the existing sales/stock tables changes.


class StorefrontOrder(Base):
    __tablename__ = "storefront_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','processing','waybilled','delivered','cancelled')",
            name="ck_storefront_order_status",
        ),
        CheckConstraint(
            "payment_status IN ('unpaid','paid')",
            name="ck_storefront_order_payment_status",
        ),
        CheckConstraint(
            "payment_method IS NULL OR payment_method IN ('bank_transfer','cash_on_delivery')",
            name="ck_storefront_order_payment_method",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Idempotency key from the storefront (stops a double-click placing two orders).
    reference: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    # Snapshot of what the customer typed at checkout, so the waybill details
    # never change if the customer record is edited later.
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str] = mapped_column(String, nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String, nullable=True)
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_city: Mapped[str | None] = mapped_column(String, nullable=True)
    delivery_lga: Mapped[str | None] = mapped_column(String, nullable=True)
    delivery_state: Mapped[str] = mapped_column(String, nullable=False)
    shipping_fee_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shipping_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    subtotal_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coupon_code: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    waybill_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # PATCH19B-PAYMENT-FIELDS (columns added by migration wopay01paid)
    payment_status: Mapped[str] = mapped_column(
        String, nullable=False, default="unpaid", server_default="unpaid"
    )
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_received_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    payment_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class StorefrontOrderSale(Base):
    __tablename__ = "storefront_order_sales"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storefront_orders.id"), nullable=False)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), unique=True, nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)


class StorefrontCoupon(Base):
    """Website-only coupon codes (separate from POS coupons): fixed naira
    amount, single use, with an expiry. Generated by a super admin."""

    __tablename__ = "storefront_coupons"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','redeemed','voided','expired')",
            name="ck_storefront_coupon_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    discount_amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # NULL until the code is first revealed; then started_at + valid_hours.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_hours: Mapped[int] = mapped_column(BigInteger, nullable=False, default=24, server_default="24")
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    issued_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    redeemed_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("storefront_orders.id"), nullable=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ------------------------------------------------- ADMIN-APPROVED PASSWORD RESET

class PasswordResetRequest(Base):
    """A user's request for an admin to approve a password reset.

    Flow: pending -> approved (admin) -> used (user sets new password),
    or pending -> rejected, or superseded by a newer request (cancelled).
    The requester's browser holds a random secret (only its hash is
    stored here); without it the approval can't be used, so nobody else
    can hijack an approved reset just by knowing the email address.
    """

    __tablename__ = "password_reset_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secret_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------ SHELF LIMITS / GOODS RECEIVING
# Shelf Stock is NOT stored anywhere new: it is, and stays, the sum of
# StockBatch.quantity_remaining for a branch+product (what POS sells
# from). Warehouse Stock stays WarehouseStock.quantity_pieces.
# Total = Warehouse + Shelf is always derived, never stored.


class ProductShelfSetting(Base):
    """Per branch+product shelf limit and replenishment alert level.
    A row means "limits are configured for this product here"; a product
    with no row has no enforced limit (the UI shows the 24 / 5 defaults
    as suggestions). All quantities are in pieces.
    """

    __tablename__ = "product_shelf_settings"
    __table_args__ = (
        UniqueConstraint("branch_id", "product_id", name="uq_product_shelf_settings_branch_product"),
        CheckConstraint("max_shelf_quantity >= 0", name="ck_shelf_settings_max_nonneg"),
        CheckConstraint("replenish_alert_level >= 0", name="ck_shelf_settings_alert_nonneg"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    max_shelf_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    replenish_alert_level: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class GoodsReceipt(Base):
    """One confirmation of a physical delivery. Stock only changes when
    status becomes 'posted'; a 'draft' never affects any balance. The
    invoice may be linked later (documentation_status 'pending' ->
    'documented') without posting stock a second time. idempotency_key
    makes a repeated submission post only once.
    """

    __tablename__ = "goods_receipts"
    __table_args__ = (
        UniqueConstraint("branch_id", "idempotency_key", name="uq_goods_receipts_branch_idem"),
        CheckConstraint("status IN ('draft','posted','voided')", name="ck_goods_receipt_status"),
        CheckConstraint("documentation_status IN ('documented','pending')", name="ck_goods_receipt_doc_status"),
        CheckConstraint("cost_status IN ('verified','unverified')", name="ck_goods_receipt_cost_status"),
        CheckConstraint(
            "reconciliation_status IN ('not_required','clear','discrepancy_confirmed')",
            name="ck_goods_receipt_recon_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_invoices.id"), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    invoice_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    documentation_status: Mapped[str] = mapped_column(String, nullable=False, default="documented")
    cost_status: Mapped[str] = mapped_column(String, nullable=False, default="unverified")
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # When the goods physically arrived (may be earlier than when this
    # was recorded) vs. when the system entry was posted. Never backdate
    # posted_at.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_late_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciliation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # not_required = not a late entry; clear = late entry, nothing overlapped;
    # discrepancy_confirmed = posted after a manager confirmed a mismatch (stock count needed).
    reconciliation_status: Mapped[str] = mapped_column(
        String, nullable=False, default="not_required", server_default="not_required"
    )
    reconciliation_flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    lines: Mapped[list["GoodsReceiptLine"]] = relationship(
        back_populates="receipt",
        order_by="GoodsReceiptLine.position",
        cascade="all, delete-orphan",
    )


class GoodsReceiptLine(Base):
    """One product on a receipt. All quantities are in pieces.
    quantity_received = quantity_to_shelf + quantity_to_warehouse.
    quantity_invoiced is what the supplier invoice listed (for partial
    deliveries); null when no invoice is linked yet.
    """

    __tablename__ = "goods_receipt_lines"
    __table_args__ = (
        CheckConstraint("quantity_received >= 0", name="ck_receipt_line_received_nonneg"),
        CheckConstraint("quantity_to_shelf >= 0", name="ck_receipt_line_shelf_nonneg"),
        CheckConstraint("quantity_to_warehouse >= 0", name="ck_receipt_line_wh_nonneg"),
        CheckConstraint(
            "quantity_to_shelf + quantity_to_warehouse = quantity_received",
            name="ck_receipt_line_split_sums",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    invoice_line_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_invoice_line_items.id"), nullable=True
    )
    quantity_invoiced: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_to_shelf: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_to_warehouse: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_cost_kobo: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    receipt: Mapped["GoodsReceipt"] = relationship(back_populates="lines")


# ------------------------------------------------------ CUSTOMER REQUESTS
# A customer asks the shop to source an item and pays a deposit up front.
# The item goes on the Restock List (so it reaches the supplier), but the
# customer, their contact details and the deposit live HERE, because a
# Restock List entry is deleted the moment its PDF is downloaded -- the
# customer's order must survive that. restock_list_entry_id is therefore
# ON DELETE SET NULL, and the item name/photo/quantity are snapshotted.
#
# Lifecycle:  pending -> arrived -> collected      (or -> cancelled)
#   pending   waiting for the item to reach the shelf
#   arrived   item is in stock; hold_pieces units are held back from POS
#             (see sales.py::_get_open_booking_holds) until collected or
#             cancelled; the bell rings
#   collected customer picked it up (hold released)
#   cancelled customer no longer wants it (hold released; deposit_refunded
#             records whether the deposit was given back)


class CustomerRequest(Base):
    __tablename__ = "customer_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','arrived','collected','cancelled')",
            name="ck_customer_request_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    restock_list_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restock_list_entries.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    item_name: Mapped[str] = mapped_column(String, nullable=False)
    item_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_label: Mapped[str] = mapped_column(String, default="pieces")
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str] = mapped_column(String, nullable=False)
    customer_address: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    deposit_kobo: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")
    hold_pieces: Mapped[int] = mapped_column(Integer, default=0)
    # PATCH1-CUSTOMER-ORDER-FIELDS
    order_pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_method: Mapped[str | None] = mapped_column(String, nullable=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sales.id", ondelete="SET NULL"), nullable=True
    )
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deposit_refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
