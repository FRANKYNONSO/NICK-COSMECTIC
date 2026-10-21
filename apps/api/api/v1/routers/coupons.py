import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from api.dependencies import get_current_user, get_db_session, require_permission
from api.v1.schemas.coupons import CouponIssueRequest, CouponOut, CouponRedeemRequest, CouponRefundSummary
from api.v1.schemas.sales import SaleItemOut
from infrastructure.db.models import Branch as ORMBranch
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import Coupon as CouponRow
from infrastructure.db.models import PendingChange
from infrastructure.db.models import Refund as ORMRefund
from infrastructure.db.models import Sale as ORMSale
from infrastructure.db.models import User
from infrastructure.db.repositories import CouponRepository
from nickcosmetics_core.application.coupon_service import generate_coupon_code
from nickcosmetics_core.domain.coupons.coupon import CouponStatus
from nickcosmetics_core.exceptions import (
    CouponAlreadyUsedError,
    CouponExpiredError,
    CouponNotFoundError,
    CouponVoidedError,
)

router = APIRouter(prefix="/coupons", tags=["coupons"])

# Spec: every generated coupon is only ever valid for 30 minutes --
# not a client-chosen number of days. Fixed here rather than left as
# a caller-supplied value so it can't be issued longer by mistake (or
# by an old client still sending valid_days). Raised from the original
# 20 minutes because a coupon relayed to a different branch realistically
# needs more time than same-branch use to find the customer, ring up
# items, and check out.
COUPON_VALIDITY_MINUTES = 30


def _effective_status(row: CouponRow) -> str:
    """`row.status` only changes when redeem/void is actually called,
    so a coupon that quietly passed its expires_at without ever being
    used still reads "active" in the column. Traceability views want
    to show that as expired, not active.
    """
    if row.status == CouponStatus.ACTIVE.value and row.expires_at <= datetime.now(timezone.utc):
        return "expired"
    return row.status


def _to_out(
    row: CouponRow,
    user_names: dict[uuid.UUID, str] | None = None,
    redeemed_sale: ORMSale | None = None,
    product_names: dict[uuid.UUID, str] | None = None,
    branch_names: dict[uuid.UUID, str] | None = None,
    refunds_by_sale: dict[uuid.UUID, list[ORMRefund]] | None = None,
    credit_codes: dict[uuid.UUID, str] | None = None,
) -> CouponOut:
    user_names = user_names or {}
    product_names = product_names or {}
    branch_names = branch_names or {}
    refunds_by_sale = refunds_by_sale or {}
    credit_codes = credit_codes or {}
    sale_refunds = refunds_by_sale.get(row.redeemed_sale_id) if row.redeemed_sale_id else None
    redeemed_branch_id = redeemed_sale.branch_id if redeemed_sale is not None else None
    return CouponOut(
        id=row.id,
        code=row.code,
        discount_amount_naira=row.discount_amount_kobo / 100,
        status=row.status,
        effective_status=_effective_status(row),
        expires_at=row.expires_at,
        issued_by=row.issued_by,
        issued_by_name=user_names.get(row.issued_by),
        redeemed_by=row.redeemed_by,
        redeemed_by_name=user_names.get(row.redeemed_by) if row.redeemed_by else None,
        redeemed_sale_id=row.redeemed_sale_id,
        redeemed_at=row.redeemed_at,
        created_at=row.created_at,
        redeemed_branch_id=redeemed_branch_id,
        redeemed_branch_name=branch_names.get(redeemed_branch_id) if redeemed_branch_id else None,
        credit_reference_code=(
            credit_codes.get(row.pending_change_id) if getattr(row, "pending_change_id", None) else None
        ),
        # Only populated on the /history view (and only for redeemed
        # coupons) -- lets the super admin open a coupon in the
        # traceability list and see exactly which items it was
        # applied to, the same way a receipt shows line items.
        redeemed_items=(
            [
                SaleItemOut(
                    id=i.id,
                    product_id=i.product_id,
                    stock_batch_id=i.stock_batch_id,
                    quantity=i.quantity,
                    unit_price_naira=i.unit_price_kobo / 100,
                    line_total_naira=i.line_total_kobo / 100,
                    product_name=product_names.get(i.product_id),
                )
                for i in redeemed_sale.items
            ]
            if redeemed_sale is not None
            else None
        ),
        refunds=(
            [
                CouponRefundSummary(
                    id=rf.id,
                    amount_naira=rf.amount_kobo / 100,
                    status=rf.status or "completed",
                    reason=rf.reason,
                    created_at=rf.created_at,
                )
                for rf in sale_refunds
            ]
            if sale_refunds is not None
            else None
        ),
    )


