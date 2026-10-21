"""pending_changes: settlement methods, reference code, admin approval (platform update spec section 2)

Revision ID: ee22ff33gg44
Revises: dd11ee22ff33
Create Date: 2026-09-09 00:00:01.000000

Platform update spec section 2 (POS -- Change Handling & Refund
Routing): the pending_changes table only ever supported a bare "cash
owed, settle when handed over" case. This adds:

  - reference_code: the actual unique identifier for matching a
    returning customer (section 2.2) -- system-generated, not the
    customer's name, which can collide.
  - settlement_method + bank/airtime detail columns (section 2.3).
  - a 'pending' status between 'outstanding' and 'settled', plus
    approved_by/processed_at, so a transfer/airtime request routes
    through the same Super Admin/Branch Manager approval flow as a
    refund (section 2.4) instead of being settleable by any cashier.

Existing rows (all pre-dating this feature, so all plain cash-owed
cases with no method recorded) are backfilled with
settlement_method='cash' and a generated reference_code, since the
column is unique and existing rows can't be left NULL if a later
lookup needs to treat NULL specially -- leaving them exactly as they
already are (outstanding/settled) requires no status backfill.

*** Same caveat as prior migrations in this chain: this repo has had
duplicate/branching revision ids in the past, so please run
`alembic heads` against your actual database before applying this and
adjust down_revision below if dd11ee22ff33 isn't really your current
head. ***
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ee22ff33gg44"
down_revision: Union[str, None] = "dd11ee22ff33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


pending_changes = sa.table(
    "pending_changes",
    sa.column("id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.column("reference_code", sa.String),
    sa.column("settlement_method", sa.String),
)


def upgrade() -> None:
    op.add_column("pending_changes", sa.Column("reference_code", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_pending_change_reference_code", "pending_changes", ["reference_code"]
    )
    op.add_column("pending_changes", sa.Column("settlement_method", sa.String(), nullable=True))
    op.add_column("pending_changes", sa.Column("bank_name", sa.String(), nullable=True))
    op.add_column("pending_changes", sa.Column("account_number", sa.String(), nullable=True))
    op.add_column("pending_changes", sa.Column("account_name", sa.String(), nullable=True))
    op.add_column("pending_changes", sa.Column("airtime_phone_number", sa.String(), nullable=True))
    op.add_column("pending_changes", sa.Column("airtime_network", sa.String(), nullable=True))
    op.add_column("pending_changes", sa.Column("approved_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_pending_change_approved_by", "pending_changes", "users", ["approved_by"], ["id"]
    )
    op.add_column("pending_changes", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("ck_pending_change_status", "pending_changes", type_="check")
    op.create_check_constraint(
        "ck_pending_change_status", "pending_changes", "status IN ('outstanding','pending','settled')"
    )
    op.create_check_constraint(
        "ck_pending_change_settlement_method",
        "pending_changes",
        "settlement_method IN ('cash','transfer','airtime') OR settlement_method IS NULL",
    )

    # Backfill every pre-existing row (all of them plain cash-owed
    # cases, per the docstring above) with settlement_method='cash'
    # and a short generated reference code, so the new unique
    # reference_code column and settlement_method field are populated
    # consistently rather than left NULL on history that predates them.
    bind = op.get_bind()
    rows = bind.execute(sa.select(pending_changes.c.id)).fetchall()
    for (row_id,) in rows:
        code = f"CHG-{str(row_id).split('-')[0].upper()}"
        bind.execute(
            pending_changes.update()
            .where(pending_changes.c.id == row_id)
            .values(reference_code=code, settlement_method="cash")
        )


def downgrade() -> None:
    op.drop_constraint("ck_pending_change_settlement_method", "pending_changes", type_="check")
    op.drop_constraint("ck_pending_change_status", "pending_changes", type_="check")
    op.create_check_constraint(
        "ck_pending_change_status", "pending_changes", "status IN ('outstanding','settled')"
    )
    op.drop_constraint("fk_pending_change_approved_by", "pending_changes", type_="foreignkey")
    op.drop_column("pending_changes", "processed_at")
    op.drop_column("pending_changes", "approved_by")
    op.drop_column("pending_changes", "airtime_network")
    op.drop_column("pending_changes", "airtime_phone_number")
    op.drop_column("pending_changes", "account_name")
    op.drop_column("pending_changes", "account_number")
    op.drop_column("pending_changes", "bank_name")
    op.drop_column("pending_changes", "settlement_method")
    op.drop_constraint("uq_pending_change_reference_code", "pending_changes", type_="unique")
    op.drop_column("pending_changes", "reference_code")
