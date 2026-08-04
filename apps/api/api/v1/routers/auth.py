from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import uuid

from api.dependencies import get_current_user, get_db_session
from api.v1.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserOut
from infrastructure.db.models import User
from infrastructure.db.repositories import UserRepository
from infrastructure.security.jwt import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from infrastructure.security.passwords import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


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

    if user is None or not verify_password(body.password, user.password_hash):
        raise invalid_credentials

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

    branch_id = str(user.branch_id) if user.branch_id else None
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role, branch_id),
        refresh_token=create_refresh_token(str(user.id), user.role, branch_id),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
