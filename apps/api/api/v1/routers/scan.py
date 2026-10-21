import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db_session
from api.v1.schemas.scan import ScanPollOut, ScanSessionOut, ScanSubmitRequest
from infrastructure.db.models import ScanSession, User

router = APIRouter(prefix="/scan", tags=["scan"])

# Sessions are throwaway pairing codes, not accounts -- a short TTL
# means an old QR code left on a screen or a leaked link stops being
# useful on its own within a few hours, without needing any cleanup
# job (an expired row is simply never matched again; see
# get_scan_session_for_submit below).
_SESSION_TTL = timedelta(hours=4)


@router.post("/sessions", response_model=ScanSessionOut, status_code=status.HTTP_201_CREATED)
async def create_scan_session(
    branch_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ScanSession:
    """Called by the desktop till to start a pairing -- it then shows
    `code` (as text and/or a QR of the phone-scanner URL) for a
    cashier's phone to open and start scanning against.
    """
    code = secrets.token_hex(4)  # short enough to type by hand if the QR can't be scanned
    record = ScanSession(
        code=code,
        branch_id=branch_id,
        created_by=current_user.id,
        expires_at=datetime.now(timezone.utc) + _SESSION_TTL,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.post("/sessions/{code}/submit", status_code=status.HTTP_204_NO_CONTENT)
async def submit_scan(
    code: str,
    body: ScanSubmitRequest,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Called by the phone-scanner web page every time its camera
    decodes a barcode. Deliberately does NOT require staff auth (a
    cashier's own phone, on the shop wifi, briefly holding a pairing
    code is the trust boundary here -- matching a QR code someone can
    see on the till screen is already the same bar as a USB scanner
    plugged into that till), just a valid, unexpired session code.
    """
    stmt = select(ScanSession).where(ScanSession.code == code)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None or record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Scan session not found or expired")

    record.last_barcode = body.barcode.strip()
    record.last_scanned_at = datetime.now(timezone.utc)
    await session.commit()


@router.get("/sessions/{code}", response_model=ScanPollOut)
async def poll_scan_session(
    code: str,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> ScanPollOut:
    """Polled by the desktop till (every ~1s while a pairing dialog is
    open) to pick up whatever the phone most recently scanned.
    """
    stmt = select(ScanSession).where(ScanSession.code == code)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None or record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Scan session not found or expired")

    return ScanPollOut(code=record.code, barcode=record.last_barcode, scanned_at=record.last_scanned_at)