"""warehouse_stock: enforce one row per branch + product

The model declares uq_warehouse_stock_branch_product but the database never got
it (the variant-retirement migration dropped the old variant-based constraint).
Refuses to run if duplicate rows exist, so nothing is merged or deleted silently.

Revision ID: whstk2uniq01
Revises: shelf1rcpt01
"""
from alembic import op
import sqlalchemy as sa

revision = "whstk2uniq01"
down_revision = "shelf1rcpt01"
branch_labels = None
depends_on = None

CONSTRAINT = "uq_warehouse_stock_branch_product"


def upgrade():
    bind = op.get_bind()
    dupes = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT 1 FROM warehouse_stock "
            "GROUP BY branch_id, product_id HAVING count(*) > 1) d"
        )
    ).scalar()
    if dupes:
        raise RuntimeError(
            "warehouse_stock has %s duplicated (branch_id, product_id) groups. "
            "Reconcile them manually before adding the unique constraint." % dupes
        )
    existing = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = :n "
            "AND conrelid = 'warehouse_stock'::regclass"
        ),
        {"n": CONSTRAINT},
    ).first()
    if existing is None:
        op.create_unique_constraint(CONSTRAINT, "warehouse_stock", ["branch_id", "product_id"])


def downgrade():
    op.drop_constraint(CONSTRAINT, "warehouse_stock", type_="unique")