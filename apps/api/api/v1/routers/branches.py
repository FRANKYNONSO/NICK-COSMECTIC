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
