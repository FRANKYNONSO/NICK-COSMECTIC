"""shift compliance: violation exemption flag on shifts + shift_compliance_events table

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-30 00:00:00.000000

Extends the existing shift-tracking feature with a staff accountability
layer, per the "Staff Shift Tracking / Shift Management" extension spec.
Deliberately additive only -- no existing shift columns are touched or
renamed at the DB level (the spec explicitly says not to rename
opening_float_kobo; "Opening Cash" is a display-only relabel handled in
the API/frontend).

  * shifts.violation_exempt / violation_exempt_reason -- lets an admin
    mark a specific auto-closed shift as NOT the staff member's fault
    (server downtime, an admin closing it on their behalf, etc. --
    spec section 18), so it's excluded from compliance history.

  * shift_compliance_events -- one row per auto-closed shift that DOES
    count as a violation, unique on shift_id so it's always 1:1 with
    the shift that triggered it and can never double-count.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shifts",
        sa.Column("violation_exempt", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "shifts",
        sa.Column("violation_exempt_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "shifts",
        sa.Column("late_close", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "branches",
        sa.Column("shift_expected_close_hour", sa.Integer(), nullable=True),
    )
    op.add_column(
        "branches",
        sa.Column("shift_expected_close_minute", sa.Integer(), nullable=True),
    )
    op.add_column(
        "branches",
        sa.Column("shift_grace_minutes", sa.Integer(), nullable=False, server_default="10"),
    )

    op.create_table(
        "shift_compliance_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("shift_id", sa.UUID(), sa.ForeignKey("shifts.id"), nullable=False, unique=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("occurrence_number", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_shift_compliance_events_user_id", "shift_compliance_events", ["user_id"]
    )
    op.create_index(
        "ix_shift_compliance_events_branch_id", "shift_compliance_events", ["branch_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_shift_compliance_events_branch_id", table_name="shift_compliance_events")
    op.drop_index("ix_shift_compliance_events_user_id", table_name="shift_compliance_events")
    op.drop_table("shift_compliance_events")
    op.drop_column("branches", "shift_grace_minutes")
    op.drop_column("branches", "shift_expected_close_minute")
    op.drop_column("branches", "shift_expected_close_hour")
    op.drop_column("shifts", "late_close")
    op.drop_column("shifts", "violation_exempt_reason")
    op.drop_column("shifts", "violation_exempt")
