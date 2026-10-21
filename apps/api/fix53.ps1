# fix53.ps1 -- add BranchProductPrice model
# Run from: C:\Dev\nick-cosmetics-python-platform\apps\api

$ErrorActionPreference = "Stop"
$file = "infrastructure\db\models.py"
if (-not (Test-Path $file)) { Write-Host "ERROR: run from apps\api" -ForegroundColor Red; exit 1 }

$content = Get-Content -Path $file -Raw

$old = 'class SyncLog(Base):'
$new = @'
class BranchProductPrice(Base):
    """A branch_manager's per-branch price override for a product.

    Product.selling_price_kobo remains the single global/default price,
    editable only by super_admin -- see catalog.py::update_product().
    A row here means this specific branch sells the product at a
    different price than the global default; deleted (not merely set
    to the global value) the moment super_admin changes the global
    price for that product, so every branch reverts to following the
    new global price until a branch_manager overrides it again.
    """
    __tablename__ = "branch_product_prices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    selling_price_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SyncLog(Base):
'@

$count = ([regex]::Matches($content, [regex]::Escape($old))).Count
if ($count -ne 1) {
    Write-Host "SKIPPED: found $count match(es) (need exactly 1) for '$old'" -ForegroundColor Yellow
    exit 1
}
$content = $content.Replace($old, $new)
Set-Content -Path $file -Value $content -NoNewline
Write-Host "OK: patched $file" -ForegroundColor Green