"""warehouse: real backstock ledger, distinct from branch-to-branch transfers

Revision ID: 846a32c3bb81
Revises: 6706d7e805da
Create Date: 2026-08-29 00:10:00.000000

Spec: "Push to store simply means... a shop... can only contain just
two cartons... the remaining [cartons] are now going to be stored
somewhere [a warehouse]... whenever they need it, they will go there,
pick it, and bring it to the shop to restock." The existing
stock_transfers table only ever modeled branch-to-branch movement of
sellable shop stock -- there was no concept of a branch's own
backstock warehouse at all, which is what was actually being asked
for. This adds:

  1. warehouse_stock -- a new, independent ledger of stock physically
     sitting in a branch's own warehouse (not on the shop floor, not
     sellable). Deliberately separate from stock_batches so none of
     the existing sale/POS/low-stock/inventory math needs to change.
  2. stock_transfers.transfer_type -- distinguishes a plain shop-to-
     shop transfer (the original, still-legitimate behavior) from the
     three new warehouse-specific movements: store_to_warehouse
     (push excess off the shop floor), warehouse_to_store (restock
     the shop from backstock), and warehouse_to_branch (push
     backstock directly to a *different* branch's shop floor).
     Existing rows are backfilled as 'branch_to_branch' since that's
     what they always represented.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '846a32c3bb81'
down_revision: Union[str, None] = '6706d7e805da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "warehouse_stock",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("quantity_pieces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("branch_id", "variant_id", name="uq_warehouse_stock_branch_variant"),
    )
    op.create_index("ix_warehouse_stock_branch_id", "warehouse_stock", ["branch_id"])

    op.add_column(
        "stock_transfers",
        sa.Column("transfer_type", sa.String(), nullable=False, server_default="branch_to_branch"),
    )
    op.create_check_constraint(
        "ck_stock_transfer_type",
        "stock_transfers",
        "transfer_type IN ('branch_to_branch','store_to_warehouse','warehouse_to_store','warehouse_to_branch')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_stock_transfer_type", "stock_transfers", type_="check")
    op.drop_column("stock_transfers", "transfer_type")
    op.drop_index("ix_warehouse_stock_branch_id", table_name="warehouse_stock")
    op.drop_table("warehouse_stock")
