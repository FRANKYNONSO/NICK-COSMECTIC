# fix50b.ps1 -- branch-scope list_audit_log() (Booking/Transaction History leak)
# Regex-based, since exact-string matching failed on line-ending differences.
# Run from: C:\Dev\nick-cosmetics-python-platform\apps\api

$ErrorActionPreference = "Stop"
$file = "api\v1\routers\sales.py"

if (-not (Test-Path $file)) {
    Write-Host "ERROR: $file not found. Run this script from the apps\api folder." -ForegroundColor Red
    exit 1
}

# Read raw bytes-as-string so we don't let PowerShell normalize line endings on read.
$content = [System.IO.File]::ReadAllText((Resolve-Path $file))

$pattern = '(?s)@router\.get\("/audit-log".*?stmt = stmt\.limit\(min\(limit, 500\)\)'

if ($content -notmatch $pattern) {
    Write-Host "SKIPPED: regex did not match in $file -- no changes made." -ForegroundColor Yellow
    exit 1
}

$replacement = @"
@router.get("/audit-log", response_model=list[AuditLogOut])
async def list_audit_log(
    entity_type: str | None = None,
    branch_id: uuid.UUID | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("view_all_reports")),
) -> list[AuditLogOut]:
    """Accountability trail for who voided/refunded a sale or applied
    a discount, and when. Same permission gate as the reports
    endpoints -- this is manager-level visibility, not for every
    cashier to browse.

    AuditLog has no branch_id column of its own, so a non-super-admin
    is branch-scoped by joining through entity_id back to Sale (every
    booking/sale-related event -- deposit taken, payment added,
    completed, item changed, cancelled, discount applied -- has
    entity_type='sale' and entity_id=Sale.id). Any non-sale audit
    entry (e.g. a refund mark-processed event) has no branch it can be
    safely attributed to, so it's excluded entirely for a
    non-super-admin rather than shown unscoped -- same "exclude if we
    can't prove it's safe" rule the rest of the branch-scoping fixes
    follow.
    """
    stmt = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.created_at.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if current_user.role != "super_admin":
        stmt = stmt.join(ORMSale, ORMSale.id == AuditLog.entity_id).where(
            AuditLog.entity_type == "sale",
            ORMSale.branch_id == current_user.branch_id,
        )
    elif branch_id:
        stmt = stmt.join(ORMSale, ORMSale.id == AuditLog.entity_id).where(
            AuditLog.entity_type == "sale",
            ORMSale.branch_id == branch_id,
        )
    stmt = stmt.limit(min(limit, 500))
"@

$updated = [regex]::Replace($content, $pattern, [System.Text.RegularExpressions.Regex]::Escape($replacement).Replace('\ ', ' '))
# Use literal replacement (not scriptblock evaluator) to avoid the fix30-style
# newline-corruption bug -- .Replace() below is the safe two-arg overload.
$updated = [regex]::Replace($content, $pattern, ($replacement -replace '\$', '$$'))

[System.IO.File]::WriteAllText((Resolve-Path $file), $updated)
Write-Host "OK: patched $file" -ForegroundColor Green