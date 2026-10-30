"""branch transfer returns (RET1)

Revision ID: xfer7ret01
Revises: xfer5inv01

Adds the transfer_returns table: a request to send transferred goods back
to the branch that sent them. It is linked to the first transfer, so a
return is never a new sale and never a new invoice.
downgrade() fails if any return row exists, so history is never lost.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "xfer7ret01"
down_revision = "xfer5inv01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_returns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("transfer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_transfers.id"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("from_branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("to_branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("started_by_side", sa.String(), nullable=False),
        sa.Column("quantity_pieces", sa.Integer(), nullable=False),
        sa.Column("unit_price_kobo", sa.BigInteger(), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="requested"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("responded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("taken_shelf_pieces", sa.Integer(), nullable=True),
        sa.Column("taken_warehouse_pieces", sa.Integer(), nullable=True),
        sa.Column("landed_shelf_pieces", sa.Integer(), nullable=True),
        sa.Column("landed_warehouse_pieces", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('requested','accepted','declined','cancelled','completed')",
            name="ck_transfer_return_status",
        ),
        sa.CheckConstraint("started_by_side IN ('sender','receiver')", name="ck_transfer_return_side"),
        sa.CheckConstraint(
            "payment_method IN ('cash','transfer') OR payment_method IS NULL",
            name="ck_transfer_return_payment_method",
        ),
        sa.CheckConstraint("quantity_pieces > 0", name="ck_transfer_return_quantity"),
    )
    op.create_index("ix_transfer_returns_transfer_id", "transfer_returns", ["transfer_id"])


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM transfer_returns")).scalar()
    if count:
        raise RuntimeError("Cannot downgrade: %s transfer return row(s) exist." % count)
    op.drop_index("ix_transfer_returns_transfer_id", table_name="transfer_returns")
    op.drop_table("transfer_returns")
