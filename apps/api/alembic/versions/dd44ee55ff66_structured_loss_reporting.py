"""structured Damaged/Stolen/Expired reporting (section 4)

Revision ID: dd44ee55ff66
Revises: cc33dd44ee55
Create Date: 2026-09-06 00:00:00.000000

September 2026 change spec section 4: adds the fields needed for the
Damaged/Stolen/Expired structured reporting workflow --
theft_caused_by/theft_by_staff_name (mirrors damaged's own
caused_by/staff_name pair, but for who did it, as opposed to
theft_staff_on_duty which is who was on shift), image_url (all three
reasons require image/video evidence), and status/reviewed_by/
reviewed_at (theft starts 'pending_confirmation' -- quantity isn't
deducted until a super admin confirms it; reviewed_by/reviewed_at is a
generic "an admin has seen this" marker for damaged/expired).

*** Same caveat as the last two migrations in this chain: this repo
has had duplicate/branching revision ids in the past, so please run
`alembic heads` against your actual database before applying this and
adjust down_revision below if cc33dd44ee55 isn't really your current
head. ***
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "dd44ee55ff66"
down_revision = "cc33dd44ee55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_adjustments", sa.Column("theft_caused_by", sa.String(), nullable=True))
    op.add_column("stock_adjustments", sa.Column("theft_by_staff_name", sa.String(), nullable=True))
    op.add_column("stock_adjustments", sa.Column("image_url", sa.String(), nullable=True))
    op.add_column(
        "stock_adjustments",
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
    )
    op.add_column(
        "stock_adjustments",
        sa.Column("reviewed_by", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("stock_adjustments", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_adjustment_theft_caused_by",
        "stock_adjustments",
        "theft_caused_by IN ('customer','staff') OR theft_caused_by IS NULL",
    )
    op.create_check_constraint(
        "ck_adjustment_status",
        "stock_adjustments",
        "status IN ('completed','pending_confirmation')",
    )
    # Drop the server_default after backfilling existing rows -- new
    # rows always set status explicitly from application code.
    op.alter_column("stock_adjustments", "status", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_adjustment_status", "stock_adjustments", type_="check")
    op.drop_constraint("ck_adjustment_theft_caused_by", "stock_adjustments", type_="check")
    op.drop_column("stock_adjustments", "reviewed_at")
    op.drop_column("stock_adjustments", "reviewed_by")
    op.drop_column("stock_adjustments", "status")
    op.drop_column("stock_adjustments", "image_url")
    op.drop_column("stock_adjustments", "theft_by_staff_name")
    op.drop_column("stock_adjustments", "theft_caused_by")
