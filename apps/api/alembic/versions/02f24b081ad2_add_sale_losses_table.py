"""add sale_losses table for the Sold Below Original Price loss category

Revision ID: 02f24b081ad2
Revises: 2bed15eb4a86
Create Date: 2026-09-02 00:30:00.000000

Update spec ("nick-cosmetics-update-spec-3.md", Section 18.1 #4, 18.3):
the 4th loss category ("Sold Below Original Price") is a real sale,
not an inventory write-off like the other three (which already had
stock_adjustments) -- this table records what it *would* have sold for
alongside what it actually did, so the Losses History can show it with
the same item/category/amount/date/branch shape as the other three.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "02f24b081ad2"
down_revision: Union[str, None] = "2bed15eb4a86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sale_losses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column(
            "sale_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sale_items.id"), nullable=False
        ),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("original_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("sold_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("value_kobo", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sale_losses_branch_id", "sale_losses", ["branch_id"])
    op.create_index("ix_sale_losses_created_at", "sale_losses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_sale_losses_created_at", table_name="sale_losses")
    op.drop_index("ix_sale_losses_branch_id", table_name="sale_losses")
    op.drop_table("sale_losses")
