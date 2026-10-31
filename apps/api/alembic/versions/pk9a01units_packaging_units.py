"""PATCH9A: packaging_units table (carton / dozen / bag / roll / piece + any added later)"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "pk9a01units"
down_revision = "loss02groupid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "packaging_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("plural_label", sa.String(), nullable=False),
        # NULL = size differs per product (carton / bag / roll use the
        # product's own default_pieces_per_* box on the product form).
        sa.Column("fixed_pieces", sa.Integer(), nullable=True),
        sa.Column("include_in_breakdown", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("""
        INSERT INTO packaging_units (code, label, plural_label, fixed_pieces, include_in_breakdown, is_builtin, sort_order) VALUES
          ('carton', 'Carton', 'Cartons', NULL, true,  true, 10),
          ('bag',    'Bag',    'Bags',    NULL, true,  true, 20),
          ('roll',   'Roll',   'Rolls',   NULL, true,  true, 30),
          ('dozen',  'Dozen',  'Dozens',  12,   true,  true, 40),
          ('piece',  'Piece',  'Pieces',  1,    false, true, 50)
    """)


def downgrade() -> None:
    op.drop_table("packaging_units")
