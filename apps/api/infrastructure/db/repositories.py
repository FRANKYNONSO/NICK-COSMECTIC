import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import User


class UserRepository:
    """Infrastructure-layer repository for the `users` table. Distinct
    from the framework-agnostic interfaces in nickcosmetics_core —
    this one is API-specific (auth/user-management concerns), not part
    of the shared checkout domain, so it lives here rather than in the
    core package.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user
