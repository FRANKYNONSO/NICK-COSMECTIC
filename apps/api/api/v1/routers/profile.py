"""Self-service account endpoints -- what the logged-in person can do
to their OWN account (spec Sections 13-21). Deliberately under /me,
not nested inside /users/{id}, so there's no path-ordering risk of
"/users/me" ever being mistaken for a user_id, and so these routes
never need require_role -- get_current_user is enough, since every
handler only ever touches current_user's own row.

Protected vs editable (spec Section 15/16) is enforced by what
MyProfileUpdate accepts, not by field-level checks here -- full legal
name, DOB, address, emergency contact, and ID info simply aren't
fields on that schema, so there's no path for a staff member to
change them regardless of what the frontend sends.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session
from api.v1.routers.staff_applications import (
    _PHOTO_DIR,
    _ALLOWED_PHOTO_EXTENSIONS,
    _MAX_PHOTO_BYTES,
    _PUBLIC_BASE_URL,
    _save_upload,
    public_photo_url,
    resolve_id_document_path,
)
from api.v1.schemas.profile import (
    ChangePasswordRequest,
    MyProfileOut,
    MyProfileUpdate,
    RequestEmailChange,
    VerifyEmailChange,
)
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import User
from infrastructure.db.repositories import StaffApplicationRepository, UserRepository
from infrastructure.email import send_email
from infrastructure.security.jwt import mark_password_changed
from infrastructure.security.passwords import hash_password, verify_password
from infrastructure.security.tokens import expires_at, generate_token, hash_token, is_expired

router = APIRouter(prefix="/me", tags=["profile"])

_EMAIL_CHANGE_TOKEN_MINUTES = 30


def _to_profile_out(user: User, application) -> MyProfileOut:
    base = dict(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        pending_email=user.pending_email,
        phone=user.phone,
        role=user.role,
        branch_id=user.branch_id,
        employee_id=user.employee_id,
        date_joined=user.date_joined,
        is_active=user.is_active,
        has_application=application is not None,
    )
    if application is not None:
        base.update(
            application_status=application.status,
            date_of_birth=application.date_of_birth,
            gender=application.gender,
            residential_address=application.residential_address,
            state_of_origin=application.state_of_origin,
            lga_of_origin=application.lga_of_origin,
            hometown=application.hometown,
            home_address_of_origin=application.home_address_of_origin,
            emergency_contact_name=application.emergency_contact_name,
            emergency_contact_phone=application.emergency_contact_phone,
            emergency_contact_relationship=application.emergency_contact_relationship,
            id_type=application.id_type,
            id_number=application.id_number,
            has_id_document=bool(application.id_document_path),
        )
    # Prefer the account-level photo (works for every account, including
    # ones with no staff application). Fall back to the application's
    # photo so accounts that uploaded one before this field existed on
    # User don't appear to have lost it.
    base["profile_photo_url"] = public_photo_url(
        user.profile_photo_url
        or (application.profile_photo_url if application is not None else None)
    )
    base["preferred_name"] = user.preferred_name or (
        application.preferred_name if application is not None else None
    )
    return MyProfileOut(**base)


@router.get("/profile", response_model=MyProfileOut)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> MyProfileOut:
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(current_user.id)
    return _to_profile_out(current_user, application)


@router.get("/id-document")
async def get_my_id_document(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Streams the caller's OWN ID document from private storage. Same
    rule as everywhere else: the file never has a public URL, and this
    route only ever looks up current_user's own application, so there is
    no id in the path to tamper with. Works while an application is
    still under review (no require_role), since the applicant should be
    able to see what they submitted.
    """
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(current_user.id)
    path = resolve_id_document_path(application.id_document_path) if application is not None else None
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No ID document on file")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@router.patch("/profile", response_model=MyProfileOut)
async def update_my_profile(
    body: MyProfileUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> MyProfileOut:
    user_repo = UserRepository(session)
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(current_user.id)

    if body.phone_number is not None:
        existing = await user_repo.get_by_phone(body.phone_number)
        if existing is not None and existing.id != current_user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="That phone number is already in use")
        current_user.phone = body.phone_number or None
        if application is not None:
            application.phone_number = body.phone_number or None

    if body.preferred_name is not None:
        # Written to the User row directly so this works for every
        # account, not only ones that went through the staff-application
        # flow. Also mirrored onto the application (when one exists) so
        # anything still reading it from there stays in sync.
        current_user.preferred_name = body.preferred_name
        if application is not None:
            application.preferred_name = body.preferred_name

    await user_repo.save(current_user)
    if application is not None:
        await app_repo.save(application)
    log_audit_event(session, user_id=current_user.id, action="profile_updated", entity_type="user", entity_id=current_user.id)
    await session.commit()

    return _to_profile_out(current_user, application)


