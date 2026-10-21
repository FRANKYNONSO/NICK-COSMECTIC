$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$root = (Get-Location).Path

foreach ($f in 'dashboard.html', 'js\dashboard.js', 'css\style.css') {
  if (-not (Test-Path (Join-Path $root $f))) { throw "Run this from apps\web_admin - missing $f" }
}

# ---------------------------------------------------------------- backups
$bk = Join-Path $root ('_backup_revenue_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory $bk | Out-Null
Copy-Item (Join-Path $root 'dashboard.html')  $bk
Copy-Item (Join-Path $root 'js\dashboard.js') $bk
Copy-Item (Join-Path $root 'css\style.css')   $bk
Write-Host "Backups saved in $bk" -ForegroundColor Cyan

# ---------------------------------------------------------------- read everything first
$htmlPath = Join-Path $root 'dashboard.html'
$jsPath   = Join-Path $root 'js\dashboard.js'
$cssPath  = Join-Path $root 'css\style.css'
$html = [IO.File]::ReadAllText($htmlPath, $utf8)
$js   = [IO.File]::ReadAllText($jsPath, $utf8)
$css  = [IO.File]::ReadAllText($cssPath, $utf8)

if ($js.Contains('rev-plot') -or $css.Contains('rev-plot')) { throw 'Revenue restyle already applied. Nothing changed.' }

# ---------------------------------------------------------------- dashboard.html
$pattern = '<div class="section-card">(?=\s*<div class="card-head-row">\s*<div class="card-head" style="margin-bottom:0;">\s*<span class="card-icon"><svg[^>]*><path d="M4 15\.5 9\.5 10l4 4L21 6"/>)'
$rx = [regex]::new($pattern)
if (-not $rx.IsMatch($html)) { throw 'Revenue section card not found in dashboard.html.' }
$html = $rx.Replace($html, '<div class="section-card revenue-analytics">', 1)

if (-not $html.Contains('<h3>Revenue analysis</h3>')) { throw 'Revenue title not found in dashboard.html.' }
$html = $html.Replace('<h3>Revenue analysis</h3>', '<h3>Revenue Analytics</h3>')
Write-Host "  dashboard.html: card class + title updated" -ForegroundColor Green

# ---------------------------------------------------------------- dashboard.js
$nl = if ($js.Contains("`r`n")) { "`r`n" } else { "`n" }
$startMarker = 'function renderRevenueAnalysis() {'
$endMarker   = "document.querySelectorAll('#revenue-tabs .tab-btn')"
$s = $js.IndexOf($startMarker)
$e = $js.IndexOf($endMarker)
if ($s -lt 0 -or $e -le $s) { throw 'Could not locate renderRevenueAnalysis in dashboard.js.' }
if (-not $js.Substring($s, $e - $s).Contains('bar-chart-empty')) { throw 'renderRevenueAnalysis does not look like the expected version.' }

$newFn = @'
// Round the top of the Y axis up to a "nice" number so gridlines land on clean values.
function revNiceMax(v) {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return step * pow;
}

function revAxisLabel(v) {
  const a = Math.abs(v);
  let s;
  if (a >= 1e6) s = Number((v / 1e6).toFixed(2)) + 'M';
  else if (a >= 1e3) s = Number((v / 1e3).toFixed(2)) + 'k';
  else s = String(Number(v.toFixed(2)));
  return '\u20A6' + s;
}

function renderRevenueAnalysis() {
  const container = document.getElementById('revenue-bar-chart');
  const subEl = document.getElementById('revenue-analysis-sub');
  if (!container) return;

  const periodLabels = { daily: 'last 7 days', weekly: 'last 8 weeks', monthly: 'last 6 months' };
  if (subEl) subEl.textContent = `Viewing ${periodLabels[currentRevenuePeriod]}`;

  const buckets = buildRevenueBuckets(currentRevenuePeriod);
  const periodTotal = buckets.reduce((t, b) => t + b.total, 0);
  const average = buckets.length ? periodTotal / buckets.length : 0;
  const best = buckets.reduce((max, b) => (b.total > max.total ? b : max), buckets[0]);

  document.getElementById('revenue-period-total').textContent = formatNaira(periodTotal);
  document.getElementById('revenue-average').textContent = formatNaira(average);
  document.getElementById('revenue-best-period').textContent = formatNaira(best ? best.total : 0);
  document.getElementById('revenue-best-period-date').textContent = best ? best.fullLabel : '';

  const isEmpty = !periodTotal;
  const axisMax = isEmpty ? 1 : revNiceMax(Math.max(...buckets.map((b) => b.total)));
  const STEPS = 5;
  let yLabels = '';
  let grid = '';
  for (let i = 0; i <= STEPS; i++) {
    const top = (i / STEPS) * 100;
    const val = axisMax - (axisMax / STEPS) * i;
    yLabels += `<span style="top:${top}%">${revAxisLabel(val)}</span>`;
    grid += `<i style="top:${top}%"></i>`;
  }

  let plot;
  let xLabels = '';
  if (isEmpty) {
    plot = `
      <div class="rev-empty">
        <div class="rev-empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="30" height="30"><rect x="3" y="12" width="5" height="9" rx="1"/><rect x="9.5" y="7" width="5" height="14" rx="1"/><rect x="16" y="3" width="5" height="18" rx="1"/></svg>
        </div>
        <div class="rev-empty-title">No revenue in this period yet</div>
        <div class="rev-empty-hint">Sales from POS will appear here automatically.</div>
      </div>`;
  } else {
    plot = '<div class="rev-bars">' + buckets.map((b) => {
      const pct = Math.max((b.total / axisMax) * 100, b.total > 0 ? 1.5 : 0);
      return `
      <div class="rev-col${b === best && b.total > 0 ? ' best' : ''}" title="${escapeHtml(b.fullLabel)}: ${formatNaira(b.total)}">
        ${b.total > 0 ? `<div class="rev-val" style="bottom:calc(${pct}% + 4px);">${revAxisLabel(b.total)}</div>` : ''}
        <div class="rev-fill" style="height:${pct}%;"></div>
      </div>`;
    }).join('') + '</div>';
    xLabels = buckets.map((b) => `<span>${escapeHtml(b.label)}</span>`).join('');
  }

  container.innerHTML = `
    <div class="rev-plot">
      <div class="rev-y">${yLabels}</div>
      <div class="rev-area"><div class="rev-grid">${grid}</div>${plot}</div>
      <div class="rev-x">${xLabels}</div>
    </div>`;
}
'@

$js = $js.Substring(0, $s) + ($newFn -replace "`r?`n", $nl) + $nl + $nl + $js.Substring($e)
Write-Host "  dashboard.js: renderRevenueAnalysis rewritten" -ForegroundColor Green

# ---------------------------------------------------------------- style.css
$newCss = @'

/* ---------------------------------------------------- Revenue Analytics (restyle) */
.section-card.revenue-analytics { padding: 0; overflow: hidden; }

.section-card.revenue-analytics .card-head-row {
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  margin: 0; padding: 24px 30px;
  background: linear-gradient(90deg, rgba(79,140,255,.12), rgba(52,211,153,.07));
  border-bottom: 1px solid var(--border);
}
.section-card.revenue-analytics .card-head { display: flex; align-items: center; gap: 16px; }
.section-card.revenue-analytics .card-icon {
  width: 56px; height: 56px; border-radius: 16px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(79,140,255,.18); color: var(--accent);
}
.section-card.revenue-analytics .card-icon svg { width: 26px; height: 26px; }
.section-card.revenue-analytics .card-titles h3 { margin: 0 0 4px; font-size: 21px; font-weight: 700; color: var(--ink); }
.section-card.revenue-analytics .card-titles .chart-sub { margin: 0; font-size: 15px; color: var(--ink-muted); }

/* segmented Daily / Weekly / Monthly toggle */
.section-card.revenue-analytics .tabs {
  margin: 0; padding: 4px; gap: 2px; border-radius: 14px;
  background: var(--bg); border: 1px solid var(--border);
}
.section-card.revenue-analytics .tabs .tab-btn {
  padding: 10px 20px; border-radius: 10px; font-size: 15px; font-weight: 500;
  color: var(--ink-muted); background: transparent; border: 1px solid transparent; box-shadow: none;
}
.section-card.revenue-analytics .tabs .tab-btn:hover:not(.active) { color: var(--ink); }
.section-card.revenue-analytics .tabs .tab-btn.active {
  background: var(--surface); color: var(--accent); border-color: var(--border); font-weight: 600;
}

/* three stat cells with dividers */
.section-card.revenue-analytics .stat-trio.plain { gap: 0; margin: 0; border-bottom: 1px solid var(--border); }
.section-card.revenue-analytics .stat-trio.plain .cell { padding: 22px 30px; border-left: none; }
.section-card.revenue-analytics .stat-trio.plain .cell:not(:first-child) { border-left: 1px solid var(--border); }
.section-card.revenue-analytics .stat-trio.plain .cell .label { font-size: 13px; letter-spacing: .05em; color: var(--ink-muted); margin-bottom: 8px; }
.section-card.revenue-analytics .stat-trio.plain .cell .value { font-size: 28px; font-weight: 700; color: var(--ink); }
.section-card.revenue-analytics .stat-trio.plain .cell .value.accent { color: var(--accent); }
.section-card.revenue-analytics .stat-trio.plain .cell .value.positive { color: var(--success); }
.section-card.revenue-analytics .stat-trio.plain .cell .sub { font-size: 13px !important; color: var(--ink-muted) !important; margin-top: 4px !important; }

/* inset chart panel */
.section-card.revenue-analytics .chart-container {
  box-sizing: border-box; height: 400px; margin: 30px;
  padding: 26px 24px 14px 12px;
  background: var(--bg); border: 1px solid var(--border); border-radius: 18px;
}
.section-card.revenue-analytics .bar-chart { display: block; height: 100%; padding: 0; }

.rev-plot { display: grid; grid-template-columns: 58px 1fr; grid-template-rows: 1fr 28px; height: 100%; }
.rev-y { position: relative; }
.rev-y span { position: absolute; right: 12px; transform: translateY(-50%); font-size: 12.5px; color: var(--ink-faint); white-space: nowrap; }
.rev-area { position: relative; }
.rev-grid i { position: absolute; left: 0; right: 0; height: 1px; background: var(--border); opacity: .7; }
.rev-bars { position: absolute; inset: 0; display: flex; gap: 10px; padding: 0 8px; }
.rev-col { flex: 1; min-width: 0; position: relative; display: flex; align-items: flex-end; justify-content: center; }
.rev-fill { width: 70%; max-width: 46px; background: var(--accent); border-radius: 6px 6px 0 0; min-height: 2px; transition: height .25s ease; }
.rev-col.best .rev-fill { background: var(--success); }
.rev-val { position: absolute; left: 0; right: 0; text-align: center; font-size: 11px; color: var(--ink-muted); white-space: nowrap; }
.rev-x { grid-column: 2; display: flex; gap: 10px; padding: 10px 8px 0; }
.rev-x span { flex: 1; min-width: 0; text-align: center; font-size: 12px; color: var(--ink-faint); white-space: nowrap; }

.rev-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 6px; }
.rev-empty-icon { width: 70px; height: 70px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: var(--surface); color: var(--ink-muted); margin-bottom: 10px; }
.rev-empty-title { font-size: 16px; font-weight: 600; color: var(--ink); }
.rev-empty-hint { font-size: 13.5px; color: var(--ink-muted); }

@media (max-width: 900px) {
  .section-card.revenue-analytics .card-head-row { padding: 18px 20px; }
  .section-card.revenue-analytics .stat-trio.plain { grid-template-columns: 1fr; }
  .section-card.revenue-analytics .stat-trio.plain .cell { padding: 16px 20px !important; }
  .section-card.revenue-analytics .stat-trio.plain .cell:not(:first-child) { border-left: none !important; border-top: 1px solid var(--border); }
  .section-card.revenue-analytics .chart-container { margin: 16px; height: 320px; padding: 20px 12px 10px 4px; }
  .rev-plot { grid-template-columns: 50px 1fr; }
}
'@

# ---------------------------------------------------------------- write everything
[IO.File]::WriteAllText($htmlPath, $html, $utf8)
[IO.File]::WriteAllText($jsPath, $js, $utf8)
[IO.File]::WriteAllText($cssPath, ($css + ($newCss -replace "`r?`n", $nl)), $utf8)

Write-Host "`nDone. Hard refresh the dashboard (Ctrl+Shift+R)." -ForegroundColor Green
Write-Host "Rollback: copy the files in $bk back over dashboard.html, js\dashboard.js and css\style.css."