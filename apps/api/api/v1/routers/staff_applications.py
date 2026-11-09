import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user_allow_pending, get_db_session, require_permission  # PATCHP3G-IMPORT
from api.v1.schemas.staff_applications import (
    StaffApplicationAccept,
    StaffApplicationOut,
    StaffApplicationReject,
    StaffApplicationStatusOut,
    StaffApplicationSummary,
    StaffRegisterRequest,
)
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import Branch, StaffApplication, User
from infrastructure.db.models import Role as RoleModel
from infrastructure.db.repositories import StaffApplicationRepository, UserRepository
from infrastructure.db.user_deletion import deletion_blockers, hard_delete_user
from infrastructure.security.passwords import hash_password
from nickcosmetics_core.domain.users.user import Role as DomainRole
from nickcosmetics_core.exceptions import InvalidRoleError

router = APIRouter(prefix="/staff-applications", tags=["staff-applications"])

# Same roles allowed to manage staff elsewhere (api/v1/routers/users.py) --
# reviewing applications is a staff-management action, so it stays
# consistent with that router rather than introducing a new role tier.
_REVIEW_ROLES = ("super_admin", "branch_manager")

_PUBLIC_BASE_URL = os.getenv("API_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# Profile photos are low-sensitivity, so they're served the same way
# product images are (static/uploads, public URL).
_PHOTO_DIR = Path("static/uploads/staff_photos")
_ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB

# Identity documents are NOT mounted as static anywhere -- they only
# ever leave disk through the authenticated download_id_document
# endpoint below, which checks the requester is either the applicant
# themselves or an authorized reviewer. Spec Section 3/26/27.
_ID_DOCUMENT_DIR = Path("private_uploads/id_documents")
_ALLOWED_ID_DOCUMENT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_MAX_ID_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def public_photo_url(stored: str | None) -> str | None:
    """Profile photos are stored with an absolute URL built from
    _PUBLIC_BASE_URL at upload time, which pins them to whatever host
    the API was on then (e.g. http://127.0.0.1:8000 -- broken on any
    other device). Re-base the stored value onto the CURRENT public
    base URL whenever it is sent out, so old rows keep working after a
    host change. The stored data itself is left untouched.
    """
    if not stored:
        return None
    idx = stored.find("/static/")
    if idx == -1:
        return stored
    return f"{_PUBLIC_BASE_URL}{stored[idx:]}"


def resolve_id_document_path(stored: str | None) -> Path | None:
    """Locate a stored ID document on disk. Only the file NAME is taken
    from the stored value and joined onto _ID_DOCUMENT_DIR, so a path
    saved on Windows (backslashes) still resolves when the API runs on
    Linux, and a tampered value can never point outside that folder.
    """
    if not stored:
        return None
    name = Path(stored.replace("\\", "/")).name
    if not name:
        return None
    path = _ID_DOCUMENT_DIR / name
    return path if path.is_file() else None


# PATCHP3G-HELPER
async def _holds_review_key(session: AsyncSession, user: User) -> bool:
    """Same rule as require_permission, checked inside a route body.

    Role keys plus grants given to the account itself, minus denials on the
    account itself (a super_admin is never denied). Used where the route must
    stay open to the applicant (id-document), so a guard dependency cannot be
    used. Like the old role-name check, it ignores the employment status.
    """
    key = "review_staff_applications"
    role_row = await session.execute(select(RoleModel.permissions).where(RoleModel.name == user.role))
    granted = set(role_row.scalar_one_or_none() or []) | set(user.granted_permissions or [])
    denied = set(user.denied_permissions or [])
    return key in granted and (user.role == "super_admin" or key not in denied)


async def _ensure_can_view_credentials(
    session: AsyncSession, current_user: User, application: StaffApplication
) -> None:
    """Who may open an application's details / ID document:
    - the applicant themselves, always;
    - a super_admin, always;
    - a branch_manager only for employees of THEIR OWN branch, and never
      for a super_admin or another branch manager. Applicants who are
      not employees yet (under_review / rejected) keep the existing
      review behaviour: accepting one is already open to any branch
      manager, and they have no branch membership to check.
    """
    if current_user.id == application.user_id or current_user.role == "super_admin":
        return
    # PATCHP3G-CRED: any reviewer who is not super_admin gets the branch limits below
    if application.status in ("under_review", "rejected"):
        return
    target = await UserRepository(session).get_by_id(application.user_id)
    if (
        target is None
        or current_user.branch_id is None
        or target.branch_id != current_user.branch_id
        or target.role in ("super_admin", "branch_manager")
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Not permitted to view this employee's credentials",
        )


def _save_upload(directory: Path, allowed_extensions: set[str], max_bytes: int, file: UploadFile, contents: bytes) -> str:
    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
        )
    if len(contents) > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds the maximum allowed size of {max_bytes // (1024 * 1024)}MB",
        )
    directory.mkdir(parents=True, exist_ok=True)
    new_filename = f"{uuid.uuid4()}{ext}"
    dest = directory / new_filename
    dest.write_bytes(contents)
    return new_filename


