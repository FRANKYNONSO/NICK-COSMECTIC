"""add bags unit: restock list constraint + warehouse push bag columns

Revision ID: bg1bg2bg3bg4
Revises: c9d0e1f2a3b4
"""
from alembic import op
import sqlalchemy as sa

revision = "bg1bg2bg3bg4"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

_OLD = "unit_label IN ('pieces','dozens','cartons','half_dozen','half_carton')"
_NEW = "unit_label IN ('pieces','dozens','cartons','bags','half_dozen','half_carton')"


def _drop_unit_check():
    # Drop whichever name the constraint has in this database.
    op.execute("ALTER TABLE restock_list_entries DROP CONSTRAINT IF EXISTS ck_restock_entry_unit_label")
    op.execute("ALTER TABLE restock_list_entries DROP CONSTRAINT IF EXISTS ck_restock_list_entries_ck_restock_entry_unit_label")


def upgrade() -> None:
    _drop_unit_check()
    op.execute(f"ALTER TABLE restock_list_entries ADD CONSTRAINT ck_restock_entry_unit_label CHECK ({_NEW})")

    op.add_column("stock_transfers", sa.Column("bags_count", sa.Integer(), nullable=True))
    op.add_column("stock_transfers", sa.Column("bags_pieces_per_unit", sa.Integer(), nullable=True))
    op.add_column(
        "stock_transfers",
        sa.Column("bags_half", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("stock_transfers", "bags_half")
    op.drop_column("stock_transfers", "bags_pieces_per_unit")
    op.drop_column("stock_transfers", "bags_count")

    op.execute("DELETE FROM restock_list_entries WHERE unit_label = 'bags'")
    _drop_unit_check()
    op.execute(f"ALTER TABLE restock_list_entries ADD CONSTRAINT ck_restock_entry_unit_label CHECK ({_OLD})")