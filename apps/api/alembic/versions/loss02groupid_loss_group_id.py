"""group the rows of one loss submission: stock_adjustments.loss_group_id"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "loss02groupid"
down_revision = "usrname01login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_adjustments", sa.Column("loss_group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_stock_adjustments_loss_group_id", "stock_adjustments", ["loss_group_id"])
    # Best-effort grouping of existing losses: rows recorded by the same
    # person, same reason/notes/photo/product/branch/status in the same
    # second were one submission split across batches.
    op.execute("""
        WITH src AS (
            SELECT sa.id, sa.adjusted_by, sa.reason, sa.status, sa.branch_id, sb.product_id,
                   COALESCE(sa.notes, '') AS n, COALESCE(sa.image_url, '') AS i,
                   date_trunc('second', sa.created_at) AS ts
            FROM stock_adjustments sa
            JOIN stock_batches sb ON sb.id = sa.stock_batch_id
        ),
        grp AS (
            SELECT adjusted_by, reason, status, branch_id, product_id, n, i, ts,
                   gen_random_uuid() AS gid
            FROM src
            GROUP BY adjusted_by, reason, status, branch_id, product_id, n, i, ts
        )
        UPDATE stock_adjustments sa
        SET loss_group_id = grp.gid
        FROM src
        JOIN grp ON src.adjusted_by IS NOT DISTINCT FROM grp.adjusted_by
                AND src.reason = grp.reason AND src.status = grp.status
                AND src.branch_id IS NOT DISTINCT FROM grp.branch_id
                AND src.product_id = grp.product_id
                AND src.n = grp.n AND src.i = grp.i AND src.ts = grp.ts
        WHERE sa.id = src.id
    """)


def downgrade() -> None:
    op.drop_index("ix_stock_adjustments_loss_group_id", table_name="stock_adjustments")
    op.drop_column("stock_adjustments", "loss_group_id")