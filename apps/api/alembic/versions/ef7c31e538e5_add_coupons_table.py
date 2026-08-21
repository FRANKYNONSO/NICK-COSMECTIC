"""add_coupons_table

Revision ID: ef7c31e538e5
Revises: c7f1ea55cde7
Create Date: 2026-08-09 16:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ef7c31e538e5'
down_revision: Union[str, None] = 'c7f1ea55cde7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("discount_amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("used_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("discount_applied_kobo", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','used','expired','voided')", name="ck_coupon_status"
        ),
    )
    op.create_unique_constraint("uq_coupons_code", "coupons", ["code"])
    op.create_index("ix_coupons_status", "coupons", ["status"])
    op.create_index("ix_coupons_created_by", "coupons", ["created_by"])
    op.create_index("ix_coupons_used_by", "coupons", ["used_by"])


def downgrade() -> None:
    op.drop_index("ix_coupons_used_by", table_name="coupons")
    op.drop_index("ix_coupons_created_by", table_name="coupons")
    op.drop_index("ix_coupons_status", table_name="coupons")
    op.drop_constraint("uq_coupons_code", "coupons", type_="unique")
    op.drop_table("coupons")