@router.post("/issue", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
async def issue_coupon(
    body: CouponIssueRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_coupons")),
) -> CouponOut:
    repo = CouponRepository(session)

    # PATCH3C-COUPON-SERVER (issue side)
    amount_kobo = round(body.discount_amount_naira * 100)
    linked_change_id = None
    ref = (body.reference_code or "").strip().upper()
    if ref:
        change = (
            await session.execute(
                select(PendingChange).where(PendingChange.reference_code == ref).with_for_update()
            )
        ).scalar_one_or_none()
        if change is None or change.status != "outstanding":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No outstanding credit with this reference code.")
        if current_user.branch_id is not None and change.branch_id != current_user.branch_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This credit belongs to another branch.")
        live_id = await session.scalar(
            select(CouponRow.id)
            .where(
                CouponRow.pending_change_id == change.id,
                CouponRow.status == CouponStatus.ACTIVE.value,
                CouponRow.expires_at > datetime.now(timezone.utc),
            )
            .limit(1)
        )
        if live_id is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A discount code for this credit is still valid. Try again when it expires.",
            )
        amount_kobo = int(change.amount_kobo)
        linked_change_id = change.id

    # Vanishingly unlikely, but a code collision would silently
    # overwrite an existing coupon's uniqueness constraint -- retry a
    # couple of times rather than surface a raw 500 to the cashier.
    code = generate_coupon_code()
    for _ in range(3):
        if await repo.get_by_code(code) is None:
            break
        code = generate_coupon_code()
    else:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not generate a unique coupon code")

    row = CouponRow(
        code=code,
        discount_amount_kobo=amount_kobo,
        status=CouponStatus.ACTIVE.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=COUPON_VALIDITY_MINUTES),
        issued_by=current_user.id,
        pending_change_id=linked_change_id,
    )
    row = await repo.create(row)
    return _to_out(row)


