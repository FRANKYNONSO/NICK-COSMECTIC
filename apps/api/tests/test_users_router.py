import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio


async def _make_branch(db_session, name: str = "Main Branch") -> Branch:
    branch = Branch(name=name)
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(
    db_session,
    role: str,
    branch_id=None,
    email: str | None = None,
    full_name: str = "Test User",
) -> User:
    email = email or f"{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        full_name=full_name,
        role=role,
        email=email,
        password_hash=hash_password("Password123!"),
        branch_id=branch_id,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_headers(user: User) -> dict:
    token = create_access_token(str(user.id), user.role, str(user.branch_id) if user.branch_id else None)
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------ CREATE


async def test_super_admin_can_create_staff_in_any_branch(client, db_session):
    branch = await _make_branch(db_session)
    admin = await _make_user(db_session, role="super_admin")
    await db_session.commit()

    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "New Staff",
            "role": "staff",
            "email": "newstaff@example.com",
            "password": "SecurePass123",
            "branch_id": str(branch.id),
        },
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "staff"
    assert body["branch_id"] == str(branch.id)


async def test_branch_manager_can_only_create_staff_in_own_branch(client, db_session):
    own_branch = await _make_branch(db_session, "Own Branch")
    other_branch = await _make_branch(db_session, "Other Branch")
    manager = await _make_user(db_session, role="branch_manager", branch_id=own_branch.id)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Branch Staff",
            "role": "staff",
            "email": "branchstaff@example.com",
            "password": "SecurePass123",
            "branch_id": str(other_branch.id),
        },
        headers=_auth_headers(manager),
    )

    assert resp.status_code == 201
    assert resp.json()["branch_id"] == str(own_branch.id)


async def test_branch_manager_cannot_create_manager_or_admin_accounts(client, db_session):
    branch = await _make_branch(db_session)
    manager = await _make_user(db_session, role="branch_manager", branch_id=branch.id)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Wannabe Manager",
            "role": "branch_manager",
            "email": "wannabe@example.com",
            "password": "SecurePass123",
        },
        headers=_auth_headers(manager),
    )

    assert resp.status_code == 403


async def test_staff_cannot_create_users(client, db_session):
    branch = await _make_branch(db_session)
    staff = await _make_user(db_session, role="staff", branch_id=branch.id)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Nope",
            "role": "staff",
            "email": "nope@example.com",
            "password": "SecurePass123",
        },
        headers=_auth_headers(staff),
    )

    assert resp.status_code == 403


async def test_create_user_rejects_duplicate_email(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    await _make_user(db_session, role="staff", email="taken@example.com")
    await db_session.commit()

    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Duplicate",
            "role": "staff",
            "email": "taken@example.com",
            "password": "SecurePass123",
        },
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 409


async def test_create_user_rejects_invalid_role(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    await db_session.commit()

    resp = await client.post(
        "/api/v1/users",
        json={
            "full_name": "Bad Role",
            "role": "warehouse_king",
            "email": "badrole@example.com",
            "password": "SecurePass123",
        },
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 422


# -------------------------------------------------------------- LIST


async def test_branch_manager_list_is_scoped_to_own_branch(client, db_session):
    branch_a = await _make_branch(db_session, "Branch A")
    branch_b = await _make_branch(db_session, "Branch B")
    manager = await _make_user(db_session, role="branch_manager", branch_id=branch_a.id)
    await _make_user(db_session, role="staff", branch_id=branch_a.id, full_name="A Staff")
    await _make_user(db_session, role="staff", branch_id=branch_b.id, full_name="B Staff")
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/users?branch_id={branch_b.id}",
        headers=_auth_headers(manager),
    )

    assert resp.status_code == 200
    names = {u["full_name"] for u in resp.json()}
    assert "A Staff" in names or manager.full_name in names
    assert "B Staff" not in names


# ------------------------------------------------------------- ROLE


async def test_only_super_admin_can_change_roles(client, db_session):
    branch = await _make_branch(db_session)
    manager = await _make_user(db_session, role="branch_manager", branch_id=branch.id)
    staff = await _make_user(db_session, role="staff", branch_id=branch.id)
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/users/{staff.id}/role",
        json={"role": "branch_manager"},
        headers=_auth_headers(manager),
    )

    assert resp.status_code == 403


async def test_super_admin_can_promote_user_to_super_admin(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    staff = await _make_user(db_session, role="staff")
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/users/{staff.id}/role",
        json={"role": "super_admin"},
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 200
    assert resp.json()["role"] == "super_admin"


async def test_super_admin_cannot_demote_self_via_role_endpoint(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/users/{admin.id}/role",
        json={"role": "staff"},
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 400


# --------------------------------------------------------- ACTIVATION


async def test_cannot_deactivate_own_account(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/users/{admin.id}/deactivate",
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 400


async def test_deactivate_then_activate_user(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    staff = await _make_user(db_session, role="staff")
    await db_session.commit()

    deactivate_resp = await client.patch(
        f"/api/v1/users/{staff.id}/deactivate",
        headers=_auth_headers(admin),
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False

    activate_resp = await client.patch(
        f"/api/v1/users/{staff.id}/activate",
        headers=_auth_headers(admin),
    )
    assert activate_resp.status_code == 200
    assert activate_resp.json()["is_active"] is True


async def test_branch_manager_cannot_deactivate_user_in_other_branch(client, db_session):
    branch_a = await _make_branch(db_session, "Branch A")
    branch_b = await _make_branch(db_session, "Branch B")
    manager = await _make_user(db_session, role="branch_manager", branch_id=branch_a.id)
    other_staff = await _make_user(db_session, role="staff", branch_id=branch_b.id)
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/users/{other_staff.id}/deactivate",
        headers=_auth_headers(manager),
    )

    assert resp.status_code == 403


# ---------------------------------------------------- PASSWORD RESET


async def test_reset_password_updates_hash(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    staff = await _make_user(db_session, role="staff")
    await db_session.commit()
    old_hash = staff.password_hash

    resp = await client.post(
        f"/api/v1/users/{staff.id}/reset-password",
        json={"new_password": "BrandNewPassword1"},
        headers=_auth_headers(admin),
    )

    assert resp.status_code == 204
    result = await db_session.execute(select(User).where(User.id == staff.id))
    refreshed = result.scalar_one()
    assert refreshed.password_hash != old_hash