"""stock_transfers.created_by_role (role snapshot) and the store_assistant role

Revision ID: stasst01
Revises: stfcorr01

PATCHA2M-STORE-ASSISTANT-MIGRATION

Adds an empty, nullable column. Old rows stay empty on purpose: the role they
had at the time was never recorded, so nothing is guessed or rewritten.
Also creates the Store Assistant role if it does not exist yet. Permissions
can be added later on the Roles page.
"""
from alembic import op

revision = "stasst01"
down_revision = "stfcorr01"
branch_labels = None
depends_on = None

_PERMS = '["restock_shelf", "view_stock", "view_low_stock", "search_products", "change_own_password"]'


def upgrade() -> None:
    op.execute("ALTER TABLE stock_transfers ADD COLUMN IF NOT EXISTS created_by_role VARCHAR")
    op.execute(
        "INSERT INTO roles (id, name, display_name, permissions, is_system, created_at, updated_at) "
        "SELECT gen_random_uuid(), 'store_assistant', 'Store Assistant', '" + _PERMS + "'::jsonb, false, now(), now() "
        "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'store_assistant')"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM roles WHERE name = 'store_assistant' "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'store_assistant')"
    )
    op.execute("ALTER TABLE stock_transfers DROP COLUMN IF EXISTS created_by_role")
