"""add username to users (login by username)

Revision ID: usrname01login
Revises: xfer8retacct01
"""
from alembic import op
import sqlalchemy as sa

revision = "usrname01login"
down_revision = "xfer8retacct01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(), nullable=True))
    op.execute("CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_username_lower")
    op.drop_column("users", "username")