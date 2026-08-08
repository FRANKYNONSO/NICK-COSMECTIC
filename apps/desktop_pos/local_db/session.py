from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import LOCAL_DB_PATH
from local_db.models import Base

# Sync engine (not async) -- deliberately simple for a single-user
# desktop app talking to a local file; no connection pooling concerns
# like the cloud API has.
engine = create_engine(f"sqlite:///{LOCAL_DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_local_db() -> None:
    """Creates the local SQLite file/tables if they don't exist yet.
    Safe to call every app startup -- create_all() no-ops on tables
    that already exist.
    """
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
