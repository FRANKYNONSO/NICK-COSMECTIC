"""restock list: allow freeform (non-catalog) items

Revision ID: rl2freeform01
Revises: rl1rl2rl3rl4
Create Date: 2026-09-20

Adds support for typing a brand-new item straight onto the Restock
List -- name + optional photo, no matching Product -- alongside the
existing catalog-linked entries. See RestockListEntry's docstring in
models.py for the full design note.

NOTE: down_revision below assumes rl1rl2rl3rl4_add_restock_list.py
already got its own down_revision fixed and was applied (i.e.
`restock_list_entries` already exists in your database). If you
haven't run that one yet, run it first; if you HAVE and your actual
head is something else because more migrations landed since, check
`alembic heads` again the same way as before.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "rl2freeform01"
down_revision: Union[str, None] = "rl1rl2rl3rl4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "restock_list_entries",
        "product_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column("restock_list_entries", sa.Column("custom_name", sa.String(), nullable=True))
    op.add_column("restock_list_entries", sa.Column("custom_image_url", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_restock_entry_has_product_or_custom_name",
        "restock_list_entries",
        "(product_id IS NOT NULL) OR (custom_name IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_restock_entry_has_product_or_custom_name", "restock_list_entries", type_="check")
    op.drop_column("restock_list_entries", "custom_image_url")
    op.drop_column("restock_list_entries", "custom_name")
    op.alter_column(
        "restock_list_entries",
        "product_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
