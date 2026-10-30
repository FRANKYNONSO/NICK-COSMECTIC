import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, require_role
from api.v1.schemas.roles import RoleCreate, RoleOut, RoleUpdate
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import Role as RoleModel
from infrastructure.db.models import User
from infrastructure.db.repositories import RoleRepository
from nickcosmetics_core.domain.users.user import ALL_PERMISSIONS

router = APIRouter(prefix="/roles", tags=["roles"])

# Listing roles is useful to branch_manager too (e.g. the "assign role"
# dropdown on a user they're creating shows whatever exists) -- but
# creating, editing, or deleting a role stays super_admin-only,
# enforced per-endpoint below rather than at the router level.
_VIEW_ROLES = ("super_admin", "branch_manager")


def _validate_permissions(permissions: list[str]) -> None:
    unknown = set(permissions) - ALL_PERMISSIONS
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown permission(s): {', '.join(sorted(unknown))}",
        )


async def _to_role_out(session: AsyncSession, role: RoleModel) -> RoleOut:
    repo = RoleRepository(session)
    return RoleOut(
        id=role.id,
        name=role.name,
        display_name=role.display_name,
        permissions=role.permissions,
        is_system=role.is_system,
        user_count=await repo.count_users_with_role(role.name),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@router.get("", response_model=list[RoleOut])
async def list_roles(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_role(*_VIEW_ROLES)),
) -> list[RoleOut]:
    repo = RoleRepository(session)
    roles = await repo.list_all()
    return [await _to_role_out(session, r) for r in roles]


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> RoleOut:
    _validate_permissions(body.permissions)
    repo = RoleRepository(session)
    if await repo.get_by_name(body.name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Role '{body.name}' already exists")

    role = RoleModel(
        name=body.name,
        display_name=body.display_name,
        permissions=body.permissions,
        is_system=False,
        created_by=current_user.id,
    )
    session.add(role)
    await session.flush()  # assigns role.id for the audit log below

    # log_audit_event never commits itself -- it's meant to ride along
    # in the same transaction as the action it's logging, so a crash
    # between the two can never leave one without the other.
    log_audit_event(
        session,
        user_id=current_user.id,
        action="role_created",
        entity_type="role",
        entity_id=role.id,
        metadata={"name": role.name, "permissions": role.permissions},
    )
    await session.commit()
    await session.refresh(role)

    return await _to_role_out(session, role)


@router.patch("/{name}", response_model=RoleOut)
async def update_role(
    name: str,
    body: RoleUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> RoleOut:
    """Editing `permissions` here applies to every account currently
    holding this role immediately -- api/dependencies.py's
    require_permission() reads the roles table fresh on every request,
    there's no per-user copy that needs a separate propagation step.
    Works for the three system roles too (a super admin can retune
    what branch_manager or staff can do), just not renaming/deleting
    them -- see delete_role below.
    """
    repo = RoleRepository(session)
    role = await repo.get_by_name(name)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Role '{name}' not found")

    if body.display_name is not None:
        role.display_name = body.display_name
    if body.permissions is not None:
        _validate_permissions(body.permissions)
        role.permissions = body.permissions

    log_audit_event(
        session,
        user_id=current_user.id,
        action="role_permissions_updated",
        entity_type="role",
        entity_id=role.id,
        metadata={"name": role.name, "permissions": role.permissions},
    )
    await session.commit()
    await session.refresh(role)

    return await _to_role_out(session, role)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    name: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> None:
    repo = RoleRepository(session)
    role = await repo.get_by_name(name)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Role '{name}' not found")
    if role.name == "super_admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The super admin role can't be deleted")

    in_use = await repo.count_users_with_role(name)
    if in_use:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"{in_use} account(s) still hold this role -- reassign them first",
        )

    log_audit_event(
        session,
        user_id=current_user.id,
        action="role_deleted",
        entity_type="role",
        entity_id=role.id,
        metadata={"name": name},
    )
    await session.delete(role)
    await session.commit()
