import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import Branch, Customer, User
from infrastructure.db.models import Role as RoleModel
from infrastructure.db.repositories import CustomerRepository, StaffApplicationRepository, UserRepository
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.security.jwt import TokenError, decode_token, token_predates_password_change

_bearer_scheme = HTTPBearer(auto_error=True)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Decode the access token, load the user, and reject if the token
    is invalid/expired or the account is inactive. Every protected
    route depends on this -- role is always re-validated server-side
    from the DB, never trusted from the token/client alone, per the
    security architecture in docs/architecture Section 8.
    """
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")

    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    # PATCH25B-PASSWORD-CHANGED-AT: refuse a token issued before the last password change
    if token_predates_password_change(payload, getattr(user, "password_changed_at", None)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Branch suspension: staff of a suspended branch are locked out until
    # a super admin resumes it. Super admins are never locked out.
    if user.role != "super_admin" and user.branch_id is not None:
        branch_row = await session.get(Branch, user.branch_id)
        if branch_row is not None and branch_row.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your branch is currently suspended. Please contact the administrator.",
            )

    return user


_bearer_scheme_optional = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme_optional),
    session: AsyncSession = Depends(get_db_session),
) -> User | None:
    """Same decode/lookup as get_current_user, but never raises -- used
    by endpoints the storefront/unauthenticated callers can also hit
    (GET /catalog/products, GET /catalog/products/{id}) that still need
    to know *if* the caller is a logged-in staff account, specifically
    to apply the hidden_from_staff filter (feature spec 2). Returns
    None on a missing/invalid/expired token or an inactive account
    rather than 401ing, since those endpoints are otherwise open.
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError:
        return None

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None

    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        return None
    # PATCH25B-PASSWORD-CHANGED-AT: same rule as get_current_user, but no error
    if token_predates_password_change(payload, getattr(user, "password_changed_at", None)):
        return None
    return user


async def get_current_customer(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> Customer:
    """Customer-facing equivalent of get_current_user -- a separate
    dependency (not a role check bolted onto get_current_user) because
    customers and staff are different tables with different token
    issuers (auth.py vs customer_auth.py). The `role: "customer"` claim
    is defense-in-depth on top of that separation, not the only thing
    stopping a customer token from working on staff-only routes: even
    without checking it, decode succeeds but CustomerRepository would
    still fail to find a staff user's id in the customers table.
    """
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("role") != "customer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This endpoint requires a customer login")

    try:
        customer_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")

    repo = CustomerRepository(session)
    customer = await repo.get_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Customer not found")

    return customer


async def _ensure_active_employment(current_user: User, session: AsyncSession) -> None:
    """Blocks protected business functions for anyone whose staff
    application isn't in the 'accepted' state -- under_review,
    rejected, and suspended all land here. See StaffApplication in
    infrastructure/db/models.py: a user with no application row at
    all (e.g. created directly via POST /users) skips this check
    entirely, same as before this feature existed. Called from both
    require_role and require_permission so this is enforced on the
    backend for every protected route, not just hidden in the
    frontend nav -- per the upgrade spec's Section 6/26 requirement.
    """
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_user_id(current_user.id)
    if application is not None and application.status != "accepted":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Your staff application is '{application.status}'. "
                "Access to this feature is restricted until an administrator approves your account."
            ),
        )


def require_role(*allowed_roles: str):
    """Dependency factory: require_role("super_admin", "branch_manager").
    Usage: `current_user: User = Depends(require_role("super_admin"))`
    """

    async def _check(
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not permitted to perform this action",
            )
        await _ensure_active_employment(current_user, session)
        return current_user

    return _check


def require_permission(action: str):
    """Dependency factory checking a specific permission (e.g.
    "manage_products", "view_financials") against the current value of
    the `roles` table -- NOT the hardcoded table in
    nickcosmetics_core.domain.users.user, which only ever seeded the
    three built-in roles and would raise on any custom role name a
    super_admin has since created. Reading the DB per request (instead
    of a fixed enum) is also what makes editing a role's permissions
    take effect for everyone holding that role immediately.

    Usage: `Depends(require_permission("manage_inventory"))`
    """

    async def _check(
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> User:
        await _ensure_active_employment(current_user, session)

        role_row = await session.execute(select(RoleModel.permissions).where(RoleModel.name == current_user.role))
        role_granted = set(role_row.scalar_one_or_none() or [])
        # Per-account grants (see User.granted_permissions) extend what
        # the role alone allows -- lets a super_admin/branch_manager
        # hand one trusted staff member an extra capability (e.g.
        # "view_low_stock") without upgrading their whole role.
        granted = role_granted | set(current_user.granted_permissions or [])
        denied = set(current_user.denied_permissions or [])
        # Per-account denial never applies to super_admin, mirroring
        # the old domain rule -- that's the one tier an override can't
        # touch, so there's no path to every super_admin locking
        # themselves out by mistake. Denial always wins over a grant on
        # the same permission name, for both role-level and per-account
        # grants -- see the User.granted_permissions comment.
        allowed = action in granted and (current_user.role == "super_admin" or action not in denied)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not permitted to '{action}'",
            )
        return current_user

    return _check
