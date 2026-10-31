"""PATCH10A: product_unit_sizes -- how many pieces one unit holds, per product.

Carton / bag / roll sizes stay on the product row (default_pieces_per_*).
Every other unit (dozen, pack, ...) can have its own size per product here.
A product with no row for a unit uses the unit's own default number, if any.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "pk10a01prodsizes"
down_revision = "pk9a01units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_unit_sizes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("packaging_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pieces", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("product_id", "unit_id", name="uq_product_unit_sizes_product_unit"),
        sa.CheckConstraint("pieces >= 2", name="ck_product_unit_sizes_pieces"),
    )
    op.create_index("ix_product_unit_sizes_product_id", "product_unit_sizes", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_product_unit_sizes_product_id", table_name="product_unit_sizes")
    op.drop_table("product_unit_sizes")
