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


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
