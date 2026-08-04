from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.branches import BranchCreate, BranchOut
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
