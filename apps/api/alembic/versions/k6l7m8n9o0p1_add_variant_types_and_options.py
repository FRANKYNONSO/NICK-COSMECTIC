"""add variant_types and variant_options tables

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-08-21 12:05:00.000000

This backs the super-admin-editable variant system: a VariantType is a
reusable category like "Size" or "Product Line" (spec: Caro White's
Lotion/Oil/Soap each need their own set of options -- Big/Medium/Small
for Lotion, brand names like "Fair & White So White" for Oil, scents
like Papaya/Carrot/Avocado for Soap). A VariantOption is one concrete
choice within a type. Deliberately NOT a foreign key on
product_variants.variant_name -- that stays free text so existing
variants and the "type something new" case both keep working; these
tables just power the picker that fills it in, and super_admin can
add/edit/soft-delete types and options without touching product data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'k6l7m8n9o0p1'
down_revision: Union[str, None] = 'j5k6l7m8n9o0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "variant_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_variant_types_name"),
    )
    op.create_table(
        "variant_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("variant_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("variant_types.id"), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("variant_type_id", "value", name="uq_variant_options_type_value"),
    )
    op.create_index("ix_variant_options_type", "variant_options", ["variant_type_id"])


def downgrade() -> None:
    op.drop_index("ix_variant_options_type", table_name="variant_options")
    op.drop_table("variant_options")
    op.drop_table("variant_types")
