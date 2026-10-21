import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import ChatMessage, ChatReadState, Coupon, Customer, Role, StaffApplication, User


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

    async def get_by_phone(self, phone: str) -> User | None:
        result = await self._session.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    async def get_by_preferred_name(self, preferred_name: str) -> User | None:
        # Case-insensitive so "Nkechi" and "nkechi" can't both be
        # registered as separate "usernames" -- see the register()
        # uniqueness check in staff_applications.py.
        result = await self._session.execute(
            select(User).where(func.lower(User.preferred_name) == preferred_name.lower())
        )
        return result.scalar_one_or_none()

    async def get_by_password_reset_token_hash(self, token_hash: str) -> User | None:
        result = await self._session.execute(select(User).where(User.password_reset_token_hash == token_hash))
        return result.scalar_one_or_none()

    async def get_by_pending_email_token_hash(self, token_hash: str) -> User | None:
        result = await self._session.execute(select(User).where(User.pending_email_token_hash == token_hash))
        return result.scalar_one_or_none()

    async def list_all(
        self,
        branch_id: uuid.UUID | None = None,
        role: str | None = None,
        include_inactive: bool = False,
    ) -> list[User]:
        stmt = select(User)
        if branch_id is not None:
            stmt = stmt.where(User.branch_id == branch_id)
        if role is not None:
            stmt = stmt.where(User.role == role)
        if not include_inactive:
            stmt = stmt.where(User.is_active.is_(True))
        stmt = stmt.order_by(User.full_name)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save(self, user: User) -> User:
        """Persist changes already applied to an attached `user` instance
        (e.g. after setattr-based updates in the router)."""
        await self._session.commit()
        await self._session.refresh(user)
        return user


