"""coupons: link to a pending change (store credit)

Revision ID: cust3coupon01
Revises: cust2order01
Marker: PATCH3C-COUPON-SERVER
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "cust3coupon01"
down_revision = "cust2order01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("coupons", sa.Column("pending_change_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_coupons_pending_change_id", "coupons", "pending_changes",
        ["pending_change_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_constraint("ck_pending_change_settlement_method", "pending_changes", type_="check")
    op.create_check_constraint(
        "ck_pending_change_settlement_method",
        "pending_changes",
        "settlement_method IN ('cash','transfer','airtime','coupon') OR settlement_method IS NULL",
    )


def downgrade():
    used = op.get_bind().execute(
        sa.text("SELECT count(*) FROM pending_changes WHERE settlement_method = 'coupon'")
    ).scalar()
    if used:
        raise RuntimeError("Cannot downgrade: " + str(used) + " pending change(s) were settled by coupon.")
    op.drop_constraint("ck_pending_change_settlement_method", "pending_changes", type_="check")
    op.create_check_constraint(
        "ck_pending_change_settlement_method",
        "pending_changes",
        "settlement_method IN ('cash','transfer','airtime') OR settlement_method IS NULL",
    )
    op.drop_constraint("fk_coupons_pending_change_id", "coupons", type_="foreignkey")
    op.drop_column("coupons", "pending_change_id")
