"""Persists and restores the login session so the app can resume
without a fresh login -- see local_db/models.py SessionCache for why.
"""

from sqlalchemy.orm import Session

from api_client import ApiClient
from local_db.models import SessionCache

_SESSION_ROW_ID = 1  # always a single row


def save_session(session: Session, api: ApiClient, last_branch_id: str | None = None) -> None:
    user = api.current_user or {}
    existing = session.get(SessionCache, _SESSION_ROW_ID)
    if existing is None:
        existing = SessionCache(id=_SESSION_ROW_ID)
        session.add(existing)

    existing.user_id = str(user.get("id", ""))
    existing.full_name = user.get("full_name", "")
    existing.email = user.get("email", "") or ""
    existing.role = user.get("role", "")
    existing.branch_id = str(user["branch_id"]) if user.get("branch_id") else None
    existing.access_token = api.access_token or ""
    existing.refresh_token = api.refresh_token or ""
    if last_branch_id is not None:
        existing.last_branch_id = last_branch_id

    session.commit()


def save_last_branch(session: Session, branch_id: str) -> None:
    existing = session.get(SessionCache, _SESSION_ROW_ID)
    if existing is not None:
        existing.last_branch_id = branch_id
        session.commit()


def load_session(session: Session) -> SessionCache | None:
    return session.get(SessionCache, _SESSION_ROW_ID)


def clear_session(session: Session) -> None:
    existing = session.get(SessionCache, _SESSION_ROW_ID)
    if existing is not None:
        session.delete(existing)
        session.commit()