class StaffApplicationRepository:
    """Infrastructure-layer repository for the `staff_applications`
    table. See StaffApplication in models.py for why this is a single
    table covering the whole application -> employee lifecycle rather
    than separate application/employee tables.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, application_id: uuid.UUID) -> StaffApplication | None:
        result = await self._session.execute(
            select(StaffApplication).where(StaffApplication.id == application_id)
        )
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: uuid.UUID) -> StaffApplication | None:
        result = await self._session.execute(
            select(StaffApplication).where(StaffApplication.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self, status: str | None = None) -> list[StaffApplication]:
        stmt = select(StaffApplication)
        if status is not None:
            stmt = stmt.where(StaffApplication.status == status)
        stmt = stmt.order_by(StaffApplication.submitted_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, application: StaffApplication) -> StaffApplication:
        self._session.add(application)
        await self._session.commit()
        await self._session.refresh(application)
        return application

    async def save(self, application: StaffApplication) -> StaffApplication:
        await self._session.commit()
        await self._session.refresh(application)
        return application

    async def next_employee_id(self) -> str:
        """Generate the next sequential "EMP-0001"-style code. Scans
        the max existing numeric suffix rather than keeping a separate
        counter table -- simplest thing that works at this volume, and
        avoids a second table just for a sequence.
        """
        result = await self._session.execute(select(func.max(User.employee_id)))
        highest = result.scalar_one_or_none()
        next_number = 1
        if highest:
            try:
                next_number = int(highest.split("-")[-1]) + 1
            except ValueError:
                next_number = 1
        return f"EMP-{next_number:04d}"


class CouponRepository:
    """Infrastructure-layer repository for the `coupons` table.

    Deliberately NOT an implementation of nickcosmetics_core's
    (synchronous) CouponRepository interface -- that interface matches
    apps/desktop_pos's sync SQLite session, same as StockBatchRepository
    and SaleRepository. This API is async end-to-end (AsyncSession), so
    the coupons router talks to this repository directly rather than
    going through the sync CouponService, the same tradeoff already
    made for sales in checkout_adapter.py.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_code(self, code: str) -> Coupon | None:
        result = await self._session.execute(select(Coupon).where(Coupon.code == code))
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Coupon]:
        """Active, unexpired coupons -- what the desktop app's
        cache_sync.pull_coupons() pulls down for offline redemption.
        """
        result = await self._session.execute(
            select(Coupon).where(Coupon.status == "active").order_by(Coupon.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, coupon: Coupon) -> Coupon:
        self._session.add(coupon)
        await self._session.commit()
        await self._session.refresh(coupon)
        return coupon

    async def save(self, coupon: Coupon) -> Coupon:
        await self._session.commit()
        await self._session.refresh(coupon)
        return coupon

    async def list_history(
        self, issued_by: uuid.UUID | None = None, limit: int = 200
    ) -> list[Coupon]:
        """Every coupon regardless of status -- active, redeemed,
        voided, or expired -- newest first. Backs the super-admin
        traceability screen (who generated a code, who used it, and
        whether an unused code has since expired), as distinct from
        list_active() which only serves the offline-sync cache.
        """
        stmt = select(Coupon).order_by(Coupon.created_at.desc())
        if issued_by is not None:
            stmt = stmt.where(Coupon.issued_by == issued_by)
        stmt = stmt.limit(min(limit, 500))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class RoleRepository:
    """Infrastructure-layer repository for the `roles` table -- see
    infrastructure/db/models.py's Role class for why this exists
    (custom roles beyond super_admin/branch_manager/staff).
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_name(self, name: str) -> Role | None:
        result = await self._session.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Role]:
        result = await self._session.execute(select(Role).order_by(Role.is_system.desc(), Role.name))
        return list(result.scalars().all())

    async def create(self, role: Role) -> Role:
        self._session.add(role)
        await self._session.commit()
        await self._session.refresh(role)
        return role

    async def save(self, role: Role) -> Role:
        await self._session.commit()
        await self._session.refresh(role)
        return role

    async def count_users_with_role(self, name: str) -> int:
        result = await self._session.execute(select(func.count()).select_from(User).where(User.role == name))
        return int(result.scalar() or 0)

    async def delete(self, role: Role) -> None:
        await self._session.delete(role)
        await self._session.commit()


class ChatRepository:
    """Infrastructure-layer repository for the group channel + DM chat
    feature -- see infrastructure/db/models.py's ChatMessage/
    ChatReadState classes and api/v1/routers/chat.py.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_message(self, message: ChatMessage) -> ChatMessage:
        self._session.add(message)
        await self._session.commit()
        await self._session.refresh(message)
        return message

    async def list_group_messages(self, limit: int = 100) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.recipient_id.is_(None), ChatMessage.is_announcement.is_(False))
            .order_by(ChatMessage.created_at.desc())
            .limit(min(limit, 200))
        )
        result = await self._session.execute(stmt)
        return list(reversed(result.scalars().all()))

    async def list_announcements(self, limit: int = 100) -> list[ChatMessage]:
        """Section 17: the universal Announcements channel -- same
        table as the group channel, filtered to is_announcement rows
        only (see ChatMessage docstring)."""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.recipient_id.is_(None), ChatMessage.is_announcement.is_(True))
            .order_by(ChatMessage.created_at.desc())
            .limit(min(limit, 200))
        )
        result = await self._session.execute(stmt)
        return list(reversed(result.scalars().all()))

    async def list_dm_messages(self, user_a: uuid.UUID, user_b: uuid.UUID, limit: int = 100) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(
                ((ChatMessage.sender_id == user_a) & (ChatMessage.recipient_id == user_b))
                | ((ChatMessage.sender_id == user_b) & (ChatMessage.recipient_id == user_a))
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(min(limit, 200))
        )
        result = await self._session.execute(stmt)
        return list(reversed(result.scalars().all()))

    async def get_read_state(self, user_id: uuid.UUID, conversation_key: str) -> ChatReadState | None:
        stmt = select(ChatReadState).where(
            ChatReadState.user_id == user_id, ChatReadState.conversation_key == conversation_key
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_read_states(self, user_id: uuid.UUID) -> list[ChatReadState]:
        result = await self._session.execute(select(ChatReadState).where(ChatReadState.user_id == user_id))
        return list(result.scalars().all())

    async def mark_read(self, user_id: uuid.UUID, conversation_key: str, when: datetime) -> None:
        existing = await self.get_read_state(user_id, conversation_key)
        if existing is None:
            self._session.add(
                ChatReadState(user_id=user_id, conversation_key=conversation_key, last_read_at=when)
            )
        else:
            existing.last_read_at = when
        await self._session.commit()

    async def count_unread(self, conversation_key: str, since: datetime | None, is_group: bool, **peer_filter) -> int:
        stmt = select(func.count()).select_from(ChatMessage)
        if is_group:
            stmt = stmt.where(ChatMessage.recipient_id.is_(None), ChatMessage.is_announcement.is_(False))
        else:
            stmt = stmt.where(
                ChatMessage.sender_id == peer_filter["peer_id"],
                ChatMessage.recipient_id == peer_filter["user_id"],
            )
        if since is not None:
            stmt = stmt.where(ChatMessage.created_at > since)
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_unread_announcements(self, since: datetime | None) -> int:
        stmt = select(func.count()).select_from(ChatMessage).where(
            ChatMessage.recipient_id.is_(None), ChatMessage.is_announcement.is_(True)
        )
        if since is not None:
            stmt = stmt.where(ChatMessage.created_at > since)
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def list_recent_dm_peers(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Every user who's ever exchanged a DM with `user_id`, most
        recent first -- backs the conversation list on the chat page so
        it's not just "everyone in the company", but who they've
        actually talked to (plus whoever the person picks fresh from
        the staff directory, handled client-side).
        """
        stmt = (
            select(ChatMessage.sender_id, ChatMessage.recipient_id, func.max(ChatMessage.created_at))
            .where(
                (ChatMessage.sender_id == user_id) | (ChatMessage.recipient_id == user_id),
                ChatMessage.recipient_id.is_not(None),
            )
            .group_by(ChatMessage.sender_id, ChatMessage.recipient_id)
            .order_by(func.max(ChatMessage.created_at).desc())
        )
        result = await self._session.execute(stmt)
        peers: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for sender_id, recipient_id, _ in result.all():
            peer = recipient_id if sender_id == user_id else sender_id
            if peer not in seen:
                seen.add(peer)
                peers.append(peer)
        return peers


class CustomerRepository:
    """Infrastructure-layer repository for the `customers` table, used
    by the customer-facing login/register flow (api/v1/routers/
    customer_auth.py). Mirrors UserRepository's shape -- same reasoning
    as CouponRepository above about keeping API-specific persistence
    concerns out of the framework-agnostic core package.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_email(self, email: str) -> Customer | None:
        result = await self._session.execute(select(Customer).where(Customer.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, customer_id: uuid.UUID) -> Customer | None:
        result = await self._session.execute(select(Customer).where(Customer.id == customer_id))
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Customer | None:
        result = await self._session.execute(select(Customer).where(Customer.phone == phone))
        return result.scalar_one_or_none()

    async def create(self, customer: Customer) -> Customer:
        self._session.add(customer)
        await self._session.commit()
        await self._session.refresh(customer)
        return customer

    async def save(self, customer: Customer) -> Customer:
        await self._session.commit()
        await self._session.refresh(customer)
        return customer
