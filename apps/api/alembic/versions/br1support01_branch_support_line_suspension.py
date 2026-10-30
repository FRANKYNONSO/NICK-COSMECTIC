"""branches: support line and suspension

Revision ID: br1support01
Revises: pwchg01at
"""
from alembic import op
import sqlalchemy as sa

revision = "br1support01"
down_revision = "pwchg01at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("branches", sa.Column("support_line", sa.String(), nullable=True))
    op.add_column(
        "branches",
        sa.Column("is_suspended", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("branches", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("branches", "suspended_at")
    op.drop_column("branches", "is_suspended")
    op.drop_column("branches", "support_line")