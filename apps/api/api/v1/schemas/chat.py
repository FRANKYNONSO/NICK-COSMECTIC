import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatMessageCreate(BaseModel):
    # None = post to the group channel; set = a DM to that user.
    recipient_id: uuid.UUID | None = None
    body: str = Field(default="", max_length=2000)
    # Set for an image or voice-note message; body can be empty in
    # that case (a caption is optional, not required).
    attachment_url: str | None = None
    attachment_type: str | None = Field(default=None, pattern="^(image|voice)$")


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str
    sender_photo_url: str | None = None
    recipient_id: uuid.UUID | None
    body: str
    attachment_url: str | None = None
    attachment_type: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatContactOut(BaseModel):
    """One entry in the staff directory a person can start a chat
    with -- every active account, not just people already messaged
    (that's the `/chat/unread` list, which is DM-history-only).
    """

    id: uuid.UUID
    full_name: str
    preferred_name: str | None = None
    role: str
    photo_url: str | None = None


class ChatMarkReadRequest(BaseModel):
    # None = mark the group channel read; set = mark that DM thread read.
    peer_id: uuid.UUID | None = None


class ChatConversationUnread(BaseModel):
    # peer_id None identifies the group channel's row in this list.
    peer_id: uuid.UUID | None
    peer_name: str | None
    unread_count: int


class ChatUnreadOut(BaseModel):
    total_unread: int
    conversations: list[ChatConversationUnread]


class AnnouncementCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    attachment_url: str | None = None
    attachment_type: str | None = Field(default=None, pattern="^(image|voice)$")


class AnnouncementOut(BaseModel):
    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str
    sender_photo_url: str | None = None
    body: str
    attachment_url: str | None = None
    attachment_type: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AnnouncementUnreadOut(BaseModel):
    unread_count: int