@router.post("/photo", response_model=MyProfileOut)
async def update_my_photo(
    profile_photo: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> MyProfileOut:
    # Written to the User row directly so any account can set a photo,
    # not only ones with a staff application (previously this 400'd for
    # admin-created accounts -- see model comment on User.profile_photo_url).
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(current_user.id)
    user_repo = UserRepository(session)

    contents = await profile_photo.read()
    filename = _save_upload(_PHOTO_DIR, _ALLOWED_PHOTO_EXTENSIONS, _MAX_PHOTO_BYTES, profile_photo, contents)
    photo_url = f"{_PUBLIC_BASE_URL}/static/uploads/staff_photos/{filename}"
    current_user.profile_photo_url = photo_url
    await user_repo.save(current_user)
    if application is not None:
        application.profile_photo_url = photo_url
        application = await app_repo.save(application)

    log_audit_event(session, user_id=current_user.id, action="profile_updated", entity_type="user", entity_id=current_user.id)
    await session.commit()
    return _to_profile_out(current_user, application)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    user_repo = UserRepository(session)
    current_user.password_hash = hash_password(body.new_password)
    mark_password_changed(current_user)  # PATCH25B-PASSWORD-CHANGED-AT
    await user_repo.save(current_user)
    log_audit_event(session, user_id=current_user.id, action="password_changed", entity_type="user", entity_id=current_user.id)
    await session.commit()


@router.post("/email/request-change", status_code=status.HTTP_204_NO_CONTENT)
async def request_email_change(
    body: RequestEmailChange,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Spec Section 17. The verified `email` column is left alone until
    /me/email/verify succeeds -- login and everything else keeps using
    the old address in the meantime.
    """
    user_repo = UserRepository(session)
    existing = await user_repo.get_by_email(body.new_email)
    if existing is not None and existing.id != current_user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That email address is already in use")

    token = generate_token()
    current_user.pending_email = body.new_email
    current_user.pending_email_token_hash = hash_token(token)
    current_user.pending_email_expires_at = expires_at(_EMAIL_CHANGE_TOKEN_MINUTES)
    await user_repo.save(current_user)

    verify_link = f"{os.getenv('WEB_ADMIN_BASE_URL', 'http://127.0.0.1:5500')}/verify-email.html?token={token}"
    send_email(
        body.new_email,
        "Verify your new Nick Cosmetics email address",
        f"Hi {current_user.full_name},\n\n"
        f"Confirm this is your new email address by clicking this link. It expires in "
        f"{_EMAIL_CHANGE_TOKEN_MINUTES} minutes:\n\n{verify_link}\n\n"
        "Your account will keep using your current email until you confirm.",
    )


@router.post("/email/verify", response_model=MyProfileOut)
async def verify_email_change(
    body: VerifyEmailChange,
    session: AsyncSession = Depends(get_db_session),
) -> MyProfileOut:
    """Deliberately not behind get_current_user -- the verification
    link is opened from an email client, which may not be signed in
    to the admin portal at all; the token itself is the credential
    here, same as password-reset.
    """
    user_repo = UserRepository(session)
    token_hash = hash_token(body.token)
    user = await user_repo.get_by_pending_email_token_hash(token_hash)

    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, detail="This verification link is invalid or has expired.")
    if user is None or is_expired(user.pending_email_expires_at) or not user.pending_email:
        raise invalid

    existing = await user_repo.get_by_email(user.pending_email)
    if existing is not None and existing.id != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That email address was taken while this link was pending")

    old_email = user.email
    user.email = user.pending_email
    user.pending_email = None
    user.pending_email_token_hash = None
    user.pending_email_expires_at = None
    user = await user_repo.save(user)

    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(user.id)
    if application is not None:
        application.email = user.email
        await app_repo.save(application)

    log_audit_event(
        session, user_id=user.id, action="email_changed", entity_type="user", entity_id=user.id,
        metadata={"old_email": old_email, "new_email": user.email},
    )
    await session.commit()
    return _to_profile_out(user, application)
