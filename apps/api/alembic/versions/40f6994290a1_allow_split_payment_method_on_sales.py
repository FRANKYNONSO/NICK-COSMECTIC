"""allow split payment method on sales

Revision ID: 40f6994290a1
Revises: v2w3x4y5z6a7
Create Date: 2026-08-26 13:41:44.972546

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '40f6994290a1'
down_revision: Union[str, None] = 'v2w3x4y5z6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_sale_payment_method", "sales", type_="check")
    op.create_check_constraint(
        "ck_sale_payment_method",
        "sales",
        "payment_method IN ('cash','card','transfer','online','split')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sale_payment_method", "sales", type_="check")
    op.create_check_constraint(
        "ck_sale_payment_method",
        "sales",
        "payment_method IN ('cash','card','transfer','online')",
    )