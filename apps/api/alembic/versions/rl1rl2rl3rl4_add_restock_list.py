"""add restock list tables

Revision ID: rl1rl2rl3rl4
Revises: <SET THIS -- see note below>
Create Date: 2026-09-20

NOTE FOR WHOEVER RUNS THIS: this repo's migration history has more
than one head in the copy this was written against (two folder
copies of the project can drift out of sync per the project notes).
Before running, check the real current head with:

    alembic heads

...and paste whichever revision id that gives you into down_revision
below. If there's more than one head, merge them first the same way
the existing f2a7cffbcd81_merge_all_heads.py / j5k6l7m8n9o0 migrations
in this folder already do, then point down_revision at the merge.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "rl1rl2rl3rl4"
down_revision: Union[str, None] = "v5w6x7y8z9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "restock_list_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_label", sa.String(), nullable=False, server_default="pieces"),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "unit_label IN ('pieces','dozens','cartons','half_dozen','half_carton')",
            name="ck_restock_entry_unit_label",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "branch_id", name="uq_restock_entry_product_branch"),
    )
    op.create_table(
        "restock_list_entry_sends",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("restock_list_entry_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_name", sa.String(), nullable=False),
        sa.Column("quantity_sent", sa.Integer(), nullable=False),
        sa.Column("unit_label_sent", sa.String(), nullable=False),
        sa.Column("sent_by", sa.Uuid(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["restock_list_entry_id"], ["restock_list_entries.id"]),
        sa.ForeignKeyConstraint(["sent_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("restock_list_entry_sends")
    op.drop_table("restock_list_entries")
