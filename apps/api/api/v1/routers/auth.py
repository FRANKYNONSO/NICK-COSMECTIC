from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import os
import time
import uuid

from api.dependencies import get_current_user, get_current_user_allow_pending, get_db_session
from api.v1.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)
from infrastructure.db.models import Role as RoleModel, StaffApplication, User
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
# After _LOGIN_MAX_FAILS wrong passwords for one login name, that login is
# locked for _LOGIN_LOCK_SECONDS. The counts live in memory only (one process,
# and a restart clears them). The shop's machine account is never locked.
_NO_LOGIN_LIMIT_ROLES = ("storefront_service",)
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_LOCK_SECONDS = 15 * 60
_LOGIN_LOCK_TEXT = "Too many failed attempts. Please wait 15 minutes and try again."
_LOGIN_FAILS: dict = {}
_LOGIN_LOCKED_UNTIL: dict = {}


def _login_now() -> float:
    return time.time()


def _phone_digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _looks_like_phone(value: str) -> bool:
    """True for things like 08031234567, +2348031234567, 0803 123 4567."""
    stripped = value.strip()
    if stripped.startswith("+"):
        stripped = stripped[1:]
    for ch in (" ", "-", "(", ")", "."):
        stripped = stripped.replace(ch, "")
    return stripped.isdigit() and len(stripped) >= 10


def _login_key(identifier) -> str:
    """Key for the lockout counters. Phone numbers are reduced to their last
    10 digits so 0803..., +234803... and 234803... share one counter."""
    text = (identifier or "").strip().lower()
    if "@" not in text and _looks_like_phone(text):
        return "phone:" + _phone_digits(text)[-10:]
    return text


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


async def _one_or_none(session: AsyncSession, stmt):
    """Run a query and return the single matching user. If zero or more than
    one row matches, return None (an ambiguous login never picks a user)."""
    result = await session.execute(stmt.limit(2))
    rows = result.scalars().all()
    return rows[0] if len(rows) == 1 else None


async def _find_user_by_identifier(session: AsyncSession, identifier: str):
    """Find a user by email, username or phone number."""
    ident = (identifier or "").strip()
    if not ident:
        return None

    # 1) Email
    if "@" in ident:
        return await _one_or_none(
            session, select(User).where(func.lower(User.email) == ident.lower())
        )

    # 2) Username (only once the users table has a username column)
    if hasattr(User, "username"):
        user = await _one_or_none(
            session, select(User).where(func.lower(User.username) == ident.lower())
        )
        if user is not None:
            return user

    # 2b) Preferred name. The "Preferred name / username" typed on the staff
    #     signup form and on My Profile is stored in preferred_name, so it
    #     works as a username too (capital letters and outer spaces ignored).
    user = await _one_or_none(
        session,
        select(User).where(func.lower(func.trim(User.preferred_name)) == ident.lower()),
    )
    if user is not None:
        return user

    # 3) Phone number, matched on the last 10 digits so every common format
    #    (0803..., +234803..., 234803..., with spaces or dashes) finds the
    #    same account.
    if _looks_like_phone(ident):
        last10 = _phone_digits(ident)[-10:]

        user_digits = func.regexp_replace(User.phone, "[^0-9]", "", "g")
        user = await _one_or_none(
            session,
            select(User).where(User.phone.is_not(None), func.right(user_digits, 10) == last10),
        )
        if user is not None:
            return user

        # Fallback: the phone typed on the staff application form.
        app_digits = func.regexp_replace(StaffApplication.phone_number, "[^0-9]", "", "g")
        return await _one_or_none(
            session,
            select(User)
            .join(StaffApplication, StaffApplication.user_id == User.id)
            .where(
                StaffApplication.phone_number.is_not(None),
                func.right(app_digits, 10) == last10,
            ),
        )

    return None


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    user = await _find_user_by_identifier(session, body.identifier)

    # Deliberately identical error for "no such user" and "wrong
    # password" -- distinguishing them lets an attacker find out which
    # emails, usernames or phone numbers have accounts.
    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login or password"
    )

    exempt = user is not None and user.role in _NO_LOGIN_LIMIT_ROLES
    limit_key = _login_key(body.identifier)
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
async def me(current_user: User = Depends(get_current_user_allow_pending)) -> User:
    return current_user


# PATCHP1-MATRIX
@router.get("/me/permissions")
async def my_permissions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """The caller's effective permissions: role row plus per-account grants,
    minus per-account denials (never applied to super_admin). Same rules as
    require_permission in api/dependencies.py."""
    row = await session.execute(select(RoleModel.permissions).where(RoleModel.name == current_user.role))
    granted = set(row.scalar_one_or_none() or []) | set(current_user.granted_permissions or [])
    if current_user.role != "super_admin":
        granted -= set(current_user.denied_permissions or [])
    return {"role": current_user.role, "permissions": sorted(granted)}


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