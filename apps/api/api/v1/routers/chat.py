import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import _ensure_active_employment, get_current_user, get_db_session, require_role
from api.v1.routers.catalog import _PUBLIC_BASE_URL
from api.v1.schemas.chat import (
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUnreadOut,
    ChatConversationUnread,
    ChatContactOut,
    ChatMarkReadRequest,
    ChatMessageCreate,
    ChatMessageOut,
    ChatUnreadOut,
)
from infrastructure.db.models import ChatMessage as ChatMessageRow
from infrastructure.db.models import User
from infrastructure.db.repositories import ChatRepository

router = APIRouter(prefix="/chat", tags=["chat"])

_CHAT_ATTACHMENT_DIR = Path("static/uploads/chat")
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ALLOWED_VOICE_EXTENSIONS = {".webm", ".mp3", ".m4a", ".wav", ".ogg"}

# Available to any active, accepted account -- not gated behind a
# specific business permission (unlike process_sale, manage_inventory,
# etc.), since every role from staff up should be able to use the
# group channel and DM anyone else. _ensure_active_employment still
# blocks an account whose staff application was rejected/suspended or
# is still under review, same as every other protected endpoint.
async def _require_active_user(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    await _ensure_active_employment(current_user, session)
    return current_user


def _conversation_key(peer_id: uuid.UUID | None) -> str:
    return "group" if peer_id is None else str(peer_id)


def _to_message_out(row: ChatMessageRow, sender_names: dict[uuid.UUID, str], sender_photos: dict[uuid.UUID, str | None] | None = None) -> ChatMessageOut:
    sender_photos = sender_photos or {}
    return ChatMessageOut(
        id=row.id,
        sender_id=row.sender_id,
        sender_name=sender_names.get(row.sender_id, "Unknown"),
        sender_photo_url=sender_photos.get(row.sender_id),
        recipient_id=row.recipient_id,
        body=row.body,
        attachment_url=row.attachment_url,
        attachment_type=row.attachment_type,
        created_at=row.created_at,
    )


@router.get("/contacts", response_model=list[ChatContactOut])
async def list_contacts(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> list[ChatContactOut]:
    """Every active account a person can start a chat with -- the
    whole staff directory (super admin, branch manager, staff alike),
    not just people they've already exchanged a message with. That
    history-only list is what /chat/unread returns; this is the
    WhatsApp-style contact picker used to start a *new* conversation.
    """
    result = await session.execute(
        select(User).where(User.is_active.is_(True), User.id != current_user.id).order_by(User.full_name)
    )
    users = list(result.scalars().all())
    return [
        ChatContactOut(
            id=u.id,
            full_name=u.full_name,
            preferred_name=u.preferred_name,
            role=u.role,
            photo_url=u.profile_photo_url,
        )
        for u in users
    ]


@router.post("/attachments")
async def upload_chat_attachment(
    kind: str,
    file: UploadFile = File(...),
    current_user: User = Depends(_require_active_user),
) -> dict:
    """Uploads an image or voice-note file ahead of POST /chat/messages
    -- the returned URL goes in ChatMessageCreate.attachment_url with
    attachment_type=kind.
    """
    if kind not in ("image", "voice"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="kind must be 'image' or 'voice'")
    allowed = _ALLOWED_IMAGE_EXTENSIONS if kind == "image" else _ALLOWED_VOICE_EXTENSIONS
    original_name = file.filename or kind
    ext = Path(original_name).suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}' for {kind}. Allowed: {', '.join(sorted(allowed))}",
        )
    _CHAT_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _CHAT_ATTACHMENT_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"url": f"{_PUBLIC_BASE_URL}/static/uploads/chat/{new_filename}"}


