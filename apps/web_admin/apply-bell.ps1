$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)   # UTF-8 without BOM, keeps your existing symbols intact
$root = (Get-Location).Path

foreach ($f in 'dashboard.html', 'js\dashboard.js', 'css\style.css') {
  if (-not (Test-Path (Join-Path $root $f))) { throw "Run this from apps\web_admin - missing $f" }
}

# ---------------------------------------------------------------- backups
$bk = Join-Path $root ('_backup_bell_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory $bk | Out-Null
Copy-Item (Join-Path $root 'dashboard.html')  $bk
Copy-Item (Join-Path $root 'js\dashboard.js') $bk
Copy-Item (Join-Path $root 'css\style.css')   $bk
Write-Host "Backups saved in $bk" -ForegroundColor Cyan

# ------------------------------------------- warn about JS that touches removed elements
Write-Host "`nJS references to elements being removed (should only be dashboard.js banner code):" -ForegroundColor Cyan
Select-String -Path (Join-Path $root 'js\*.js') -Pattern 'full-reports-link', 'performance-records-bar', 'unclosed-shift-banner' |
  ForEach-Object { "{0}:{1}: {2}" -f $_.Filename, $_.LineNumber, $_.Line.Trim() }

# ---------------------------------------------------------------- dashboard.html
$htmlPath = Join-Path $root 'dashboard.html'
$html = [IO.File]::ReadAllText($htmlPath, $utf8)

function Remove-Block($text, $pattern, $name) {
  if (-not [regex]::IsMatch($text, $pattern, 'Singleline')) { throw "HTML block not found: $name (already applied?)" }
  Write-Host "  removed: $name" -ForegroundColor Green
  return [regex]::Replace($text, $pattern, '', 'Singleline')
}

Write-Host "`nEditing dashboard.html" -ForegroundColor Cyan
$html = Remove-Block $html '[ \t]*<a href="#" id="full-reports-link"[^>]*>.*?</a>\r?\n' 'Full reports link'
$html = Remove-Block $html '[ \t]*<div id="unclosed-shift-banner".*?</div>\r?\n(\r?\n)?' 'Shift report banner'
$html = Remove-Block $html '[ \t]*<div class="admin-only-section" id="performance-records-bar".*?</div>\r?\n(\r?\n)?' 'View history bar'

# ---------------------------------------------------------------- dashboard.js
$jsPath = Join-Path $root 'js\dashboard.js'
$jsText = [IO.File]::ReadAllText($jsPath, $utf8)
if ($jsText.Contains('notifState')) { throw 'dashboard.js already has the bell changes. Nothing applied.' }
$nl = if ($jsText.Contains("`r`n")) { "`r`n" } else { "`n" }
$lines = [System.Collections.Generic.List[string]]::new([string[]]($jsText -split '\r?\n'))

# Anchor checks (1-based line numbers from your output)
if ($lines[329] -notlike 'async function refreshUnclosedShiftBanner*') { throw 'Line 330 is not refreshUnclosedShiftBanner. File changed; send me the new output.' }
if ($lines[348].Trim() -ne '}')                                        { throw 'Line 349 is not the end of refreshUnclosedShiftBanner.' }
if ($lines[959] -notlike 'async function loadNotificationsSummary*')   { throw 'Line 960 is not loadNotificationsSummary.' }
if ($lines[979] -notlike 'function renderNotificationsPanel*')         { throw 'Line 980 is not renderNotificationsPanel.' }
if ($lines[1002].Trim() -ne '}')                                       { throw 'Line 1003 is not the end of renderNotificationsPanel.' }

# ---- Block B: replaces lines 960-1003 (bell load + render, plus new ring/pop-up logic)
$blockB = @'
// Shared bell state lives on window so load order between the shift
// check and the summary poll never matters.
function notifState() {
  return (window.__notifState = window.__notifState || { items: [], total: 0, shift: null, active: [], popupTimer: null, lastPopupLabel: null });
}

// "Review shift report" now lives inside the bell instead of a banner.
NOTIF_ICON_MAP.unshift({ match: /shift report/i, color: '#7c9cff', svg: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M9 13h6M9 17h6"/>' });

async function loadNotificationsSummary() {
  const bellBtn = document.getElementById('notif-bell-btn');
  if (!bellBtn) return;
  try {
    const branchId = currentUser?.role === 'branch_manager' ? currentUser?.branch_id : currentBranchId();
    const summary = await Api.notificationsSummary(branchId);
    const state = notifState();
    state.items = summary.items || [];
    state.total = summary.total || 0;
    refreshNotificationsUi();
  } catch (err) {
    // Non-fatal -- leave the badge/panel as they were on the last
    // successful poll.
  }
}

function refreshNotificationsUi() {
  const state = notifState();
  const items = state.items.slice();
  if (state.shift) items.push(state.shift);
  const total = state.total + (state.shift ? state.shift.count : 0);
  const badge = document.getElementById('notif-badge');
  if (badge) {
    if (total > 0) {
      badge.textContent = total;
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  }
  renderNotificationsPanel(items);
  state.active = items.filter((i) => i.count === undefined || Number(i.count) > 0);
  updateBellRinging();
  startNotifPopupTimer();
}

function renderNotificationsPanel(items) {
  const panel = document.getElementById('notif-panel');
  if (!panel) return;
  if (!items.length) {
    panel.innerHTML = '<div class="notif-panel-empty">Nothing needs attention</div>';
    return;
  }
  panel.innerHTML =
    '<div style="padding:8px 10px;font-size:12.5px;font-weight:600;color:var(--ink);">Notifications</div>' +
    items.map((item) => {
      const icon = notifIconFor(item.label);
      return `
      <a href="${escapeHtml(item.href)}" class="notif-row" style="display:flex;align-items:center;gap:10px;padding:9px 10px;text-decoration:none;color:inherit;">
        <span style="width:30px;height:30px;border-radius:8px;background:${icon.color}22;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
          <svg viewBox="0 0 24 24" fill="none" stroke="${icon.color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">${icon.svg}</svg>
        </span>
        <span style="flex:1;min-width:0;">
          <span style="display:block;font-size:12.5px;font-weight:600;color:var(--ink);">${escapeHtml(item.label)}</span>
          <span style="display:block;font-size:11.5px;color:var(--ink-muted);">${escapeHtml(item.sub)}</span>
        </span>
        <span style="font-size:11px;color:${icon.color};white-space:nowrap;">${item.count} &rsaquo;</span>
      </a>`;
    }).join('');
}

// ---- Ringing bell: rings whenever anything is pending, pauses while the panel is open.
function updateBellRinging() {
  const btn = document.getElementById('notif-bell-btn');
  const panel = document.getElementById('notif-panel');
  if (!btn) return;
  const open = panel && panel.style.display !== 'none';
  btn.classList.toggle('bell-ringing', notifState().active.length > 0 && !open);
}
document.getElementById('notif-bell-btn')?.addEventListener('click', () => setTimeout(updateBellRinging, 0));
document.addEventListener('click', () => setTimeout(updateBellRinging, 0));

// ---- Random pop-up: every 10s, shows one pending category (e.g. "Low stock: 5").
function startNotifPopupTimer() {
  const state = notifState();
  if (state.popupTimer) return;
  state.popupTimer = setInterval(showNextNotifPopup, 10000);
}

function showNextNotifPopup() {
  const state = notifState();
  if (document.hidden || !state.active.length) return;
  const panel = document.getElementById('notif-panel');
  if (panel && panel.style.display !== 'none') return;
  let pool = state.active;
  if (pool.length > 1 && state.lastPopupLabel) {
    const others = pool.filter((i) => i.label !== state.lastPopupLabel);
    if (others.length) pool = others;
  }
  const item = pool[Math.floor(Math.random() * pool.length)];
  state.lastPopupLabel = item.label;
  showNotifPopup(item);
}

function showNotifPopup(item) {
  const icon = notifIconFor(item.label);
  let pop = document.getElementById('notif-popup');
  if (!pop) {
    pop = document.createElement('a');
    pop.id = 'notif-popup';
    pop.className = 'notif-popup';
    document.body.appendChild(pop);
  }
  pop.href = item.href || '#';
  pop.innerHTML = `
    <span class="notif-popup-icon" style="background:${icon.color}33;">
      <svg viewBox="0 0 24 24" fill="none" stroke="${icon.color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">${icon.svg}</svg>
    </span>
    <span style="flex:1;min-width:0;">
      <span style="display:block;font-size:13px;font-weight:600;">${escapeHtml(item.label)}: ${escapeHtml(String(item.count ?? ''))}</span>
      <span style="display:block;font-size:11.5px;opacity:.7;">${escapeHtml(item.sub || '')}</span>
    </span>`;
  pop.classList.remove('visible');
  void pop.offsetWidth;
  pop.classList.add('visible');
  clearTimeout(pop._hideTimer);
  pop._hideTimer = setTimeout(() => pop.classList.remove('visible'), 5000);
}
'@

# ---- Block A: replaces lines 330-349 (shift banner -> feeds the bell)
$blockA = @'
async function refreshUnclosedShiftBanner() {
  if (!['super_admin', 'branch_manager'].includes(currentUser?.role)) return;
  const state = notifState();
  try {
    const branchId = currentUser?.role === 'branch_manager' ? currentUser?.branch_id : currentBranchId();
    const events = await Api.complianceEvents({ branchId, sinceDays: 30 });
    const outstanding = events.filter((e) => !e.admin_acknowledged_at);
    if (!outstanding.length) {
      state.shift = null;
    } else {
      const staffCount = new Set(outstanding.map((e) => e.user_id)).size;
      state.shift = {
        label: 'Review shift report',
        sub: `${outstanding.length} auto-close incident${outstanding.length === 1 ? '' : 's'} need${outstanding.length === 1 ? 's' : ''} follow-up (${staffCount} staff)`,
        count: outstanding.length,
        href: 'staff.html?tab=shifts',
      };
    }
  } catch (err) {
    state.shift = null;
  }
  refreshNotificationsUi();
}
'@

# Bottom-first so earlier line numbers stay valid
$lines.RemoveRange(959, 44)
$lines.InsertRange(959, [string[]]($blockB -split '\r?\n'))
$lines.RemoveRange(329, 20)
$lines.InsertRange(329, [string[]]($blockA -split '\r?\n'))

# ---------------------------------------------------------------- style.css
$cssPath = Join-Path $root 'css\style.css'
$cssText = [IO.File]::ReadAllText($cssPath, $utf8)
if ($cssText.Contains('bell-ring')) { throw 'style.css already has the bell animation. Nothing applied.' }
$css = @'

/* ---- Ringing bell + rotating notification pop-up ---- */
@keyframes bell-ring {
  0%, 36%, 100% { transform: rotate(0); }
  4%  { transform: rotate(16deg); }
  8%  { transform: rotate(-14deg); }
  12% { transform: rotate(12deg); }
  16% { transform: rotate(-10deg); }
  20% { transform: rotate(7deg); }
  24% { transform: rotate(-5deg); }
  28% { transform: rotate(3deg); }
}
#notif-bell-btn.bell-ringing svg {
  transform-origin: 50% 10%;
  animation: bell-ring 2.6s ease-in-out infinite;
}
.notif-popup {
  position: fixed; top: 70px; right: 24px; z-index: 1200;
  display: flex; align-items: center; gap: 10px;
  min-width: 220px; max-width: 320px; padding: 10px 14px;
  border-radius: 12px; background: #1f2433; color: #fff;
  box-shadow: 0 8px 24px rgba(0,0,0,.28); text-decoration: none;
  opacity: 0; transform: translateY(-8px); pointer-events: none;
  transition: opacity .25s, transform .25s;
}
.notif-popup.visible { opacity: 1; transform: none; pointer-events: auto; }
.notif-popup-icon {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
'@

# ---------------------------------------------------------------- write everything
[IO.File]::WriteAllText($htmlPath, $html, $utf8)
[IO.File]::WriteAllText($jsPath, ($lines -join $nl), $utf8)
[IO.File]::AppendAllText($cssPath, ($css -replace "`r?`n", $nl), $utf8)

Write-Host "`nDone. Hard refresh the dashboard (Ctrl+Shift+R)." -ForegroundColor Green
Write-Host "Rollback: copy the files in $bk back over dashboard.html, js\dashboard.js and css\style.css."