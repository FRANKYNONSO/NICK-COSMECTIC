# PATCHP3L-TESTS: /complete-profile refuses super_admin as the requested role
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Role, StaffApplication, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

URL = "/api/v1/staff-applications/complete-profile"
REFUSAL = "'super_admin' is not a role you can apply for"
INVALID = "Choose a valid role."

_HASH_CACHE = []


def _password_hash():
    if not _HASH_CACHE:
        _HASH_CACHE.append(hash_password("Password123!"))
    return _HASH_CACHE[0]


async def _make_user(db_session, role, branch_id=None):
    user = User(
        full_name="P3L Test User",
        role=role,
        email=uuid.uuid4().hex[:8] + "@example.com",
        password_hash=_password_hash(),
        branch_id=branch_id,
        is_active=True,
        denied_permissions=[],
        granted_permissions=[],
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_headers(user):
    token = create_access_token(str(user.id), user.role, str(user.branch_id) if user.branch_id else None)
    return {"Authorization": "Bearer " + token}


async def _ensure_role(db_session, name):
    # Creates the role row only when it is missing. Rolled back after the test.
    role = (await db_session.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        db_session.add(Role(name=name, display_name=name, permissions=[], is_system=False, created_by=None))
        await db_session.flush()


async def _make_applicant(db_session, status):
    user = await _make_user(db_session, "cashier", None)
    row = StaffApplication(
        user_id=user.id,
        status=status,
        branch_id=None,
        full_legal_name="P3L Applicant",
        preferred_name="P3L",
        email=user.email,
        id_document_path=None,
    )
    db_session.add(row)
    await db_session.flush()
    return user, row


async def _stored_role(db_session, application_id):
    result = await db_session.execute(
        select(StaffApplication.role_applied_for).where(StaffApplication.id == application_id)
    )
    return result.scalar_one_or_none()


def _form(role):
    return {"preferred_name": "p3l_" + uuid.uuid4().hex[:8], "role_applied_for": role}


@pytest.mark.parametrize("status", ["incomplete", "needs_changes"])
async def test_complete_profile_refuses_super_admin_role(client, db_session, status):
    await _ensure_role(db_session, "super_admin")
    user, row = await _make_applicant(db_session, status)
    app_id = row.id
    headers = _auth_headers(user)
    await db_session.commit()
    before = await _stored_role(db_session, app_id)

    resp = await client.post(URL, data=_form("super_admin"), headers=headers)
    assert resp.status_code == 422, resp.text
    assert REFUSAL in resp.text
    assert await _stored_role(db_session, app_id) == before


async def test_complete_profile_unknown_role_still_refused(client, db_session):
    user, row = await _make_applicant(db_session, "incomplete")
    app_id = row.id
    headers = _auth_headers(user)
    await db_session.commit()
    before = await _stored_role(db_session, app_id)

    resp = await client.post(URL, data=_form("p3l_no_such_role_" + uuid.uuid4().hex[:6]), headers=headers)
    assert resp.status_code == 422, resp.text
    assert INVALID in resp.text
    assert await _stored_role(db_session, app_id) == before


async def test_complete_profile_website_role_still_refused(client, db_session):
    user, row = await _make_applicant(db_session, "incomplete")
    headers = _auth_headers(user)
    await db_session.commit()

    resp = await client.post(URL, data=_form("my_website_role"), headers=headers)
    assert resp.status_code == 422, resp.text
    assert INVALID in resp.text


async def test_complete_profile_cashier_request_not_blocked_by_new_rule(client, db_session):
    await _ensure_role(db_session, "cashier")
    user, row = await _make_applicant(db_session, "incomplete")
    headers = _auth_headers(user)
    await db_session.commit()

    resp = await client.post(URL, data=_form("cashier"), headers=headers)
    assert REFUSAL not in resp.text, resp.text
    assert resp.status_code < 500, resp.text


async def test_complete_profile_status_check_runs_before_role_rule(client, db_session):
    await _ensure_role(db_session, "super_admin")
    user, row = await _make_applicant(db_session, "under_review")
    headers = _auth_headers(user)
    await db_session.commit()

    resp = await client.post(URL, data=_form("super_admin"), headers=headers)
    assert resp.status_code == 400, resp.text
    assert REFUSAL not in resp.text