import uuid

import pytest

from infrastructure.db.models import User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio


async def _make_user(db_session, role: str) -> User:
    user = User(
        full_name="Test User",
        role=role,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_headers(user: User) -> dict:
    token = create_access_token(str(user.id), user.role, None)
    return {"Authorization": f"Bearer {token}"}


async def test_super_admin_can_download_blank_staff_form(client, db_session):
    admin = await _make_user(db_session, "super_admin")
    await db_session.commit()

    resp = await client.get("/api/v1/staff-applications/form.pdf", headers=_auth_headers(admin))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


async def test_staff_cannot_download_blank_staff_form(client, db_session):
    staff = await _make_user(db_session, "cashier")
    await db_session.commit()

    resp = await client.get("/api/v1/staff-applications/form.pdf", headers=_auth_headers(staff))

    assert resp.status_code == 403
