from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import LOCAL_DB_PATH
from local_db.models import Base

# Sync engine (not async) -- deliberately simple for a single-user
# desktop app talking to a local file; no connection pooling concerns
# like the cloud API has.
engine = create_engine(f"sqlite:///{LOCAL_DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)

# Columns added to existing tables after they first shipped.
# create_all() (below) only creates whole tables that don't exist yet
# -- it never alters an existing table, so a device that already has
# a local DB from before these columns existed needs them added
# explicitly, or every query touching them fails with "no such
# column". table -> [(column_name, "SQL_TYPE DEFAULT ...")].
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "session_cache": [
        ("last_branch_id", "VARCHAR"),
    ],
    "outbox_sales": [
        ("shift_id", "VARCHAR"),
        ("voided", "BOOLEAN DEFAULT 0"),
        ("voided_at", "DATETIME"),
    ],
}


def _run_column_migrations() -> None:
    with engine.connect() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for table, columns in _COLUMN_MIGRATIONS.items():
            if table not in existing_tables:
                continue  # brand-new install -- create_all() already made it with every column
            existing_columns = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            for column_name, column_type in columns:
                if column_name in existing_columns:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"))
        conn.commit()


def init_local_db() -> None:
    """Creates the local SQLite file/tables if they don't exist yet,
    and adds any columns introduced since (see _COLUMN_MIGRATIONS).
    Safe to call every app startup -- both steps no-op on a database
    that's already fully up to date.
    """
    Base.metadata.create_all(engine)
    _run_column_migrations()


def get_session() -> Session:
    return SessionLocal()
