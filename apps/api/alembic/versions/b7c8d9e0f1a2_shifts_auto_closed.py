"""shifts: add auto_closed flag for forced end-of-day closure

Revision ID: b7c8d9e0f1a2
Revises: w4x5y6z7a8b9
Create Date: 2026-08-27 00:00:00.000000

Spec: "there are times that a staff will actually open a shift and
forget to close it for the day... automatically, the system should
be able to trigger the close shift once it is 12 a.m... whenever they
come back the next day to open a shift, before they should be able
to open a shift, they should be able to see a prompt telling them
they have to complete their close shift informations."

Adds `auto_closed` (default false) to `shifts`. When the API's lazy
end-of-day sweep (see _auto_close_stale_shifts in
api/v1/routers/shifts.py, which runs on every open-shift/current-shift
call) finds a shift still `open` after its calendar day has passed in
Africa/Lagos time, it marks the row `status='closed', auto_closed=true`
with `closing_counted_kobo` left NULL -- nobody actually counted the
drawer at midnight. `POST /shifts` then refuses to open a new shift
for that user while any of their shifts sit in that state, and
`PATCH /shifts/{id}/close` accepts a confirmation (the belated counted
cash) for a closed-but-unconfirmed auto-closed shift, not just an
open one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'NEWID1'
down_revision: Union[str, None] = 'w4x5y6z7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shifts",
        sa.Column("auto_closed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Drop the server_default after backfill so future inserts rely on
    # the ORM-side default (matches the pattern of other boolean columns
    # in this schema) rather than carrying a permanent DB-side default.
    op.alter_column("shifts", "auto_closed", server_default=None)


def downgrade() -> None:
    op.drop_column("shifts", "auto_closed")
