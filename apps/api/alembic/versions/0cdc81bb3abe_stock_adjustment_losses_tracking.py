"""stock_adjustments: losses tracking (branch_id, value_kobo, intake_receipt_id, expanded reasons)

Revision ID: 0cdc81bb3abe
Revises: d5e6f7a8b9c0
Create Date: 2026-08-30 01:00:00.000000

Spec section 1: the Super Admin dashboard needs a Losses section
covering expired/damaged/missing/defective/supplier-return/other
stock. stock_adjustments already recorded the underlying event but
had no branch (needed a join through stock_batches for any
branch-scoped or "All Branches" report), no captured monetary value
(so a dashboard total had nowhere to read from without re-pricing
historical batches at today's cost), and a reason list that only
covered damaged/expired/theft/recount/other -- missing "missing"
(distinct from theft: no accusation of wrongdoing implied),
"defective", and "supplier_return" from the spec. `theft` and
`recount` are left in place for backward compatibility with existing
rows; nothing is removed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0cdc81bb3abe"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_adjustments",
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id"), nullable=True),
    )
    op.add_column("stock_adjustments", sa.Column("value_kobo", sa.BigInteger(), nullable=True))
    op.add_column(
        "stock_adjustments",
        sa.Column(
            "intake_receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("intake_receipts.id"),
            nullable=True,
        ),
    )

    # Backfill branch_id for existing rows from their stock batch, and
    # value_kobo for existing loss rows (negative delta) from the
    # batch's current unit cost -- the best information available for
    # history filed before this column existed.
    op.execute(
        """
        UPDATE stock_adjustments sa
        SET branch_id = sb.branch_id,
            value_kobo = CASE
                WHEN sa.quantity_delta < 0 THEN ABS(sa.quantity_delta) * sb.unit_cost_kobo
                ELSE NULL
            END
        FROM stock_batches sb
        WHERE sa.stock_batch_id = sb.id
        """
    )

    op.drop_constraint("ck_adjustment_reason", "stock_adjustments", type_="check")
    op.create_check_constraint(
        "ck_adjustment_reason",
        "stock_adjustments",
        "reason IN ('damaged','expired','theft','recount','other',"
        "'missing','defective','supplier_return')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_adjustment_reason", "stock_adjustments", type_="check")
    op.create_check_constraint(
        "ck_adjustment_reason",
        "stock_adjustments",
        "reason IN ('damaged','expired','theft','recount','other')",
    )
    op.drop_column("stock_adjustments", "intake_receipt_id")
    op.drop_column("stock_adjustments", "value_kobo")
    op.drop_column("stock_adjustments", "branch_id")
