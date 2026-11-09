"""rename staff role key to cashier"""
from alembic import op

revision = "cashier_key_01"
down_revision = "rolenames01"
branch_labels = None
depends_on = None


def _rename(old, new):
    # users.role has an FK to roles.name (no ON UPDATE CASCADE), so: add the new
    # role row as a copy, repoint everything that uses it, then drop the old row.
    op.execute(f"""
        INSERT INTO roles (id, name, display_name, permissions, is_system, created_by, created_at, updated_at)
        SELECT gen_random_uuid(), '{new}', display_name, permissions, is_system, created_by, created_at, now()
        FROM roles WHERE name = '{old}'
    """)
    op.execute(f"UPDATE users SET role = '{new}' WHERE role = '{old}'")
    op.execute(f"UPDATE staff_applications SET role_applied_for = '{new}' WHERE role_applied_for = '{old}'")
    op.execute(f"DELETE FROM roles WHERE name = '{old}'")


def upgrade():
    _rename("staff", "cashier")
    op.alter_column("staff_applications", "role_applied_for", server_default="cashier")


def downgrade():
    _rename("cashier", "staff")
    op.alter_column("staff_applications", "role_applied_for", server_default="staff")