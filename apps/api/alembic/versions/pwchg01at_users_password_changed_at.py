"""users: password_changed_at (old sessions after a password change)

Revision ID: pwchg01at
Revises: wopay01paid
Marker: PATCH25A-PASSWORD-CHANGED-AT
"""
from alembic import op
import sqlalchemy as sa

revision = "pwchg01at"
down_revision = "wopay01paid"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("users", "password_changed_at")
