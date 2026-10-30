import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# PATCH22B-NO-DB-FALLBACK: no password and no default database in code.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Set it to the database you mean to use "
        "(tests: the shelf_test copy). Nothing was changed."
    )

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# BRANCH-SUSPEND-GUARD: refuse writes to records of a suspended branch (all roles)
from fastapi import HTTPException as _HTTPException
from sqlalchemy import bindparam as _bindparam, event as _event, text as _text
from sqlalchemy.orm import Session as _SyncSession

_BRANCH_ATTRS = ("branch_id", "from_branch_id", "to_branch_id")
_GUARD_SKIP_TABLES = {"users", "branches"}


def _branch_suspension_guard(session, flush_context, instances):
    ids = set()
    changed = list(session.new) + [o for o in session.dirty if session.is_modified(o)]
    for obj in changed:
        table = getattr(obj, "__tablename__", None)
        if table is None or table in _GUARD_SKIP_TABLES:
            continue
        for attr in _BRANCH_ATTRS:
            value = getattr(obj, attr, None)
            if value is not None:
                ids.add(value)
    if not ids:
        return
    stmt = _text("SELECT name FROM branches WHERE is_suspended = true AND id IN :ids").bindparams(
        _bindparam("ids", expanding=True)
    )
    rows = session.execute(stmt, {"ids": list(ids)}).all()
    if rows:
        raise _HTTPException(status_code=403, detail="This branch is suspended.")


_event.listen(_SyncSession, "before_flush", _branch_suspension_guard)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
