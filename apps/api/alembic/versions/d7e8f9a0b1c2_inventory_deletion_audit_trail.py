"""inventory deletion audit trail with reason + loss classification

Revision ID: d7e8f9a0b1c2
Revises: a2b3c4d5e6f7
Create Date: 2026-08-29 14:00:00.000000

Spec sections 8-10: deleting a product must never make it simply
disappear. The user must supply a reason (damaged / expired / lost /
spoiled / incorrect entry / duplicate / other, with a required
explanation for "other"), and the system must retain a traceable
record of what was deleted, how much stock it represented, who did
it, when, and whether it counts as an inventory loss for P&L
purposes. Product.is_deleted already made this a soft delete (so
historical sales/refunds/invoices referencing the product are
untouched); this table is the missing audit trail on top of that.

Snapshots product_name/category_name/sku/unit_of_measure at deletion
time rather than joining live, since the point of an audit trail is
that it still reads correctly after the product row is gone from
active use.

(Originally filed as revision c3d4e5f6a7b8 -- renamed to d7e8f9a0b1c2
because that ID collided with an existing, unrelated migration
already in this project's history, add_sale_payments.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_deletion_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(), nullable=False),
        sa.Column("category_name", sa.String(), nullable=True),
        sa.Column("sku", sa.String(), nullable=True),
        sa.Column("unit_of_measure", sa.String(), nullable=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("quantity_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reason", sa.String(), nullable=False,
        ),
        sa.Column("reason_note", sa.Text(), nullable=True),
        # Whether this counts as a stock/financial loss for P&L
        # purposes (spec section 10) vs. a data-entry correction
        # (incorrect_entry/duplicate). Reviewable/reclassifiable by a
        # super admin after the fact.
        sa.Column("is_loss", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("deleted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "reason IN ('damaged','expired','lost','spoiled','incorrect_entry','duplicate','other')",
            name="ck_inventory_deletion_reason",
        ),
    )
    op.create_index("ix_inventory_deletion_records_product_id", "inventory_deletion_records", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_deletion_records_product_id", table_name="inventory_deletion_records")
    op.drop_table("inventory_deletion_records")