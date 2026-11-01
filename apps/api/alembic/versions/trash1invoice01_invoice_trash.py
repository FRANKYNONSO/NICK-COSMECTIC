"""invoice Trash: purchase_invoices trash columns + super admin final-deletion ledger (TRASH1)

Revision ID: trash1invoice01
Revises: pk10a01prodsizes

Adds seven nullable columns to purchase_invoices (trash_state and who/when/why),
a CHECK on trash_state, and the table purchase_invoice_final_deletions.
Every existing invoice keeps trash_state NULL, so nothing changes for them.
downgrade() refuses to run while any invoice is in the Trash or the ledger
holds rows, so nothing is lost.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "trash1invoice01"
down_revision = "pk10a01prodsizes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_invoices", sa.Column("trash_state", sa.String(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "purchase_invoices",
        sa.Column("trashed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("purchase_invoices", sa.Column("trash_reason", sa.Text(), nullable=True))
    op.add_column("purchase_invoices", sa.Column("review_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "purchase_invoices",
        sa.Column("review_sent_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("purchase_invoices", sa.Column("review_reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_purchase_invoice_trash_state",
        "purchase_invoices",
        "trash_state IS NULL OR trash_state IN ('trashed', 'pending_final')",
    )
    op.create_index("ix_purchase_invoices_trash_state", "purchase_invoices", ["trash_state"])

    op.create_table(
        "purchase_invoice_final_deletions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_by_name", sa.String(), nullable=True),
        sa.Column("deleted_reason", sa.Text(), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("branch_name", sa.String(), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("invoice_number", sa.String(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("landed_cost_kobo", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("trashed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trashed_by_name", sa.String(), nullable=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trash_reason", sa.Text(), nullable=True),
        sa.Column("review_sent_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_sent_by_name", sa.String(), nullable=True),
        sa.Column("review_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_purchase_invoice_final_deletions_deleted_at", "purchase_invoice_final_deletions", ["deleted_at"])
    op.create_index("ix_purchase_invoice_final_deletions_invoice_id", "purchase_invoice_final_deletions", ["invoice_id"])


def downgrade() -> None:
    bind = op.get_bind()
    in_trash = bind.execute(sa.text("SELECT count(*) FROM purchase_invoices WHERE trash_state IS NOT NULL")).scalar()
    in_ledger = bind.execute(sa.text("SELECT count(*) FROM purchase_invoice_final_deletions")).scalar()
    if in_trash or in_ledger:
        raise RuntimeError(
            "Cannot downgrade: %s invoice(s) are in the Trash and the final-deletion ledger holds %s row(s)."
            % (in_trash, in_ledger)
        )
    op.drop_index("ix_purchase_invoice_final_deletions_invoice_id", table_name="purchase_invoice_final_deletions")
    op.drop_index("ix_purchase_invoice_final_deletions_deleted_at", table_name="purchase_invoice_final_deletions")
    op.drop_table("purchase_invoice_final_deletions")
    op.drop_index("ix_purchase_invoices_trash_state", table_name="purchase_invoices")
    op.drop_constraint("ck_purchase_invoice_trash_state", "purchase_invoices", type_="check")
    op.drop_column("purchase_invoices", "review_reason")
    op.drop_column("purchase_invoices", "review_sent_by")
    op.drop_column("purchase_invoices", "review_sent_at")
    op.drop_column("purchase_invoices", "trash_reason")
    op.drop_column("purchase_invoices", "trashed_by")
    op.drop_column("purchase_invoices", "trashed_at")
    op.drop_column("purchase_invoices", "trash_state")
