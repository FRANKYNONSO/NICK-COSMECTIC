# PATCHP3N-TESTS: GET /api/v1/roles/registry (super_admin only).
import pytest

from nickcosmetics_core.domain.users.permission_registry import grouped_registry
from tests.test_goods_receiving import _branch, _headers, _user

pytestmark = pytest.mark.asyncio

URL = "/api/v1/roles/registry"
OWNER_ONLY = ("remove_super_admin", "transfer_ownership")


def _all_keys(groups):
    keys = []
    for group in groups:
        for tab in group["tabs"]:
            for perm in tab["permissions"]:
                keys.append(perm["key"])
    return keys


async def test_super_admin_gets_the_registry(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    resp = await client.get(URL, headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0
    assert body == grouped_registry()


async def test_owner_only_keys_are_hidden(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    resp = await client.get(URL, headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    keys = _all_keys(resp.json())
    assert len(keys) > 0
    for key in OWNER_ONLY:
        assert key not in keys


@pytest.mark.parametrize("role", ["branch_manager", "cashier"])
async def test_non_super_admin_is_refused(client, db_session, role):
    branch = await _branch(db_session)
    user = await _user(db_session, role, branch.id)
    resp = await client.get(URL, headers=_headers(user))
    assert resp.status_code == 403, resp.text


async def test_no_token_is_refused(client):
    resp = await client.get(URL)
    assert resp.status_code in (401, 403), resp.status_code


async def test_registry_is_not_taken_as_a_role_name(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    resp = await client.get(URL, headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    for item in resp.json():
        assert "group" in item
        assert "tabs" in item
        assert "name" not in item


async def test_roles_list_still_works(client, db_session):
    admin = await _user(db_session, "super_admin", None)
    resp = await client.get("/api/v1/roles", headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0
    for item in body:
        assert "name" in item