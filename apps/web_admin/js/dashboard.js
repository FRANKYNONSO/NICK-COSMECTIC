
Auth.requireAuth();
Perms.applyNav();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier', store_assistant: 'Store assistant' }; // PATCHP3C-ROLE-LABEL
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });
document.getElementById('full-reports-link')?.addEventListener('click', (e) => e.preventDefault());

// The header avatar's photo comes from GET /me/profile's
// profile_photo_url -- that field isn't part of /auth/me's UserOut, so
// it isn't in the cached Auth.getUser() object above and needs its own
// fetch. Runs once on load, same as profile.html's own avatar. Falls
// back to the person's first initial (same rule as profile.html:
// preferred_name over full_name) if they haven't uploaded a photo, or
// if this fetch fails for any reason.
document.getElementById('avatar-fallback').textContent = (currentUser?.full_name || '?').charAt(0).toUpperCase();
Api.getMyProfile().then((profile) => {
  if (profile.profile_photo_url) {
    const img = document.getElementById('avatar-img');
    img.src = profile.profile_photo_url;
    window.__myPhotoUrl = profile.profile_photo_url;
    try { renderTeamPerformance(); renderLiveActivity(); } catch (e) { /* data not loaded yet; first render will pick the photo up */ }
    img.style.display = 'block';
    document.getElementById('avatar-fallback').style.display = 'none';
  } else {
    document.getElementById('avatar-fallback').textContent = (profile.preferred_name || profile.full_name || '?').charAt(0).toUpperCase();
  }
}).catch(() => {
  // Non-fatal -- the initial-letter fallback set above stays as-is.
});

document.getElementById('avatar-btn')?.addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = document.getElementById('avatar-menu');
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.avatar-wrap')) {
    const menu = document.getElementById('avatar-menu');
    if (menu) menu.style.display = 'none';
  }
});

// -------------------------------------------------------- KPI hide/reveal
// Total revenue and Total invested in inventory render masked by
// default on every fresh page load. State is per-card in
// localStorage, defaulting to hidden if no key exists yet. The real
// string is cached on the value/sub element's own dataset the moment
// loadKpis()/loadTotalInvested() write it, so toggling back and forth
// never needs a re-fetch.
const KPI_HIDE_CARDS = {
  'total-revenue': { value: 'kpi-total-revenue', sub: 'kpi-total-revenue-sub' },
  invested: { value: 'kpi-invested', sub: 'kpi-invested-sub' },
};
const KPI_MASK = '•••••••';
const KPI_EYE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
const KPI_EYE_OFF_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a21.06 21.06 0 0 1 5.06-5.94"/><path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 7 11 7a21.06 21.06 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="M1 1l22 22"/></svg>';

function isKpiHidden(name) {
  const v = localStorage.getItem(`kpi-hidden:${name}`);
  return v === null ? true : v === 'true';
}
function setKpiHidden(name, hidden) {
  localStorage.setItem(`kpi-hidden:${name}`, hidden ? 'true' : 'false');
}
function renderKpiMaskState(name) {
  const ids = KPI_HIDE_CARDS[name];
  const hidden = isKpiHidden(name);
  const valueEl = document.getElementById(ids.value);
  const subEl = ids.sub ? document.getElementById(ids.sub) : null;
  if (valueEl) valueEl.textContent = hidden ? KPI_MASK : (valueEl.dataset.real ?? valueEl.textContent);
  if (subEl && subEl.dataset.real !== undefined) subEl.textContent = hidden ? KPI_MASK : subEl.dataset.real;
  const toggleBtn = document.querySelector(`[data-kpi-hide="${name}"]`);
  if (toggleBtn) {
    toggleBtn.innerHTML = hidden ? KPI_EYE_OFF_SVG : KPI_EYE_SVG;
    toggleBtn.setAttribute('aria-label', `${hidden ? 'Show' : 'Hide'} ${name.replace('-', ' ')}`);
    toggleBtn.classList.toggle('revealed', !hidden);
  }
}
// Called after loadKpis()/loadTotalInvested() write the real figures
// to the DOM -- caches each one on its own dataset.real (skipping the
// cache step if it's already showing the mask, so a card left
// revealed across a poll doesn't get its live value trampled by a
// stale one) then re-applies the current hidden/revealed state.
function applyKpiMasking() {
  Object.keys(KPI_HIDE_CARDS).forEach((name) => {
    const ids = KPI_HIDE_CARDS[name];
    const valueEl = document.getElementById(ids.value);
    const subEl = ids.sub ? document.getElementById(ids.sub) : null;
    if (valueEl && valueEl.textContent !== KPI_MASK) valueEl.dataset.real = valueEl.textContent;
    if (subEl && subEl.textContent !== KPI_MASK) subEl.dataset.real = subEl.textContent;
    renderKpiMaskState(name);
  });
}
document.querySelectorAll('[data-kpi-hide]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const name = btn.dataset.kpiHide;
    setKpiHidden(name, !isKpiHidden(name));
    renderKpiMaskState(name);
  });
});
// Show the correct eye/eye-off icon immediately, before the first
// loadKpis() round trip completes.
Object.keys(KPI_HIDE_CARDS).forEach(renderKpiMaskState);

// Today's sales -- replaces the old Losses KPI card. Count of
// completed sales today, plus a live percent-change line comparing
// against the same count yesterday. Both numbers come straight out
// of allSalesCache (see loadLiveActivity() below), so this recomputes
// on every dashboard poll without an extra network round trip.
function renderTodaySalesKpi() {
  const valueEl = document.getElementById('kpi-today-sales-count');
  const subEl = document.getElementById('kpi-today-sales-delta');
  if (!valueEl || !subEl) return;

  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);

  const todayCount = allSalesCache.filter((s) => new Date(s.created_at).toDateString() === now.toDateString()).length;
  const yesterdayCount = allSalesCache.filter((s) => new Date(s.created_at).toDateString() === yesterday.toDateString()).length;

  valueEl.textContent = todayCount.toLocaleString('en-NG');

  if (yesterdayCount === 0) {
    subEl.textContent = todayCount === 0
      ? 'No sales yet today'
      : `${todayCount} sale${todayCount === 1 ? '' : 's'} \u2014 none yesterday to compare`;
    subEl.style.color = '';
  } else {
    const pct = Math.round(((todayCount - yesterdayCount) / yesterdayCount) * 100);
    if (pct === 0) {
      subEl.textContent = 'Same as yesterday';
      subEl.style.color = '';
    } else {
      const arrow = pct > 0 ? '\u2191' : '\u2193';
      subEl.textContent = `${arrow} ${Math.abs(pct)}% vs yesterday`;
      subEl.style.color = pct > 0 ? '#34d399' : '#f56d63';
    }
  }
}

