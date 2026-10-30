"""keep the PDF and the item list of every saved Restock Request

restock_requests.pdf_data: the exact PDF that was downloaded.
restock_requests.items_json: the items (name, quantity, unit, note) as JSON,
used for searching past requests and for later exports.
"""
from alembic import op
import sqlalchemy as sa

revision = "rl5reqpdf01"
down_revision = "loss01review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("restock_requests", sa.Column("pdf_data", sa.LargeBinary(), nullable=True))
    op.add_column("restock_requests", sa.Column("items_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("restock_requests", "items_json")
    op.drop_column("restock_requests", "pdf_data")