@router.get("", response_model=list[CouponOut])
async def list_active_coupons(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[CouponOut]:
    """Any authenticated staff can list active coupons -- this is what
    the desktop POS's cache_sync.pull_coupons() calls to refresh the
    local offline cache, and any cashier needs to be able to trigger
    that sync, not just super_admin.
    """
    repo = CouponRepository(session)
    rows = await repo.list_active()
    # A coupon whose validity window has quietly lapsed still reads
    # "active" in the raw status column until something explicitly
    # marks it otherwise (see _effective_status above). POS calls this
    # endpoint to validate a typed-in code before applying a discount,
    # so it must never hand back a code that's already dead -- filter
    # to only the ones still genuinely live.
    rows = [r for r in rows if _effective_status(r) == "active"]
    return [_to_out(r) for r in rows]


@router.get("/history", response_model=list[CouponOut])
async def coupon_history(
    issued_by: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_coupons")),
) -> list[CouponOut]:
    """Traceability view for the super admin. Spec: a coupon that was
    generated but never used should drop off this history the moment
    it expires -- only (a) coupons still active/running, and (b)
    coupons that were actually redeemed (kept permanently, even long
    after their 30-minute window, since they're now a record of a
    real sale) belong here. An expired-and-never-used code is filtered
    out entirely rather than shown as "expired". `issued_by` optionally
    narrows the list to codes generated by one staff member.

    Branch scoping (spec section 2 -- coupon traceability across
    branches): a coupon has no branch of its own -- only a *redemption*
    does, via the sale it was applied to. A non-super-admin whose
    account is pinned to one branch only sees redemptions from that
    branch, plus any coupon still active/running.

    For a super admin, this view is deliberately branch-agnostic no
    matter which branch is currently selected on their dashboard --
    "no matter the branch... he should be able to trace [it] even if
    it is in that branch or not." The UI still sends whatever branch
    is currently selected as `branch_id` (same as every other report),
    but that's ignored here: a super admin always sees every
    redemption everywhere on this one screen, so a code can be traced
    end-to-end regardless of which branch they happen to be viewing.
    """
    effective_branch_id = (
        current_user.branch_id if current_user.role != "super_admin" else None
    )

    repo = CouponRepository(session)
    rows = await repo.list_history(issued_by=issued_by, limit=limit)
    # Traceability's job is tracing usage, not listing every code ever
    # generated -- a coupon that was never redeemed (whether it's still
    # running or has since expired unused) doesn't belong here at all.
    rows = [r for r in rows if r.redeemed_by is not None]
    if not rows:
        return []

    user_ids = {r.issued_by for r in rows} | {r.redeemed_by for r in rows if r.redeemed_by}
    user_names: dict[uuid.UUID, str] = {}
    if user_ids:
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        user_names = {u.id: u.full_name for u in users_result.scalars().all()}

    # For every redeemed coupon, pull its sale's line items so the
    # admin can view "what was this used on", the same way a receipt
    # shows items -- one query for all sales, not N+1.
    sale_ids = {r.redeemed_sale_id for r in rows if r.redeemed_sale_id}
    sales_by_id: dict[uuid.UUID, ORMSale] = {}
    product_names: dict[uuid.UUID, str] = {}
    branch_names: dict[uuid.UUID, str] = {}
    refunds_by_sale: dict[uuid.UUID, list[ORMRefund]] = {}
    if sale_ids:
        refunds_result = await session.execute(select(ORMRefund).where(ORMRefund.sale_id.in_(sale_ids)))
        for rf in refunds_result.scalars().all():
            refunds_by_sale.setdefault(rf.sale_id, []).append(rf)
        from api.v1.routers.sales import _resolve_product_names  # local import: avoid a routers<->routers cycle

        sales_result = await session.execute(
            select(ORMSale).options(selectinload(ORMSale.items)).where(ORMSale.id.in_(sale_ids))
        )
        sales_by_id = {s.id: s for s in sales_result.scalars().all()}
        all_items = [item for sale in sales_by_id.values() for item in sale.items]
        product_names = await _resolve_product_names(session, all_items)

        branch_ids = {s.branch_id for s in sales_by_id.values() if s.branch_id}
        if branch_ids:
            branches_result = await session.execute(select(ORMBranch).where(ORMBranch.id.in_(branch_ids)))
            branch_names = {b.id: b.name for b in branches_result.scalars().all()}

    if effective_branch_id is not None:
        # Every row here is a redemption now (rows without a redeemer
        # were dropped above), so branch scoping is a straight match
        # against the branch the redeeming sale happened in.
        rows = [
            r
            for r in rows
            if r.redeemed_sale_id is not None
            and sales_by_id.get(r.redeemed_sale_id) is not None
            and sales_by_id[r.redeemed_sale_id].branch_id == effective_branch_id
        ]

    # PATCH7A-COUPON-CREDIT-LINE: CHG codes of coupons made from a store credit
    # (one read-only query for the whole list, no change to repositories.py).
    change_ids = {r.pending_change_id for r in rows if getattr(r, "pending_change_id", None)}
    credit_codes: dict[uuid.UUID, str] = {}
    if change_ids:
        codes_result = await session.execute(
            select(PendingChange.id, PendingChange.reference_code).where(PendingChange.id.in_(change_ids))
        )
        credit_codes = {cid: ref for cid, ref in codes_result.all() if ref}

    return [
        _to_out(
            r,
            user_names,
            redeemed_sale=sales_by_id.get(r.redeemed_sale_id) if r.redeemed_sale_id else None,
            product_names=product_names,
            branch_names=branch_names,
            refunds_by_sale=refunds_by_sale,
            credit_codes=credit_codes,
        )
        for r in rows
    ]


@router.post("/redeem", response_model=CouponOut)
async def redeem_coupon(
    body: CouponRedeemRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> CouponOut:
    """Redeems a coupon for an online checkout. The desktop app's
    offline path redeems locally first (via CouponService against
    LocalCouponRepository) and this endpoint isn't yet wired into the
    outbox sync to reconcile that redemption up to the cloud -- see
    the note in sync/outbox_sync.py once that's built out. This
    endpoint exists now so a direct online checkout (or a future web
    POS) has a working redeem path without waiting on that sync work.
    """
    repo = CouponRepository(session)
    code = body.code.strip().upper()
    row = await repo.get_by_code(code)

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(CouponNotFoundError(code)))
    if row.status == CouponStatus.REDEEMED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(CouponAlreadyUsedError(code)))
    if row.status == CouponStatus.VOIDED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(CouponVoidedError(code)))
    if row.status == CouponStatus.EXPIRED.value or row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(CouponExpiredError(code)))

    # Guard 1: quick sanity check against the client-supplied sale total --
    # catches an obviously wrong/spoofed value before we bother hitting
    # the sales table.
    sale_total_kobo = round(body.sale_total_naira * 100)
    if row.discount_amount_kobo > sale_total_kobo:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Coupon discount ({row.discount_amount_kobo / 100} NGN) exceeds "
                f"the reported sale total ({body.sale_total_naira} NGN)"
            ),
        )

    # Guard 2: authoritative check against the actual persisted sale --
    # this is the one that can't be spoofed by the client. Confirms the
    # discount that was really applied at checkout matches this coupon's
    # discount, not just some number the request happened to send.
    orm_sale = await session.get(ORMSale, body.sale_id)
    if orm_sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Sale {body.sale_id} not found")
    if orm_sale.discount_kobo < row.discount_amount_kobo:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Coupon discount ({row.discount_amount_kobo / 100} NGN) exceeds "
                f"the total discount applied on sale {body.sale_id} "
                f"({orm_sale.discount_kobo / 100} NGN) -- the sale must account for at "
                f"least the coupon's discount."
            ),
        )

    # PATCH3C-COUPON-SERVER (redeem side): a coupon made from a store credit
    # settles that credit in the SAME commit, after re-checking both rows.
    if row.pending_change_id is not None:
        row = (
            await session.execute(
                select(CouponRow)
                .where(CouponRow.id == row.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if row.status != CouponStatus.ACTIVE.value:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(CouponAlreadyUsedError(code)))
        linked = (
            await session.execute(
                select(PendingChange)
                .where(PendingChange.id == row.pending_change_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if linked is None or linked.status != "outstanding":
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="The credit behind this code is no longer outstanding."
            )
        linked.status = "settled"
        linked.settlement_method = "coupon"
        linked.settled_by = current_user.id
        linked.settled_at = datetime.now(timezone.utc)
        log_audit_event(
            session,
            user_id=current_user.id,
            action="pending_change_settled_by_coupon",
            entity_type="pending_change",
            entity_id=linked.id,
            metadata={"coupon_code": row.code, "sale_id": str(body.sale_id)},
        )

    row.status = CouponStatus.REDEEMED.value
    row.redeemed_by = current_user.id
    row.redeemed_sale_id = body.sale_id
    row.redeemed_at = datetime.now(timezone.utc)
    row = await repo.save(row)
    return _to_out(row)

@router.post("/{code}/void", response_model=CouponOut)
async def void_coupon(
    code: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("manage_coupons")),
) -> CouponOut:
    repo = CouponRepository(session)
    row = await repo.get_by_code(code.strip().upper())
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(CouponNotFoundError(code)))
    if row.status == CouponStatus.REDEEMED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(CouponAlreadyUsedError(code)))

    row.status = CouponStatus.VOIDED.value
    row = await repo.save(row)
    return _to_out(row)
