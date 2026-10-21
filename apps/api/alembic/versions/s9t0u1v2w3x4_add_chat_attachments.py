"""add chat message attachments (image/voice notes)

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-08-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 's9t0u1v2w3x4'
down_revision: Union[str, None] = 'r8s9t0u1v2w3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("attachment_url", sa.String(), nullable=True))
    op.add_column("chat_messages", sa.Column("attachment_type", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_chat_attachment_type",
        "chat_messages",
        "attachment_type IS NULL OR attachment_type IN ('image','voice')",
    )
    # body used to be required (min_length=1 at the API layer); an
    # attachment-only message (photo/voice note, no caption) needs it
    # to allow empty, so relax the NOT NULL default at the column
    # level too. Existing rows are untouched -- they already have text.
    op.alter_column("chat_messages", "body", server_default="")


def downgrade() -> None:
    op.alter_column("chat_messages", "body", server_default=None)
    op.drop_constraint("ck_chat_attachment_type", "chat_messages", type_="check")
    op.drop_column("chat_messages", "attachment_type")
    op.drop_column("chat_messages", "attachment_url")