@router.get("/messages", response_model=list[ChatMessageOut])
async def list_messages(
    peer_id: uuid.UUID | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> list[ChatMessageOut]:
    """`peer_id` omitted -> the group channel. `peer_id` set -> the DM
    thread between the current user and that person (either side of
    it -- no separate "sent"/"received" endpoints).
    """
    repo = ChatRepository(session)
    if peer_id is None:
        rows = await repo.list_group_messages(limit=limit)
    else:
        peer = await session.get(User, peer_id)
        if peer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        rows = await repo.list_dm_messages(current_user.id, peer_id, limit=limit)

    if not rows:
        return []
    sender_ids = {r.sender_id for r in rows}
    users_result = await session.execute(select(User).where(User.id.in_(sender_ids)))
    sender_users = list(users_result.scalars().all())
    sender_names = {u.id: u.full_name for u in sender_users}
    sender_photos = {u.id: u.profile_photo_url for u in sender_users}
    return [_to_message_out(r, sender_names, sender_photos) for r in rows]


@router.post("/messages", response_model=ChatMessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    body: ChatMessageCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> ChatMessageOut:
    if body.recipient_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Can't DM yourself")
    if body.recipient_id is not None:
        recipient = await session.get(User, body.recipient_id)
        if recipient is None or not recipient.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recipient not found")
    if not body.body and not body.attachment_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Message needs text or an attachment.")

    repo = ChatRepository(session)
    message = await repo.create_message(
        ChatMessageRow(
            sender_id=current_user.id,
            recipient_id=body.recipient_id,
            body=body.body,
            attachment_url=body.attachment_url,
            attachment_type=body.attachment_type,
        )
    )
    return _to_message_out(message, {current_user.id: current_user.full_name}, {current_user.id: current_user.profile_photo_url})


@router.post("/mark-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    body: ChatMarkReadRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> None:
    repo = ChatRepository(session)
    await repo.mark_read(current_user.id, _conversation_key(body.peer_id), datetime.now(timezone.utc))


@router.get("/unread", response_model=ChatUnreadOut)
async def unread_summary(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> ChatUnreadOut:
    """Powers the nav badge and the per-conversation dot in the chat
    sidebar. Only ever looks at the group channel plus DM threads the
    person has actually exchanged a message in -- a brand-new DM thread
    someone hasn't been messaged in yet obviously has nothing unread.
    """
    repo = ChatRepository(session)
    read_states = {rs.conversation_key: rs.last_read_at for rs in await repo.list_read_states(current_user.id)}

    conversations: list[ChatConversationUnread] = []

    group_unread = await repo.count_unread("group", read_states.get("group"), is_group=True)
    conversations.append(ChatConversationUnread(peer_id=None, peer_name="Group chat", unread_count=group_unread))

    peer_ids = await repo.list_recent_dm_peers(current_user.id)
    peer_names: dict[uuid.UUID, str] = {}
    if peer_ids:
        users_result = await session.execute(select(User).where(User.id.in_(peer_ids)))
        peer_names = {u.id: u.full_name for u in users_result.scalars().all()}

    for peer_id in peer_ids:
        unread = await repo.count_unread(
            str(peer_id), read_states.get(str(peer_id)), is_group=False,
            peer_id=peer_id, user_id=current_user.id,
        )
        conversations.append(
            ChatConversationUnread(peer_id=peer_id, peer_name=peer_names.get(peer_id), unread_count=unread)
        )

    return ChatUnreadOut(
        total_unread=sum(c.unread_count for c in conversations),
        conversations=conversations,
    )


# ----------------------------------------------------------- ANNOUNCEMENTS
# Section 17: "a universal Announcements channel where the Super Admin
# can broadcast a message that is visible to all staff in all
# branches." Read access uses the same _require_active_user tier as
# the rest of chat (any accepted, active account); posting is
# super_admin-only.

@router.get("/announcements", response_model=list[AnnouncementOut])
async def list_announcements(
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> list[AnnouncementOut]:
    repo = ChatRepository(session)
    messages = await repo.list_announcements(limit)
    sender_ids = {m.sender_id for m in messages}
    sender_names: dict[uuid.UUID, str] = {}
    sender_photos: dict[uuid.UUID, str | None] = {}
    if sender_ids:
        users_result = await session.execute(select(User).where(User.id.in_(sender_ids)))
        for u in users_result.scalars().all():
            sender_names[u.id] = u.full_name
            sender_photos[u.id] = u.profile_photo_url
    return [
        AnnouncementOut(
            id=m.id,
            sender_id=m.sender_id,
            sender_name=sender_names.get(m.sender_id, "Unknown"),
            sender_photo_url=sender_photos.get(m.sender_id),
            body=m.body,
            attachment_url=m.attachment_url,
            attachment_type=m.attachment_type,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post("/announcements", response_model=AnnouncementOut, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    body: AnnouncementCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_role("super_admin")),
) -> AnnouncementOut:
    repo = ChatRepository(session)
    message = await repo.create_message(
        ChatMessageRow(
            sender_id=current_user.id,
            recipient_id=None,
            is_announcement=True,
            body=body.body,
            attachment_url=body.attachment_url,
            attachment_type=body.attachment_type,
        )
    )
    return AnnouncementOut(
        id=message.id,
        sender_id=message.sender_id,
        sender_name=current_user.full_name,
        sender_photo_url=current_user.profile_photo_url,
        body=message.body,
        attachment_url=message.attachment_url,
        attachment_type=message.attachment_type,
        created_at=message.created_at,
    )


@router.post("/announcements/mark-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_announcements_read(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> None:
    repo = ChatRepository(session)
    await repo.mark_read(current_user.id, "announcements", datetime.now(timezone.utc))


@router.get("/announcements/unread-count", response_model=AnnouncementUnreadOut)
async def announcements_unread_count(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_active_user),
) -> AnnouncementUnreadOut:
    repo = ChatRepository(session)
    read_state = await repo.get_read_state(current_user.id, "announcements")
    count = await repo.count_unread_announcements(read_state.last_read_at if read_state else None)
    return AnnouncementUnreadOut(unread_count=count)
