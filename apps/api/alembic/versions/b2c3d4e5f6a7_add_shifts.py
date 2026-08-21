# _column_exists_guard_applied
"""add shifts table and sales.shift_id

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-18 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shifts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("opened_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opening_float_kobo", sa.BigInteger(), nullable=False),
        sa.Column("closing_counted_kobo", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open','closed')", name="ck_shift_status"),
    )
    op.create_index("ix_shifts_branch_status", "shifts", ["branch_id", "status"])
    op.create_index("ix_shifts_opened_by_status", "shifts", ["opened_by", "status"])

    if "shift_id" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("sales")]:
        op.add_column(
            "sales",
            sa.Column("shift_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shifts.id"), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("sales", "shift_id")
    op.drop_index("ix_shifts_opened_by_status", table_name="shifts")
    op.drop_index("ix_shifts_branch_status", table_name="shifts")
    op.drop_table("shifts")