def _to_out(application: StaffApplication) -> StaffApplicationOut:
    return StaffApplicationOut(
        id=application.id,
        user_id=application.user_id,
        status=application.status,
        role_applied_for=application.role_applied_for,
        branch_id=application.branch_id,
        full_legal_name=application.full_legal_name,
        preferred_name=application.preferred_name,
        profile_photo_url=public_photo_url(application.profile_photo_url),
        date_of_birth=application.date_of_birth,
        gender=application.gender,
        phone_number=application.phone_number,
        email=application.email,
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
        reviewed_by=application.reviewed_by,
        reviewed_at=application.reviewed_at,
        rejection_reason=application.rejection_reason,
        submitted_at=application.submitted_at,
        correction_note=application.correction_note,
        correction_fields=[f for f in (application.correction_fields or "").split(",") if f],
    )


@router.get("/application-options", response_model=dict)
async def get_application_options(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Unauthenticated -- populates the role and branch dropdowns on
    the public registration form (register.html), so an applicant
    picks both themselves at sign-up instead of a role-only choice
    with the branch assigned later by whoever reviews it. Always
    reflects live data: a role or branch a super_admin adds later
    shows up here automatically, no code change needed.

    Deliberately excludes super_admin from the role list (see
    role_applied_for's docstring in the schema) and returns only the
    non-sensitive fields of each -- no permission lists, no phone
    numbers, nothing an anonymous visitor shouldn't see.
    """
    roles_result = await session.execute(
        select(RoleModel).where(RoleModel.name != "super_admin").order_by(RoleModel.display_name)
    )
    branches_result = await session.execute(select(Branch).order_by(Branch.name))
    return {
        "roles": [
            {"name": r.name, "display_name": r.display_name} for r in roles_result.scalars().all()
        ],
        "branches": [
            {"id": str(b.id), "name": b.name, "address": b.address} for b in branches_result.scalars().all()
        ],
    }


@router.post("/register", response_model=StaffApplicationOut, status_code=status.HTTP_201_CREATED)
async def register(
    email: Annotated[str, Form()],
    preferred_name: Annotated[str, Form()],
    password: Annotated[str, Form()],
    full_legal_name: Annotated[str, Form()],
    role_applied_for: Annotated[str, Form()] = "cashier",
    branch_id: Annotated[uuid.UUID | None, Form()] = None,
    date_of_birth: Annotated[date | None, Form()] = None,
    gender: Annotated[str | None, Form()] = None,
    phone_number: Annotated[str | None, Form()] = None,
    residential_address: Annotated[str | None, Form()] = None,
    state_of_origin: Annotated[str | None, Form()] = None,
    lga_of_origin: Annotated[str | None, Form()] = None,
    hometown: Annotated[str | None, Form()] = None,
    home_address_of_origin: Annotated[str | None, Form()] = None,
    emergency_contact_name: Annotated[str | None, Form()] = None,
    emergency_contact_phone: Annotated[str | None, Form()] = None,
    emergency_contact_relationship: Annotated[str | None, Form()] = None,
    id_type: Annotated[str | None, Form()] = None,
    id_number: Annotated[str | None, Form()] = None,
    profile_photo: UploadFile | None = File(default=None),
    id_document: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> StaffApplication:
    """Create Account -> Staff Registration/Application (spec Sections
    2-5). Creates a login-capable User plus a StaffApplication in
    'under_review' status. Deliberately does NOT log the person in --
    the spec's flow sends them back to the existing login page, where
    they'll see a restricted status page instead of the dashboard
    until an admin accepts them (enforced by
    api.dependencies._ensure_active_employment on every protected
    route, not just hidden in the frontend).
    """
    try:
        body = StaffRegisterRequest(
            email=email,
            preferred_name=preferred_name,
            password=password,
            role_applied_for=role_applied_for,
            branch_id=branch_id,
            full_legal_name=full_legal_name,
            date_of_birth=date_of_birth,
            gender=gender,
            phone_number=phone_number,
            residential_address=residential_address,
            state_of_origin=state_of_origin,
            lga_of_origin=lga_of_origin,
            hometown=hometown,
            home_address_of_origin=home_address_of_origin,
            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
            emergency_contact_relationship=emergency_contact_relationship,
            id_type=id_type,
            id_number=id_number,
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors())

    # role_applied_for and branch_id are both free-form now (not a
    # fixed Literal / hardcoded list) so that a role or branch a
    # super_admin adds later shows up here automatically -- validated
    # against the live tables instead, right where the DB session is
    # available.
    role_row = (
        await session.execute(select(RoleModel).where(RoleModel.name == body.role_applied_for))
    ).scalar_one_or_none()
    if role_row is None or body.role_applied_for == "super_admin":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{body.role_applied_for}' is not a role you can apply for",
        )
    branch_row = (
        await session.execute(select(Branch).where(Branch.id == body.branch_id))
    ).scalar_one_or_none()
    if branch_row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected branch was not found")

    user_repo = UserRepository(session)
    if await user_repo.get_by_email(body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Email '{body.email}' is already in use")
    if body.phone_number and await user_repo.get_by_phone(body.phone_number) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Phone '{body.phone_number}' is already in use")
    if await user_repo.get_by_preferred_name(body.preferred_name) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"'{body.preferred_name}' is already taken by another staff member -- please choose a different preferred name.",
        )

    photo_url = None
    if profile_photo is not None and profile_photo.filename:
        contents = await profile_photo.read()
        filename = _save_upload(_PHOTO_DIR, _ALLOWED_PHOTO_EXTENSIONS, _MAX_PHOTO_BYTES, profile_photo, contents)
        photo_url = f"{_PUBLIC_BASE_URL}/static/uploads/staff_photos/{filename}"

    id_document_path = None
    if id_document is not None and id_document.filename:
        contents = await id_document.read()
        filename = _save_upload(
            _ID_DOCUMENT_DIR, _ALLOWED_ID_DOCUMENT_EXTENSIONS, _MAX_ID_DOCUMENT_BYTES, id_document, contents
        )
        id_document_path = str(_ID_DOCUMENT_DIR / filename)

    # Actual role is a placeholder until an admin accepts the
    # application and assigns a real one (spec Section 10) -- 'cashier'
    # is the minimal existing role, so nothing is granted by default;
    # the account is gated regardless by _ensure_active_employment
    # while under_review. role_applied_for (below) is just the
    # applicant's stated preference for the reviewer to see.
    user = User(
        full_name=body.full_legal_name,
        email=body.email,
        phone=body.phone_number,
        password_hash=hash_password(body.password),
        role="cashier",
        is_active=True,
        preferred_name=body.preferred_name,
    )
    user = await user_repo.create(user)

    application = StaffApplication(
        user_id=user.id,
        status="under_review",
        role_applied_for=body.role_applied_for,
        branch_id=body.branch_id,
        full_legal_name=body.full_legal_name,
        preferred_name=body.preferred_name,
        profile_photo_url=photo_url,
        date_of_birth=body.date_of_birth,
        gender=body.gender,
        phone_number=body.phone_number,
        email=body.email,
        residential_address=body.residential_address,
        state_of_origin=body.state_of_origin,
        lga_of_origin=body.lga_of_origin,
        hometown=body.hometown,
        home_address_of_origin=body.home_address_of_origin,
        emergency_contact_name=body.emergency_contact_name,
        emergency_contact_phone=body.emergency_contact_phone,
        emergency_contact_relationship=body.emergency_contact_relationship,
        id_type=body.id_type,
        id_number=body.id_number,
        id_document_path=id_document_path,
    )
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.create(application)
    log_audit_event(
        session, user_id=user.id, action="application_submitted",
        entity_type="staff_application", entity_id=application.id,
    )
    await session.commit()
    return _to_out(application)


# PENDSTAFF-COMPLETE-PROFILE
# CORR-CONTEXT
@router.get("/complete-profile/context")
async def complete_profile_context(
    current_user: User = Depends(get_current_user_allow_pending),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Everything the completion / correction form needs: what the admin
    entered, what the applicant already submitted (when sent back for
    corrections), and the reviewer's note + flagged fields."""
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(current_user.id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No staff application on this account")
    if application.status not in {"incomplete", "needs_changes"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Nothing to complete (status: '{application.status}').",
        )
    full_name = (current_user.full_name or application.full_legal_name or "").strip()
    preferred = (getattr(application, "preferred_name", None) or "").strip()
    if application.status == "incomplete" and preferred == full_name:
        preferred = ""  # placeholder copied from the full name at creation
    return {
        "status": application.status,
        "full_legal_name": full_name,
        "email": (current_user.email or "").strip(),
        "phone_number": (current_user.phone or "").strip(),
        "role_applied_for": application.role_applied_for or current_user.role or "",
        "branch_id": str(application.branch_id) if application.branch_id else "",
        "preferred_name": preferred,
        "date_of_birth": application.date_of_birth.isoformat() if application.date_of_birth else "",
        "gender": application.gender or "",
        "residential_address": application.residential_address or "",
        "state_of_origin": application.state_of_origin or "",
        "lga_of_origin": application.lga_of_origin or "",
        "hometown": application.hometown or "",
        "home_address_of_origin": application.home_address_of_origin or "",
        "emergency_contact_name": application.emergency_contact_name or "",
        "emergency_contact_phone": application.emergency_contact_phone or "",
        "emergency_contact_relationship": application.emergency_contact_relationship or "",
        "id_type": application.id_type or "",
        "id_number": application.id_number or "",
        "has_photo": bool(application.profile_photo_url),
        "has_id_document": bool(application.id_document_path),
        "correction_note": application.correction_note or "",
        "correction_fields": [f for f in (application.correction_fields or "").split(",") if f],
    }


@router.post("/complete-profile", response_model=StaffApplicationOut)
async def complete_profile(
    preferred_name: Annotated[str, Form()],
    full_legal_name: Annotated[str | None, Form()] = None,
    role_applied_for: Annotated[str | None, Form()] = None,
    branch_id: Annotated[str | None, Form()] = None,
    email: Annotated[str | None, Form()] = None,
    phone_number: Annotated[str | None, Form()] = None,
    date_of_birth: Annotated[date | None, Form()] = None,
    gender: Annotated[str | None, Form()] = None,
    residential_address: Annotated[str | None, Form()] = None,
    state_of_origin: Annotated[str | None, Form()] = None,
    lga_of_origin: Annotated[str | None, Form()] = None,
    hometown: Annotated[str | None, Form()] = None,
    home_address_of_origin: Annotated[str | None, Form()] = None,
    emergency_contact_name: Annotated[str | None, Form()] = None,
    emergency_contact_phone: Annotated[str | None, Form()] = None,
    emergency_contact_relationship: Annotated[str | None, Form()] = None,
    id_type: Annotated[str | None, Form()] = None,
    id_number: Annotated[str | None, Form()] = None,
    profile_photo: UploadFile | None = File(default=None),
    id_document: UploadFile | None = File(default=None),
    current_user: User = Depends(get_current_user_allow_pending),
    session: AsyncSession = Depends(get_db_session),
) -> StaffApplicationOut:
    """Second step for accounts created from the Staff page's "New staff"
    button. Same fields as the Create Account form. Anything the admin
    already entered (name, email, phone, role, branch) is pre-filled and
    the person may change it here. Role and branch are only a request: the
    reviewer picks the final role and branch when approving, and the role
    'super_admin' cannot be requested. Only valid while the
    application is 'incomplete'; afterwards it is 'under_review' and the
    person still has no access until a super admin / branch manager accepts.
    """
    import re

    from api.v1.schemas.validators import validate_digits_only

    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_user_id(current_user.id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No staff application on this account")
    if application.status not in ("incomplete", "needs_changes"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Your details were already submitted (status: '{application.status}').",
        )

    def _clean(value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    def _bad(message: str, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> HTTPException:
        return HTTPException(code, detail=message)

    user_repo = UserRepository(session)

    # ---- identity fields: pre-filled by the admin, editable here ----
    # PENDSTAFF-V3. Role and branch are only a request here: the reviewer
    # picks the final role and branch when approving.  # PATCHP3L-TEXT
    cur_name = (current_user.full_name or "").strip()
    new_name = _clean(full_legal_name) or cur_name
    if not new_name:
        raise _bad("Enter your first name and surname.")
    current_user.full_name = new_name
    application.full_legal_name = new_name

    cur_email = (current_user.email or "").strip()
    new_email = _clean(email) or cur_email
    if not new_email:
        raise _bad("Enter your email address.")
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", new_email):
        raise _bad("Enter a valid email address.")
    if new_email.lower() != cur_email.lower():
        other = await user_repo.get_by_email(new_email)
        if other is not None and other.id != current_user.id:
            raise _bad(f"Email '{new_email}' is already in use", status.HTTP_409_CONFLICT)
    current_user.email = new_email

    cur_phone = (current_user.phone or "").strip()
    new_phone = _clean(phone_number)
    if new_phone:
        try:
            new_phone = validate_digits_only(new_phone, "Phone number")
        except ValueError as exc:
            raise _bad(str(exc))
        if new_phone != cur_phone:
            other = await user_repo.get_by_phone(new_phone)
            if other is not None and other.id != current_user.id:
                raise _bad(f"Phone '{new_phone}' is already in use", status.HTTP_409_CONFLICT)
        current_user.phone = new_phone


    # ---- role / branch: pre-filled, editable. Only a request -- the reviewer
    # picks the final role/branch on approval. Must exist in the lists. ----
    from sqlalchemy import text as _sql_text

    new_role = _clean(role_applied_for)
    if new_role and "website" in new_role.lower():  # PENDSTAFF-V6
        raise _bad("Choose a valid role.")
    if new_role == "super_admin":  # PATCHP3L-ROLE
        raise _bad(f"'{new_role}' is not a role you can apply for")
    if new_role:
        found = (await session.execute(
            _sql_text("SELECT name FROM roles WHERE name = :n LIMIT 1"), {"n": new_role}
        )).scalar_one_or_none()
        if found is None:
            raise _bad("Choose a valid role.")
        application.role_applied_for = found
    new_branch = _clean(branch_id)
    if new_branch:
        found_branch = (await session.execute(
            _sql_text("SELECT id FROM branches WHERE CAST(id AS text) = :i LIMIT 1"), {"i": new_branch}
        )).scalar_one_or_none()
        if found_branch is None:
            raise _bad("Choose a valid branch.")
        application.branch_id = found_branch

    preferred_name = _clean(preferred_name)
    if not preferred_name:
        raise _bad("Enter your preferred name / username.")
    other = await user_repo.get_by_preferred_name(preferred_name)
    if other is not None and other.id != current_user.id:
        raise _bad(
            f"'{preferred_name}' is already taken by another staff member -- please choose a different preferred name.",
            status.HTTP_409_CONFLICT,
        )

    # ---- optional fields (same as the Create Account form) ----
    if date_of_birth is not None and date_of_birth > date.today():
        raise _bad("Date of birth cannot be in the future")
    gender = _clean(gender)
    if gender is not None:
        gender = gender.lower()
        if gender not in ("male", "female"):
            raise _bad("Choose a valid gender")
    id_type = _clean(id_type)
    if id_type is not None and id_type not in ("national_id", "passport", "drivers_license"):
        raise _bad("Choose a valid ID type")
    emergency_contact_phone = _clean(emergency_contact_phone)
    if emergency_contact_phone:
        try:
            emergency_contact_phone = validate_digits_only(emergency_contact_phone, "Emergency contact phone")
        except ValueError as exc:
            raise _bad(str(exc))

    if profile_photo is not None and profile_photo.filename:
        contents = await profile_photo.read()
        filename = _save_upload(_PHOTO_DIR, _ALLOWED_PHOTO_EXTENSIONS, _MAX_PHOTO_BYTES, profile_photo, contents)
        application.profile_photo_url = f"{_PUBLIC_BASE_URL}/static/uploads/staff_photos/{filename}"
    if id_document is not None and id_document.filename:
        contents = await id_document.read()
        filename = _save_upload(
            _ID_DOCUMENT_DIR, _ALLOWED_ID_DOCUMENT_EXTENSIONS, _MAX_ID_DOCUMENT_BYTES, id_document, contents
        )
        application.id_document_path = str(_ID_DOCUMENT_DIR / filename)

    current_user.preferred_name = preferred_name
    await user_repo.save(current_user)

    # name / email / phone / role / branch on the application mirror the
    # account (already set by the admin) -- never taken from the request.
    # PENDSTAFF-V4
    application.preferred_name = preferred_name
    application.email = (current_user.email or "").strip()
    application.phone_number = (current_user.phone or "").strip() or application.phone_number
    application.date_of_birth = date_of_birth
    application.gender = gender
    application.residential_address = _clean(residential_address)
    application.state_of_origin = _clean(state_of_origin)
    application.lga_of_origin = _clean(lga_of_origin)
    application.hometown = _clean(hometown)
    application.home_address_of_origin = _clean(home_address_of_origin)
    application.emergency_contact_name = _clean(emergency_contact_name)
    application.emergency_contact_phone = emergency_contact_phone
    application.emergency_contact_relationship = _clean(emergency_contact_relationship)
    application.id_type = id_type
    application.id_number = _clean(id_number)
    application.status = "under_review"
    application.correction_note = None
    application.correction_fields = None
    application.submitted_at = datetime.now(timezone.utc)
    application = await app_repo.save(application)
    log_audit_event(
        session, user_id=current_user.id, action="profile_completed",
        entity_type="staff_application", entity_id=application.id,
    )
    await session.commit()
    return _to_out(application)

@router.get("/me", response_model=StaffApplicationStatusOut)
async def my_status(
    current_user: User = Depends(get_current_user_allow_pending),
    session: AsyncSession = Depends(get_db_session),
) -> StaffApplication:
    """Powers the "Application Status: Under Review" limited page
    (spec Section 6) -- deliberately available regardless of
    application status (unlike require_role/require_permission-gated
    routes), since a pending/rejected/suspended applicant still needs
    to see where they stand.
    """
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_user_id(current_user.id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No staff application on this account")
    return application


@router.get("", response_model=list[StaffApplicationSummary])
async def list_applications(
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> list[StaffApplication]:
    repo = StaffApplicationRepository(session)
    rows = await repo.list_all(status=status_filter)
    # PENDSTAFF-LIST-SCOPE: a branch manager only sees their own branch
    if current_user.role != "super_admin":  # PATCHP3G-BRANCH
        rows = [r for r in rows if r.branch_id is None or r.branch_id == current_user.branch_id]
    return rows


# PATCH26A-STAFF-FORM-PDF
# Blank, printable version of the staff application form (register.html), so an
# admin can hand it to a candidate to fill in by hand. There is deliberately NO
# password line: the person chooses one when they submit the online form.
_FORM_SECTIONS = [
    ("Role and branch", [
        ("Role applying for", None),
        ("Branch applying to", None),
    ]),
    ("Account", [
        ("Email", None),
        ("Preferred name / username", None),
    ]),
    ("Basic information", [
        ("First name", None),
        ("Middle name (optional)", None),
        ("Surname", None),
        ("Date of birth (DD/MM/YYYY)", None),
        ("Gender", "[   ] Male          [   ] Female"),
        ("Phone number", None),
        ("Current residential address", None),
    ]),
    ("Origin", [
        ("State of origin", None),
        ("LGA of origin", None),
        ("Hometown", None),
        ("Home address of origin", None),
    ]),
    ("Emergency contact", [
        ("Contact name", None),
        ("Contact phone", None),
        ("Relationship to contact", None),
    ]),
    ("Identification", [
        ("ID type", "[  ] National ID    [  ] Passport    [  ] Driver's License"),
        ("ID number", None),
        ("Attached", "[  ] Passport photo      [  ] Copy of ID"),
    ]),
]


def _build_blank_staff_form_pdf() -> bytes:
    from fpdf import FPDF  # fpdf2 -- already listed in requirements.txt

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    right_edge = pdf.w - pdf.r_margin

    def heading(title: str, rows_needed: int) -> None:
        if pdf.will_page_break(rows_needed * 9 + 12):
            pdf.add_page()
        pdf.set_fill_color(230, 230, 230)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, title, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)

    def line_row(label: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(62, 8, label)
        y = pdf.get_y() + 6.5
        pdf.line(pdf.get_x(), y, right_edge, y)
        pdf.ln(9)

    def choice_row(label: str, choices: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(62, 8, label)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 8, choices, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    import pdf_branding as _brand  # Prestige / Nick Cosmetics logo
    _logo = _brand.lockup_path()
    if _logo:
        from PIL import Image as _PILImage
        with _PILImage.open(_logo) as _im:
            _lw = 14 * _im.width / _im.height  # width in mm at 14 mm tall
        pdf.image(_logo, x=(pdf.w - _lw) / 2, y=12, h=14)  # centred on the page
        pdf.set_y(30)
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 9, "Staff application form", align="C", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 9, "Nick Cosmetics", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 7, "Staff application form", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(
        0, 4.5,
        "Print clearly in block letters. Do not write a password on this form - "
        "you will choose one yourself when you create your online account.",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(3)

    for title, rows in _FORM_SECTIONS:
        heading(title, len(rows))
        for label, choices in rows:
            if choices:
                choice_row(label, choices)
            else:
                line_row(label)
        pdf.ln(2)

    heading("Declaration", 2)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        0, 4.5,
        "I confirm that the information above is true and that I may be asked to show the "
        "original identification document.",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)
    line_row("Applicant signature")
    line_row("Date")
    pdf.ln(3)

    heading("For office use only", 4)
    line_row("Verified by")
    line_row("Date verified")
    choice_row("Decision", "[  ] Approve      [  ] Decline")
    line_row("Notes")

    return bytes(pdf.output())


@router.get("/form.pdf")
async def download_blank_staff_form(
    _: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> Response:
    """Blank printable staff application form. Contains no personal data."""
    return Response(
        content=_build_blank_staff_form_pdf(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="nick-cosmetics-staff-form.pdf"'},
    )


@router.get("/{application_id}", response_model=StaffApplicationOut)
async def get_application(
    application_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> StaffApplicationOut:
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")
    await _ensure_can_view_credentials(session, current_user, application)
    # Built through _to_out instead of returning the ORM row: the row has
    # no `has_id_document` attribute, so serialising it directly always
    # reported False and the admin UI never showed the ID document.
    return _to_out(application)


@router.get("/{application_id}/id-document")
async def download_id_document(
    application_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user_allow_pending),
) -> FileResponse:
    """Streams the private ID document -- never exposed via a public
    static URL. Only the applicant themselves or an authorized
    reviewer can retrieve it (spec Section 26/27).
    """
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_id(application_id)
    if application is None or not application.id_document_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")

    is_owner = application.user_id == current_user.id
    is_reviewer = (not is_owner) and await _holds_review_key(session, current_user)  # PATCHP3G-IDDOC
    if not (is_owner or is_reviewer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to view this document")

    await _ensure_can_view_credentials(session, current_user, application)

    path = resolve_id_document_path(application.id_document_path)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@router.post("/{application_id}/accept", response_model=StaffApplicationOut)
async def accept_application(
    application_id: uuid.UUID,
    body: StaffApplicationAccept,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> StaffApplicationOut:  # PATCHP3G-OUT
    """Accept -> convert applicant into an active employee (spec
    Sections 8-10): auto-assign an employee ID, and set the branch and
    role the admin chose. Nothing from the application is re-entered
    -- it was already collected at registration.
    """
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")
    if application.status == "incomplete":  # PENDSTAFF-ACCEPT-INCOMPLETE
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This person has not completed their profile yet, so they cannot be approved.",
        )
    if application.status not in ("under_review", "rejected"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Cannot accept an application in '{application.status}' status")

    # PENDSTAFF-ACCEPT-ROLE
    # No role sent -> keep the user's current role: 'cashier' for self-registered
    # applicants (same as before), or the role an admin picked on "New staff".
    linked_user = await UserRepository(session).get_by_id(application.user_id)
    if "role" in body.model_fields_set:
        role = body.role
    else:
        role = (linked_user.role if linked_user is not None else None) or body.role
    if (await session.execute(select(RoleModel.id).where(RoleModel.name == role))).first() is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown role '{role}'")

    branch_id = body.branch_id or application.branch_id  # PENDSTAFF-ACCEPT-BRANCH
    if current_user.role != "super_admin":  # PATCHP3G-BRANCH
        # Same restriction as create_user in users.py -- a branch
        # manager can only bring someone on as 'cashier', into their own
        # branch, never as a peer/admin or into another branch.
        if role != "cashier":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Branch managers may only accept applicants as staff")
        if current_user.branch_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Branch manager has no assigned branch")
        if application.branch_id is not None and application.branch_id != current_user.branch_id:  # PENDSTAFF-ACCEPT-SCOPE
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This applicant belongs to another branch")
        branch_id = current_user.branch_id
    elif branch_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No branch on the application and none was provided -- pick a branch to accept into",
        )

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(application.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Linked user account not found")

    user.branch_id = branch_id
    user.role = role
    user.is_active = True
    if not user.employee_id:
        user.employee_id = await app_repo.next_employee_id()
        user.date_joined = date.today()
    await user_repo.save(user)

    application.status = "accepted"
    application.reviewed_by = current_user.id
    application.reviewed_at = datetime.now(timezone.utc)
    application.rejection_reason = None
    application = await app_repo.save(application)
    log_audit_event(
        session, user_id=current_user.id, action="application_accepted",
        entity_type="staff_application", entity_id=application.id,
        metadata={"employee_id": user.employee_id, "branch_id": str(branch_id), "role": role},
    )
    await session.commit()
    return _to_out(application)  # PATCHP3G-OUT


from pydantic import BaseModel as _PydBase, Field as _PydField  # CORR-REQUEST-CHANGES


class StaffApplicationRequestChanges(_PydBase):
    note: str = _PydField(min_length=1, max_length=2000)
    flagged_fields: list[str] = _PydField(default_factory=list)


# Form field ids a reviewer may flag ("name" = first/middle/surname group).
_CORRECTABLE_FIELDS = {
    "name", "email", "phone_number", "preferred_name", "role_applied_for", "branch_id",
    "profile_photo", "date_of_birth", "gender", "residential_address", "state_of_origin",
    "lga_of_origin", "hometown", "home_address_of_origin", "emergency_contact_name",
    "emergency_contact_phone", "emergency_contact_relationship", "id_type", "id_number",
    "id_document",
}


@router.post("/{application_id}/request-changes", response_model=StaffApplicationOut)
async def request_application_changes(
    application_id: uuid.UUID,
    body: StaffApplicationRequestChanges,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> StaffApplicationOut:
    """Send an application back to the applicant with a note and the fields
    that need fixing. Status becomes 'needs_changes' -- still no access. The
    applicant sees their saved data, fixes it and resubmits (-> under_review).
    """
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")
    if application.status != "under_review":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot request changes on an application in '{application.status}' status",
        )
    if (
        current_user.role != "super_admin"  # PATCHP3G-BRANCH
        and application.branch_id is not None
        and application.branch_id != current_user.branch_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This applicant belongs to another branch")

    note = body.note.strip()
    if not note:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Write a note telling the applicant what to fix")
    flagged: list[str] = []
    for raw_field in body.flagged_fields:
        field_id = raw_field.strip()
        if field_id not in _CORRECTABLE_FIELDS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown field '{field_id}'")
        if field_id not in flagged:
            flagged.append(field_id)

    application.status = "needs_changes"
    application.correction_note = note
    application.correction_fields = ",".join(flagged) or None
    application.reviewed_by = current_user.id
    application.reviewed_at = datetime.now(timezone.utc)
    application = await repo.save(application)
    log_audit_event(
        session, user_id=current_user.id, action="application_changes_requested",
        entity_type="staff_application", entity_id=application.id,
        metadata={"note": note, "fields": flagged},
    )
    await session.commit()
    return _to_out(application)


@router.post("/{application_id}/reject", response_model=StaffApplicationOut)
async def reject_application(
    application_id: uuid.UUID,
    body: StaffApplicationReject,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> StaffApplicationOut:  # PATCHP3G-OUT
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")
    if application.status != "under_review":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Cannot reject an application in '{application.status}' status")
    if (  # PENDSTAFF-REJECT-SCOPE
        current_user.role != "super_admin"  # PATCHP3G-BRANCH
        and application.branch_id is not None
        and application.branch_id != current_user.branch_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This applicant belongs to another branch")

    application.status = "rejected"
    application.reviewed_by = current_user.id
    application.reviewed_at = datetime.now(timezone.utc)
    application.rejection_reason = body.reason
    application = await repo.save(application)
    log_audit_event(
        session, user_id=current_user.id, action="application_rejected",
        entity_type="staff_application", entity_id=application.id,
        metadata={"reason": body.reason} if body.reason else None,
    )
    await session.commit()
    return _to_out(application)  # PATCHP3G-OUT


@router.post("/{application_id}/suspend", response_model=StaffApplicationOut)
async def suspend_application(
    application_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> StaffApplicationOut:  # PATCHP3G-OUT
    """Suspend an active employee (spec Section 24). Blocks protected
    business functions and, matching the existing deactivate_user
    behaviour in users.py, also blocks login outright -- the employee
    record, sales/activity history and audit history are untouched.
    """
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")
    if application.status != "accepted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only an accepted/active employee can be suspended")

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(application.user_id)
    if user is not None:
        if user.id == current_user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot suspend your own account")
        if current_user.role != "super_admin" and user.branch_id != current_user.branch_id:  # PATCHP3G-BRANCH
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this employee")
        if current_user.role != "super_admin" and (  # PATCHP3K-TARGET
            current_user.branch_id is None
            or user.branch_id != current_user.branch_id
            or user.role in ("super_admin", "branch_manager")
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this employee")
        user.is_active = False
        await user_repo.save(user)

    application.status = "suspended"
    application.reviewed_by = current_user.id
    application.reviewed_at = datetime.now(timezone.utc)
    application = await repo.save(application)
    log_audit_event(
        session, user_id=current_user.id, action="account_suspended",
        entity_type="staff_application", entity_id=application.id,
    )
    await session.commit()
    return _to_out(application)  # PATCHP3G-OUT


@router.post("/{application_id}/reactivate", response_model=StaffApplicationOut)
async def reactivate_application(
    application_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> StaffApplicationOut:  # PATCHP3G-OUT
    repo = StaffApplicationRepository(session)
    application = await repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")
    if application.status != "suspended":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only a suspended employee can be reactivated")

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(application.user_id)
    if user is not None:
        if current_user.role != "super_admin" and user.branch_id != current_user.branch_id:  # PATCHP3G-BRANCH
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this employee")
        if current_user.role != "super_admin" and (  # PATCHP3K-TARGET
            current_user.branch_id is None
            or user.branch_id != current_user.branch_id
            or user.role in ("super_admin", "branch_manager")
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this employee")
        user.is_active = True
        await user_repo.save(user)

    application.status = "accepted"
    application.reviewed_by = current_user.id
    application.reviewed_at = datetime.now(timezone.utc)
    application = await repo.save(application)
    log_audit_event(
        session, user_id=current_user.id, action="account_reactivated",
        entity_type="staff_application", entity_id=application.id,
    )
    await session.commit()
    return _to_out(application)  # PATCHP3G-OUT


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    application_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("review_staff_applications")),  # PATCHP3G-GUARDS
) -> None:
    """Permanently deletes the applicant's account, not just the
    application record -- same hard-delete rules as
    DELETE /users/{id} (infrastructure/db/user_deletion.py): blocked
    if they've actually done any business-transaction work, which
    for someone still under_review/rejected should essentially never
    be the case since they never had real access.
    """
    app_repo = StaffApplicationRepository(session)
    application = await app_repo.get_by_id(application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Application not found")

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(application.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Linked user account not found")
    if current_user.role != "super_admin" and user.branch_id != current_user.branch_id:  # PATCHP3G-BRANCH
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this employee")
    if user.id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
    if current_user.role != "super_admin" and (  # PATCHP3K-TARGET
        current_user.branch_id is None
        or user.branch_id != current_user.branch_id
        or user.role in ("super_admin", "branch_manager")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this employee")

    blockers = await deletion_blockers(session, user.id)
    if blockers:
        shown = ", ".join(blockers[:3]) + ("…" if len(blockers) > 3 else "")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete this account -- it has {shown} on record. "
                "Suspend the account instead to preserve that history."
            ),
        )

    deleted_name, deleted_email = user.full_name, user.email
    await hard_delete_user(session, user)
    log_audit_event(
        session, user_id=current_user.id, action="account_deleted",
        entity_type="user", entity_id=user.id,
        metadata={"deleted_name": deleted_name, "deleted_email": deleted_email},
    )
    await session.commit()
