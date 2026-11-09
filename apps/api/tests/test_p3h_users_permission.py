# PATCHP3H-TESTS: users routes use the keys create_staff and view_staff
import uuid

import pytest
from sqlalchemy import select

from infrastructure.db.models import Branch, Role, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

CREATE_KEY = "create_staff"
VIEW_KEY = "view_staff"
OTHER_KEY = "view_dashboard_notifications"
USERS_URL = "/api/v1/users"


async def _make_branch(db_session, name):
    branch = Branch(name=name + " " + uuid.uuid4().hex[:6])
    db_session.add(branch)
    await db_session.flush()
    return branch


def _name(label):
    return label + " " + uuid.uuid4().hex[:6]


async def _make_user(db_session, role, branch_id=None, denied=None, granted=None, name=None):
    user = User(
        full_name=name or _name("P3H user"),
        role=role,
        email=uuid.uuid4().hex[:8] + "@example.com",
        password_hash=hash_password("Password123!"),
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


def _new_role_name():
    return "p3h_" + uuid.uuid4().hex[:8]


def _create_body(role="cashier", branch_id=None):
    body = {
        "full_name": "P3H New Staff",
        "role": role,
        "email": uuid.uuid4().hex[:8] + "@example.com",
        "password": "SecurePass123",
    }
    if branch_id is not None:
        body["branch_id"] = str(branch_id)
    return body


async def _three_statuses(client, headers, target_id=None):
    # Calls list, get and create. Returns the 3 status codes in this order.
    target = target_id or uuid.uuid4()
    lst = await client.get(USERS_URL, headers=headers)
    one = await client.get(USERS_URL + "/" + str(target), headers=headers)
    new = await client.post(USERS_URL, json=_create_body(), headers=headers)
    return [lst.status_code, one.status_code, new.status_code]


async def _listed_names(client, headers, query=""):
    resp = await client.get(USERS_URL + query, headers=headers)
    assert resp.status_code == 200, resp.text
    return [row["full_name"] for row in resp.json()]


async def test_a_role_without_the_keys_gets_403_on_all_three_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name)
    branch = await _make_branch(db_session, "P3H branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(user)) == [403, 403, 403]


async def test_b_unrelated_key_alone_gets_403_on_all_three_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[OTHER_KEY])
    branch = await _make_branch(db_session, "P3H branch")
    user = await _make_user(db_session, role_name, branch.id)
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(user)) == [403, 403, 403]


async def test_c_denied_keys_get_403_on_all_three_routes(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[CREATE_KEY, VIEW_KEY])
    branch = await _make_branch(db_session, "P3H branch")
    user = await _make_user(db_session, role_name, branch.id, denied=[CREATE_KEY, VIEW_KEY])
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(user)) == [403, 403, 403]


async def test_d_view_key_is_limited_to_the_own_branch(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[VIEW_KEY])
    branch_a = await _make_branch(db_session, "P3H branch A")
    branch_b = await _make_branch(db_session, "P3H branch B")
    own_name = _name("Own staff")
    other_name = _name("Other staff")
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    own_staff = await _make_user(db_session, "cashier", branch_a.id, name=own_name)
    other_staff = await _make_user(db_session, "cashier", branch_b.id, name=other_name)
    boss = await _make_user(db_session, "super_admin")
    own_id = own_staff.id
    other_id = other_staff.id
    boss_id = boss.id
    other_branch_id = branch_b.id
    await db_session.commit()
    headers = _auth_headers(reviewer)

    listed = await _listed_names(client, headers, "?branch_id=" + str(other_branch_id))
    assert own_name in listed
    assert other_name not in listed

    resp = await client.get(USERS_URL + "/" + str(own_id), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.get(USERS_URL + "/" + str(other_id), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.get(USERS_URL + "/" + str(boss_id), headers=headers)
    assert resp.status_code == 403, resp.text

    resp = await client.post(USERS_URL, json=_create_body(), headers=headers)
    assert resp.status_code == 403, resp.text


async def test_e_view_key_without_a_branch_gets_403_on_list_and_get(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[VIEW_KEY])
    branch = await _make_branch(db_session, "P3H branch")
    reviewer = await _make_user(db_session, role_name)
    target = await _make_user(db_session, "cashier", branch.id)
    target_id = target.id
    await db_session.commit()
    headers = _auth_headers(reviewer)

    resp = await client.get(USERS_URL, headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.get(USERS_URL + "/" + str(target_id), headers=headers)
    assert resp.status_code == 403, resp.text


async def test_f_create_key_creates_a_cashier_in_the_own_branch_only(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[CREATE_KEY])
    branch_a = await _make_branch(db_session, "P3H branch A")
    branch_b = await _make_branch(db_session, "P3H branch B")
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    own_branch_id = branch_a.id
    other_branch_id = branch_b.id
    await db_session.commit()
    headers = _auth_headers(reviewer)

    resp = await client.post(USERS_URL, json=_create_body("cashier", other_branch_id), headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["branch_id"] == str(own_branch_id)

    resp = await client.post(USERS_URL, json=_create_body("branch_manager", own_branch_id), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(USERS_URL, json=_create_body("super_admin", own_branch_id), headers=headers)
    assert resp.status_code == 403, resp.text

    resp = await client.get(USERS_URL, headers=headers)
    assert resp.status_code == 403, resp.text


async def test_g_create_key_without_a_branch_gets_403(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[CREATE_KEY])
    reviewer = await _make_user(db_session, role_name)
    await db_session.commit()

    resp = await client.post(USERS_URL, json=_create_body(), headers=_auth_headers(reviewer))
    assert resp.status_code == 403, resp.text


async def test_h_view_key_granted_to_the_account_works(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name)
    branch = await _make_branch(db_session, "P3H branch")
    own_name = _name("Own staff")
    reviewer = await _make_user(db_session, role_name, branch.id, granted=[VIEW_KEY])
    await _make_user(db_session, "cashier", branch.id, name=own_name)
    await db_session.commit()
    headers = _auth_headers(reviewer)

    listed = await _listed_names(client, headers)
    assert own_name in listed

    resp = await client.post(USERS_URL, json=_create_body(), headers=headers)
    assert resp.status_code == 403, resp.text


async def test_i_super_admin_sees_every_branch(client, db_session):
    admin = await _make_user(db_session, "super_admin")
    branch_a = await _make_branch(db_session, "P3H branch A")
    branch_b = await _make_branch(db_session, "P3H branch B")
    name_a = _name("Staff A")
    name_b = _name("Staff B")
    staff_a = await _make_user(db_session, "cashier", branch_a.id, name=name_a)
    staff_b = await _make_user(db_session, "cashier", branch_b.id, name=name_b)
    id_a = staff_a.id
    id_b = staff_b.id
    branch_b_id = branch_b.id
    await db_session.commit()
    headers = _auth_headers(admin)

    listed = await _listed_names(client, headers)
    assert name_a in listed
    assert name_b in listed

    listed = await _listed_names(client, headers, "?branch_id=" + str(branch_b_id))
    assert name_b in listed
    assert name_a not in listed

    resp = await client.get(USERS_URL + "/" + str(id_a), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.get(USERS_URL + "/" + str(id_b), headers=headers)
    assert resp.status_code == 200, resp.text


async def test_j_cashier_stays_403_on_all_three_routes(client, db_session):
    branch = await _make_branch(db_session, "P3H branch")
    cashier = await _make_user(db_session, "cashier", branch.id)
    await db_session.commit()

    assert await _three_statuses(client, _auth_headers(cashier)) == [403, 403, 403]
