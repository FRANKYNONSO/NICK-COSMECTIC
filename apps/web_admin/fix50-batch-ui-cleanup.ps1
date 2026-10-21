<#
fix50-batch-ui-cleanup.ps1
Nick Cosmetics - web_admin - 2026-09-22 walkthrough batch

SECTION 1 = safe, applied automatically (self-contained help-text removal/replacement)
SECTION 2 = diagnostics only (prints matches + context, changes NOTHING) for items
            that need real markup in front of us before we touch them.

Run this, then paste me the console output (especially Section 2) and I'll
write fix51 with exact, targeted edits for those items.
#>

$base = "C:\Dev\nick-cosmetics-python-platform\apps\web_admin"

if (-not (Test-Path $base)) {
    Write-Host "Path not found: $base  (edit `$base at the top of the script)" -ForegroundColor Red
    exit 1
}

$files = Get-ChildItem -Path $base -Recurse -Include *.html, *.js -File

function Remove-Match {
    param(
        [string]$Label,
        [string]$Pattern
    )
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    $total = 0
    foreach ($f in $files) {
        $content = Get-Content -Raw -Path $f.FullName
        $m = [regex]::Matches($content, $Pattern, 'Singleline')
        if ($m.Count -gt 0) {
            $new = [regex]::Replace($content, $Pattern, '', 'Singleline')
            Set-Content -Path $f.FullName -Value $new -NoNewline
            Write-Host "  Fixed: $($f.Name)  ($($m.Count) match)" -ForegroundColor Green
            $total += $m.Count
        }
    }
    if ($total -eq 0) {
        Write-Host "  No match anywhere - wording differs from what I guessed, needs manual check." -ForegroundColor Yellow
    }
}

function Replace-Match {
    param(
        [string]$Label,
        [string]$Pattern,
        [string]$Replacement
    )
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    $total = 0
    foreach ($f in $files) {
        $content = Get-Content -Raw -Path $f.FullName
        $m = [regex]::Matches($content, $Pattern, 'Singleline')
        if ($m.Count -gt 0) {
            $new = [regex]::Replace($content, $Pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($x) $Replacement }, 'Singleline')
            Set-Content -Path $f.FullName -Value $new -NoNewline
            Write-Host "  Fixed: $($f.Name)  ($($m.Count) match)" -ForegroundColor Green
            $total += $m.Count
        }
    }
    if ($total -eq 0) {
        Write-Host "  No match anywhere - wording differs from what I guessed, needs manual check." -ForegroundColor Yellow
    }
}

function Diagnose {
    param(
        [string]$Label,
        [string]$Pattern
    )
    Write-Host "`n--- DIAGNOSE: $Label ---" -ForegroundColor Magenta
    $found = $false
    foreach ($f in $files) {
        $content = Get-Content -Raw -Path $f.FullName
        $m = [regex]::Matches($content, $Pattern, 'Singleline, IgnoreCase')
        if ($m.Count -gt 0) {
            $found = $true
            Write-Host "  $($f.Name)  ($($m.Count) match)"
            foreach ($mm in $m) {
                $snippet = ($mm.Value -replace '\s+', ' ')
                if ($snippet.Length -gt 200) { $snippet = $snippet.Substring(0, 200) + " ..." }
                Write-Host "    -> $snippet"
            }
        }
    }
    if (-not $found) {
        Write-Host "  Nothing found with this guess-pattern. I'll need a filename or a snippet from you." -ForegroundColor Yellow
    }
}

# ============================================================
# SECTION 1 - APPLIED
# ============================================================

Remove-Match -Label "POS: remove 'discount set by coupon only' notice" `
  -Pattern '(?is)<(p|small|span|div)[^>]*>\s*Discount is set by (an )?applied coupon only.*?(Ask management for (a )?coupon code[^<]*)?</\1>\s*'

Remove-Match -Label "Add Product: remove 'type the menu directly / Brand is optional' hint" `
  -Pattern '(?is)<(p|small|span|div)[^>]*>\s*(Type the menu directly[^<]*?)?Brand is optional and only helps with search.{0,20}filtering.*?</\1>\s*'

Remove-Match -Label "Add Product: remove 'selecting a suggestion fills details' hint" `
  -Pattern '(?is)<(p|small|span|div)[^>]*>\s*Selecting a suggestion below fills.*?instead of creating a duplicate.*?</\1>\s*'

Replace-Match -Label "Add Purchase Invoice: clearer description text" `
  -Pattern '(?is)What was bought from a supplier,?\s*typed in line by line,?\s*with an optional photo of the paper invoice\.?' `
  -Replacement 'Record what you purchased from a supplier - add each item as a line, then attach a photo of the paper invoice if you have one.'

# ============================================================
# SECTION 2 - DIAGNOSTIC ONLY (no files changed)
# ============================================================

Diagnose -Label "Price field '?' tooltip" `
  -Pattern '(?is)(price)[^<>]{0,80}\?'

Diagnose -Label "View All Inventory arrow/button" `
  -Pattern '(?is)View All Inventory[^<]*'

Diagnose -Label "Restock: Add New Item form" `
  -Pattern '(?is)Add (a )?New Item[^<]{0,40}'

Diagnose -Label "Restock Products table select-all checkbox" `
  -Pattern '(?is)<input[^>]*type=["'']checkbox["''][^>]*(select-?all|selectAll)[^>]*>'

Diagnose -Label "Reports: standalone Performance nav item" `
  -Pattern '(?is)<[^>]*>\s*Performance\s*</[^>]*>'

Diagnose -Label "Reports: 'Generated [date]' + live dashboard link" `
  -Pattern '(?is)Generated[^<]{0,40}|live dashboard[^<]{0,20}'

Diagnose -Label "Branches: closing time search field" `
  -Pattern '(?is)closing.?time'

Diagnose -Label "Coupon invalid message copy" `
  -Pattern '(?is)(coupon[^<]{0,15}not found|not active)[^<]{0,60}'

Write-Host "`n============================================================"
Write-Host "Section 1 changes are applied. Section 2 is read-only."
Write-Host "Copy the DIAGNOSE output back to Claude to get fix51 (precise, targeted edits)."
Write-Host "============================================================"
