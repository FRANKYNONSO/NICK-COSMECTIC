"""add is_announcement flag to chat_messages

Revision ID: a25a2a1f8624
Revises: 02f24b081ad2
Create Date: 2026-09-02 01:00:00.000000

Update spec ("nick-cosmetics-update-spec-3.md", Section 17): a
universal Announcements channel, broadcast by a Super Admin to all
staff in all branches. Reuses the existing group-channel mechanism
(chat_messages.recipient_id IS NULL) rather than a new table -- this
flag is what the router uses to tell an announcement apart from the
ordinary all-staff group chat.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a25a2a1f8624"
down_revision: Union[str, None] = "02f24b081ad2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("is_announcement", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "is_announcement")
