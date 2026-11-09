"""staff_applications: 'needs_changes' status + reviewer correction note/fields

Revision ID: stfcorr01
Revises: stfpend01
"""
from alembic import op

revision = "stfcorr01"
down_revision = "stfpend01"
branch_labels = None
depends_on = None

_DROP_STATUS_CHECK = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = CAST('staff_applications' AS regclass)
      AND contype = 'c'
      AND position('under_review' in pg_get_constraintdef(oid)) > 0
  LOOP
    EXECUTE 'ALTER TABLE staff_applications DROP CONSTRAINT ' || quote_ident(r.conname);
  END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(_DROP_STATUS_CHECK)
    op.execute("ALTER TABLE staff_applications ADD COLUMN IF NOT EXISTS correction_note TEXT")
    op.execute("ALTER TABLE staff_applications ADD COLUMN IF NOT EXISTS correction_fields TEXT")
    op.create_check_constraint(
        "ck_staff_application_status",
        "staff_applications",
        "status IN ('incomplete','under_review','needs_changes','accepted','rejected','suspended')",
    )


def downgrade() -> None:
    op.execute(_DROP_STATUS_CHECK)
    op.execute("UPDATE staff_applications SET status = 'under_review' WHERE status = 'needs_changes'")
    op.create_check_constraint(
        "ck_staff_application_status",
        "staff_applications",
        "status IN ('incomplete','under_review','accepted','rejected','suspended')",
    )
    op.execute("ALTER TABLE staff_applications DROP COLUMN IF EXISTS correction_fields")
    op.execute("ALTER TABLE staff_applications DROP COLUMN IF EXISTS correction_note")
