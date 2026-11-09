# PATCHP3G-TESTS: staff application routes use the review_staff_applications key
import uuid

import pytest
from sqlalchemy import select

from api.v1.routers import staff_applications as sa_module
from infrastructure.db.models import Branch, Role, StaffApplication, User
from infrastructure.security.jwt import create_access_token
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

KEY = "review_staff_applications"
OTHER_KEY = "manage_users"
BASE = "/api/v1/staff-applications"
CHANGE_BODY = {"note": "Please fix", "flagged_fields": ["email"]}

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
        full_name="P3G Test User",
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
    return "p3g_" + uuid.uuid4().hex[:8]


async def _add_application(db_session, user, status="under_review", app_branch_id=None, doc=None):
    row = StaffApplication(
        user_id=user.id,
        status=status,
        branch_id=app_branch_id,
        full_legal_name="P3G Applicant",
        preferred_name="P3G",
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


async def _nine_statuses(client, headers):
    # Calls the 9 guarded routes with an unknown id. The guard runs first, so 403 means the guard said no.
    fake = uuid.uuid4()
    codes = []
    codes.append((await client.get(BASE, headers=headers)).status_code)
    codes.append((await client.get(BASE + "/form.pdf", headers=headers)).status_code)
    codes.append((await client.get(_url(fake), headers=headers)).status_code)
    codes.append((await client.post(_url(fake, "/accept"), json={}, headers=headers)).status_code)
    codes.append((await client.post(_url(fake, "/request-changes"), json=CHANGE_BODY, headers=headers)).status_code)
    codes.append((await client.post(_url(fake, "/reject"), json={}, headers=headers)).status_code)
    codes.append((await client.post(_url(fake, "/suspend"), headers=headers)).status_code)
    codes.append((await client.post(_url(fake, "/reactivate"), headers=headers)).status_code)
    codes.append((await client.delete(_url(fake), headers=headers)).status_code)
    return codes


async def _listed_ids(client, headers):
    resp = await client.get(BASE, headers=headers)
    assert resp.status_code == 200, resp.text
    return [row["id"] for row in resp.json()]


async def test_a_custom_role_list_is_limited_to_its_own_branch(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3G A-A")
    branch_b = await _make_branch(db_session, "P3G A-B")
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    _, own_app = await _make_applicant(db_session, app_branch_id=branch_a.id)
    _, other_app = await _make_applicant(db_session, app_branch_id=branch_b.id)
    _, free_app = await _make_applicant(db_session, app_branch_id=None)
    own_id, other_id, free_id = str(own_app.id), str(other_app.id), str(free_app.id)
    headers = _auth_headers(reviewer)
    await db_session.commit()

    ids = await _listed_ids(client, headers)
    assert own_id in ids
    assert free_id in ids
    assert other_id not in ids


async def test_b_custom_role_accept_follows_the_branch_manager_limits(client, db_session):
    await _ensure_role(db_session, "cashier")
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3G B-A")
    branch_b = await _make_branch(db_session, "P3G B-B")
    branch_a_id = branch_a.id
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    lone_reviewer = await _make_user(db_session, role_name, None)
    _, super_app = await _make_applicant(db_session, app_branch_id=branch_a.id)
    good_user, good_app = await _make_applicant(db_session, app_branch_id=branch_a.id)
    _, other_app = await _make_applicant(db_session, app_branch_id=branch_b.id)
    _, lone_app = await _make_applicant(db_session, app_branch_id=branch_a.id)
    super_id, good_id, other_id, lone_id = super_app.id, good_app.id, other_app.id, lone_app.id
    headers = _auth_headers(reviewer)
    lone_headers = _auth_headers(lone_reviewer)
    await db_session.commit()

    # accepting as super_admin is refused
    resp = await client.post(_url(super_id, "/accept"), json={"role": "super_admin"}, headers=headers)
    assert resp.status_code == 403, resp.text
    await db_session.refresh(super_app)
    assert super_app.status == "under_review"

    # accepting as cashier into its own branch works
    resp = await client.post(_url(good_id, "/accept"), json={"role": "cashier"}, headers=headers)
    assert resp.status_code == 200, resp.text
    await db_session.refresh(good_app)
    await db_session.refresh(good_user)
    assert good_app.status == "accepted"
    assert good_user.role == "cashier"
    assert good_user.branch_id == branch_a_id

    # an applicant of another branch is refused
    resp = await client.post(_url(other_id, "/accept"), json={"role": "cashier"}, headers=headers)
    assert resp.status_code == 403, resp.text
    await db_session.refresh(other_app)
    assert other_app.status == "under_review"

    # a reviewer with no branch is refused
    resp = await client.post(_url(lone_id, "/accept"), json={"role": "cashier"}, headers=lone_headers)
    assert resp.status_code == 403, resp.text
    await db_session.refresh(lone_app)
    assert lone_app.status == "under_review"


async def test_c_custom_role_get_one_follows_the_credential_rules(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3G C-A")
    branch_b = await _make_branch(db_session, "P3G C-B")
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    _, super_app = await _make_applicant(db_session, "accepted", branch_a.id, branch_a.id, role="super_admin")
    _, manager_app = await _make_applicant(db_session, "accepted", branch_a.id, branch_a.id, role="branch_manager")
    _, applicant_app = await _make_applicant(db_session, "under_review", None, branch_b.id)
    _, same_app = await _make_applicant(db_session, "accepted", branch_a.id, branch_a.id)
    _, other_app = await _make_applicant(db_session, "accepted", branch_b.id, branch_b.id)
    super_id, manager_id, applicant_id = super_app.id, manager_app.id, applicant_app.id
    same_id, other_id = same_app.id, other_app.id
    headers = _auth_headers(reviewer)
    await db_session.commit()

    resp = await client.get(_url(super_id), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.get(_url(manager_id), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.get(_url(applicant_id), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.get(_url(same_id), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.get(_url(other_id), headers=headers)
    assert resp.status_code == 403, resp.text


async def test_d_id_document_stays_open_to_the_owner_and_follows_the_key(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(sa_module, "_ID_DOCUMENT_DIR", tmp_path)
    (tmp_path / "p3g_doc.pdf").write_bytes(b"%PDF-1.4 p3g test file")
    keyed_role = _new_role_name()
    await _set_role(db_session, keyed_role, add=[KEY])
    plain_role = _new_role_name()
    await _set_role(db_session, plain_role)
    branch_a = await _make_branch(db_session, "P3G D-A")
    owner, owner_app = await _make_applicant(db_session, "under_review", None, branch_a.id, doc="p3g_doc.pdf")
    _, super_app = await _make_applicant(db_session, "accepted", branch_a.id, branch_a.id, role="super_admin", doc="p3g_doc.pdf")
    plain_user = await _make_user(db_session, plain_role, branch_a.id)
    keyed_user = await _make_user(db_session, keyed_role, branch_a.id)
    denied_user = await _make_user(db_session, keyed_role, branch_a.id, denied=[KEY])
    owner_app_id, super_app_id = owner_app.id, super_app.id
    owner_headers = _auth_headers(owner)
    plain_headers = _auth_headers(plain_user)
    keyed_headers = _auth_headers(keyed_user)
    denied_headers = _auth_headers(denied_user)
    await db_session.commit()

    # the applicant opens their own document (no key needed)
    resp = await client.get(_url(owner_app_id, "/id-document"), headers=owner_headers)
    assert resp.status_code == 200, resp.text
    # a role without the key is refused
    resp = await client.get(_url(owner_app_id, "/id-document"), headers=plain_headers)
    assert resp.status_code == 403, resp.text
    # a custom role with the key may open an applicant under review
    resp = await client.get(_url(owner_app_id, "/id-document"), headers=keyed_headers)
    assert resp.status_code == 200, resp.text
    # the same key denied on the account is refused
    resp = await client.get(_url(owner_app_id, "/id-document"), headers=denied_headers)
    assert resp.status_code == 403, resp.text
    # a custom role with the key may NOT open the document of a super_admin employee
    resp = await client.get(_url(super_app_id, "/id-document"), headers=keyed_headers)
    assert resp.status_code == 403, resp.text


async def test_e_without_the_key_all_nine_routes_give_403(client, db_session):
    branch = await _make_branch(db_session, "P3G E")
    plain_role = _new_role_name()
    await _set_role(db_session, plain_role)
    other_role = _new_role_name()
    await _set_role(db_session, other_role, add=[OTHER_KEY])
    denied_role = _new_role_name()
    await _set_role(db_session, denied_role, add=[KEY])
    plain = await _make_user(db_session, plain_role, branch.id)
    other = await _make_user(db_session, other_role, branch.id)
    denied = await _make_user(db_session, denied_role, branch.id, denied=[KEY])
    cases = [
        ("role without the key", _auth_headers(plain)),
        ("unrelated key only", _auth_headers(other)),
        ("key denied on the account", _auth_headers(denied)),
    ]
    await db_session.commit()

    for label, headers in cases:
        codes = await _nine_statuses(client, headers)
        assert codes == [403] * 9, label + " gave " + str(codes)


async def test_f_super_admin_passes_every_route_in_any_branch(client, db_session):
    await _ensure_role(db_session, "cashier")
    branch_a = await _make_branch(db_session, "P3G F-A")
    branch_b = await _make_branch(db_session, "P3G F-B")
    admin = await _make_user(db_session, "super_admin", None)
    _, acc_app = await _make_applicant(db_session, "under_review", None, branch_b.id)
    _, chg_app = await _make_applicant(db_session, "under_review", None, branch_b.id)
    _, rej_app = await _make_applicant(db_session, "under_review", None, branch_a.id)
    _, sus_app = await _make_applicant(db_session, "accepted", branch_b.id, branch_b.id)
    _, rea_app = await _make_applicant(db_session, "suspended", branch_a.id, branch_a.id)
    _, del_app = await _make_applicant(db_session, "under_review", branch_b.id, branch_b.id)
    acc_id, chg_id, rej_id = str(acc_app.id), str(chg_app.id), str(rej_app.id)
    sus_id, rea_id, del_id = sus_app.id, rea_app.id, del_app.id
    headers = _auth_headers(admin)
    await db_session.commit()

    ids = await _listed_ids(client, headers)
    assert acc_id in ids
    assert rej_id in ids

    resp = await client.get(BASE + "/form.pdf", headers=headers)
    assert resp.status_code == 200
    resp = await client.get(_url(acc_id), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(acc_id, "/accept"), json={"role": "cashier"}, headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(chg_id, "/request-changes"), json=CHANGE_BODY, headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(rej_id, "/reject"), json={"reason": "not now"}, headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(sus_id, "/suspend"), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(rea_id, "/reactivate"), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.delete(_url(del_id), headers=headers)
    assert resp.status_code == 204, resp.text

    await db_session.refresh(acc_app)
    await db_session.refresh(chg_app)
    await db_session.refresh(rej_app)
    await db_session.refresh(sus_app)
    await db_session.refresh(rea_app)
    assert acc_app.status == "accepted"
    assert chg_app.status == "needs_changes"
    assert rej_app.status == "rejected"
    assert sus_app.status == "suspended"
    assert rea_app.status == "accepted"


async def test_g1_custom_role_request_changes_and_reject_respect_the_branch(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3G G1-A")
    branch_b = await _make_branch(db_session, "P3G G1-B")
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    _, chg_other = await _make_applicant(db_session, app_branch_id=branch_b.id)
    _, chg_same = await _make_applicant(db_session, app_branch_id=branch_a.id)
    _, rej_other = await _make_applicant(db_session, app_branch_id=branch_b.id)
    _, rej_same = await _make_applicant(db_session, app_branch_id=branch_a.id)
    chg_other_id, chg_same_id = chg_other.id, chg_same.id
    rej_other_id, rej_same_id = rej_other.id, rej_same.id
    headers = _auth_headers(reviewer)
    await db_session.commit()

    resp = await client.post(_url(chg_other_id, "/request-changes"), json=CHANGE_BODY, headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(_url(chg_same_id, "/request-changes"), json=CHANGE_BODY, headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(rej_other_id, "/reject"), json={"reason": "no"}, headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(_url(rej_same_id, "/reject"), json={"reason": "no"}, headers=headers)
    assert resp.status_code == 200, resp.text

    await db_session.refresh(chg_other)
    await db_session.refresh(chg_same)
    await db_session.refresh(rej_other)
    await db_session.refresh(rej_same)
    assert chg_other.status == "under_review"
    assert chg_same.status == "needs_changes"
    assert rej_other.status == "under_review"
    assert rej_same.status == "rejected"


async def test_g2_custom_role_suspend_reactivate_delete_respect_the_branch(client, db_session):
    role_name = _new_role_name()
    await _set_role(db_session, role_name, add=[KEY])
    branch_a = await _make_branch(db_session, "P3G G2-A")
    branch_b = await _make_branch(db_session, "P3G G2-B")
    reviewer = await _make_user(db_session, role_name, branch_a.id)
    own_app = await _add_application(db_session, reviewer, "accepted", branch_a.id)
    _, sus_other = await _make_applicant(db_session, "accepted", branch_b.id, branch_b.id)
    _, sus_same = await _make_applicant(db_session, "accepted", branch_a.id, branch_a.id)
    _, rea_other = await _make_applicant(db_session, "suspended", branch_b.id, branch_b.id)
    _, rea_same = await _make_applicant(db_session, "suspended", branch_a.id, branch_a.id)
    _, del_other = await _make_applicant(db_session, "under_review", branch_b.id, branch_b.id)
    _, del_same = await _make_applicant(db_session, "under_review", branch_a.id, branch_a.id)
    own_id = own_app.id
    sus_other_id, sus_same_id = sus_other.id, sus_same.id
    rea_other_id, rea_same_id = rea_other.id, rea_same.id
    del_other_id, del_same_id = del_other.id, del_same.id
    headers = _auth_headers(reviewer)
    await db_session.commit()

    resp = await client.post(_url(sus_other_id, "/suspend"), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(_url(sus_same_id, "/suspend"), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(rea_other_id, "/reactivate"), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(_url(rea_same_id, "/reactivate"), headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.delete(_url(del_other_id), headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.delete(_url(del_same_id), headers=headers)
    assert resp.status_code == 204, resp.text
    # own account
    resp = await client.post(_url(own_id, "/suspend"), headers=headers)
    assert resp.status_code == 400, resp.text
    resp = await client.delete(_url(own_id), headers=headers)
    assert resp.status_code == 400, resp.text

    await db_session.refresh(sus_other)
    await db_session.refresh(sus_same)
    await db_session.refresh(rea_other)
    await db_session.refresh(rea_same)
    assert sus_other.status == "accepted"
    assert sus_same.status == "suspended"
    assert rea_other.status == "suspended"
    assert rea_same.status == "accepted"


async def test_h_branch_manager_behaves_as_before(client, db_session):
    await _ensure_role(db_session, "cashier")
    branch_a = await _make_branch(db_session, "P3G H-A")
    branch_b = await _make_branch(db_session, "P3G H-B")
    manager = await _make_user(db_session, "branch_manager", branch_a.id)
    _, role_app = await _make_applicant(db_session, app_branch_id=branch_a.id)
    _, good_app = await _make_applicant(db_session, app_branch_id=branch_a.id)
    _, other_app = await _make_applicant(db_session, app_branch_id=branch_b.id)
    _, free_app = await _make_applicant(db_session, app_branch_id=None)
    role_id, good_id, other_id, free_id = role_app.id, good_app.id, other_app.id, free_app.id
    headers = _auth_headers(manager)
    await db_session.commit()

    ids = await _listed_ids(client, headers)
    assert str(good_id) in ids
    assert str(free_id) in ids
    assert str(other_id) not in ids

    resp = await client.post(_url(role_id, "/accept"), json={"role": "branch_manager"}, headers=headers)
    assert resp.status_code == 403, resp.text
    resp = await client.post(_url(good_id, "/accept"), json={"role": "cashier"}, headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(_url(other_id, "/accept"), json={"role": "cashier"}, headers=headers)
    assert resp.status_code == 403, resp.text

    await db_session.refresh(role_app)
    await db_session.refresh(good_app)
    await db_session.refresh(other_app)
    assert role_app.status == "under_review"
    assert good_app.status == "accepted"
    assert other_app.status == "under_review"


async def test_i_cashier_gets_403_on_all_nine_routes(client, db_session):
    branch = await _make_branch(db_session, "P3G I")
    cashier = await _make_user(db_session, "cashier", branch.id)
    headers = _auth_headers(cashier)
    await db_session.commit()

    codes = await _nine_statuses(client, headers)
    assert codes == [403] * 9, str(codes)


async def test_j_holds_review_key_agrees_with_require_permission(client, db_session):
    with_key = _new_role_name()
    await _set_role(db_session, with_key, add=[KEY])
    without_key = _new_role_name()
    await _set_role(db_session, without_key)
    specs = [
        ("role with key", with_key, None, None, True),
        ("role without key", without_key, None, None, False),
        ("role with key, denied on the account", with_key, [KEY], None, False),
        ("role without key, granted on the account", without_key, None, [KEY], True),
        ("role with key, granted and denied on the account", with_key, [KEY], [KEY], False),
        ("super_admin denied on the account", "super_admin", [KEY], None, True),
    ]
    cases = []
    for label, role, denied, granted, expected in specs:
        user = await _make_user(db_session, role, None, denied=denied, granted=granted)
        cases.append((label, user, _auth_headers(user), expected))
    await db_session.commit()

    for label, user, headers, expected in cases:
        held = await sa_module._holds_review_key(db_session, user)
        resp = await client.get(BASE + "/form.pdf", headers=headers)
        assert held is expected, label + ": helper said " + str(held)
        assert (resp.status_code == 200) is expected, label + ": route gave " + str(resp.status_code)
