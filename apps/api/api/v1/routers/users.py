import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission, require_role
from api.v1.schemas.users import (
    PasswordResetRequest,
    UserCreate,
    UserOut,
    UserPermissionsUpdate,
    UserRoleUpdate,
    UserUpdate,
)
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import StaffApplication, User
from infrastructure.db.repositories import RoleRepository, StaffApplicationRepository, UserRepository
from infrastructure.db.user_deletion import deletion_blockers, hard_delete_user
from infrastructure.security.jwt import mark_password_changed
from infrastructure.security.passwords import hash_password
from nickcosmetics_core.domain.users.user import ALL_PERMISSIONS


def _validate_permission(permission: str) -> None:
    if permission not in ALL_PERMISSIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown permission '{permission}'",
        )

router = APIRouter(prefix="/users", tags=["users"])

# Roles allowed to reach any endpoint in this router at all. Finer-grained
# scoping (branch_manager limited to their own branch, staff-only creation,
# role changes reserved for super_admin) is enforced inside each handler,
# since the shared permission table in nickcosmetics_core doesn't grant
# branch_manager "manage_users" -- that table governs cross-cutting
# permissions, not this router's branch-scoped exceptions.
_MANAGE_USERS_ROLES = ("super_admin", "branch_manager")

# Viewing the staff roster (list/get) stays open to any branch_manager,
# gated on role alone above. Editing an existing account -- updating
# fields, deactivating/reactivating, deleting, or resetting a
# password -- additionally requires the "manage_users" permission,
# which branch_manager no longer carries by default (see migration
# w4x5y6z7a8b9): "he should be able to see them... but he shouldn't be
# able to edit it." A super_admin can still grant a specific, trusted
# branch manager edit rights via a custom role on the Roles page.
_require_edit_staff = require_permission("manage_users")


async def _validate_role(role: str, session: AsyncSession) -> None:
    """Role names are no longer a fixed enum -- they're rows in the
    `roles` table (see infrastructure/db/models.py's Role class), so
    validity now means "exists in that table" rather than "is one of
    three hardcoded strings."
    """
    repo = RoleRepository(session)
    if await repo.get_by_name(role) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown role '{role}'")


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("create_staff")),  # PATCHP3H-GUARDS
) -> User:
    await _validate_role(body.role, session)

    branch_id = body.branch_id
    if current_user.role != "super_admin":  # PATCHP3H-BRANCH
        # Branch managers may only create staff, and only within their
        # own branch -- they can't create peers/admins or reach into
        # another branch.
        if body.role != "cashier":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Branch managers may only create staff accounts",
            )
        if current_user.branch_id is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Branch manager has no assigned branch",
            )
        branch_id = current_user.branch_id

    repo = UserRepository(session)

    if body.email and await repo.get_by_email(body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Email '{body.email}' is already in use")
    if body.phone and await repo.get_by_phone(body.phone) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Phone '{body.phone}' is already in use")

    user = User(
        full_name=body.full_name,
        role=body.role,
        email=body.email,
        phone=body.phone,
        branch_id=branch_id,
        password_hash=hash_password(body.password),
        is_active=True,
    )
    user = await repo.create(user)

    # PENDSTAFF-CREATE
    # Accounts made from the "New staff" button are NOT usable yet. They get an
    # 'incomplete' staff application, which the access gate in
    # api/dependencies.py already treats as "no access to anything". At first
    # login the person sees a form to finish their details; submitting it moves
    # the application to 'under_review', and a super admin (or the branch
    # manager of that branch) must still approve it before they get in.
    # The role/branch chosen here are kept and used when it is approved.
    application = await StaffApplicationRepository(session).create(
        StaffApplication(
            user_id=user.id,
            status="incomplete",
            role_applied_for=body.role,
            branch_id=branch_id,
            full_legal_name=body.full_name,
            preferred_name=body.full_name,
            phone_number=body.phone,
            email=body.email or "",
        )
    )
    log_audit_event(
        session, user_id=current_user.id, action="staff_account_created_pending",
        entity_type="staff_application", entity_id=application.id,
    )
    await session.commit()
    return user


@router.get("", response_model=list[UserOut])
async def list_users(
    branch_id: uuid.UUID | None = None,
    role: str | None = None,
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_staff")),  # PATCHP3H-GUARDS
) -> list[User]:
    scoped_branch_id = branch_id
    if current_user.role != "super_admin":  # PATCHP3H-BRANCH
        # Everyone except super_admin only sees their own branch's roster,
        # regardless of what branch_id they pass in.
        if current_user.branch_id is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Your account has no assigned branch",
            )
        scoped_branch_id = current_user.branch_id

    repo = UserRepository(session)
    return await repo.list_all(branch_id=scoped_branch_id, role=role, include_inactive=include_inactive)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_staff")),  # PATCHP3H-GUARDS
) -> User:
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if current_user.role != "super_admin" and (  # PATCHP3H-BRANCH
        current_user.branch_id is None or user.branch_id != current_user.branch_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to view this user")
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_edit_staff),
) -> User:
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if current_user.role == "branch_manager" and user.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this user")

    updates = body.model_dump(exclude_unset=True)

    if "branch_id" in updates and current_user.role == "branch_manager":
        # Branch managers can't move staff out of their own branch.
        updates["branch_id"] = current_user.branch_id

    if "email" in updates and updates["email"] and updates["email"] != user.email:
        if await repo.get_by_email(updates["email"]) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Email is already in use")
    if "phone" in updates and updates["phone"] and updates["phone"] != user.phone:
        if await repo.get_by_phone(updates["phone"]) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Phone is already in use")

    for field, value in updates.items():
        setattr(user, field, value)

    return await repo.save(user)


