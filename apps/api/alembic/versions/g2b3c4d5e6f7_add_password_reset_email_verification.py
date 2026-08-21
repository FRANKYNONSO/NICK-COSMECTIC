# _column_exists_guard_applied
"""add password reset and pending-email-verification columns to users

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-19 13:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "password_reset_token_hash" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("password_reset_token_hash", sa.String(), nullable=True))
    if "password_reset_expires_at" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True))
    if "pending_email" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("pending_email", sa.String(), nullable=True))
    if "pending_email_token_hash" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("pending_email_token_hash", sa.String(), nullable=True))
    if "pending_email_expires_at" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("pending_email_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "pending_email_expires_at")
    op.drop_column("users", "pending_email_token_hash")
    op.drop_column("users", "pending_email")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_token_hash")
