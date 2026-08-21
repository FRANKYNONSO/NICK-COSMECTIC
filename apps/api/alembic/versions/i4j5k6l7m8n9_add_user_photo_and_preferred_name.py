# _column_exists_guard_applied
"""add profile_photo_url and preferred_name columns to users

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-08-20 09:00:00.000000

Lets ANY account (including one created directly via POST /users, with
no staff application) set a profile photo and a preferred display name.
Previously both lived only on StaffApplication, so admin-created
accounts got a 400 ("This account has no staff application profile...")
when trying to use either. See api/v1/routers/profile.py.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "profile_photo_url" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column(
            "users",
            sa.Column("profile_photo_url", sa.String(), nullable=True),
        )
    if "preferred_name" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column(
            "users",
            sa.Column("preferred_name", sa.String(), nullable=True),
        )
    # Backfill from any existing staff_applications rows so accounts that
    # already had a photo/preferred name set don't appear to lose it.
    op.execute(
        """
        UPDATE users
        SET profile_photo_url = sa.profile_photo_url,
            preferred_name = sa.preferred_name
        FROM staff_applications sa
        WHERE sa.user_id = users.id
          AND (sa.profile_photo_url IS NOT NULL OR sa.preferred_name IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.drop_column("users", "preferred_name")
    op.drop_column("users", "profile_photo_url")
