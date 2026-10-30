"""loss review flow: pending_review/rejected statuses, backfill branch and value on losses"""
from alembic import op

revision = "loss01review"
down_revision = "rl4orders01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE c record;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'stock_adjustments'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%pending_confirmation%'
          LOOP
            EXECUTE format('ALTER TABLE stock_adjustments DROP CONSTRAINT %I', c.conname);
          END LOOP;
        END $$;
    """)
    op.create_check_constraint(
        "ck_adjustment_status",
        "stock_adjustments",
        "status IN ('completed','pending_confirmation','pending_review','rejected')",
    )
    # Losses made before this change (spoiled refunds) had no branch and no value.
    op.execute("""
        UPDATE stock_adjustments sa SET branch_id = sb.branch_id
        FROM stock_batches sb
        WHERE sa.stock_batch_id = sb.id AND sa.branch_id IS NULL
    """)
    op.execute("""
        UPDATE stock_adjustments sa
        SET value_kobo = ABS(sa.quantity_delta) * p.selling_price_kobo
        FROM stock_batches sb JOIN products p ON p.id = sb.product_id
        WHERE sa.stock_batch_id = sb.id AND sa.value_kobo IS NULL AND sa.quantity_delta < 0
    """)
    # Damaged/expired losses nobody has acknowledged yet now wait for review.
    op.execute("""
        UPDATE stock_adjustments SET status = 'pending_review'
        WHERE reason IN ('damaged','expired') AND reviewed_at IS NULL AND status = 'completed'
    """)


def downgrade() -> None:
    op.execute("UPDATE stock_adjustments SET status = 'completed' WHERE status IN ('pending_review','rejected')")
    op.drop_constraint("ck_adjustment_status", "stock_adjustments", type_="check")
    op.create_check_constraint(
        "ck_adjustment_status",
        "stock_adjustments",
        "status IN ('completed','pending_confirmation')",
    )