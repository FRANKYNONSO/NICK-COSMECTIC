from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import os
import time
import uuid

from api.dependencies import get_current_user, get_db_session
from api.v1.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)
from infrastructure.db.models import User
from infrastructure.db.repositories import UserRepository
from infrastructure.email import send_email
from infrastructure.security.jwt import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    mark_password_changed,
    token_predates_password_change,
)
from infrastructure.security.passwords import hash_password, verify_password
from infrastructure.security.tokens import expires_at, generate_token, hash_token, is_expired

router = APIRouter(prefix="/auth", tags=["auth"])

_RESET_TOKEN_MINUTES = 30

# PATCH17A-NO-SERVICE-RESET
# Roles that must never get a password reset mail or use a reset token.
# The storefront shop account is a machine account. Its password is
# changed only by an admin or by the account script.
_NO_SELF_RESET_ROLES = ("storefront_service",)

# PATCH19E-LOGIN-LIMIT
# After _LOGIN_MAX_FAILS wrong passwords for one email, that email is locked
# for _LOGIN_LOCK_SECONDS. The counts live in memory only (one process, and a
# restart clears them). The shop's machine account is never locked.
_NO_LOGIN_LIMIT_ROLES = ("storefront_service",)
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_LOCK_SECONDS = 15 * 60
_LOGIN_LOCK_TEXT = "Too many failed attempts. Please wait 15 minutes and try again."
_LOGIN_FAILS: dict = {}
_LOGIN_LOCKED_UNTIL: dict = {}


def _login_now() -> float:
    return time.time()


def _login_key(email) -> str:
    return (email or "").strip().lower()


def _login_is_locked(key: str) -> bool:
    until = _LOGIN_LOCKED_UNTIL.get(key)
    if until is None:
        return False
    if _login_now() < until:
        return True
    _LOGIN_LOCKED_UNTIL.pop(key, None)
    _LOGIN_FAILS.pop(key, None)
    return False


def _login_record_failure(key: str) -> None:
    now = _login_now()
    fails = [t for t in _LOGIN_FAILS.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
    fails.append(now)
    if len(fails) >= _LOGIN_MAX_FAILS:
        _LOGIN_LOCKED_UNTIL[key] = now + _LOGIN_LOCK_SECONDS
        _LOGIN_FAILS.pop(key, None)
    else:
        _LOGIN_FAILS[key] = fails


def _login_clear(key: str) -> None:
    _LOGIN_FAILS.pop(key, None)
    _LOGIN_LOCKED_UNTIL.pop(key, None)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    repo = UserRepository(session)
    user = await repo.get_by_email(body.email)

    # Deliberately identical error for "no such user" and "wrong
    # password" — distinguishing them lets an attacker enumerate valid
    # email addresses.
    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
    )

    exempt = user is not None and user.role in _NO_LOGIN_LIMIT_ROLES
    limit_key = _login_key(body.email)
    if not exempt and _login_is_locked(limit_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_LOGIN_LOCK_TEXT
        )

    if user is None or not verify_password(body.password, user.password_hash):
        if not exempt:
            _login_record_failure(limit_key)
        raise invalid_credentials

    if not exempt:
        _login_clear(limit_key)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    branch_id = str(user.branch_id) if user.branch_id else None
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role, branch_id),
        refresh_token=create_refresh_token(str(user.id), user.role, branch_id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except TokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    repo = UserRepository(session)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    # PATCH25B-PASSWORD-CHANGED-AT: an old refresh token must not hand out new tokens
    if token_predates_password_change(payload, getattr(user, "password_changed_at", None)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    branch_id = str(user.branch_id) if user.branch_id else None
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role, branch_id),
        refresh_token=create_refresh_token(str(user.id), user.role, branch_id),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(body: ForgotPasswordRequest, session: AsyncSession = Depends(get_db_session)) -> None:
    """Spec Section 22. Always returns 204 regardless of whether the
    email is registered -- responding differently would let someone
    enumerate valid staff email addresses, same reasoning as the
    identical-error choice in login() above.
    """
    repo = UserRepository(session)
    user = await repo.get_by_email(body.email)
    if user is not None and user.is_active and user.role not in _NO_SELF_RESET_ROLES:
        token = generate_token()
        user.password_reset_token_hash = hash_token(token)
        user.password_reset_expires_at = expires_at(_RESET_TOKEN_MINUTES)
        await repo.save(user)

        reset_link = f"{os.getenv('WEB_ADMIN_BASE_URL', 'http://127.0.0.1:5500')}/reset-password.html?token={token}"
        send_email(
            user.email,
            "Reset your Nick Cosmetics password",
            f"Hi {user.full_name},\n\n"
            f"Use this link to reset your password. It expires in {_RESET_TOKEN_MINUTES} minutes "
            f"and can only be used once:\n\n{reset_link}\n\n"
            "If you didn't request this, you can safely ignore this email.",
        )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, session: AsyncSession = Depends(get_db_session)) -> None:
    repo = UserRepository(session)
    token_hash = hash_token(body.token)
    user = await repo.get_by_password_reset_token_hash(token_hash)

    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This reset link is invalid or has expired.")
    if user is None or is_expired(user.password_reset_expires_at) or user.role in _NO_SELF_RESET_ROLES:
        raise invalid

    user.password_hash = hash_password(body.new_password)
    mark_password_changed(user)  # PATCH25B-PASSWORD-CHANGED-AT
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    await repo.save(user)
