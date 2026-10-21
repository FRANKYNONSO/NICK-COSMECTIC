"""september update pass: product templates, size free-text, shift
admin-acknowledge, loss accountability fields

Revision ID: aa11bb22cc33
Revises: a25a2a1f8624
Create Date: 2026-09-05 00:00:00.000000

*** IMPORTANT -- please confirm the down_revision above before running
this ***. While preparing this migration I found that
alembic/versions/ in this repo already contains several files with
DUPLICATE revision IDs (e.g. a1b2c3d4e5f6, b7c8d9e0f1a2, c3d4e5f6a7b8,
and p2q3r4s5t6u7 each appear as the "Revision ID" in more than one
file), which gives `alembic heads` more than one answer and means the
true current head can't be determined by reading the files alone. That
issue is pre-existing and unrelated to this update -- I did not touch
any of those files. I traced what looked like the most complete,
most-recently-described chain to a25a2a1f8624 and pointed this
migration at it, but please run `alembic heads` / `alembic history`
against your actual database before applying this, and change
down_revision below if it doesn't match. If you'd like, paste me the
output and I'll rebase this migration onto the correct head.

Four unrelated changes from this update pass, bundled into one
migration since none of them are large enough to need their own:

  1. Item 5 (Add Product rework): products.size used to be locked to
     ('Big','Medium','Small') by ck_product_size. Dropped so "quick
     add size" can save anything typed in, free text.
  2. Item 15 (Dashboard unclosed-shift alerts): shift_compliance_events
     gets a *second*, admin-side acknowledgment
     (admin_acknowledged_at/admin_acknowledged_by/admin_note),
     separate from the existing staff self-ack (acknowledged_at,
     untouched) -- lets a manager mark "I've informed this staff
     member" with an optional corrective note straight from the Shift
     report.
  3. Item 11 (Losses reasons): stock_adjustments gets accountability
     columns for the two "someone needs to be answerable" loss
     reasons -- damage_caused_by/damage_staff_name for `damaged`,
     theft_staff_on_duty/theft_noticed_at for `theft`. A new check
     constraint keeps damage_caused_by to 'customer'/'staff'/NULL.
  4. Item 8 (Product Categories rework): two new tables,
     product_templates and product_template_categories -- a catalog of
     product-name + category + size templates that don't need to
     exist in live inventory yet. See the ProductTemplate /
     ProductTemplateCategory models for the full reasoning.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "aa11bb22cc33"
down_revision = "a25a2a1f8624"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Free-text product size
    op.drop_constraint("ck_product_size", "products", type_="check")

    # 2. Shift compliance admin-side acknowledgment
    op.add_column("shift_compliance_events", sa.Column("admin_acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shift_compliance_events", sa.Column("admin_acknowledged_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("shift_compliance_events", sa.Column("admin_note", sa.Text(), nullable=True))

    # 3. Loss accountability fields
    op.add_column("stock_adjustments", sa.Column("damage_caused_by", sa.String(), nullable=True))
    op.add_column("stock_adjustments", sa.Column("damage_staff_name", sa.String(), nullable=True))
    op.add_column("stock_adjustments", sa.Column("theft_staff_on_duty", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("stock_adjustments", sa.Column("theft_noticed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_adjustment_damage_caused_by",
        "stock_adjustments",
        "damage_caused_by IN ('customer','staff') OR damage_caused_by IS NULL",
    )

    # 4. Product templates catalog
    op.create_table(
        "product_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "product_template_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_templates.id"), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("sizes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("byproduct_variants", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("template_id", "category_id", name="uq_template_category"),
    )


def downgrade() -> None:
    op.drop_table("product_template_categories")
    op.drop_table("product_templates")

    op.drop_constraint("ck_adjustment_damage_caused_by", "stock_adjustments", type_="check")
    op.drop_column("stock_adjustments", "theft_noticed_at")
    op.drop_column("stock_adjustments", "theft_staff_on_duty")
    op.drop_column("stock_adjustments", "damage_staff_name")
    op.drop_column("stock_adjustments", "damage_caused_by")

    op.drop_column("shift_compliance_events", "admin_note")
    op.drop_column("shift_compliance_events", "admin_acknowledged_by")
    op.drop_column("shift_compliance_events", "admin_acknowledged_at")

    op.create_check_constraint(
        "ck_product_size",
        "products",
        "size IN ('Big','Medium','Small') OR size IS NULL",
    )
