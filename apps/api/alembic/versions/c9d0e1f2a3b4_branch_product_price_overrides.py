"""branch_product_prices table for per-branch price overrides

Revision ID: b7c8d9e0f1a2
Revises: 68a3226f3633
Create Date: 2026-09-25 00:00:00.000000

Adds branch-specific price override support (per the price-scoping
fix request): Product.selling_price_kobo remains the single global/
default price, editable only by super_admin. A branch_manager editing
"price" now writes to this new table instead -- one row per
(branch_id, product_id), applied only at that branch (catalog display
and POS checkout), never touching the global Product price. When
super_admin changes the global price, the API layer (not this
migration) is responsible for clearing any existing override rows for
that product, so every branch reverts to following the new global
price until a branch_manager sets a fresh override.

set_by/updated_at are for the price-change notification/history
feature: who changed it and when, surfaced to super_admin via the
notification bell and to that branch's Reports page.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "68a3226f3633"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "branch_product_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selling_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("set_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("branch_id", "product_id", name="uq_branch_product_price"),
    )
    op.create_index(
        "ix_branch_product_prices_product_id",
        "branch_product_prices",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_branch_product_prices_product_id", table_name="branch_product_prices")
    op.drop_table("branch_product_prices")