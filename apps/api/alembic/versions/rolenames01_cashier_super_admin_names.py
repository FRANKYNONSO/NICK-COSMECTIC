"""role display names: Cashier and Super Admin

PATCHROLE-NAMES. Only the display_name of two rows changes; the role keys
(staff, super_admin) never change. A row is only touched while it still
holds the old text, so nothing typed by hand in between is overwritten.

Revision ID: rolenames01
Revises: permseed01
"""
import sqlalchemy as sa
from alembic import op

revision = "rolenames01"
down_revision = "permseed01"
branch_labels = None
depends_on = None

# (role key, old display name, new display name)
_CHANGES = (
    ("staff", "Cashiers", "Cashier"),
    ("super_admin", "BOSS", "Super Admin"),
)

_SQL = "UPDATE roles SET display_name = :new WHERE name = :name AND display_name = :old"


def upgrade() -> None:
    bind = op.get_bind()
    for name, old, new in _CHANGES:
        bind.execute(sa.text(_SQL), {"name": name, "old": old, "new": new})


def downgrade() -> None:
    bind = op.get_bind()
    for name, old, new in _CHANGES:
        bind.execute(sa.text(_SQL), {"name": name, "old": new, "new": old})