@router.patch("/{user_id}/role", response_model=UserOut)
async def update_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> User:
    # Role changes -- including promotion to super_admin -- are
    # super_admin-only. Branch managers never reach this endpoint.
    await _validate_role(body.role, session)

    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == current_user.id and body.role != "super_admin":
        # Mirrors the domain's "remove_super_admin" permission being a
        # distinct, deliberate action -- prevents a super_admin from
        # accidentally demoting themselves via this generic endpoint.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role here; use account transfer instead",
        )

    user.role = body.role
    return await repo.save(user)


@router.patch("/{user_id}/permissions", response_model=UserOut)
async def update_user_permissions(
    user_id: uuid.UUID,
    body: UserPermissionsUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> User:
    """Restrict (denied_permissions) or extend (granted_permissions) an
    individual account relative to what its role would otherwise
    grant -- e.g. one branch_manager loses "manage_coupons" while
    everyone else with that role keeps it, or one trusted staff member
    gains "view_low_stock" while every other staff account still can't,
    without upgrading their role either way. Works for any role except
    super_admin, since that's the role that hands out these overrides
    in the first place and already has every permission there is. See
    api/dependencies.py's require_permission() for how both lists are
    enforced on every permission check, not just displayed in the UI.
    """
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.role == "super_admin":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="super_admin accounts cannot be restricted or granted extra permissions",
        )

    for permission in body.denied_permissions:
        _validate_permission(permission)
    for permission in body.granted_permissions:
        _validate_permission(permission)

    user.denied_permissions = body.denied_permissions
    user.granted_permissions = body.granted_permissions
    return await repo.save(user)


@router.patch("/{user_id}/deactivate", response_model=UserOut)
async def deactivate_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_edit_staff),
) -> User:
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if current_user.role == "branch_manager" and user.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this user")
    if user.id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account")

    user.is_active = False
    return await repo.save(user)


@router.patch("/{user_id}/activate", response_model=UserOut)
async def activate_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_edit_staff),
) -> User:
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if current_user.role == "branch_manager" and user.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this user")

    user.is_active = True
    return await repo.save(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_edit_staff),
) -> None:
    """Permanently removes the account -- only allowed when it has no
    business-transaction history (see infrastructure/db/user_deletion.py
    for exactly what that checks). Anyone who's actually worked a shift,
    rung up a sale, etc. can't be hard-deleted; Deactivate is the
    reversible alternative that keeps that history intact.
    """
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if current_user.role == "branch_manager" and user.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this user")
    if user.id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")

    blockers = await deletion_blockers(session, user_id)
    if blockers:
        shown = ", ".join(blockers[:3]) + ("…" if len(blockers) > 3 else "")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete this account -- it has {shown} on record. "
                "Deactivate the account instead to preserve that history."
            ),
        )

    deleted_name, deleted_email = user.full_name, user.email
    await hard_delete_user(session, user)
    log_audit_event(
        session, user_id=current_user.id, action="account_deleted",
        entity_type="user", entity_id=user_id,
        metadata={"deleted_name": deleted_name, "deleted_email": deleted_email},
    )
    await session.commit()


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: uuid.UUID,
    body: PasswordResetRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_edit_staff),
) -> None:
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if current_user.role == "branch_manager" and user.branch_id != current_user.branch_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this user")

    user.password_hash = hash_password(body.new_password)
    mark_password_changed(user)  # PATCH25B-PASSWORD-CHANGED-AT
    await repo.save(user)