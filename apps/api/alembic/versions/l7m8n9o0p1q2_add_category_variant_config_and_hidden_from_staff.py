"""link variant types to categories, product-variant option refs, hidden_from_staff

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-08-22 09:00:00.000000

Backs variant-category-system-spec.md:

Feature Spec 1 (category-linked variant system) --
* variant_types gets `category_id` (nullable FK -> categories.id) and
  `slot` ('primary' | 'secondary'), so a category's variant
  configuration is looked up by (category_id, slot) instead of variant
  types floating unattached. A partial unique index enforces at most
  one VariantType per (category, slot) -- categories with no linked
  type at all are treated as "Standard" (no options). The previous
  global `uq_variant_types_name` uniqueness is dropped in favour of
  per-category uniqueness, since two different categories legitimately
  reusing the same type name (e.g. "Size" for both Lotion and Soap) is
  now the expected case.
* product_variants gets `primary_option_id` / `secondary_option_id`
  (nullable FKs -> variant_options.id) recording which saved category
  option(s) built this variant row, on top of the existing free-text
  `variant_name` (kept as-is -- every other reader in the app still
  just reads that column).

Feature Spec 2 (staff warehouse view + per-product visibility) --
* products gets `hidden_from_staff` (boolean, default false).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'l7m8n9o0p1q2'
down_revision: Union[str, None] = 'k6l7m8n9o0p1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- variant_types: category_id + slot ----
    op.add_column(
        "variant_types",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=True),
    )
    op.add_column(
        "variant_types",
        sa.Column("slot", sa.String(), nullable=False, server_default="primary"),
    )
    op.create_check_constraint("ck_varianttype_slot", "variant_types", "slot IN ('primary','secondary')")

    # Global name-uniqueness no longer applies once types are scoped
    # per category -- drop it in favour of the partial one-per-slot
    # index below. Constraint name matches the one created in
    # k6l7m8n9o0p1_add_variant_types_and_options.py.
    op.drop_constraint("uq_variant_types_name", "variant_types", type_="unique")

    # Partial unique index: at most one VariantType per (category, slot).
    # Rows with category_id IS NULL (legacy/unlinked types) are excluded
    # entirely, matching how Postgres already treats multiple NULLs in a
    # normal unique constraint -- explicit here since this is a filtered
    # index, not a plain UniqueConstraint.
    op.create_index(
        "uq_varianttype_category_slot",
        "variant_types",
        ["category_id", "slot"],
        unique=True,
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )

    # ---- product_variants: primary/secondary option refs ----
    op.add_column(
        "product_variants",
        sa.Column("primary_option_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("variant_options.id"), nullable=True),
    )
    op.add_column(
        "product_variants",
        sa.Column("secondary_option_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("variant_options.id"), nullable=True),
    )
    op.create_index("ix_product_variants_primary_option", "product_variants", ["primary_option_id"])
    op.create_index("ix_product_variants_secondary_option", "product_variants", ["secondary_option_id"])

    # ---- products: hidden_from_staff ----
    op.add_column(
        "products",
        sa.Column("hidden_from_staff", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("products", "hidden_from_staff")

    op.drop_index("ix_product_variants_secondary_option", table_name="product_variants")
    op.drop_index("ix_product_variants_primary_option", table_name="product_variants")
    op.drop_column("product_variants", "secondary_option_id")
    op.drop_column("product_variants", "primary_option_id")

    op.drop_index("uq_varianttype_category_slot", table_name="variant_types")
    op.create_unique_constraint("uq_variant_types_name", "variant_types", ["name"])
    op.drop_constraint("ck_varianttype_slot", "variant_types", type_="check")
    op.drop_column("variant_types", "slot")
    op.drop_column("variant_types", "category_id")
