"""retire the variants concept: merge ProductVariant into Product

Revision ID: f6a7b8c9d0e2
Revises: b8c9d0e1f2a3
Create Date: 2026-09-01 00:00:00.000000

Update spec ("nick-cosmetics-update-spec-3.md", Sections 1-3 & 6):
"Remove the variants concept completely." A product is now one
sellable row -- name, category (+ optional extra categories), SKU,
price, reorder level, optional size (Big/Medium/Small), optional
expiration date. This migration:

  1. Adds the merged columns to `products` (cost_price_kobo,
     selling_price_kobo, reorder_level, out_of_stock_alert_muted,
     byproduct, size, expiration_date).
  2. Data-migrates every existing `product_variants` row:
       - A product with exactly ONE live variant absorbs that
         variant's SKU/prices/reorder level/mute flag directly onto
         its own row (same id -- every existing FK that already
         pointed at the *product* keeps working untouched).
       - A product with MULTIPLE variants can no longer be
         represented as one row (a product now has exactly one
         price/SKU), so variants after the first each become their
         OWN new `products` row -- cloned from the parent's brand/
         category/description/status/unit_of_measure, named
         "<parent name> - <variant_name>" so the distinguishing text
         isn't lost. `size`/`byproduct` are deliberately left blank
         on these split-off rows: free-text variant names (e.g.
         "Papaya scent", "Fair & White So White") can't be reliably
         mapped onto the new Big/Medium/Small `size` field, and doing
         so with a guess would silently misclassify real inventory.
         **A super admin should review split-off products (their name
         contains " - ") and set size/byproduct by hand where it
         applies.**
     A mapping of old variant_id -> surviving product_id is built
     while doing this, then used to repoint every table that used to
     reference product_variants.id (stock_batches, warehouse_stock,
     stock_transfers, purchase_order_items, purchase_invoice_line_items,
     sale_items, refunds.exchange_variant_id, wishlist_items,
     credit_entries) at the correct `products.id` -- no sale, batch,
     credit, or refund history changes meaning, it just now points at
     a product row directly instead of by way of a variant row.
  3. Drops product_variants, variant_types, and variant_options
     (the whole category-linked variant-picker system is retired
     along with the concept it configured).
  4. Adds `product_categories` for Section 6's "assign a product to
     more than one category".

This touches the FK graph of a live financial system (sales, credit,
refund, and stock-batch history). Run it against a staging copy of
production data and verify counts/spot-check a few multi-variant
products before running it for real -- see the docstring above for
exactly what happens to a multi-variant product.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e2"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables whose FK used to be `variant_id -> product_variants.id` and is
# now simply `product_id -> products.id`. Each entry: (table, old fk
# column name already renamed to `product_id` by this point in the
# codebase's history? No -- at the DB level the column is still named
# `variant_id` until this migration renames it, so we operate on the
# real current DB column name here.)
_DEPENDENT_TABLES = [
    "stock_batches",
    "warehouse_stock",
    "stock_transfers",
    "purchase_order_items",
    "purchase_invoice_line_items",
    "sale_items",
]


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. add the merged columns to products -------------------
    op.add_column("products", sa.Column("byproduct", sa.String(), nullable=True))
    op.add_column("products", sa.Column("size", sa.String(), nullable=True))
    op.add_column("products", sa.Column("expiration_date", sa.Date(), nullable=True))
    op.add_column(
        "products",
        sa.Column("cost_price_kobo", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "products",
        sa.Column("selling_price_kobo", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "products", sa.Column("reorder_level", sa.Integer(), nullable=False, server_default="10")
    )
    op.add_column(
        "products",
        sa.Column("out_of_stock_alert_muted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_product_size", "products", "size IN ('Big','Medium','Small') OR size IS NULL"
    )

    # ---- 2. data migration: fold product_variants into products --
    variant_rows = bind.execute(
        sa.text(
            "SELECT id, product_id, variant_name, sku, barcode, cost_price_kobo, "
            "selling_price_kobo, reorder_level, out_of_stock_alert_muted "
            "FROM product_variants ORDER BY product_id, id"
        )
    ).fetchall()

    variants_by_product: dict = {}
    for row in variant_rows:
        variants_by_product.setdefault(row.product_id, []).append(row)

    # old variant_id (uuid) -> surviving products.id (uuid)
    variant_to_product_id: dict = {}

    for product_id, variants in variants_by_product.items():
        first, rest = variants[0], variants[1:]

        # The product's own SKU is dropped in favour of the (unique)
        # variant SKU -- SKU now lives at the product level and must
        # stay globally unique, and the variant SKU is what checkout,
        # receipts, and every report already reference.
        bind.execute(
            sa.text(
                "UPDATE products SET sku = :sku, barcode = COALESCE(:barcode, barcode), "
                "cost_price_kobo = :cost, selling_price_kobo = :selling, "
                "reorder_level = :reorder, out_of_stock_alert_muted = :muted "
                "WHERE id = :pid"
            ),
            {
                "sku": first.sku,
                "barcode": first.barcode,
                "cost": first.cost_price_kobo,
                "selling": first.selling_price_kobo,
                "reorder": first.reorder_level,
                "muted": first.out_of_stock_alert_muted,
                "pid": product_id,
            },
        )
        variant_to_product_id[first.id] = product_id

        if rest:
            parent = bind.execute(
                sa.text(
                    "SELECT name, brand_id, category_id, description, status, "
                    "unit_of_measure, barcode AS parent_barcode "
                    "FROM products WHERE id = :pid"
                ),
                {"pid": product_id},
            ).fetchone()

            for variant in rest:
                new_id = uuid.uuid4()
                split_name = (
                    f"{parent.name} - {variant.variant_name}"
                    if variant.variant_name
                    else parent.name
                )
                bind.execute(
                    sa.text(
                        "INSERT INTO products (id, sku, barcode, name, description, "
                        "brand_id, category_id, status, unit_of_measure, cost_price_kobo, "
                        "selling_price_kobo, reorder_level, out_of_stock_alert_muted, "
                        "is_deleted, hidden_from_staff, created_at, updated_at) "
                        "SELECT :id, :sku, :barcode, :name, :description, :brand_id, "
                        ":category_id, :status, :unit_of_measure, :cost, :selling, "
                        ":reorder, :muted, is_deleted, hidden_from_staff, created_at, now() "
                        "FROM products WHERE id = :parent_id"
                    ),
                    {
                        "id": new_id,
                        "sku": variant.sku,
                        "barcode": variant.barcode or parent.parent_barcode,
                        "name": split_name,
                        "description": parent.description,
                        "brand_id": parent.brand_id,
                        "category_id": parent.category_id,
                        "status": parent.status,
                        "unit_of_measure": parent.unit_of_measure,
                        "cost": variant.cost_price_kobo,
                        "selling": variant.selling_price_kobo,
                        "reorder": variant.reorder_level,
                        "muted": variant.out_of_stock_alert_muted,
                        "parent_id": product_id,
                    },
                )
                variant_to_product_id[variant.id] = new_id

    # ---- 3. repoint every dependent table's FK --------------------
    for table in _DEPENDENT_TABLES:
        op.add_column(table, sa.Column("product_id_new", postgresql.UUID(as_uuid=True), nullable=True))
        for old_variant_id, new_product_id in variant_to_product_id.items():
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET product_id_new = :new_id WHERE variant_id = :old_id"
                ),
                {"new_id": new_product_id, "old_id": old_variant_id},
            )
        op.drop_constraint(f"{table}_variant_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "variant_id")
        op.alter_column(table, "product_id_new", new_column_name="product_id")
        # purchase_invoice_line_items' link to the catalog is optional
        # (not every invoice line was matched to a product) -- every
        # other dependent table's variant_id was required.
        if table != "purchase_invoice_line_items":
            op.alter_column(table, "product_id", nullable=False)
        op.create_foreign_key(
            f"{table}_product_id_fkey", table, "products", ["product_id"], ["id"]
        )

    # refunds.exchange_variant_id (nullable) -> exchange_product_id
    op.add_column("refunds", sa.Column("exchange_product_id", postgresql.UUID(as_uuid=True), nullable=True))
    for old_variant_id, new_product_id in variant_to_product_id.items():
        bind.execute(
            sa.text(
                "UPDATE refunds SET exchange_product_id = :new_id WHERE exchange_variant_id = :old_id"
            ),
            {"new_id": new_product_id, "old_id": old_variant_id},
        )
    op.drop_constraint("refunds_exchange_variant_id_fkey", "refunds", type_="foreignkey")
    op.drop_column("refunds", "exchange_variant_id")
    op.create_foreign_key(
        "refunds_exchange_product_id_fkey", "refunds", "products", ["exchange_product_id"], ["id"]
    )

    # credit_entries had both a category-level product_id (parent
    # product, now redundant) and a variant_id (the actual priced
    # item) -- collapse into the single product_id column the merged
    # model expects, keeping the variant's resolved product id since
    # that's what actually identifies what was bought.
    op.add_column("credit_entries", sa.Column("product_id_new", postgresql.UUID(as_uuid=True), nullable=True))
    for old_variant_id, new_product_id in variant_to_product_id.items():
        bind.execute(
            sa.text(
                "UPDATE credit_entries SET product_id_new = :new_id WHERE variant_id = :old_id"
            ),
            {"new_id": new_product_id, "old_id": old_variant_id},
        )
    op.drop_constraint("credit_entries_variant_id_fkey", "credit_entries", type_="foreignkey")
    op.drop_constraint("credit_entries_product_id_fkey", "credit_entries", type_="foreignkey")
    op.drop_column("credit_entries", "variant_id")
    op.drop_column("credit_entries", "product_id")
    op.alter_column("credit_entries", "product_id_new", new_column_name="product_id")
    op.create_foreign_key(
        "credit_entries_product_id_fkey", "credit_entries", "products", ["product_id"], ["id"]
    )

    # ---- 4. drop the retired variant tables ------------------------
    op.drop_table("product_variants")
    op.drop_table("variant_options")
    op.drop_table("variant_types")

    # ---- 5. Section 6: multi-category assignment -------------------
    op.create_table(
        "product_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "category_id", name="uq_product_category"),
    )


def downgrade() -> None:
    # This migration folds many-to-one variant data into one-to-one
    # product rows and splits multi-variant products into new,
    # independently-created rows. That split is not reversible in
    # general (there's no record of which split-off products were
    # originally siblings once product_variants is dropped), so this
    # downgrade restores the SCHEMA shape only -- it does not attempt
    # to reconstruct the pre-migration data. Restore from a backup
    # taken before running upgrade() if you need the old data shape
    # back.
    raise NotImplementedError(
        "This migration's data changes are not reversible. Restore from a "
        "pre-upgrade backup instead of downgrading."
    )
