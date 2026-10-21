"""Store-wide settings -- currently just the printed-receipt branding
(store name, contact email, logo image, and the store's online-shop
URL + caption used to build the receipt QR code). Previously these
lived only in each browser's localStorage, set once via the console,
and never seen by any other till/device. Now a super admin sets them
once here -- and can change them again any time, they are not a
one-time setup step -- and every receipt on every device reads the
same values.
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.routers.catalog import _PUBLIC_BASE_URL
from api.v1.schemas.settings import StoreSettingsOut, StoreSettingsUpdate
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import StoreSetting, User

router = APIRouter(prefix="/settings", tags=["settings"])

_LOGO_DIR = Path("static/uploads/branding")
_ALLOWED_LOGO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".svg"}

# Every field the store's receipt branding can carry. Kept as a plain
# key/value table (one row per key) rather than a dedicated single-row
# table -- simplest thing that works for a handful of settings, and
# adding a new one later is just a new key, no migration.
_KEYS = ("store_name", "store_email", "logo_url", "website_url", "website_qr_caption")


@router.get("/store", response_model=StoreSettingsOut)
async def get_store_settings(
    session: AsyncSession = Depends(get_db_session),
    # Any signed-in device needs to read this to print a receipt --
    # not admin-only, same as the logo showing up on every till.
    current_user: User = Depends(get_current_user),
) -> StoreSettingsOut:
    rows = await session.execute(select(StoreSetting).where(StoreSetting.key.in_(_KEYS)))
    values = {row.key: row.value for row in rows.scalars().all()}
    return StoreSettingsOut(
        store_name=values.get("store_name") or "Nick Cosmetics",
        store_email=values.get("store_email"),
        logo_url=values.get("logo_url"),
        website_url=values.get("website_url"),
        website_qr_caption=values.get("website_qr_caption"),
    )


@router.put("/store", response_model=StoreSettingsOut)
async def update_store_settings(
    body: StoreSettingsUpdate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("configure_system")),
) -> StoreSettingsOut:
    updates = {
        "store_name": body.store_name,
        "store_email": body.store_email,
        "website_url": body.website_url,
        "website_qr_caption": body.website_qr_caption,
    }
    for key, value in updates.items():
        if value is None:
            continue
        row = await session.get(StoreSetting, key)
        if row is None:
            row = StoreSetting(key=key)
            session.add(row)
        row.value = value.strip() or None
        row.updated_by = current_user.id
    log_audit_event(
        session,
        user_id=current_user.id,
        action="settings.store.update",
        entity_type="store_setting",
        entity_id=None,
        metadata={k: v for k, v in updates.items() if v is not None},
    )
    await session.commit()
    return await get_store_settings(session=session, current_user=current_user)


@router.post("/store/logo", response_model=StoreSettingsOut)
async def upload_store_logo(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("configure_system")),
) -> StoreSettingsOut:
    """Uploads the store's own logo so it shows up on every printed
    receipt everywhere, instead of the generic placeholder. Answers
    the spec's literal question ("is there a file location I should
    upload this logo to?") with an actual upload endpoint.
    """
    original_name = file.filename or "logo"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_LOGO_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_LOGO_EXTENSIONS))}",
        )
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = _LOGO_DIR / new_filename
    contents = await file.read()
    dest.write_bytes(contents)
    logo_url = f"{_PUBLIC_BASE_URL}/static/uploads/branding/{new_filename}"

    row = await session.get(StoreSetting, "logo_url")
    if row is None:
        row = StoreSetting(key="logo_url")
        session.add(row)
    row.value = logo_url
    row.updated_by = current_user.id
    log_audit_event(
        session,
        user_id=current_user.id,
        action="settings.store.logo_upload",
        entity_type="store_setting",
        entity_id=None,
        metadata={"logo_url": logo_url},
    )
    await session.commit()
    return await get_store_settings(session=session, current_user=current_user)
