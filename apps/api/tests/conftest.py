"""Shared fixtures for API-layer tests.

These are integration tests against a real Postgres database -- not
SQLite -- because the schema uses Postgres-specific types (UUID,
JSONB) that don't map cleanly onto SQLite, and because faithfully
testing the `role IN (...)` CheckConstraint on `users` matters here.
Point DATABASE_URL at the dev-compose Postgres (see
infra/docker/docker-compose.dev.yml) or a dedicated test database --
never at a database with real data, since each test truncates tables.

Run:
    docker compose -f infra/docker/docker-compose.dev.yml up -d postgres
    DATABASE_URL=postgresql+asyncpg://nickcosmetics:<password>@localhost:5432/shelf_test \
        pytest apps/api/tests -x
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# PATCH22B-NO-DB-FALLBACK: the tests never guess a database. The caller must set DATABASE_URL.
if not os.environ.get("DATABASE_URL"):
    raise RuntimeError(
        "DATABASE_URL is not set. Tests wipe tables, so set it to the shelf_test copy first."
    )
# PATCH6D-TEST-JWT-KEY: test-only key, 32 or more characters (no real secret).
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-for-pytest-runs-0123456789")

from api.dependencies import get_db_session  # noqa: E402
from infrastructure.db.models import Base  # noqa: E402
from main import app  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]


@pytest_asyncio.fixture(scope="session")
async def _engine():
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine):
    """One connection + transaction per test, rolled back afterwards --
    tests never leave data behind or see each other's writes."""
    connection = await _engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    session = session_factory()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()