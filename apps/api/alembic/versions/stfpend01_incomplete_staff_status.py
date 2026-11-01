"""staff_applications: add 'incomplete' status (accounts made from New staff must finish their profile)

Revision ID: stfpend01
Revises: trash1invoice01
"""
from alembic import op

revision = "stfpend01"
down_revision = "trash1invoice01"
branch_labels = None
depends_on = None

# Drops whatever CHECK constraint currently guards staff_applications.status,
# regardless of what it is named, so this cannot fail on a naming mismatch.
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
    op.execute(
        "ALTER TABLE staff_applications ADD CONSTRAINT ck_staff_application_status "
        "CHECK (status IN ('incomplete','under_review','accepted','rejected','suspended'))"
    )


def downgrade() -> None:
    op.execute("UPDATE staff_applications SET status = 'under_review' WHERE status = 'incomplete'")
    op.execute(_DROP_STATUS_CHECK)
    op.execute(
        "ALTER TABLE staff_applications ADD CONSTRAINT ck_staff_application_status "
        "CHECK (status IN ('under_review','accepted','rejected','suspended'))"
    )