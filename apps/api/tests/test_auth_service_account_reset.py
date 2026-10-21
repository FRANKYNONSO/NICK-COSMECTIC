# Tests for patch 17a: the storefront service account cannot use the
# password reset routes. No database and no network: the router functions
# are called directly with a fake repository and a fake send_email.
import asyncio
import threading
import types

import pytest
from fastapi import HTTPException

from api.v1.routers import auth as auth_router
from api.v1.schemas.auth import ForgotPasswordRequest, ResetPasswordRequest


def _run(coro):
    box = {}

    def worker():
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:
            box["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class _FakeRepo:
    def __init__(self, user):
        self.user = user
        self.saves = 0

    async def get_by_email(self, email):
        if self.user is not None and self.user.email == email:
            return self.user
        return None

    async def get_by_password_reset_token_hash(self, token_hash):
        if self.user is not None and self.user.password_reset_token_hash == token_hash:
            return self.user
        return None

    async def save(self, user):
        self.saves += 1
        return user


def _make_user(role, active=True):
    return types.SimpleNamespace(
        email="who@example.com",
        full_name="Test Person",
        is_active=active,
        role=role,
        password_reset_token_hash=None,
        password_reset_expires_at=None,
        password_hash="old-hash",
    )


def _setup(monkeypatch, user):
    repo = _FakeRepo(user)
    sent = []
    monkeypatch.setattr(auth_router, "UserRepository", lambda session: repo)
    monkeypatch.setattr(auth_router, "send_email", lambda to, subject, body: sent.append((to, subject)))
    return repo, sent


def _forgot(email):
    body = ForgotPasswordRequest.model_construct(email=email)
    return _run(auth_router.forgot_password(body, session=None))


def _reset(token):
    body = ResetPasswordRequest.model_construct(token=token, new_password="Another-Long-Passw0rd-123")
    return _run(auth_router.reset_password(body, session=None))


def test_service_account_gets_no_reset_mail(monkeypatch):
    user = _make_user("storefront_service")
    repo, sent = _setup(monkeypatch, user)
    _forgot("who@example.com")
    assert sent == []


def test_service_account_gets_no_reset_token(monkeypatch):
    user = _make_user("storefront_service")
    repo, sent = _setup(monkeypatch, user)
    _forgot("who@example.com")
    assert user.password_reset_token_hash is None
    assert user.password_reset_expires_at is None
    assert repo.saves == 0


def test_service_account_answer_looks_the_same(monkeypatch):
    user = _make_user("storefront_service")
    _setup(monkeypatch, user)
    assert _forgot("who@example.com") is None
    _setup(monkeypatch, None)
    assert _forgot("nobody@example.com") is None


def test_staff_still_gets_reset_mail(monkeypatch):
    user = _make_user("super_admin")
    repo, sent = _setup(monkeypatch, user)
    assert _forgot("who@example.com") is None
    assert len(sent) == 1
    assert sent[0][0] == "who@example.com"
    assert user.password_reset_token_hash is not None
    assert repo.saves == 1


def test_unknown_email_gets_no_mail(monkeypatch):
    repo, sent = _setup(monkeypatch, None)
    assert _forgot("nobody@example.com") is None
    assert sent == []


def test_service_account_cannot_use_a_reset_token(monkeypatch):
    user = _make_user("storefront_service")
    user.password_reset_token_hash = auth_router.hash_token("tok-service")
    user.password_reset_expires_at = auth_router.expires_at(30)
    repo, sent = _setup(monkeypatch, user)
    with pytest.raises(HTTPException) as info:
        _reset("tok-service")
    assert info.value.status_code == 400
    assert user.password_hash == "old-hash"
    assert repo.saves == 0


def test_staff_can_still_use_a_reset_token(monkeypatch):
    user = _make_user("branch_manager")
    user.password_reset_token_hash = auth_router.hash_token("tok-staff")
    user.password_reset_expires_at = auth_router.expires_at(30)
    repo, sent = _setup(monkeypatch, user)
    assert _reset("tok-staff") is None
    assert user.password_hash != "old-hash"
    assert user.password_reset_token_hash is None
    assert repo.saves == 1
