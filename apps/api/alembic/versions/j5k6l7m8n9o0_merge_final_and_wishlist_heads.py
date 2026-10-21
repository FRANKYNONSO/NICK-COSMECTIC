"""merge final-merge, wishlist/staff-applications, and orphaned
password-reset/intake-receipt heads

Revision ID: j5k6l7m8n9o0
Revises: 54dba09d89c8, e863a5a15855, h3i4j5k6l7m8
Create Date: 2026-08-21 12:00:00.000000

Three separate migration heads existed before this merge, not two --
h3i4j5k6l7m8 (add_intake_receipt_damaged_missing, chained after
g2b3c4d5e6f7 add_password_reset_email_verification) was never folded
into 54dba09d89c8's "final merge of all migration branches" despite
that file's name. That's a real bug independent of anything else in
this changeset: User.password_reset_token_hash / pending_email /
pending_email_token_hash / pending_email_expires_at are live columns
the ORM model and auth.py's forgot-password/reset-password endpoints
already depend on, but the migration that creates them was an orphan
branch that `alembic upgrade head` would never apply. (There's also a
duplicate: e5f6a7b8c9d1_add_intake_receipt_damaged_missing.py adds the
same two intake_receipts columns as h3i4j5k6l7m8 and *is* already
merged via 54dba09d89c8 -- both are guarded with an "if column not in
existing columns" check, so applying both in either order is a no-op
the second time, not an error.)

Revision Id: j5k6l7m8n9o0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'j5k6l7m8n9o0'
down_revision: Union[str, None] = ('54dba09d89c8', 'e863a5a15855', 'h3i4j5k6l7m8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
