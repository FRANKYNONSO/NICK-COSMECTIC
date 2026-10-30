from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.branches import (
    BranchCreate,
    BranchOut,
    BranchShiftSettingsOut,
    BranchShiftSettingsUpdate,
)
from infrastructure.db.models import Branch, User

router = APIRouter(prefix="/branches", tags=["branches"])


@router.post("", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
async def create_branch(
    body: BranchCreate,
    session: AsyncSession = Depends(get_db_session),
    # Opening a new branch is a system-wide expansion decision, reserved
    # for Super Admin only -- Branch Managers operate within a branch,
    # they don't create new ones. See docs/architecture Section 3.
    _: User = Depends(require_permission("configure_system")),
) -> Branch:
    existing = await session.execute(select(Branch).where(Branch.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Branch '{body.name}' already exists")

    branch = Branch(**body.model_dump())
    session.add(branch)
    await session.commit()
    await session.refresh(branch)
    return branch


@router.get("", response_model=list[BranchOut])
async def list_branches(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[Branch]:
    result = await session.execute(select(Branch).order_by(Branch.name))
    return list(result.scalars().all())


@router.get("/{branch_id}/shift-settings", response_model=BranchShiftSettingsOut)
async def get_shift_settings(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("view_all_reports")),
) -> BranchShiftSettingsOut:
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")
    return BranchShiftSettingsOut(
        branch_id=branch.id,
        expected_close_hour=branch.shift_expected_close_hour,
        expected_close_minute=branch.shift_expected_close_minute,
        grace_minutes=branch.shift_grace_minutes,
    )


@router.put("/{branch_id}/shift-settings", response_model=BranchShiftSettingsOut)
async def update_shift_settings(
    branch_id: uuid.UUID,
    body: BranchShiftSettingsUpdate,
    session: AsyncSession = Depends(get_db_session),
    # Configuring the closing grace period is a system-configuration
    # concern (spec section 19: "The administrator should be able to
    # configure the grace period rather than hard-code it"), same tier
    # as creating a branch.
    _: User = Depends(require_permission("configure_system")),
) -> BranchShiftSettingsOut:
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")
    branch.shift_expected_close_hour = body.expected_close_hour
    branch.shift_expected_close_minute = body.expected_close_minute
    branch.shift_grace_minutes = body.grace_minutes
    await session.commit()
    await session.refresh(branch)
    return BranchShiftSettingsOut(
        branch_id=branch.id,
        expected_close_hour=branch.shift_expected_close_hour,
        expected_close_minute=branch.shift_expected_close_minute,
        grace_minutes=branch.shift_grace_minutes,
    )
from datetime import datetime as _dt, timezone as _tz

from pydantic import BaseModel as _PBase
from sqlalchemy import text as _sql_text


class BranchSupportLineUpdate(_PBase):
    support_line: str | None = None


class BranchSuspendRequest(_PBase):
    suspended: bool


def _require_super_admin(user: User) -> None:
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a super admin can do this.")


@router.patch("/{branch_id}/support-line", response_model=BranchOut)
async def update_branch_support_line(
    branch_id: uuid.UUID,
    body: BranchSupportLineUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("configure_system")),
) -> Branch:
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")
    value = (body.support_line or "").strip()
    branch.support_line = value or None
    await session.commit()
    await session.refresh(branch)
    return branch


@router.post("/{branch_id}/suspension", response_model=BranchOut)
async def set_branch_suspension(
    branch_id: uuid.UUID,
    body: BranchSuspendRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("configure_system")),
) -> Branch:
    _require_super_admin(current_user)
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")
    branch.is_suspended = body.suspended
    branch.suspended_at = _dt.now(_tz.utc) if body.suspended else None
    await session.commit()
    await session.refresh(branch)
    return branch


@router.delete("/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("configure_system")),
) -> None:
    _require_super_admin(current_user)
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Branch not found")
    # Every foreign key in the real database that points at branches
    # (except ON DELETE CASCADE ones, which only hold price overrides).
    fk_rows = (
        await session.execute(
            _sql_text(
                "SELECT c.conrelid::regclass::text AS tbl, a.attname AS col "
                "FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                "WHERE c.contype = 'f' AND c.confrelid = 'branches'::regclass AND c.confdeltype <> 'c'"
            )
        )
    ).all()
    blockers = []
    for tbl, col in fk_rows:
        count = (
            await session.execute(
                _sql_text(f'SELECT count(*) FROM {tbl} WHERE "{col}" = :bid'),
                {"bid": branch_id},
            )
        ).scalar_one()
        if count:
            blockers.append(f"{count} in {tbl}")
    if blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This branch cannot be deleted because it already has records ("
            + ", ".join(blockers)
            + "). Suspend it instead.",
        )
    await session.delete(branch)
    await session.commit()
    return None
