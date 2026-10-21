"""customer orders: pieces, deposit method, booking link

Revision ID: cust2order01
Revises: recon1late01
Marker: PATCH1-CUSTOMER-ORDER-FIELDS
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "cust2order01"
down_revision = "recon1late01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customer_requests", sa.Column("order_pieces", sa.Integer(), nullable=True))
    op.add_column("customer_requests", sa.Column("customer_pieces", sa.Integer(), nullable=True))
    op.add_column("customer_requests", sa.Column("deposit_method", sa.String(), nullable=True))
    op.add_column("customer_requests", sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_customer_requests_booking_id", "customer_requests", "sales",
        ["booking_id"], ["id"], ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_customer_requests_booking_id", "customer_requests", type_="foreignkey")
    op.drop_column("customer_requests", "booking_id")
    op.drop_column("customer_requests", "deposit_method")
    op.drop_column("customer_requests", "customer_pieces")
    op.drop_column("customer_requests", "order_pieces")
