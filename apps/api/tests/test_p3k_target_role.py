# PATCHP3K-TESTS: suspend, reactivate and delete refuse a branch_manager or super_admin target, and a reviewer with no branch
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, Role, StaffApplication, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "review_staff_applications"
BASE = "/api/v1/staff-applications"
ROUTES = [("suspend", "accepted"), ("reactivate", "suspended"), ("delete", "under_review")]
MESSAGE = "Not permitted to modify this employee"

_HASH_CACHE = []


def _password_hash():
    if not _HASH_CACHE:
        _HASH_CACHE.append(hash_password("Password123!"))
    return _HASH_CACHE[0]


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None, granted=None):
    user = User(
        full_name="P3K Test User",
        role=role,
        email=uuid.uuid4().hex[:8] + "@example.com",
        password_hash=_password_hash(),
        branch_id=branch_id,
        is_active=True,
        denied_permissions=denied or [],
        granted_permissions=granted or [],
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _auth_headers(user):
    token = create_access_token(str(user.id), user.role, str(user.branch_id) if user.branch_id else None)
    return {"Authorization": "Bearer " + token}


async def _set_role(db_session, name, add=(), remove=()):
    # Make sure the role row has the keys in add and not the keys in remove. Rolled back after the test.
    role = (await db_session.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        role = Role(name=name, display_name=name, permissions=list(add), is_system=False, created_by=None)
        db_session.add(role)
    else:
        perms = [p for p in list(role.permissions or []) if p not in remove and p not in add]
        perms.extend(add)
        role.permissions = perms
    await db_session.flush()


async def _ensure_role(db_session, name):
    # Creates the role row only when it is missing. Rolled back after the test.
    role = (await db_session.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        db_session.add(Role(name=name, display_name=name, permissions=[], is_system=False, created_by=None))
        await db_session.flush()


def _new_role_name():
    return "p3k_" + uuid.uuid4().hex[:8]


async def _add_application(db_session, user, status="under_review", app_branch_id=None, doc=None):
    row = StaffApplication(
        user_id=user.id,
        status=status,
        branch_id=app_branch_id,
        full_legal_name="P3K Applicant",
        preferred_name="P3K",
        email=user.email,
        id_document_path=doc,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _make_applicant(db_session, status="under_review", user_branch_id=None, app_branch_id=None, role="cashier", doc=None):
    user = await _make_user(db_session, role, user_branch_id)
    row = await _add_application(db_session, user, status, app_branch_id, doc)
    return user, row


def _url(application_id, tail=""):
    return BASE + "/" + str(application_id) + tail


async def _reviewer(db_session, branch_id):
    # A custom role that holds only the review key.
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    return await _make_user(db_session, role_name, branch_id)


async def _call(client, kind, application_id, headers):
    if kind == "delete":
        return await client.delete(_url(application_id), headers=headers)
    return await client.post(_url(application_id, "/" + kind), headers=headers)


async def _assert_unchanged(db_session, kind, application_id, status):
    result = await db_session.execute(select(StaffApplication).where(StaffApplication.id == application_id))
    row = result.scalar_one_or_none()
    assert row is not None
    if kind != "delete":
        await db_session.refresh(row)
        assert row.status == status


@pytest.mark.parametrize("kind,status", ROUTES)
async def test_non_super_admin_cannot_act_on_branch_manager_target(client, db_session, kind, status):
    await _ensure_role(db_session, "branch_manager")
    branch_a = await _make_branch(db_session, "P3K BM-A")
    reviewer = await _reviewer(db_session, branch_a.id)
    _, target_app = await _make_applicant(db_session, status, branch_a.id, branch_a.id, role="branch_manager")
    app_id = target_app.id
    headers = _auth_headers(reviewer)
    await db_session.commit()

    resp = await _call(client, kind, app_id, headers)
    assert resp.status_code == 403, resp.text
    assert MESSAGE in resp.text
    await _assert_unchanged(db_session, kind, app_id, status)


@pytest.mark.parametrize("kind,status", ROUTES)
async def test_non_super_admin_cannot_act_on_super_admin_target(client, db_session, kind, status):
    await _ensure_role(db_session, "super_admin")
    branch_a = await _make_branch(db_session, "P3K SA-A")
    reviewer = await _reviewer(db_session, branch_a.id)
    _, target_app = await _make_applicant(db_session, status, branch_a.id, branch_a.id, role="super_admin")
    app_id = target_app.id
    headers = _auth_headers(reviewer)
    await db_session.commit()

    resp = await _call(client, kind, app_id, headers)
    assert resp.status_code == 403, resp.text
    assert MESSAGE in resp.text
    await _assert_unchanged(db_session, kind, app_id, status)


@pytest.mark.parametrize("kind,status", ROUTES)
async def test_reviewer_with_no_branch_cannot_act_on_target_with_no_branch(client, db_session, kind, status):
    await _ensure_role(db_session, "cashier")
    reviewer = await _reviewer(db_session, None)
    _, target_app = await _make_applicant(db_session, status, None, None, role="cashier")
    app_id = target_app.id
    headers = _auth_headers(reviewer)
    await db_session.commit()

    resp = await _call(client, kind, app_id, headers)
    assert resp.status_code == 403, resp.text
    assert MESSAGE in resp.text
    await _assert_unchanged(db_session, kind, app_id, status)


async def test_super_admin_still_acts_on_branch_manager_targets(client, db_session):
    await _ensure_role(db_session, "branch_manager")
    await _set_role(db_session, "super_admin", add=[KEY])
    branch_a = await _make_branch(db_session, "P3K OWNER-A")
    admin = await _make_user(db_session, "super_admin", None)
    _, sus_app = await _make_applicant(db_session, "accepted", branch_a.id, branch_a.id, role="branch_manager")
    _, rea_app = await _make_applicant(db_session, "suspended", branch_a.id, branch_a.id, role="branch_manager")
    _, del_app = await _make_applicant(db_session, "under_review", branch_a.id, branch_a.id, role="branch_manager")
    sus_id, rea_id, del_id = sus_app.id, rea_app.id, del_app.id
    headers = _auth_headers(admin)
    await db_session.commit()

    resp = await client.post(_url(sus_id, "/suspend"), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(rea_id, "/reactivate"), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.delete(_url(del_id), headers=headers)
    assert resp.status_code == 204, resp.text


async def test_branch_manager_suspend_own_account_still_gives_400(client, db_session):
    await _set_role(db_session, "branch_manager", add=[KEY])
    branch_a = await _make_branch(db_session, "P3K SELF-S")
    manager = await _make_user(db_session, "branch_manager", branch_a.id)
    own_app = await _add_application(db_session, manager, "accepted", branch_a.id)
    own_id = own_app.id
    headers = _auth_headers(manager)
    await db_session.commit()

    resp = await client.post(_url(own_id, "/suspend"), headers=headers)
    assert resp.status_code == 400, resp.text


async def test_branch_manager_delete_own_account_still_gives_400(client, db_session):
    await _set_role(db_session, "branch_manager", add=[KEY])
    branch_a = await _make_branch(db_session, "P3K SELF-D")
    manager = await _make_user(db_session, "branch_manager", branch_a.id)
    own_app = await _add_application(db_session, manager, "accepted", branch_a.id)
    own_id = own_app.id
    headers = _auth_headers(manager)
    await db_session.commit()

    resp = await client.delete(_url(own_id), headers=headers)
    assert resp.status_code == 400, resp.text