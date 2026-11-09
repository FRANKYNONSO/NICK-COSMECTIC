"""Downloadable account statements -- a chronological CSV of a staff
member's (or the whole store's) sales and credit activity across a
chosen date range. Spec: "if we want to gather the historical work
for an individual staff... filter the duration of the time period we
want... download it as an account statement of her performance."

Gated on the `download_statements` permission -- on by default for
super_admin/branch_manager, but a plain permission like any other, so
a super admin can extend it to an individual staff account by editing
(or creating) a role that includes it, via the existing Roles page.
"""
import csv
import io
import uuid
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db_session, require_permission
from infrastructure.db.models import Branch as ORMBranch
from infrastructure.db.models import CreditEntry, Sale as ORMSale, User
from nickcosmetics_core.domain.money import Money

router = APIRouter(prefix="/statements", tags=["statements"])

_REQUIRE_STATEMENTS_ACCESS = require_permission("download_statements")

_HEADERS = ["Date & time", "Type", "Branch", "Staff", "Reference", "Status", "Amount (NGN)"]


def _build_rows(sales, credit_entries, names_by_id, branch_names) -> list[list[str]]:
    rows: list[list[str]] = []
    for s in sales:
        rows.append([
            s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "",
            "Sale" if s.status != "booking" else "Open booking",
            branch_names.get(s.branch_id, ""),
            names_by_id.get(s.served_by, "") if s.served_by else "",
            str(s.id)[:8].upper(),
            s.status,
            f"{Money(s.total_kobo).naira:.2f}",
        ])
    for c in credit_entries:
        rows.append([
            c.opened_at.strftime("%Y-%m-%d %H:%M") if c.opened_at else "",
            "Credit opened",
            branch_names.get(c.branch_id, ""),
            names_by_id.get(c.opened_by, ""),
            str(c.id)[:8].upper(),
            c.status,
            f"{Money(c.amount_kobo).naira:.2f}",
        ])
    return rows


def _render_csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_HEADERS)
    writer.writerows(rows)
    return buffer.getvalue()


def _render_pdf(rows: list[list[str]], title: str) -> bytes:
    # Not everyone wants CSV -- same data, formatted as a simple table
    # PDF instead. fpdf2 is a small pure-Python dependency (no system
    # libs like wkhtmltopdf/Cairo needed), so it stays easy to deploy.
    from fpdf import FPDF

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    import pdf_branding as _brand  # Prestige / Nick Cosmetics logo
    _logo = _brand.lockup_path()
    if _logo:
        pdf.image(_logo, x=10, y=8, h=12)
        pdf.set_y(24)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, title, ln=1)
    pdf.set_font("Helvetica", "", 9)

    col_widths = [32, 26, 40, 40, 26, 26, 30]
    pdf.set_font("Helvetica", "B", 9)
    for header, width in zip(_HEADERS, col_widths):
        pdf.cell(width, 8, header, border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8.5)
    for row in rows:
        for value, width in zip(row, col_widths):
            pdf.cell(width, 7, str(value), border=1)
        pdf.ln()

    if not rows:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 8, "No activity in this date range.", ln=1)

    return bytes(pdf.output())


@router.get("/export")
async def export_statement(
    staff_id: uuid.UUID | None = Query(default=None, description="Limit to one staff member; omit for the whole store"),
    branch_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    format: str = Query(default="csv", pattern="^(csv|pdf)$", description="'csv' or 'pdf'"),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_REQUIRE_STATEMENTS_ACCESS),
) -> StreamingResponse:
    start_dt = datetime.combine(date_from, time.min) if date_from else None
    end_dt = datetime.combine(date_to, time.max) if date_to else None

    staff_name = "all-staff"
    if staff_id is not None:
        staff = await session.get(User, staff_id)
        staff_name = (staff.full_name if staff else str(staff_id)).replace(" ", "-")

    sale_conditions = [ORMSale.status != "cancelled"]
    if staff_id is not None:
        sale_conditions.append(ORMSale.served_by == staff_id)
    if branch_id is not None:
        sale_conditions.append(ORMSale.branch_id == branch_id)
    if start_dt is not None:
        sale_conditions.append(ORMSale.created_at >= start_dt)
    if end_dt is not None:
        sale_conditions.append(ORMSale.created_at <= end_dt)

    sales_result = await session.execute(
        select(ORMSale)
        .options(selectinload(ORMSale.items))
        .where(*sale_conditions)
        .order_by(ORMSale.created_at)
    )
    sales = sales_result.scalars().all()

    credit_conditions = []
    if staff_id is not None:
        credit_conditions.append(CreditEntry.opened_by == staff_id)
    if branch_id is not None:
        credit_conditions.append(CreditEntry.branch_id == branch_id)
    if start_dt is not None:
        credit_conditions.append(CreditEntry.opened_at >= start_dt)
    if end_dt is not None:
        credit_conditions.append(CreditEntry.opened_at <= end_dt)

    credit_result = await session.execute(
        select(CreditEntry).where(*credit_conditions).order_by(CreditEntry.opened_at)
    )
    credit_entries = credit_result.scalars().all()

    # Resolve served_by/opened_by user ids -> names in one pass rather
    # than N+1 lazy-loading them off each row.
    user_ids = {s.served_by for s in sales if s.served_by} | {c.opened_by for c in credit_entries}
    names_by_id: dict[uuid.UUID, str] = {}
    if user_ids:
        rows = await session.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))
        names_by_id = dict(rows.all())

    branch_ids = {s.branch_id for s in sales} | {c.branch_id for c in credit_entries}
    branch_names: dict[uuid.UUID, str] = {}
    if branch_ids:
        rows = await session.execute(select(ORMBranch.id, ORMBranch.name).where(ORMBranch.id.in_(branch_ids)))
        branch_names = dict(rows.all())

    table_rows = _build_rows(sales, credit_entries, names_by_id, branch_names)
    date_range = f"{date_from or 'start'}_to_{date_to or 'now'}"

    if format == "pdf":
        title = f"Account statement — {staff_name.replace('-', ' ')} ({date_range.replace('_', ' ')})"
        pdf_bytes = _render_pdf(table_rows, title)
        filename = f"statement_{staff_name}_{date_range}.pdf"
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    csv_text = _render_csv(table_rows)
    filename = f"statement_{staff_name}_{date_range}.csv"
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
