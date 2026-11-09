# PATCHP3F-TESTS: password reset admin routes use the review_password_resets key
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, PasswordResetRequest, Role, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "review_password_resets"
OTHER_KEY = "manage_users"
LIST_URL = "/api/v1/password-reset-requests"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


async def _make_user(db_session, role, branch_id=None, denied=None):
    user = User(
        full_name="P3F Test User",
        role=role,
        email=uuid.uuid4().hex[:8] + "@example.com",
        password_hash=hash_password("Password123!"),
        branch_id=branch_id,
        is_active=True,
        denied_permissions=denied or [],
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


def _new_role_name():
    return "p3f_" + uuid.uuid4().hex[:8]


async def _make_request(db_session, user_id, status="pending"):
    row = PasswordResetRequest(user_id=user_id, secret_hash=uuid.uuid4().hex, status=status)
    db_session.add(row)
    await db_session.flush()
    return row


def _approve_url(request_id):
    return LIST_URL + "/" + str(request_id) + "/approve"


def _reject_url(request_id):
    return LIST_URL + "/" + str(request_id) + "/reject"


async def _three_statuses(client, headers):
    # Calls list, approve and reject (unknown request id). Returns the 3 status codes in this order.
    lst = await client.get(LIST_URL, headers=headers)
    app = await client.post(_approve_url(uuid.uuid4()), headers=headers)
    rej = await client.post(_reject_url(uuid.uuid4()), headers=headers)
    return [lst.status_code, app.status_code, rej.status_code]


async def _listed_ids(client, headers):
    resp = await client.get(LIST_URL, headers=headers)
    assert resp.status_code == 200, resp.text
    return [row["id"] for row in resp.json()]


async def test_a_custom_role_with_the_key_works_like_a_branch_manager(client, db_session):
    # PATCHP3F-CANMANAGE-TESTS: option B. _can_manage no longer checks the role name. Branch limits stay.
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3F branch")
    other_branch = await _make_branch(db_session, "P3F other branch")
    reviewer = await _make_user(db_session, role_name, branch.id)
    no_branch_reviewer = await _make_user(db_session, role_name)
    allowed_one = await _make_user(db_session, "cashier", branch.id)
    allowed_two = await _make_user(db_session, "cashier", branch.id)
    other_branch_cashier = await _make_user(db_session, "cashier", other_branch.id)
    same_branch_manager = await _make_user(db_session, "branch_manager", branch.id)
    boss = await _make_user(db_session, "super_admin", branch.id)

    req_one = await _make_request(db_session, allowed_one.id)
    req_two = await _make_request(db_session, allowed_two.id)
    blocked_reqs = [
        await _make_request(db_session, other_branch_cashier.id),
        await _make_request(db_session, same_branch_manager.id),
        await _make_request(db_session, boss.id),
        await _make_request(db_session, reviewer.id),
    ]
    one_id = req_one.id
    two_id = req_two.id
    blocked_ids = [r.id for r in blocked_reqs]
    await db_session.commit()
    headers = _auth_headers(reviewer)
    no_branch_headers = _auth_headers(no_branch_reviewer)

    listed = await _listed_ids(client, headers)
    assert str(one_id) in listed
    assert str(two_id) in listed
    for blocked_id in blocked_ids:
        assert str(blocked_id) not in listed

    for blocked_id in blocked_ids:
        resp = await client.post(_approve_url(blocked_id), headers=headers)
        assert resp.status_code == 404, resp.text
        resp = await client.post(_reject_url(blocked_id), headers=headers)
        assert resp.status_code == 404, resp.text

    resp = await client.post(_approve_url(one_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    resp = await client.post(_reject_url(two_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    # A custom reviewer without a branch still sees nobody (safe side).
    assert await _listed_ids(client, no_branch_headers) == []

    await db_session.refresh(req_one)
    await db_session.refresh(req_two)
    assert req_one.status == "approved"
    assert req_two.status == "rejected"
    for blocked_req in blocked_reqs:
        await db_session.refresh(blocked_req)
        assert blocked_req.status == "pending"


async def test_b_role_without_the_key_gets_403_on_all_three_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name)
    branch = await _make_branch(db_session, "P3F branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(user)) == [403, 403, 403]


async def test_c_unrelated_key_alone_gets_403_on_all_three_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OTHER_KEY])
    branch = await _make_branch(db_session, "P3F branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(user)) == [403, 403, 403]


async def test_d_denied_key_gets_403_on_all_three_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch = await _make_branch(db_session, "P3F branch")
    user = await _make_user(db_session, role_name, branch.id, denied=[KEY])
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(user)) == [403, 403, 403]


async def test_e_super_admin_can_list_approve_and_reject(client, db_session):
    admin = await _make_user(db_session, "super_admin")
    branch = await _make_branch(db_session, "P3F branch")
    target_one = await _make_user(db_session, "cashier", branch.id)
    target_two = await _make_user(db_session, "cashier", branch.id)
    req_one = await _make_request(db_session, target_one.id)
    req_two = await _make_request(db_session, target_two.id)
    admin_id = admin.id
    one_id = req_one.id
    two_id = req_two.id
    await db_session.commit()
    headers = _auth_headers(admin)

    listed = await _listed_ids(client, headers)
    assert str(one_id) in listed
    assert str(two_id) in listed

    resp = await client.post(_approve_url(one_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    resp = await client.post(_reject_url(two_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    resp = await client.post(_approve_url(one_id), headers=headers)
    assert resp.status_code == 400, resp.text

    await db_session.refresh(req_one)
    await db_session.refresh(req_two)
    assert req_one.status == "approved"
    assert req_one.decided_by == admin_id
    assert req_one.approval_expires_at is not None
    assert req_two.status == "rejected"
    assert req_two.decided_by == admin_id


async def test_f_branch_manager_still_works_for_a_cashier_in_the_same_branch(client, db_session):
    await _set_role(db_session, "branch_manager", add=[KEY])
    branch = await _make_branch(db_session, "P3F branch")
    manager = await _make_user(db_session, "branch_manager", branch.id)
    target_one = await _make_user(db_session, "cashier", branch.id)
    target_two = await _make_user(db_session, "cashier", branch.id)
    req_one = await _make_request(db_session, target_one.id)
    req_two = await _make_request(db_session, target_two.id)
    one_id = req_one.id
    two_id = req_two.id
    await db_session.commit()
    headers = _auth_headers(manager)

    listed = await _listed_ids(client, headers)
    assert str(one_id) in listed
    assert str(two_id) in listed

    resp = await client.post(_approve_url(one_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    resp = await client.post(_reject_url(two_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    await db_session.refresh(req_one)
    await db_session.refresh(req_two)
    assert req_one.status == "approved"
    assert req_two.status == "rejected"


async def test_g_branch_manager_is_still_blocked_outside_the_branch_rule(client, db_session):
    await _set_role(db_session, "branch_manager", add=[KEY])
    branch = await _make_branch(db_session, "P3F branch")
    other_branch = await _make_branch(db_session, "P3F other branch")
    manager = await _make_user(db_session, "branch_manager", branch.id)
    allowed = await _make_user(db_session, "cashier", branch.id)
    other_branch_cashier = await _make_user(db_session, "cashier", other_branch.id)
    other_manager = await _make_user(db_session, "branch_manager", branch.id)
    boss = await _make_user(db_session, "super_admin", branch.id)

    allowed_req = await _make_request(db_session, allowed.id)
    blocked_reqs = [
        await _make_request(db_session, other_branch_cashier.id),
        await _make_request(db_session, other_manager.id),
        await _make_request(db_session, boss.id),
    ]
    allowed_id = allowed_req.id
    blocked_ids = [r.id for r in blocked_reqs]
    await db_session.commit()
    headers = _auth_headers(manager)

    listed = await _listed_ids(client, headers)
    assert str(allowed_id) in listed
    for blocked_id in blocked_ids:
        assert str(blocked_id) not in listed

    for blocked_id in blocked_ids:
        resp = await client.post(_approve_url(blocked_id), headers=headers)
        assert resp.status_code == 404, resp.text
        resp = await client.post(_reject_url(blocked_id), headers=headers)
        assert resp.status_code == 404, resp.text

    for blocked_req in blocked_reqs:
        await db_session.refresh(blocked_req)
        assert blocked_req.status == "pending"


async def test_h_cashier_stays_403_on_all_three_routes(client, db_session):
    branch = await _make_branch(db_session, "P3F branch")
    cashier = await _make_user(db_session, "cashier", branch.id)
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(cashier)) == [403, 403, 403]
