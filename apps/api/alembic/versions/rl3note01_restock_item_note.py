"""add a per-item note to restock list entries

The note is typed on the Restock List screen and printed under the item on
the Restock Request PDF.
"""
from alembic import op
import sqlalchemy as sa

revision = "rl3note01"
down_revision = "shelf2move01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("restock_list_entries", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("restock_list_entries", "note")
