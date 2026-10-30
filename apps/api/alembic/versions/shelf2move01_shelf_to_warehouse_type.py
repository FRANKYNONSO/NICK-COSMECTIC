"""stock_transfers: allow transfer_type shelf_to_warehouse (fix51)

Revision ID: shelf2move01
Revises: br1support01
"""
from alembic import op
import sqlalchemy as sa

revision = "shelf2move01"
down_revision = "br1support01"
branch_labels = None
depends_on = None

_NEW = (
    "transfer_type IN ('branch_to_branch','store_to_warehouse','warehouse_to_store',"
    "'warehouse_to_branch','receipt_to_warehouse','receipt_to_shelf','shelf_to_warehouse')"
)
_OLD = (
    "transfer_type IN ('branch_to_branch','store_to_warehouse','warehouse_to_store',"
    "'warehouse_to_branch','receipt_to_warehouse','receipt_to_shelf')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS ck_stock_transfer_type")
    op.execute("ALTER TABLE stock_transfers ADD CONSTRAINT ck_stock_transfer_type CHECK (" + _NEW + ")")


def downgrade() -> None:
    n = op.get_bind().execute(
        sa.text("SELECT count(*) FROM stock_transfers WHERE transfer_type = 'shelf_to_warehouse'")
    ).scalar()
    if n:
        raise RuntimeError(
            "Cannot downgrade: " + str(n) + " shelf_to_warehouse row(s) exist and would be mislabelled."
        )
    op.execute("ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS ck_stock_transfer_type")
    op.execute("ALTER TABLE stock_transfers ADD CONSTRAINT ck_stock_transfer_type CHECK (" + _OLD + ")")