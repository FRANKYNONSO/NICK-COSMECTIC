# _column_exists_guard_applied
"""add staff_applications table and users.employee_id/date_joined

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-08-19 00:00:00.000000

NOTE: at the time this migration was written, alembic/versions already
had two divergent heads (c3d4e5f6a7b8 via the denied_permissions/shifts
chain, and a1b2c3d4e5f6 "add_wishlist_items_table" branching off
7ef0edc9c363 and never merged back in). This migration chains onto
c3d4e5f6a7b8, so `alembic upgrade head` will still report multiple
heads until the wishlist branch is merged in (e.g. with an
`alembic merge` revision) -- that pre-existing branch conflict isn't
something this change attempts to fix.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "employee_id" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("employee_id", sa.String(), nullable=True))
    op.create_unique_constraint("uq_users_employee_id", "users", ["employee_id"])
    if "date_joined" not in [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]:
        op.add_column("users", sa.Column("date_joined", sa.Date(), nullable=True))

    op.create_table(
        "staff_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False, server_default="under_review"),
        sa.Column("full_legal_name", sa.String(), nullable=False),
        sa.Column("preferred_name", sa.String(), nullable=False),
        sa.Column("profile_photo_url", sa.String(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(), nullable=True),
        sa.Column("phone_number", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("residential_address", sa.String(), nullable=True),
        sa.Column("state_of_residence", sa.String(), nullable=True),
        sa.Column("emergency_contact_name", sa.String(), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(), nullable=True),
        sa.Column("emergency_contact_relationship", sa.String(), nullable=True),
        sa.Column("id_type", sa.String(), nullable=True),
        sa.Column("id_number", sa.String(), nullable=True),
        sa.Column("id_document_path", sa.String(), nullable=True),
        sa.Column("id_issued_date", sa.Date(), nullable=True),
        sa.Column("id_expiry_date", sa.Date(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('under_review','accepted','rejected','suspended')",
            name="ck_staff_application_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("staff_applications")
    op.drop_column("users", "date_joined")
    op.drop_constraint("uq_users_employee_id", "users", type_="unique")
    op.drop_column("users", "employee_id")
