"""Admin-approved password reset requests.

Public (no login):
  POST /auth/password-reset-requests           user asks for a reset
  POST /auth/password-reset-requests/status    user checks the decision
  POST /auth/password-reset-requests/complete  user sets the new password (only once approved)

Admin (super_admin / branch_manager):
  GET  /password-reset-requests
  POST /password-reset-requests/{id}/approve
  POST /password-reset-requests/{id}/reject

A branch_manager can only handle requests from users in their own
branch whose role is not super_admin / branch_manager. A super_admin
can handle every request.
"""

import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.dependencies import get_db_session, require_role
from api.v1.schemas.password_reset import (
    PasswordResetComplete,
    PasswordResetRequestCreate,
    PasswordResetRequestCreated,
    PasswordResetRequestOut,
    PasswordResetStatusCheck,
    PasswordResetStatusOut,
)
from infrastructure.db.models import PasswordResetRequest, User
from infrastructure.db.repositories import UserRepository
from infrastructure.security.jwt import mark_password_changed
from infrastructure.security.passwords import hash_password
from infrastructure.security.tokens import generate_token, hash_token

router = APIRouter(tags=["password-reset-requests"])

_APPROVAL_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _secret_ok(row: PasswordResetRequest, secret: str) -> bool:
    return hmac.compare_digest(row.secret_hash, hash_token(secret))


def _effective_status(row: PasswordResetRequest) -> str:
    if row.status == "approved" and row.approval_expires_at is not None and row.approval_expires_at < _now():
        return "expired"
    return row.status


def _can_manage(admin: User, target: User) -> bool:
    if admin.role == "super_admin":
        return True
    return (
        admin.role == "branch_manager"
        and admin.branch_id is not None
        and target.branch_id == admin.branch_id
        and target.role not in ("super_admin", "branch_manager")
        and target.id != admin.id
    )


# ------------------------------------------------------------------ public


@router.post(
    "/auth/password-reset-requests",
    response_model=PasswordResetRequestCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_reset_request(
    body: PasswordResetRequestCreate, session: AsyncSession = Depends(get_db_session)
) -> PasswordResetRequestCreated:
    """Only registered staff accounts can ask for a reset."""
    repo = UserRepository(session)
    user = await repo.get_by_email(body.email)
    if user is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="This email is not registered as a staff account."
        )
    if not user.is_active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="This account is disabled. Please contact your administrator.",
        )

    request_id = uuid.uuid4()
    secret = generate_token()
    # A newer request replaces any older open one for this account.
    await session.execute(
        update(PasswordResetRequest)
        .where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.status.in_(("pending", "approved")),
        )
        .values(status="cancelled")
    )
    session.add(
        PasswordResetRequest(
            id=request_id,
            user_id=user.id,
            secret_hash=hash_token(secret),
            status="pending",
        )
    )
    await session.commit()
    return PasswordResetRequestCreated(request_id=request_id, secret=secret)


@router.post("/auth/password-reset-requests/status", response_model=PasswordResetStatusOut)
async def reset_request_status(
    body: PasswordResetStatusCheck, session: AsyncSession = Depends(get_db_session)
) -> PasswordResetStatusOut:
    row = await session.get(PasswordResetRequest, body.request_id)
    if row is None or not _secret_ok(row, body.secret):
        # Unknown id / wrong secret look identical to "still waiting".
        return PasswordResetStatusOut(status="pending")
    return PasswordResetStatusOut(status=_effective_status(row))


@router.post("/auth/password-reset-requests/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_reset_request(
    body: PasswordResetComplete, session: AsyncSession = Depends(get_db_session)
) -> None:
    invalid = HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail="This reset request is invalid, not approved yet, or has expired.",
    )
    row = await session.get(PasswordResetRequest, body.request_id)
    if row is None or not _secret_ok(row, body.secret) or _effective_status(row) != "approved":
        raise invalid
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid

    user.password_hash = hash_password(body.new_password)
    mark_password_changed(user)  # PATCH25B-PASSWORD-CHANGED-AT
    # Also kill any outstanding emailed-link token for this account.
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    row.status = "used"
    row.used_at = _now()
    await session.commit()


# ------------------------------------------------------------------- admin


@router.get("/password-reset-requests", response_model=list[PasswordResetRequestOut])
async def list_reset_requests(
    status_filter: str | None = Query(None, alias="status"),
    admin: User = Depends(require_role("super_admin", "branch_manager")),
    session: AsyncSession = Depends(get_db_session),
) -> list[PasswordResetRequestOut]:
    decider = aliased(User)
    stmt = (
        select(PasswordResetRequest, User, decider.full_name)
        .join(User, User.id == PasswordResetRequest.user_id)
        .outerjoin(decider, decider.id == PasswordResetRequest.decided_by)
        .order_by(PasswordResetRequest.requested_at.desc())
        .limit(300)
    )
    if status_filter == "open":
        # Names stay on the list until the person has changed their password
        # (or the request is rejected / replaced by a newer one). Approvals
        # whose time ran out stay visible too, labelled "expired".
        stmt = stmt.where(PasswordResetRequest.status.in_(("pending", "approved")))
    elif status_filter == "all":
        pass  # full history, including requests replaced by a newer one
    elif status_filter:
        stmt = stmt.where(PasswordResetRequest.status == status_filter)
    else:
        stmt = stmt.where(PasswordResetRequest.status != "cancelled")

    rows = (await session.execute(stmt)).all()
    return [
        PasswordResetRequestOut(
            id=req.id,
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role,
            branch_id=user.branch_id,
            status=_effective_status(req),
            requested_at=req.requested_at,
            decided_at=req.decided_at,
            approval_expires_at=req.approval_expires_at,
            used_at=req.used_at,
            decided_by_name=decider_name,
        )
        for req, user, decider_name in rows
        if _can_manage(admin, user)
    ]


async def _load_for_decision(request_id: uuid.UUID, admin: User, session: AsyncSession) -> PasswordResetRequest:
    req = await session.get(PasswordResetRequest, request_id)
    user = await session.get(User, req.user_id) if req is not None else None
    if req is None or user is None or not _can_manage(admin, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Reset request not found.")
    if req.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This request has already been handled.")
    return req


@router.post("/password-reset-requests/{request_id}/approve", response_model=PasswordResetStatusOut)
async def approve_reset_request(
    request_id: uuid.UUID,
    admin: User = Depends(require_role("super_admin", "branch_manager")),
    session: AsyncSession = Depends(get_db_session),
) -> PasswordResetStatusOut:
    req = await _load_for_decision(request_id, admin, session)
    req.status = "approved"
    req.decided_by = admin.id
    req.decided_at = _now()
    req.approval_expires_at = _now() + timedelta(hours=_APPROVAL_HOURS)
    await session.commit()
    return PasswordResetStatusOut(status="approved")


@router.post("/password-reset-requests/{request_id}/reject", response_model=PasswordResetStatusOut)
async def reject_reset_request(
    request_id: uuid.UUID,
    admin: User = Depends(require_role("super_admin", "branch_manager")),
    session: AsyncSession = Depends(get_db_session),
) -> PasswordResetStatusOut:
    req = await _load_for_decision(request_id, admin, session)
    req.status = "rejected"
    req.decided_by = admin.id
    req.decided_at = _now()
    await session.commit()
    return PasswordResetStatusOut(status="rejected")