let toastTimer = null;
function showToast(message, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 0 })}`;
}

function dateNDaysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

// NOTE: previously used `new Date().toISOString().slice(0, 10)`, which
// reads the UTC calendar date rather than the local one -- in a UTC+1
// timezone (e.g. Nigeria), the first hour of every local day was still
// the previous UTC date, so "today" sales made in that window (and
// sometimes the whole day, depending on when the browser/server clocks
// disagreed) were filtered out of the server-side date_from query even
// though they showed up fine in locally-filtered views like "this week".
function todayStr() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// ------------------------------------------------------------ Connection status

// Real connectivity check — pings the API instead of assuming success.
// `syncing` guards against overlapping checks if the user mashes "Sync Now"
// or a poll tick fires mid-request.
let syncing = false;
async function checkSyncStatus(showResultToast = false) {
  if (syncing) return;
  syncing = true;
  const el = document.getElementById('sync-status');
  el.className = 'status-pill connecting';
  el.innerHTML = '<span class="dot"></span>Connecting…';
  try {
    await Api.me();
    el.className = 'status-pill online';
    el.innerHTML = '<span class="dot"></span>Synced';
    const updated = document.getElementById('updated-at');
    if (updated) updated.textContent = 'Updated ' + new Date().toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Africa/Lagos' });
    if (showResultToast) showToast('Synced with server.');
  } catch (err) {
    el.className = 'status-pill offline';
    el.innerHTML = '<span class="dot"></span>Offline';
    if (showResultToast) showToast(err.message, true);
  } finally {
    syncing = false;
  }
}
document.getElementById('sync-btn').addEventListener('click', () => checkSyncStatus(true));

// The "Online" pill reflects the browser's own network connectivity — separate
// from "Synced", which reflects whether the API specifically responded. Was
// previously hardcoded in the HTML and never updated; now driven by real events.
function renderOnlineStatus() {
  const el = document.getElementById('online-status');
  if (navigator.onLine) {
    el.className = 'status-pill online';
    el.innerHTML = '<span class="dot"></span>Online';
  } else {
    el.className = 'status-pill offline';
    el.innerHTML = '<span class="dot"></span>No network';
  }
}
window.addEventListener('online', renderOnlineStatus);
window.addEventListener('offline', renderOnlineStatus);
renderOnlineStatus();

// ------------------------------------------------------------ Branches

let branchCache = [];
async function loadBranchFilter() {
  try {
    branchCache = await Api.listBranches();
    const select = document.getElementById('branch-filter');
    select.innerHTML = '<option value="">All branches</option>' +
      branchCache.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
    // Restore whatever branch was last selected (here or persisted from
    // any other page -- this is now the one true selector; every other
    // page just reads BranchContext) so it stays put across navigation
    // and reloads until the user deliberately changes it.
    const saved = BranchContext.get();
    if (saved && branchCache.some((b) => b.id === saved)) select.value = saved;
  } catch (err) { /* non-fatal */ }
}

function currentBranchId() {
  return document.getElementById('branch-filter').value || undefined;
}

// -------------------------------------------------------------------- KPIs

// Total Invested / Total Revenue are always true all-time totals,
// from the same 2000-01-01 stand-in this dashboard used before the
// old resettable-period feature ever existed. See records.html for
// the permanent, non-resettable per-day history.
const currentFinancialPeriodStartDate = '2000-01-01';

async function loadKpis() {
  try {
    const branchId = currentBranchId();
    const allTime = await Api.salesSummary({ branchId, dateFrom: currentFinancialPeriodStartDate });
    // Total revenue is shown net of recorded losses (damaged/expired/
    // stolen stock) -- e.g. ₦10,000 in sales against ₦5,000 in
    // recorded losses displays as ₦5,000. If the losses call fails
    // for any reason, fall back to showing gross revenue rather than
    // nothing.
    let netRevenueNaira = allTime.grand_total_naira;
    let lossNaira = 0;
    try {
      const lossesAllTime = await Api.lossesSummary(branchId, currentFinancialPeriodStartDate);
      lossNaira = lossesAllTime.total_value_naira || 0;
      // (the server total is already net of approved losses)
    } catch (err) {
      // leave netRevenueNaira as gross revenue
    }
    document.getElementById('kpi-total-revenue').textContent = formatNaira(netRevenueNaira);
    const revenueSub = document.getElementById('kpi-total-revenue-sub');
    if (revenueSub) {
      revenueSub.textContent = lossNaira > 0
        ? `${formatNaira(allTime.grand_total_naira + lossNaira)} in sales, net of ${formatNaira(lossNaira)} in recorded losses`
        : '';
    }

    const today = await Api.salesSummary({ branchId: currentBranchId(), dateFrom: todayStr() });
    document.getElementById('kpi-today-revenue').textContent = formatNaira(today.grand_total_naira);
    document.getElementById('kpi-today-sub').textContent = `${today.grand_sales_count || 0} sale${today.grand_sales_count === 1 ? '' : 's'} today`;
  } catch (err) {
    showToast(err.message, true);
  }

  try {
    // branchId falls back to the admin/manager's own assigned branch when
    // no filter is selected, same as the staff loader below; if there's
    // no branch at all (a super_admin viewing "All branches"), Api.lowStock
    // just omits branch_id and the server aggregates across every branch.
    // Kept even though nothing on this page reads the result anymore --
    // the notification bell (GET /notifications/summary) is the real
    // source of truth for low-stock counts now.
    const branchId = currentBranchId() || currentUser?.branch_id;
    await Api.lowStock(branchId);
  } catch (err) {
    // no-op
  }

  try {
    const branchId = currentBranchId() || currentUser?.branch_id;
    // Feeds loadExpiryAlertBanner() elsewhere on this page.
    await Api.expiryAlerts(branchId);
  } catch (err) {
    // no-op
  }

  await loadTotalInvested();
  applyKpiMasking();
  await refreshPendingRefundBadge();
  await refreshPendingChangeBadge();
  await refreshOutstandingChangeBadge();
  await refreshUnclosedShiftBanner();
}

// Item 15: surface unclosed/auto-closed shifts on the dashboard --
// the underlying tracking (auto-close, compliance events, staff
// self-ack) already existed, it just had no visibility here before.
// Only counts events still awaiting the admin's own follow-up
// (admin_acknowledged_at), not the staff member's separate self-ack,
// so this clears itself once someone has actually acted on it.
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

// "Total Amount Invested in Inventory" -- a cumulative figure (never
// resets), shown alongside revenue so a Super Admin can tell at a
// glance whether the business is in profit or in loss. This tile
// reads the landed-cost figure only and is NOT netted against
// losses -- the loss deduction happens on the Total Revenue card
// instead (see loadKpis() above) -- see
// api/v1/routers/purchase_invoices.py::total_invested().
async function loadTotalInvested() {
  const card = document.getElementById('kpi-invested-card');
  if (!card) return;
  try {
    const branchId = currentBranchId() || currentUser?.branch_id;
    const summary = await Api.totalInvested(branchId, currentFinancialPeriodStartDate);
    document.getElementById('kpi-invested').textContent = formatNaira(summary.total_landed_cost_naira);
    document.getElementById('kpi-invested-sub').textContent =
      `${formatNaira(summary.total_landed_cost_naira)} landed cost across all invoices`;
  } catch (err) {
    document.getElementById('kpi-invested').textContent = '—';
  }
}

// Section 10: only a Super Admin or Branch Manager can act on a
// pending refund, so this is skipped entirely for a staff/cashier
// account rather than calling an endpoint they'd get a 403 from.
// Exposed on window so the Refund History tab (sales.html) can clear
// this badge immediately after marking a refund processed, without
// waiting for the next dashboard poll.
async function refreshPendingRefundBadge() {
  if (!['super_admin', 'branch_manager'].includes(currentUser?.role)) return;
  try {
    const branchId = currentUser?.role === 'branch_manager' ? currentUser?.branch_id : currentBranchId();
    // The notification bell (GET /notifications/summary) is the real
    // source of truth for this count now; kept here anyway since
    // sales.html still calls this function directly after a refund
    // action, which is the only reason it still runs at all.
    await Api.pendingRefundCount(branchId);
  } catch (err) {
    // no-op
  }
}
window.refreshPendingRefundBadge = refreshPendingRefundBadge;

// "Pending change" (spec clarification): the customer's cash change
// is still physically at the shop -- they said they'll come back for
// it. Not gated to admin at all -- every cashier on shift should see
// this count, same visibility as Low stock/Expiry/Out of stock. Reuses
// GET /sales/pending-changes?status=outstanding (already open to
// anyone with process_sale, auto-scoped server-side to the caller's
// own branch) rather than a dedicated count endpoint -- the array
// length is all this card needs. Exposed on window so pending-change.js
// can clear this immediately after a settle action, without waiting
// for the next dashboard poll.
async function refreshOutstandingChangeBadge() {
  const card = document.getElementById('kpi-pending-change-outstanding-card');
  if (!card) return;
  try {
    const branchId = currentBranchId() || currentUser?.branch_id;
    const rows = await Api.listPendingChanges({ status: 'outstanding', branchId });
    document.getElementById('kpi-pending-change-outstanding').textContent = rows.length;
    card.classList.toggle('low-stock-blinking', rows.length > 0);
  } catch (err) {
    document.getElementById('kpi-pending-change-outstanding').textContent = '—';
  }
}
window.refreshOutstandingChangeBadge = refreshOutstandingChangeBadge;

// "Change approvals" (spec clarification -- this is specifically the
// transfer/airtime case, distinct from the "Pending change" card
// above): real money has to move out of the business, so this stays
// restricted to super_admin/branch_manager, same as
// refreshPendingRefundBadge above and same tier /sales/pending-changes/
// pending-count is gated to. Exposed on window so pending-change.js can
// clear this immediately after a settle/mark-processed action, without
// waiting for the next dashboard poll.
async function refreshPendingChangeBadge() {
  if (!['super_admin', 'branch_manager'].includes(currentUser?.role)) return;
  try {
    const branchId = currentUser?.role === 'branch_manager' ? currentUser?.branch_id : currentBranchId();
    await Api.pendingChangeCount(branchId);
  } catch (err) {
    // no-op
  }
}
window.refreshPendingChangeBadge = refreshPendingChangeBadge;

// -------------------------------------------------------- Live activity

let allSalesCache = [];

async function loadLiveActivity() {
  try {
    allSalesCache = await Api.listSales({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(30) });
    renderLiveActivity();
    renderTodaySalesKpi();
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderLiveActivity() {
  const list = document.getElementById('live-activity-list');
  if (!list) return;
  const recent = allSalesCache.slice()
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    .slice(0, 30);
  list.innerHTML = recent.map((s) => {
    const nm = s.cashier_name || 'Unknown';
    const when = new Date(s.created_at);
    return `
    <div class="la-row">
      ${workerAvatarHtml(nm, "la-avatar")}
      <div class="la-main">
        <div class="la-name">${escapeHtml(nm)}</div>
        <div class="la-meta">${s.item_count ?? '—'} item(s) · ${when.toLocaleDateString('en-NG', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'Africa/Lagos' })} · ${when.toLocaleTimeString('en-NG', { hour: 'numeric', minute: '2-digit', timeZone: 'Africa/Lagos' })}</div>
      </div>
      <div class="la-amt"><strong>${formatNaira(s.total_naira)}</strong><a href="sales.html">Receipt</a></div>
    </div>`;
  }).join('') || '<p style="color:var(--ink-faint);font-size:13px;">No recent sales.</p>';
}

// -------------------------------------------------------- Revenue analysis

let revenueSalesCache = [];
let currentRevenuePeriod = 'daily';

function monthsAgoStr(n) {
  const d = new Date();
  d.setMonth(d.getMonth() - n);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

async function loadRevenueAnalysis() {
  try {
    revenueSalesCache = await Api.listSales({ branchId: currentBranchId(), dateFrom: monthsAgoStr(6) });
    renderRevenueAnalysis();
  } catch (err) {
    // Non-fatal -- leave the last rendered state in place.
  }
}

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function buildRevenueBuckets(granularity) {
  const now = startOfDay(new Date());
  const buckets = [];

  if (granularity === 'daily') {
    for (let i = 6; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      buckets.push({
        start: d,
        end: new Date(d.getTime() + 24 * 60 * 60 * 1000),
        label: d.toLocaleDateString('en-NG', { weekday: 'short', day: 'numeric', timeZone: 'Africa/Lagos' }),
        fullLabel: d.toLocaleDateString('en-NG', { weekday: 'long', day: 'numeric', month: 'short', timeZone: 'Africa/Lagos' }),
      });
    }
  } else if (granularity === 'weekly') {
    for (let i = 7; i >= 0; i--) {
      const end = new Date(now);
      end.setDate(end.getDate() - i * 7);
      const start = new Date(end);
      start.setDate(start.getDate() - 6);
      buckets.push({
        start,
        end: new Date(end.getTime() + 24 * 60 * 60 * 1000),
        label: start.toLocaleDateString('en-NG', { day: 'numeric', month: 'short', timeZone: 'Africa/Lagos' }),
        fullLabel: `${start.toLocaleDateString('en-NG', { day: 'numeric', month: 'short', timeZone: 'Africa/Lagos' })} to ${end.toLocaleDateString('en-NG', { day: 'numeric', month: 'short', timeZone: 'Africa/Lagos' })}`,
      });
    }
  } else {
    for (let i = 5; i >= 0; i--) {
      const start = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const end = new Date(start.getFullYear(), start.getMonth() + 1, 1);
      buckets.push({
        start,
        end,
        label: start.toLocaleDateString('en-NG', { month: 'short', timeZone: 'Africa/Lagos' }),
        fullLabel: start.toLocaleDateString('en-NG', { month: 'long', year: 'numeric', timeZone: 'Africa/Lagos' }),
      });
    }
  }

  buckets.forEach((b) => { b.total = 0; });
  revenueSalesCache.forEach((s) => {
    const created = new Date(s.created_at);
    const bucket = buckets.find((b) => created >= b.start && created < b.end);
    if (bucket) bucket.total += Number(s.total_naira || 0);
  });

  return buckets;
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

  // y-axis: five gridlines at "nice" steps (₦1 / ₦0.8 ... ₦0 when there is no data yet)
  const rawMax = Math.max(...buckets.map((b) => b.total), 0);
  let step;
  if (!rawMax) { step = 0.2; } else {
    const rough = rawMax / 5, pow = Math.pow(10, Math.floor(Math.log10(rough))), n = rough / pow;
    step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * pow;
  }
  const axisMax = step * 5;
  const compact = (v) => {
    if (v >= 1e9) return '₦' + +(v / 1e9).toFixed(2) + 'B';
    if (v >= 1e6) return '₦' + +(v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return '₦' + +(v / 1e3).toFixed(1) + 'K';
    return '₦' + +v.toFixed(1);
  };
  const grid = [0, 1, 2, 3, 4, 5].map((i) => `<div class="rc-tick" style="bottom:${(i / 5) * 100}%"><span>${compact(step * i)}</span></div>`).join('');
  let body;
  if (!periodTotal) {
    body = `<div class="rc-empty">
        <span class="rc-empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="12.5" width="4.5" height="8" rx="1.3"/><rect x="9.75" y="8.5" width="4.5" height="12" rx="1.3"/><rect x="16" y="4" width="4.5" height="16.5" rx="1.3"/></svg></span>
        <strong>No revenue in this period yet</strong>
        <span>Sales from POS will appear here automatically.</span>
      </div>`;
  } else {
    body = '<div class="rc-bars">' + buckets.map((b, i) => `
      <div class="rc-col${b === best && b.total > 0 ? ' best' : ''}" title="${escapeHtml(b.fullLabel)}: ${formatNaira(b.total)}">
        <div class="rc-area"><div class="rc-fill" style="height:${Math.max((b.total / axisMax) * 100, b.total > 0 ? 1.5 : 0)}%;animation-delay:${i * 60}ms"><em>${b.total > 0 ? compact(b.total) : ''}</em></div></div>
        <div class="rc-lbl">${escapeHtml(b.label)}</div>
      </div>`).join('') + '</div>';
  }
  container.innerHTML = `<div class="rc"><div class="rc-grid">${grid}</div>${body}</div>`;
}

document.querySelectorAll('#revenue-tabs .tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#revenue-tabs .tab-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    currentRevenuePeriod = btn.dataset.period;
    renderRevenueAnalysis();
  });
});
// -------------------------------------------------------- Inventory overview

async function loadInventoryOverview() {
  try {
    const products = await Api.listProducts({ branchId: currentBranchId() });
    renderInventoryOverview(products);
  } catch (err) {
    // Non-fatal -- leave the last rendered state in place.
  }
}

function renderInventoryOverview(products) {
  const totalProducts = products.length;
  const totalUnits = products.reduce((t, p) => t + Number(p.quantity_available || 0), 0);
  const totalValue = products.reduce((t, p) => t + Number(p.quantity_available || 0) * Number(p.price_naira || 0), 0);

  document.getElementById('inv-products-count').textContent = totalProducts.toLocaleString('en-NG');
  document.getElementById('inv-units-count').textContent = totalUnits.toLocaleString('en-NG');
  document.getElementById('inv-stock-value').textContent = formatNaira(totalValue);

  let inStockCount = 0, lowCount = 0, outCount = 0;
  products.forEach((p) => {
    const qty = Number(p.quantity_available || 0);
    const reorder = Number(p.reorder_level || 0);
    if (qty <= 0) outCount++;
    else if (qty <= reorder) lowCount++;
    else inStockCount++;
  });
  const healthTotal = Math.max(inStockCount + lowCount + outCount, 1);
  const healthRows = [
    { label: 'In stock', count: inStockCount, color: 'var(--success)' },
    { label: 'Low stock', count: lowCount, color: 'var(--warning)' },
    { label: 'Out of stock', count: outCount, color: 'var(--danger)' },
  ];

  document.getElementById('inv-stock-health-bars').innerHTML = healthRows.map((r) => `
    <div class="share-row" style="margin-bottom:12px;">
      <span class="legend-row" style="margin-right:0;min-width:96px;"><span class="dot" style="background:${r.color};"></span>${escapeHtml(r.label)}</span>
      <div class="share-bar-track"><div class="share-bar-fill" style="width:${((r.count / healthTotal) * 100).toFixed(0)}%;background:${r.color};"></div></div>
      <span class="share-pct">${r.count}</span>
    </div>`).join('');

  document.getElementById('inv-stock-health-detail').innerHTML = healthRows.map((r) => `
    <div class="share-row">
      <span class="legend-row"><span class="dot" style="background:${r.color};"></span>${escapeHtml(r.label)}</span>
      <span style="margin-left:auto;font-weight:600;">${r.count}</span>
    </div>`).join('');

  const categoryMap = new Map();
  products.forEach((p) => {
    const name = p.category_name || 'Uncategorized';
    const qty = Number(p.quantity_available || 0);
    const value = qty * Number(p.price_naira || 0);
    if (!categoryMap.has(name)) categoryMap.set(name, { units: 0, value: 0 });
    const entry = categoryMap.get(name);
    entry.units += qty;
    entry.value += value;
  });
  const categories = Array.from(categoryMap.entries())
    .map(([name, v]) => ({ name, units: v.units, value: v.value }))
    .sort((a, b) => b.units - a.units);
  const maxCatUnits = Math.max(...categories.map((c) => c.units), 1);

  document.getElementById('inv-units-by-category').innerHTML = categories.length
    ? categories.slice(0, 6).map((c) => `
      <div class="share-row" style="margin-bottom:12px;">
        <span style="min-width:104px;max-width:104px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px;color:var(--ink-muted);">${escapeHtml(c.name)}</span>
        <div class="share-bar-track"><div class="share-bar-fill" style="width:${((c.units / maxCatUnits) * 100).toFixed(0)}%;"></div></div>
        <span class="share-pct">${c.units}</span>
      </div>`).join('')
    : '<p style="color:var(--ink-faint);font-size:13px;">No products yet.</p>';

  document.getElementById('inv-category-breakdown-tbody').innerHTML = categories.length
    ? categories.map((c) => `
      <tr><td>${escapeHtml(c.name)}</td><td>${c.units.toLocaleString('en-NG')}</td><td>${formatNaira(c.value)}</td></tr>`).join('')
    : '<tr><td colspan="3" style="text-align:center;color:var(--ink-faint);">No products yet.</td></tr>';

  const topProducts = [...products]
    .sort((a, b) => Number(b.quantity_available || 0) - Number(a.quantity_available || 0))
    .slice(0, 6);
  const maxQty = Math.max(...topProducts.map((p) => Number(p.quantity_available || 0)), 1);

  document.getElementById('inv-top-products').innerHTML = topProducts.length
    ? topProducts.map((p) => `
      <div class="share-row" style="margin-bottom:12px;">
        <span style="min-width:104px;max-width:104px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px;color:var(--ink-muted);">${escapeHtml(p.name)}</span>
        <div class="share-bar-track"><div class="share-bar-fill" style="width:${((Number(p.quantity_available || 0) / maxQty) * 100).toFixed(0)}%;background:var(--success);"></div></div>
        <span class="share-pct">${p.quantity_available || 0}</span>
      </div>`).join('')
    : '<p style="color:var(--ink-faint);font-size:13px;">No products yet.</p>';
}
// -------------------------------------------------- Team performance / Sales by worker

let teamSalesCache = [];
let teamUsersCache = [];
let currentTeamPeriod = 'today';

function isSameLocalDay(dateStr, ref) {
  return new Date(dateStr).toDateString() === ref.toDateString();
}
function isWithinLocalDays(dateStr, days, ref) {
  const cutoff = new Date(ref);
  cutoff.setDate(cutoff.getDate() - days);
  return new Date(dateStr) >= cutoff;
}
function isSameLocalMonth(dateStr, ref) {
  const d = new Date(dateStr);
  return d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth();
}
function isSameLocalYear(dateStr, ref) {
  return new Date(dateStr).getFullYear() === ref.getFullYear();
}

// Photo (if the users API returns one, or it is the signed-in user) or the first-initial fallback.
// A photo is clickable and opens a zoomed view.
function workerAvatarHtml(name, cls) {
  const u = (teamUsersCache || []).find((x) => x.full_name === name);
  let url = u && (u.profile_photo_url || u.photo_url || u.avatar_url || u.profile_picture || u.profile_image || u.image_url);
  if (!url && currentUser && name === currentUser.full_name) url = window.__myPhotoUrl;
  const initial = escapeHtml((name || '?').charAt(0).toUpperCase());
  if (!url) return `<span class="${cls}">${initial}</span>`;
  return `<span class="${cls} has-photo" data-zoom-src="${escapeHtml(url)}" data-zoom-name="${escapeHtml(name || '')}" title="Click to enlarge"><img src="${escapeHtml(url)}" alt="" onerror="this.parentNode.classList.remove('has-photo');this.remove()" />${initial}</span>`;
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('.has-photo[data-zoom-src]');
  if (!el) return;
  const ov = document.createElement('div');
  ov.className = 'photo-zoom-overlay';
  ov.innerHTML = `<figure><img src="${el.dataset.zoomSrc}" alt="" /><figcaption>${el.dataset.zoomName}</figcaption></figure>`;
  const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (ev) => { if (ev.key === 'Escape') close(); };
  ov.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(ov);
});

async function loadTeamAndWorkerData() {
  try {
    const [sales, users] = await Promise.all([
      Api.listSales({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(366) }),
      Api.listUsers({ branchId: currentBranchId(), includeInactive: true }).catch(() => []),
    ]);
    teamSalesCache = sales;
    teamUsersCache = users;
    renderTeamPerformance();
    renderSalesByWorker();
  } catch (err) {
    // Non-fatal -- leave the last rendered state in place.
  }
}

function renderTeamPerformance() {
  const container = document.getElementById('team-performance-bars');
  const subEl = document.getElementById('team-performance-sub');
  if (!container) return;

  const periodLabels = { today: "Today's sales by staff", week: "This week's sales by staff", month: "This month's sales by staff", year: "This year's sales by staff" };
  if (subEl) subEl.textContent = periodLabels[currentTeamPeriod];

  const now = new Date();
  const filterFn = {
    today: (s) => isSameLocalDay(s.created_at, now),
    week: (s) => isWithinLocalDays(s.created_at, 7, now),
    month: (s) => isSameLocalMonth(s.created_at, now),
    year: (s) => isSameLocalYear(s.created_at, now),
  }[currentTeamPeriod];

  const scoped = teamSalesCache.filter(filterFn);
  const byWorker = new Map();
  const byCount = new Map();
  scoped.forEach((s) => {
    const name = s.cashier_name || 'Unknown';
    if (!byWorker.has(name)) byWorker.set(name, 0);
    byWorker.set(name, byWorker.get(name) + Number(s.total_naira || 0));
    byCount.set(name, (byCount.get(name) || 0) + 1);
  });

  const ranked = Array.from(byWorker.entries())
    .map(([name, total]) => ({ name, total, count: byCount.get(name) || 0 }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 20);

  if (!ranked.length) {
    container.innerHTML = `
      <div class="bar-chart-empty" style="height:160px;">
        <span>No staff sales for this period</span>
      </div>`;
    return;
  }

  const maxTotal = Math.max(...ranked.map((r) => r.total), 1);
  const grand = ranked.reduce((t, r) => t + r.total, 0) || 1;
  container.innerHTML = ranked.map((r, i) => `
    <div class="tp-row${i === 0 ? ' tp-top' : ''}" style="animation-delay:${i * 70}ms">
      ${workerAvatarHtml(r.name, "tp-avatar")}
      <div class="tp-main">
        <div class="tp-line"><span class="tp-name">${escapeHtml(r.name)}</span><span class="tp-amt">${formatNaira(r.total)}</span></div>
        <div class="tp-track"><div class="tp-fill" style="width:${((r.total / maxTotal) * 100).toFixed(0)}%;animation-delay:${200 + i * 70}ms"></div></div>
        <div class="tp-meta">${r.count} sale${r.count === 1 ? '' : 's'} &middot; ${((r.total / grand) * 100).toFixed(0)}% of team total</div>
      </div>
    </div>`).join('');
}

document.querySelectorAll('#team-performance-tabs .tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#team-performance-tabs .tab-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    currentTeamPeriod = btn.dataset.period;
    renderTeamPerformance();
  });
});

function renderSalesByWorker() {
  const tbody = document.getElementById('sales-by-worker-tbody');
  if (!tbody) return;

  const now = new Date();
  const names = new Set(teamSalesCache.map((s) => s.cashier_name || 'Unknown'));
  teamUsersCache.forEach((u) => { if (u.full_name) names.add(u.full_name); });

  const rows = Array.from(names).map((name) => {
    const salesForWorker = teamSalesCache.filter((s) => (s.cashier_name || 'Unknown') === name);
    const sum = (rows) => rows.reduce((t, s) => t + Number(s.total_naira || 0), 0);
    const today = salesForWorker.filter((s) => isSameLocalDay(s.created_at, now));
    const week = salesForWorker.filter((s) => isWithinLocalDays(s.created_at, 7, now));
    const month = salesForWorker.filter((s) => isSameLocalMonth(s.created_at, now));
    const matchedUser = teamUsersCache.find((u) => u.full_name === name);
    const isActive = matchedUser ? (matchedUser.is_active !== false) : true;
    return {
      name,
      today: sum(today),
      week: sum(week),
      month: sum(month),
      allTime: sum(salesForWorker),
      salesCount: salesForWorker.length,
      isActive,
    };
  }).filter((r) => r.salesCount > 0 || r.name !== 'Unknown')
    .sort((a, b) => b.allTime - a.allTime);

  tbody.innerHTML = rows.length ? rows.map((r) => `
    <tr>
      <td><span class="cell-strong">${escapeHtml(r.name)}</span></td>
      <td>${formatNaira(r.today)}</td>
      <td>${formatNaira(r.week)}</td>
      <td>${formatNaira(r.month)}</td>
      <td><span class="cell-strong">${formatNaira(r.allTime)}</span></td>
      <td>${r.salesCount}</td>
      <td><span class="status-pill ${r.isActive ? 'active' : 'inactive'}">${r.isActive ? 'Active' : 'Inactive'}</span></td>
    </tr>`).join('') : '<tr><td colspan="7" style="text-align:center;color:var(--ink-faint);">No worker sales recorded yet.</td></tr>';
}
// ------------------------------------------------------------------ Init

// Staff get a scoped-down dashboard: their own sales only, plus the
// general low-stock count (view_low_stock, granted to everyone).
// Live activity (store-wide, other workers' individual sales) is
// admin-only, same category as "Sales by worker" on Reports.
if (Perms.isStaff()) {
  document.querySelectorAll('.admin-only-section').forEach((el) => { el.style.display = 'none'; });
  document.querySelectorAll('.staff-only-section').forEach((el) => { el.style.display = ''; });

  // The top bar (branch picker, "Full reports", sync controls) is
  // store-wide admin chrome that was never scoped when the dashboard
  // was split -- "All branches" doesn't apply to one cashier, and
  // "Full reports" points at the store-wide reports page they don't
  // have permission to load. Hide both; keep sync status, since that's
  // genuinely useful to everyone.
  document.getElementById('branch-filter').style.display = 'none';
  document.getElementById('full-reports-link')?.remove();
  const subtitle = document.querySelector('.page-head p');
  if (subtitle) subtitle.textContent = 'Your performance — updates automatically.';
} else if (Perms.isBranchManager()) {
  // Spec section 26: "A Branch Manager should NOT be able to switch
  // from one branch to another" -- unlike a super_admin (who roams
  // every branch and picks one from this dropdown, or "All branches"),
  // a branch_manager is tied to a single branch server-side already
  // (current_user.branch_id always wins over any branch_id sent from
  // the client, everywhere in the API), so the picker itself would
  // just be a control that does nothing except imply a capability
  // they don't have.
  document.getElementById('branch-filter').style.display = 'none';
}

async function loadMyKpis() {
  function inRange(dateStr, days) {
    const d = new Date(dateStr);
    const now = new Date();
    if (days === 0) return d.toDateString() === now.toDateString();
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    return d >= cutoff;
  }
  try {
    // MYKPI-OWN-SALES-FIX: 'my' sales = only sales served by the logged-in user.
    const myId = currentUser?.id || (await Api.me())?.id;
    const mine = myId ? await Api.listSales({ dateFrom: dateNDaysAgo(90), cashierId: myId }) : [];
    // Compare using local calendar day everywhere (matches inRange's
    // d.toDateString() basis) -- mixing this with a UTC-based string
    // slice was the bug that made "today" show 0 while sales from
    // today still correctly counted toward "this week".
    const todaySales = mine.filter((s) => inRange(s.created_at, 0));
    const weekSales = mine.filter((s) => inRange(s.created_at, 7));
    const sum = (rows) => rows.reduce((t, s) => t + Number(s.total_naira || 0), 0);

    document.getElementById('kpi-my-today-revenue').textContent = formatNaira(sum(todaySales));
    document.getElementById('kpi-my-today-sub').textContent = `${todaySales.length} sale${todaySales.length === 1 ? '' : 's'} today`;
    document.getElementById('kpi-my-week-revenue').textContent = formatNaira(sum(weekSales));
    document.getElementById('kpi-my-week-sub').textContent = `${weekSales.length} sale${weekSales.length === 1 ? '' : 's'} this week`;

    const tbody = document.getElementById('my-sales-tbody');
    const emptyState = document.getElementById('my-sales-empty');
    const recent = [...mine].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 15);
    if (!recent.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
    } else {
      emptyState.style.display = 'none';
      tbody.innerHTML = recent.map((s) => `
        <tr>
          <td class="mono">${escapeHtml((s.receipt_number || s.id).slice(0, 8))}…</td>
          <td>${new Date(s.created_at).toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short', hour12: true, timeZone: 'Africa/Lagos' })}</td>
          <td>${s.item_count ?? '—'}</td>
          <td>${formatNaira(s.total_naira)}</td>
        </tr>`).join('');
    }
  } catch (err) {
    showToast(err.message, true);
  }

  // Visible to staff too -- see refreshOutstandingChangeBadge above.
  await refreshOutstandingChangeBadge();
}

function loadAll() {
  if (Perms.isStaff()) {
    if (currentUser?.branch_id) {
      const select = document.getElementById('branch-filter');
      select.value = currentUser.branch_id;
      const opt = select.options[select.selectedIndex];
      if (!BranchContext.get()) BranchContext.set(currentUser.branch_id, opt ? opt.textContent : undefined);
      const badge = document.getElementById('my-branch-badge');
      if (badge && opt) {
        badge.textContent = opt.textContent;
        badge.style.display = '';
      }
    }
    loadMyKpis();
    return;
  }
  if (Perms.isBranchManager() && currentUser?.branch_id) {
    const select = document.getElementById('branch-filter');
    select.value = currentUser.branch_id;
    const opt = select.options[select.selectedIndex];
    if (!BranchContext.get()) BranchContext.set(currentUser.branch_id, opt ? opt.textContent : undefined);
    const badge = document.getElementById('my-branch-badge');
    if (badge && opt) {
      badge.textContent = opt.textContent;
      badge.style.display = '';
    }
  }
  loadKpis();
  loadLiveActivity();
  loadRevenueAnalysis();
  loadInventoryOverview();
  loadTeamAndWorkerData();
}

document.getElementById('branch-filter').addEventListener('change', () => {
  const select = document.getElementById('branch-filter');
  const id = select.value || undefined;
  const name = id ? select.options[select.selectedIndex].textContent : undefined;
  // This is the one branch picker left in the whole app -- every other
  // page (POS, sales, invoices, credit, inventory, low stock, warehouse,
  // reports, shifts...) now just reads this value read-only. See
  // BranchContext in api.js.
  BranchContext.set(id, name);
  loadAll();
});

loadBranchFilter().then(loadAll);
checkSyncStatus();
loadExpiryAlertBanner();
loadNotificationsSummary();

// -------------------------------------------------------- Notification bell
// Admin-only (see the admin-only-section class on .bell-wrap in
// dashboard.html), same tier as GET /notifications/summary itself.
// Covers low_stock/out_of_stock/pending_refunds/pending_change/
// loss_reports -- loss_reports is what used to be the Losses card's
// own click-to-expand dropdown.

// Icon + tint per notification category, matched against the item's
// label text from the server. Falls back to a plain bell icon for any
// category not in this list, so a future addition never renders blank.
const NOTIF_ICON_MAP = [
  { match: /low stock/i, color: '#f2b84b', svg: '<path d="M10.3 3.86 1.82 18a1.5 1.5 0 0 0 1.3 2.25h17.76a1.5 1.5 0 0 0 1.3-2.25L13.7 3.86a1.5 1.5 0 0 0-2.6 0Z"/><path d="M12 9v4"/><path d="M12 16.5h.01"/>' },
  { match: /out of stock/i, color: '#f56d63', svg: '<path d="M21 8 12 3 3 8v8l9 5 9-5V8Z"/><path d="M3 8l9 5 9-5M12 13v8"/>' },
  { match: /refund/i, color: '#f56d63', svg: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>' },
  { match: /price change/i, color: '#4ba3f2', svg: '<path d="M12 2 2 12l10 10 10-10L12 2Z"/><circle cx="12" cy="12" r="2"/>' },
  { match: /change/i, color: '#97a1b8', svg: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="3"/>' },
  { match: /loss/i, color: '#f56d63', svg: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>' },
];
const NOTIF_ICON_DEFAULT = { color: '#97a1b8', svg: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>' };
function notifIconFor(label) {
  return NOTIF_ICON_MAP.find((m) => m.match.test(label || '')) || NOTIF_ICON_DEFAULT;
}

// Shared bell state lives on window so load order between the shift
// check and the summary poll never matters.
function notifState() {
  return (window.__notifState = window.__notifState || { items: [], total: 0, shift: null, active: [], popupTimer: null, lastPopupLabel: null });
}

// "Review shift report" now lives inside the bell instead of a banner.
NOTIF_ICON_MAP.unshift({ match: /shift report/i, color: '#7c9cff', svg: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M9 13h6M9 17h6"/>' });

// ---- Website-order alarm (begin)
// Website orders get their own icon and a gentle alarm. Browsers refuse to
// play sound until the page has been clicked/tapped once, so the first click
// anywhere "unlocks" it; a small button (bottom-right) shows the state.
// When the count of NEW website orders goes up (or is above zero when this
// page opens) it beeps WO_ALARM_TIMES times, then goes quiet. The button
// stays red until the order is handled or you click it.
NOTIF_ICON_MAP.unshift({ match: /website order/i, color: '#34d399', svg: '<path d="M3 3h2l2.4 12.2a2 2 0 0 0 2 1.6h8.2a2 2 0 0 0 2-1.5L21 8H6"/><circle cx="10" cy="20.5" r="1.3"/><circle cx="18" cy="20.5" r="1.3"/>' });

const WO_ALARM_SOUND = true;        // set to false to remove the sound completely
const WO_ALARM_VOLUME = 0.25;       // 0 to 1 -- lower = quieter
const WO_ALARM_TIMES = 3;           // how many times the beeps are played
const WO_ALARM_REPEAT_MS = 4000;    // pause between plays
const WO_ALARM_MUTE_KEY = 'nc_wo_alarm_muted';
const woAlarm = { ctx: null, unlocked: false, prev: null, count: 0, ringing: false, silenced: false, timer: null, played: 0 };

function woAlarmMuted() {
  try { return localStorage.getItem(WO_ALARM_MUTE_KEY) === '1'; } catch (e) { return false; }
}
function woAlarmSetMuted(on) {
  try { localStorage.setItem(WO_ALARM_MUTE_KEY, on ? '1' : '0'); } catch (e) { /* ignore */ }
}

function woAlarmCtx() {
  if (!woAlarm.ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { woAlarm.ctx = new AC(); } catch (e) { return null; }
  }
  return woAlarm.ctx;
}

// One play: four soft beeps ("beep-beep-beep-beep"), about 1 second long.
function woAlarmPlayOnce() {
  if (!WO_ALARM_SOUND) return;
  const ctx = woAlarm.ctx;
  if (!ctx || ctx.state !== 'running') return;
  const now = ctx.currentTime;
  const peak = Math.max(WO_ALARM_VOLUME, 0.001);
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0.0001, now);
  for (let i = 0; i < 4; i += 1) {
    const t = now + i * 0.25;
    gain.gain.exponentialRampToValueAtTime(peak, t + 0.02);
    gain.gain.setValueAtTime(peak, t + 0.13);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.17);
  }
  gain.connect(ctx.destination);
  const osc = ctx.createOscillator();
  osc.type = 'sine';
  osc.frequency.setValueAtTime(1000, now);
  osc.connect(gain);
  osc.start(now);
  osc.stop(now + 1.1);
}

function woAlarmLoopWanted() {
  return WO_ALARM_SOUND && woAlarm.ringing && !woAlarm.silenced && woAlarm.unlocked
    && !woAlarmMuted() && woAlarm.played < WO_ALARM_TIMES;
}
function woAlarmStopTimer() {
  if (woAlarm.timer) { clearInterval(woAlarm.timer); woAlarm.timer = null; }
}
function woAlarmTick() {
  if (!woAlarmLoopWanted()) { woAlarmStopTimer(); return; }
  woAlarmPlayOnce();
  woAlarm.played += 1;
  if (woAlarm.played >= WO_ALARM_TIMES) woAlarmStopTimer();
}
function woAlarmSyncLoop() {
  if (woAlarmLoopWanted()) {
    if (!woAlarm.timer) {
      woAlarmTick();
      if (woAlarm.played < WO_ALARM_TIMES) woAlarm.timer = setInterval(woAlarmTick, WO_ALARM_REPEAT_MS);
    }
  } else {
    woAlarmStopTimer();
  }
}

function woAlarmUnlock() {
  const ctx = woAlarmCtx();
  if (!ctx) return;
  const finish = () => {
    woAlarm.unlocked = ctx.state === 'running';
    if (woAlarm.unlocked) {
      document.removeEventListener('pointerdown', woAlarmUnlock);
      document.removeEventListener('keydown', woAlarmUnlock);
    }
    woAlarmSyncLoop();
    woAlarmPaintBtn();
  };
  if (ctx.state === 'running') { finish(); return; }
  const p = ctx.resume ? ctx.resume() : null;
  if (p && p.then) p.then(finish, finish); else finish();
}
document.addEventListener('pointerdown', woAlarmUnlock);
document.addEventListener('keydown', woAlarmUnlock);

function woAlarmPaintBtn() {
  if (!document.getElementById('notif-bell-btn')) return; // not an admin view
  let btn = document.getElementById('wo-alarm-btn');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'wo-alarm-btn';
    btn.type = 'button';
    // Sits in the top-right header row, right before the bell, using the same
    // "pill-btn" look as Sync and the bell. It is part of the header layout, so
    // it never covers the sidebar, Sign Out, or the charts.
    btn.className = 'pill-btn';
    btn.style.whiteSpace = 'nowrap';
    btn.style.fontWeight = '600';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!woAlarm.unlocked) { woAlarmUnlock(); setTimeout(() => { woAlarmPlayOnce(); }, 150); return; }
      if (woAlarm.ringing && !woAlarm.silenced) { woAlarm.silenced = true; }
      else { woAlarmSetMuted(!woAlarmMuted()); }
      woAlarmSyncLoop();
      woAlarmPaintBtn();
    });
    const woBell = document.querySelector('.top-actions .bell-wrap');
    if (woBell && woBell.parentNode) {
      woBell.parentNode.insertBefore(btn, woBell);
    } else {
      btn.style.cssText += 'position:fixed;top:16px;right:16px;z-index:9999;';
      document.body.appendChild(btn);
    }
  }
  let text; let tip; let alert = false;
  if (!woAlarm.unlocked) { text = 'Enable alarm'; tip = 'Order alarm is off until you click here'; }
  else if (woAlarm.ringing && !woAlarm.silenced) { text = 'New website order'; tip = 'Click to dismiss'; alert = true; }
  else if (woAlarmMuted() || !WO_ALARM_SOUND) { text = 'Sound off'; tip = 'Order sound is off - click to turn on'; }
  else { text = 'Sound on'; tip = 'Order sound is on - click to turn off'; }
  btn.textContent = text;
  btn.title = tip;
  btn.style.background = alert ? '#f56d63' : '';
  btn.style.color = alert ? '#fff' : '';
  btn.style.borderColor = alert ? '#f56d63' : '';
}

// Called after every bell poll with the latest items from the server.
function woAlarmCheck(items) {
  const item = (items || []).find((i) => i && i.type === 'website_orders');
  const count = item ? (Number(item.count) || 0) : 0;
  const rose = count > 0 && (woAlarm.prev === null || count > woAlarm.prev);
  woAlarm.prev = count;
  woAlarm.count = count;
  if (count === 0) {
    woAlarm.ringing = false;
    woAlarm.silenced = false;
    woAlarm.played = 0;
  } else if (rose) {
    woAlarm.ringing = true;
    woAlarm.silenced = false;
    woAlarm.played = 0;
  }
  woAlarmSyncLoop();
  woAlarmPaintBtn();
}
// ---- Website-order alarm (end)

async function loadNotificationsSummary() {
  const bellBtn = document.getElementById('notif-bell-btn');
  if (!bellBtn) return;
  try {
    const branchId = currentUser?.role === 'branch_manager' ? currentUser?.branch_id : currentBranchId();
    const summary = await Api.notificationsSummary(branchId);
    const state = notifState();
    state.items = summary.items || [];
    state.total = summary.total || 0;
    woAlarmCheck(state.items);
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

document.getElementById('notif-bell-btn')?.addEventListener('click', (e) => {
  e.stopPropagation();
  const panel = document.getElementById('notif-panel');
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.bell-wrap')) {
    const panel = document.getElementById('notif-panel');
    if (panel) panel.style.display = 'none';
  }
});

// Auto-refresh so the dashboard reflects sales/stock changes made elsewhere
// (POS, storefront) without the admin needing to manually reload the page.
// Paused while the tab is hidden so we're not hammering the API in the background.
const DASHBOARD_POLL_MS = 30000;
setInterval(() => {
  if (document.hidden) return;
  checkSyncStatus();
  loadAll();
  loadExpiryAlertBanner();
  loadNotificationsSummary();
}, DASHBOARD_POLL_MS);

// Website orders are time-sensitive, so check the bell more often than the
// rest of the dashboard -- and keep checking even when this tab is in the
// background (the browser may slow background timers to about once a minute).
setInterval(() => { loadNotificationsSummary(); }, 15000);

// ---------------------------------------------------- Expiry alert notifications
// September 2026 change spec section 6: a real push notification --
// super admin, branch manager, and every staff member at the affected
// branch -- rather than only a KPI number nobody sees unless they
// happen to load this page. Dashboard.html is every role's post-login
// landing page (see index.html), so this is where that notification
// actually reaches everyone.
//
// Deliberately NOT scoped to the dashboard's own "All branches" KPI
// filter above -- Api.myPendingExpiryAlerts() is pre-scoped server-
// side to the signed-in user's own branch (or every branch for a
// super admin), since this is "what's actionable for me", not "what
// am I currently looking at".

async function loadExpiryAlertBanner() {
  const banner = document.getElementById('expiry-alert-banner');
  let alerts;
  try {
    alerts = await Api.myPendingExpiryAlerts();
  } catch (err) {
    banner.style.display = 'none'; // e.g. a role this doesn't apply to
    return;
  }
  if (!alerts.length) {
    banner.style.display = 'none';
    banner.innerHTML = '';
    return;
  }
  const urgent = alerts.filter((a) => a.days_until_expiry <= 30).length;
  banner.style.display = 'block';
  banner.innerHTML = `
    <div class="error-banner visible" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <span>⏰ ${alerts.length} item(s) approaching their expiry date${urgent ? ` (${urgent} within 30 days)` : ''}.</span>
      <div style="display:flex;gap:8px;flex-shrink:0;">
        <a href="low-stock.html#expiry" class="btn btn-ghost btn-sm" style="text-decoration:none;">View details →</a>
        <button type="button" class="btn btn-ghost btn-sm" id="expiry-alert-dismiss-all">Dismiss all</button>
      </div>
    </div>`;
  document.getElementById('expiry-alert-dismiss-all').addEventListener('click', async (e) => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = 'Dismissing…';
    try {
      await Promise.all(alerts.map((a) => Api.dismissExpiryAlert(a.stock_batch_id)));
      banner.style.display = 'none';
      banner.innerHTML = '';
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Dismiss all';
    }
  });
}


// ======================================================================
// Customer-order bell -- added by fix-customer-requests.
// GET /notifications/summary now carries a "customer_requests" item: the
// number of customers whose requested item has reached the shelf and who
// have not collected it yet. It always shows in the bell list. On top of
// that the bell RINGS for it (animation + a soft chime) once each time
// the dashboard is opened, at most 3 times in each part of the day
// (morning before 12:00, afternoon 12:00-17:00, evening after 17:00),
// until the customer collects or the order is cancelled.
// ======================================================================
(function () {
  'use strict';
  var MAX_RINGS_PER_SESSION = 3;
  var lastRungSession = null;
  var audioCtx = null;

  try {
    if (typeof NOTIF_ICON_MAP !== 'undefined') {
      NOTIF_ICON_MAP.unshift({
        match: /customer order/i,
        color: '#2fbf8f',
        svg: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>'
      });
    }
  } catch (e) { /* icon is cosmetic */ }

  function partOfDay() {
    var h = new Date().getHours();
    return h < 12 ? 'morning' : (h < 17 ? 'afternoon' : 'evening');
  }
  function sessionKey() {
    var d = new Date();
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate() + '_' + partOfDay();
  }
  function readRings() {
    try { return Number(localStorage.getItem('nc_customer_bell_' + sessionKey())) || 0; } catch (e) { return 0; }
  }
  function writeRings(n) {
    try { localStorage.setItem('nc_customer_bell_' + sessionKey(), String(n)); } catch (e) { /* private mode */ }
  }

  function playChime() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      var play = function () {
        var t = audioCtx.currentTime;
        [880, 1175, 880, 1175].forEach(function (freq, i) {
          var o = audioCtx.createOscillator();
          var g = audioCtx.createGain();
          var start = t + i * 0.34;
          o.type = 'sine';
          o.frequency.value = freq;
          g.gain.setValueAtTime(0.0001, start);
          g.gain.exponentialRampToValueAtTime(0.22, start + 0.03);
          g.gain.exponentialRampToValueAtTime(0.0001, start + 0.5);
          o.connect(g);
          g.connect(audioCtx.destination);
          o.start(start);
          o.stop(start + 0.55);
        });
      };
      if (audioCtx.state === 'running') { play(); return; }
      // Browsers keep sound off until the page has been clicked once.
      var onGesture = function () {
        document.removeEventListener('click', onGesture, true);
        document.removeEventListener('keydown', onGesture, true);
        try { audioCtx.resume().then(play); } catch (e) { /* ignore */ }
      };
      document.addEventListener('click', onGesture, true);
      document.addEventListener('keydown', onGesture, true);
      audioCtx.resume().then(function () { if (audioCtx.state === 'running') { onGesture(); } }).catch(function () {});
    } catch (e) { /* sound is best-effort */ }
  }

  function ring() {
    var btn = document.getElementById('notif-bell-btn');
    if (btn) {
      btn.classList.add('bell-ringing');
      setTimeout(function () { btn.classList.remove('bell-ringing'); }, 9000);
    }
    playChime();
  }

  window.customerOrderBellCheck = function (items) {
    var item = (items || []).find(function (i) { return i && i.type === 'customer_requests'; });
    var count = item ? (Number(item.count) || 0) : 0;
    if (count <= 0) return;
    var key = sessionKey();
    if (lastRungSession === key) return;            // once per page open, per part of day
    lastRungSession = key;
    var rung = readRings();
    if (rung >= MAX_RINGS_PER_SESSION) return;      // already rang 3 times this part of day
    writeRings(rung + 1);
    ring();
  };

  // Tap every bell poll: dashboard.js already refreshes the summary every
  // 15 seconds through Api.notificationsSummary.
  if (typeof Api !== 'undefined' && typeof Api.notificationsSummary === 'function') {
    var original = Api.notificationsSummary;
    Api.notificationsSummary = function () {
      var result = original.apply(this, arguments);
      if (result && typeof result.then === 'function') {
        result.then(function (summary) {
          try { window.customerOrderBellCheck(summary && summary.items); } catch (e) { /* never break the bell */ }
        }).catch(function () {});
      }
      return result;
    };
  }
})();
