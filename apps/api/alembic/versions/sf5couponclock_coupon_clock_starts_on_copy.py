"""website coupons: the code stays hidden until revealed; revealing starts the countdown

expires_at becomes nullable (NULL = code not revealed yet), started_at records
when the code was first revealed, valid_hours is how long it lasts after that.
Existing coupons keep their expires_at and count as already running.
"""
from alembic import op
import sqlalchemy as sa

revision = "sf5couponclock"
down_revision = "sf1sf2sf3sf4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "storefront_coupons",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "storefront_coupons",
        sa.Column("valid_hours", sa.BigInteger(), nullable=False, server_default="24"),
    )
    op.alter_column(
        "storefront_coupons",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    # coupons that were never revealed have no expiry: give them one so NOT NULL can return
    op.execute("UPDATE storefront_coupons SET expires_at = now() WHERE expires_at IS NULL")
    op.alter_column(
        "storefront_coupons",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.drop_column("storefront_coupons", "valid_hours")
    op.drop_column("storefront_coupons", "started_at")
