<#
fix51-diagnose-v2.ps1
Read-only. Changes NOTHING. Prints wide context around each target
so Claude can write exact fix51 edits next.
#>

$base = "C:\Dev\nick-cosmetics-python-platform\apps\web_admin"
if (-not (Test-Path $base)) {
    Write-Host "Path not found: $base" -ForegroundColor Red
    exit 1
}

function Dump {
    param(
        [string]$Label,
        [string]$RelativePath,
        [string]$Term,
        [int]$Before = 8,
        [int]$After = 8
    )
    $path = Join-Path $base $RelativePath
    Write-Host "`n===================================================="
    Write-Host "FILE: $RelativePath   TERM: '$Term'   ($Label)" -ForegroundColor Cyan
    Write-Host "===================================================="
    if (-not (Test-Path $path)) {
        Write-Host "  (file not found)" -ForegroundColor Yellow
        return
    }
    $hits = Select-String -Path $path -Pattern ([regex]::Escape($Term)) -Context $Before, $After -AllMatches
    if (-not $hits) {
        Write-Host "  (no match)" -ForegroundColor Yellow
        return
    }
    $i = 0
    foreach ($h in $hits) {
        $i++
        Write-Host "--- occurrence $i (line $($h.LineNumber)) ---"
        $h.Context.PreContext  | ForEach-Object { Write-Host "   $_" }
        Write-Host " > $($h.Line)" -ForegroundColor Green
        $h.Context.PostContext | ForEach-Object { Write-Host "   $_" }
    }
}

# --- Add Product hint text (didn't match in fix50, need real wording) ---
Dump -Label "Add Product hint - search 'Brand is optional'" -RelativePath "inventory.html" -Term "Brand is optional"
Dump -Label "Add Product hint - search 'Brand is optional'" -RelativePath "js\inventory.js" -Term "Brand is optional"
Dump -Label "Add Product hint - search 'suggestion below'" -RelativePath "inventory.html" -Term "suggestion"
Dump -Label "Add Product hint - search 'suggestion below'" -RelativePath "js\inventory.js" -Term "suggestion"

# --- Invoice description text ---
Dump -Label "Invoice description - search 'paper invoice'" -RelativePath "invoice.html" -Term "paper invoice"
Dump -Label "Invoice description - search 'paper invoice'" -RelativePath "js\invoice.js" -Term "paper invoice"

# --- Price ? tooltip ---
Dump -Label "Price tooltip" -RelativePath "inventory.html" -Term "Price ("

# --- View All Inventory arrow ---
Dump -Label "View all inventory arrow" -RelativePath "low-stock.html" -Term "View all inventory"

# --- Restock: Add new item form ---
Dump -Label "Restock add-new-item form" -RelativePath "restock-list.html" -Term "Add a new item not in your inventory" -Before 15 -After 25
Dump -Label "Restock add-new-item form (JS)" -RelativePath "js\restock-list.js" -Term "Add a new item not in your inventory" -Before 5 -After 20

# --- Restock: select-all checkbox ---
Dump -Label "Restock select-all checkbox" -RelativePath "restock-list.html" -Term 'id="restock-select-all"' -Before 10 -After 5

# --- Reports: Performance / generated-at / live dashboard ---
Dump -Label "Reports generated-at block" -RelativePath "reports.html" -Term "generated-at" -Before 15 -After 15
Dump -Label "Reports Live dashboard link" -RelativePath "reports.html" -Term "Live dashboard" -Before 10 -After 10
Dump -Label "Reports generated-at (JS)" -RelativePath "js\reports.js" -Term "generated-at" -Before 5 -After 10

# --- Search for "Performance" everywhere, broad, since prior tag-based guess found nothing ---
Write-Host "`n===================================================="
Write-Host "BROAD SEARCH: 'Performance' across every .html/.js file" -ForegroundColor Cyan
Write-Host "===================================================="
Get-ChildItem -Path $base -Recurse -Include *.html, *.js -File | ForEach-Object {
    $hits = Select-String -Path $_.FullName -Pattern "Performance" -Context 3, 3
    if ($hits) {
        foreach ($h in $hits) {
            Write-Host "`n-- $($_.Name) line $($h.LineNumber) --"
            $h.Context.PreContext  | ForEach-Object { Write-Host "   $_" }
            Write-Host " > $($h.Line)" -ForegroundColor Green
            $h.Context.PostContext | ForEach-Object { Write-Host "   $_" }
        }
    }
}

# --- Branches: closing time (js then html, need to see order/structure) ---
Dump -Label "Branches closing time (JS)" -RelativePath "js\branches.js" -Term "closing time" -Before 12 -After 12
Dump -Label "Branches closing time (staff.html)" -RelativePath "staff.html" -Term "closing time" -Before 12 -After 12

# --- Coupon invalid message, wider context to see the whole apply-coupon flow ---
Dump -Label "Coupon invalid message flow" -RelativePath "js\pos.js" -Term "Coupon not found or not active" -Before 20 -After 10

Write-Host "`n============================================================"
Write-Host "Done. Copy ALL of this output back to Claude for fix51."
Write-Host "============================================================"
