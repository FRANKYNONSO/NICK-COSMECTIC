"""staff_applications: applicant selects branch at registration

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-08-26 00:10:00.000000

Spec: "when creating an account for new staff... they have to select
the staff that they want to apply for [role]... if yes, maybe this
is what whatever they are applying, they should also be able to
apply for each branch." Previously the applicant only chose a role
(role_applied_for); which branch they'd work at was picked later, by
whichever admin accepted the application (StaffApplicationAccept.
branch_id). Now the applicant picks it themselves at sign-up time,
same as the role, and the reviewer sees it up front on the
application instead of having to ask separately and enter it at
accept time.

Nullable rather than required at the column level: existing rows
(applications submitted before this migration) have no branch on
file, and there is no sane branch to backfill them with. The API
layer (StaffRegisterRequest) is what actually requires a branch_id
on every *new* submission going forward.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'x5y6z7a8b9c0'
down_revision: Union[str, None] = 'w4x5y6z7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "staff_applications",
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("staff_applications", "branch_id")
