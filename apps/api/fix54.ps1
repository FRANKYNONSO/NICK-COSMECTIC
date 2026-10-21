# fix54.ps1 -- branch price-override write path in update_product(),
# with audit logging both directions (global change vs branch override).
# Run from: C:\Dev\nick-cosmetics-python-platform\apps\api

$ErrorActionPreference = "Stop"
$file = "api\v1\routers\catalog.py"
if (-not (Test-Path $file)) { Write-Host "ERROR: run from apps\api" -ForegroundColor Red; exit 1 }

$content = Get-Content -Path $file -Raw
$edits = @(
    @{
        old = @'
from infrastructure.db.models import (
    Branch,
    Brand,
    Category,
    InventoryDeletionRecord,
    Product,
    ProductCategory,
    ProductImage,
    StockBatch,
    User,
)
'@
        new = @'
from infrastructure.db.audit import log_audit_event
from infrastructure.db.models import (
    Branch,
    BranchProductPrice,
    Brand,
    Category,
    InventoryDeletionRecord,
    Product,
    ProductCategory,
    ProductImage,
    StockBatch,
    User,
)
'@
    },
    @{
        old = '    body_data = body.model_dump(exclude_unset=True)
    if "cost_price_naira" in body_data:
        product.cost_price_kobo = Money.from_naira(body_data["cost_price_naira"]).kobo
    if "selling_price_naira" in body_data:
        product.selling_price_kobo = Money.from_naira(body_data["selling_price_naira"]).kobo'
        new = @'
    body_data = body.model_dump(exclude_unset=True)
    if "cost_price_naira" in body_data:
        product.cost_price_kobo = Money.from_naira(body_data["cost_price_naira"]).kobo
    if "selling_price_naira" in body_data:
        new_price_kobo = Money.from_naira(body_data["selling_price_naira"]).kobo
        if current_user.role == "super_admin":
            # Global price change -- resets every branch back to
            # following it, clearing any branch_manager overrides that
            # existed for this product (per spec: a branch override is
            # only meant to hold until the super admin sets a fresh
            # global price, not to survive it indefinitely).
            old_price_kobo = product.selling_price_kobo
            product.selling_price_kobo = new_price_kobo
            await session.execute(
                delete(BranchProductPrice).where(BranchProductPrice.product_id == product.id)
            )
            log_audit_event(
                session,
                user_id=current_user.id,
                action="global_price_changed",
                entity_type="product",
                entity_id=product.id,
                metadata={
                    "product_name": product.name,
                    "old_price_naira": Money(old_price_kobo).naira,
                    "new_price_naira": Money(new_price_kobo).naira,
                },
            )
        else:
            # branch_manager: this only ever affects their own branch --
            # the global Product.selling_price_kobo is left untouched.
            # POS/catalog reads must check this table before falling
            # back to the global price (see _load_price_overrides /
            # get_product below).
            existing_override = await session.execute(
                select(BranchProductPrice).where(
                    BranchProductPrice.branch_id == current_user.branch_id,
                    BranchProductPrice.product_id == product.id,
                )
            )
            override_row = existing_override.scalar_one_or_none()
            old_price_kobo = override_row.selling_price_kobo if override_row else product.selling_price_kobo
            if override_row is None:
                override_row = BranchProductPrice(
                    branch_id=current_user.branch_id,
                    product_id=product.id,
                    selling_price_kobo=new_price_kobo,
                    set_by=current_user.id,
                )
                session.add(override_row)
            else:
                override_row.selling_price_kobo = new_price_kobo
                override_row.set_by = current_user.id
            branch = await session.get(Branch, current_user.branch_id)
            log_audit_event(
                session,
                user_id=current_user.id,
                action="branch_price_override_changed",
                entity_type="product",
                entity_id=product.id,
                metadata={
                    "product_name": product.name,
                    "branch_id": str(current_user.branch_id),
                    "branch_name": branch.name if branch else None,
                    "old_price_naira": Money(old_price_kobo).naira,
                    "new_price_naira": Money(new_price_kobo).naira,
                },
            )'
'@
    }
)

$ok = $true
foreach ($e in $edits) {
    $c = ([regex]::Matches($content, [regex]::Escape($e.old))).Count
    if ($c -ne 1) {
        Write-Host "SKIPPED: found $c match(es) (need exactly 1) for:`n---`n$($e.old)`n---" -ForegroundColor Yellow
        $ok = $false
    }
}
if (-not $ok) { exit 1 }

foreach ($e in $edits) { $content = $content.Replace($e.old, $e.new) }

# Add the `delete` import from sqlalchemy if not already present
if ($content -notmatch '(?m)^from sqlalchemy import.*\bdelete\b') {
    if ($content -match '(?m)^from sqlalchemy import (.+)$') {
        $oldImportLine = $Matches[0]
        $newImportLine = $oldImportLine -replace '^from sqlalchemy import ', 'from sqlalchemy import delete, '
        $content = $content.Replace($oldImportLine, $newImportLine)
        Write-Host "Also added 'delete' to the sqlalchemy import line." -ForegroundColor Cyan
    } else {
        Write-Host "WARNING: could not find 'from sqlalchemy import ...' line to add 'delete' to -- add it manually." -ForegroundColor Red
    }
}

Set-Content -Path $file -Value $content -NoNewline
Write-Host "OK: patched $file" -ForegroundColor Green