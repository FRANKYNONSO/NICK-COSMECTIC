"""branch-to-branch transfers recorded as sales (XFER1)

Revision ID: xfer1sale01
Revises: rl5reqpdf01

Adds the 'transfer' sale channel and, on stock_transfers, the price per
piece, how it was chosen (selling / wholesale / custom) and the sale row
that records the transfer. downgrade() fails if transfer sales exist.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "xfer1sale01"
down_revision = "rl5reqpdf01"
branch_labels = None
depends_on = None

_DROP_SALE_CHANNEL_CHECKS = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'sales'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%channel%'
  LOOP
    EXECUTE 'ALTER TABLE sales DROP CONSTRAINT ' || quote_ident(r.conname);
  END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(_DROP_SALE_CHANNEL_CHECKS)
    op.create_check_constraint(
        "ck_sale_channel", "sales", "channel IN ('pos','website','mobile','transfer')"
    )
    op.add_column("stock_transfers", sa.Column("unit_price_kobo", sa.BigInteger(), nullable=True))
    op.add_column("stock_transfers", sa.Column("price_basis", sa.String(), nullable=True))
    op.add_column("stock_transfers", sa.Column("sale_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_stock_transfers_sale_id", "stock_transfers", "sales", ["sale_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_stock_transfers_sale_id", "stock_transfers", type_="foreignkey")
    op.drop_column("stock_transfers", "sale_id")
    op.drop_column("stock_transfers", "price_basis")
    op.drop_column("stock_transfers", "unit_price_kobo")
    op.drop_constraint("ck_sale_channel", "sales", type_="check")
    op.create_check_constraint(
        "ck_sale_channel", "sales", "channel IN ('pos','website','mobile')"
    )
