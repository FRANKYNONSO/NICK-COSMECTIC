"""save each Restock Request under a running number and track what is on order

restock_requests: one row per downloaded request (RS-2026-001, ...).
restock_list_entry_sends: new request_id, request_no and received_at columns,
so an item with a send that has no received_at is "on order".
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "rl4orders01"
down_revision = "rl3note01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "restock_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_no", sa.String(), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("request_no", name="uq_restock_requests_request_no"),
    )
    op.add_column(
        "restock_list_entry_sends",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("restock_requests.id"), nullable=True),
    )
    op.add_column("restock_list_entry_sends", sa.Column("request_no", sa.String(), nullable=True))
    op.add_column("restock_list_entry_sends", sa.Column("received_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("restock_list_entry_sends", "received_at")
    op.drop_column("restock_list_entry_sends", "request_no")
    op.drop_column("restock_list_entry_sends", "request_id")
    op.drop_table("restock_requests")
