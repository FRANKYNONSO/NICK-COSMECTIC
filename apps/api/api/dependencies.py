import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from nickcosmetics_core.domain.users.user import Role as DomainRole
from nickcosmetics_core.domain.users.user import User as DomainUser

from infrastructure.db.models import User
from infrastructure.db.repositories import UserRepository
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.security.jwt import TokenError, decode_token

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

    return user


def require_role(*allowed_roles: str):
    """Dependency factory: require_role("super_admin", "branch_manager").
    Usage: `current_user: User = Depends(require_role("super_admin"))`
    """

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not permitted to perform this action",
            )
        return current_user

    return _check


def require_permission(action: str):
    """Dependency factory checking a specific permission (e.g.
    "manage_products", "view_financials") against the single shared
    permission table defined in nickcosmetics_core.domain.users.user --
    the same table the offline desktop app will eventually use. This
    avoids maintaining two separate copies of "who can do what."

    Usage: `Depends(require_permission("manage_inventory"))`
    """

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        domain_user = DomainUser(
            id=str(current_user.id),
            full_name=current_user.full_name,
            role=DomainRole(current_user.role),
            branch_id=str(current_user.branch_id) if current_user.branch_id else None,
        )
        if not domain_user.can(action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not permitted to '{action}'",
            )
        return current_user

    return _check
