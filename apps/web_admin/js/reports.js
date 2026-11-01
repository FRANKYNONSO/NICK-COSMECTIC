
Auth.requireAuth();
Perms.applyNav();
if (typeof StoreBranding !== 'undefined') StoreBranding.refresh();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

document.getElementById('generated-at').innerHTML =
  `Generated ${new Date().toLocaleString('en-NG', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`;

// Platform update spec section 7: staff see their own BRANCH's summary,
// not platform-wide reports -- /reports/sales-summary is server-scoped
// to the caller's own branch when role==staff, same tier as Sales &
// Receipts / Open Bookings (spec section 4). Inventory analytics, top
// products, and team-wide sales are store-level views that still don't
// apply here.
if (Perms.isStaff()) {
  document.querySelectorAll('.admin-only-section').forEach((el) => { el.style.display = 'none'; });
  document.getElementById('branch-badge')?.remove();
  const subtitle = document.querySelector('.page-head p');
  if (subtitle) subtitle.textContent = 'Your branch\u2019s sales summary.';

  // Staff used to still see the empty headings/cards for sections they
  // can't load (inventory charts, losses, revenue-trends link). Hide the
  // whole block, not just the .admin-only-section tables.
  ['stock-overview-hint', 'inventory-kpis', 'stock-health-chart', 'units-by-category-chart',
    'top-stock-chart', 'category-breakdown-tbody', 'reports-losses-total', 'export-category-btn',
    'analysis-window'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    (el.closest('.chart-card, .table-card, .kpi-row, .toolbar, .section-hint') || el).style.display = 'none';
  });
  document.querySelectorAll('.section-eyebrow').forEach((el) => {
    const t = el.textContent.trim();
    if (t === 'Inventory analytics' || t === 'Losses') el.style.display = 'none';
    if (t === 'Revenue trends') { el.style.display = 'none'; if (el.nextElementSibling) el.nextElementSibling.style.display = 'none'; }
  });
  const catExport = document.getElementById('export-category-btn');
  if (catExport && catExport.parentElement) catExport.parentElement.style.display = 'none';
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
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 0 })}`;
}

// ------------------------------------------------------------ Dates (Lagos time)
// The old helpers used toISOString(), which is UTC, so between 00:00 and
// 01:00 Lagos time "today" was still yesterday. Everything below works
// on YYYY-MM-DD strings computed in Africa/Lagos.

function lagosDay(d = new Date()) {
  return d.toLocaleDateString('en-CA', { timeZone: 'Africa/Lagos' });
}
function todayStr() { return lagosDay(); }
function dateNDaysAgo(n) { return lagosDay(new Date(Date.now() - n * 86400000)); }
function startOfWeekStr() {
  const [y, m, d] = todayStr().split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() - dt.getUTCDay()); // Sunday start, same as before
  return dt.toISOString().slice(0, 10);
}
function startOfMonthStr() { return todayStr().slice(0, 8) + '01'; }
function daysBetween(fromDay, toDay) {
  return Math.round((Date.parse(toDay) - Date.parse(fromDay)) / 86400000);
}

// ------------------------------------------------------------ Product field helpers
// Field names the products API might use. If a name here doesn't match
// your schema, add the right one -- the sections that depend on it show
// a clear "not available" message instead of wrong numbers.
const COST_FIELDS = ['cost_price_naira', 'cost_naira', 'unit_cost_naira', 'buying_price_naira'];
const EXPIRY_FIELDS = ['nearest_expiry_date', 'earliest_expiry_date', 'expiry_date', 'expires_at'];
// A product explicitly flagged false on any of these was never stocked at
// the branch ("not carried" rule, same as the Restock List) and is left
// out of stock-health, reorder, and value figures.
const CARRIED_FLAGS = ['is_carried', 'ever_stocked', 'has_stock_batch'];
const SLOW_COVER_DAYS = 180;
const EXPIRY_WATCH_DAYS = 90;

function firstNumber(obj, fields) {
  for (const f of fields) {
    const v = obj[f];
    if (v !== undefined && v !== null && v !== '' && !isNaN(Number(v))) return Number(v);
  }
  return null;
}
const getCost = (p) => firstNumber(p, COST_FIELDS);
function getExpiry(p) {
  for (const f of EXPIRY_FIELDS) if (p[f]) return String(p[f]).slice(0, 10);
  return null;
}
const isCarried = (p) => !CARRIED_FLAGS.some((f) => p[f] === false);

// ------------------------------------------------------------ CSV export
function downloadCsv(filename, header, rows) {
  const esc = (v) => {
    const s = String(v == null ? '' : v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = '\ufeff' + [header, ...rows].map((r) => r.map(esc).join(',')).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------ Charts
const hasCharts = () => typeof Chart !== 'undefined';

// Chart.js defaults were hardcoded dark-theme colours, unreadable in
// light mode. Read them from the page's CSS variables instead, and
// re-apply on every inventory (re)render and theme toggle.
function applyChartTheme() {
  if (!hasCharts()) return;
  const cs = getComputedStyle(document.documentElement);
  const pick = (name, fallback) => (cs.getPropertyValue(name) || '').trim() || fallback;
  Chart.defaults.color = pick('--ink-muted', '#97a1b8');
  Chart.defaults.borderColor = pick('--line', pick('--border', '#232f49'));
  Chart.defaults.font.family = "'Inter', sans-serif";
}
applyChartTheme();

function chartFallback(canvasId, message) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const wrap = canvas.closest('.chart-container');
  if (wrap) wrap.innerHTML = `<p style="color:var(--ink-faint);font-size:12.5px;padding:16px 0;">${message}</p>`;
}

// ------------------------------------------------------------ Branch filter

let branchCache = [];
async function loadBranches() {
  try {
    branchCache = await Api.listBranches();
    BranchContext.renderBadge('branch-badge');
  } catch (err) { /* non-fatal */ }
}
function currentBranchId() {
  return BranchContext.get();
}
function windowDays() {
  return Number(document.getElementById('analysis-window')?.value || 90);
}

// -------------------------------------------------------------------- Sales summary

async function loadSalesSummary() {
  const periods = [
    { key: 'today', from: todayStr(), valueEl: 'kpi-today', subEl: 'kpi-today-sub' },
    { key: 'week', from: startOfWeekStr(), valueEl: 'kpi-week', subEl: 'kpi-week-sub' },
    { key: 'month', from: startOfMonthStr(), valueEl: 'kpi-month', subEl: 'kpi-month-sub' },
    { key: 'alltime', from: '2000-01-01', valueEl: 'kpi-alltime', subEl: 'kpi-alltime-sub' },
  ];
  await Promise.all(periods.map(async (p) => {
    try {
      const summary = await Api.salesSummary({ branchId: currentBranchId(), dateFrom: p.from });
      // Shown net of approved losses (damaged/expired/stolen stock), the
      // same way the dashboard's Total Revenue card does it.
      let lossNaira = 0;
      try {
        const losses = await Api.lossesSummary(currentBranchId(), p.from);
        lossNaira = losses.total_value_naira || 0;
      } catch (err) {
        lossNaira = 0; // fall back to gross sales if losses can't be loaded
      }
      document.getElementById(p.valueEl).textContent = formatNaira(summary.grand_total_naira);
      const count = summary.grand_sales_count || 0;
      document.getElementById(p.subEl).textContent =
        `${count} transaction${count === 1 ? '' : 's'}` +
        (lossNaira > 0 ? ` \u00B7 net of ${formatNaira(lossNaira)} in losses` : '');
    } catch (err) {
      document.getElementById(p.valueEl).textContent = '—';
    }
  }));
}

// ------------------------------------------------------------ Inventory analytics

let stockHealthChart = null, unitsByCategoryChart = null, topStockChart = null;
let productCache = [];
let categoryNameById = {};
let lastCatRows = [];
let lastReorderRows = [];
let lastSlowRows = [];

// ---- Patch 26a: profit, reorder suggestions, bookings -------------------
// Sales speed per product, filled in by loadSlowMovers() (same window).
let velocityCache = null; // { days, soldById, soldByName }
const FAST_RUNOUT_DAYS = 14; // flag products that will run out within this many days

function reorderCoverDays() {
  return Number(document.getElementById('reorder-cover-days')?.value || 30);
}
function perDaySales(p) {
  if (!velocityCache) return null;
  const sold = velocityCache.soldById[p.id] != null
    ? velocityCache.soldById[p.id]
    : (velocityCache.soldByName[String(p.name || '').trim().toLowerCase()] || 0);
  return sold / velocityCache.days;
}
document.getElementById('reorder-cover-days')?.addEventListener('change', () => renderReorderList());

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

// ---- Patch 26b: sales breakdown, returns & refunds, purchases & suppliers ----
const WEEKDAY_NAMES = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const METHOD_LABELS = {
  cash: 'Cash', transfer: 'Bank transfer', pos: 'POS / card', card: 'Card',
  credit: 'On credit', split: 'Split payment', exchange: 'Exchange', other: 'Other', unknown: 'Not recorded',
};
function methodLabel(m) {
  return METHOD_LABELS[m] || (m ? m.charAt(0).toUpperCase() + m.slice(1) : 'Not recorded');
}
function windowRange() {
  const days = windowDays();
  return { days, from: dateNDaysAgo(days - 1), to: todayStr() };
}
function barCell(percent) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  return `<div style="display:flex;align-items:center;gap:8px;min-width:120px;">` +
    `<div style="flex:1;height:6px;border-radius:3px;background:rgba(127,127,127,.2);">` +
    `<div style="width:${p}%;height:6px;border-radius:3px;background:currentColor;opacity:.55;"></div></div>` +
    `<span style="font-size:12px;color:var(--ink-muted);">${p ? p.toFixed(0) : 0}%</span></div>`;
}
function changeText(cur, prev, days) {
  if (!prev) return cur > 0 ? `New (nothing in the previous ${days} days)` : `No change vs previous ${days} days`;
  const pct = ((cur - prev) / prev) * 100;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(0)}% vs previous ${days} days`;
}
function fillTable(bodyId, emptyId, rowsHtml) {
  const body = document.getElementById(bodyId);
  const empty = document.getElementById(emptyId);
  if (body) body.innerHTML = rowsHtml;
  if (empty) empty.style.display = rowsHtml ? 'none' : 'block';
}
const fmtPct = (v) => (v == null ? '\u2014' : `${v}%`);
const dash = '\u2014';

async function loadSalesBreakdown() {
  if (Perms.isStaff() || !document.getElementById('breakdown-section')) return;
  const { days, from, to } = windowRange();
  try {
    const r = await Api.salesBreakdown({ branchId: currentBranchId(), dateFrom: from, dateTo: to });
    const c = r.current, p = r.previous;
    setText('bd-sales', formatNaira(c.revenue_naira));
    setText('bd-sales-sub', changeText(c.revenue_naira, p.revenue_naira, days));
    setText('bd-count', c.sales_count.toLocaleString());
    setText('bd-count-sub', `${c.units.toLocaleString()} units \u00B7 ${changeText(c.sales_count, p.sales_count, days)}`);
    setText('bd-avg', formatNaira(c.avg_sale_naira));
    setText('bd-avg-sub', changeText(c.avg_sale_naira, p.avg_sale_naira, days));
    setText('bd-discount', formatNaira(c.discounts_naira));
    setText('bd-discount-sub', changeText(c.discounts_naira, p.discounts_naira, days));

    fillTable('bd-pay-tbody', 'bd-pay-empty', r.by_payment_method.map((m) => `
      <tr>
        <td><strong>${escapeHtml(methodLabel(m.method))}</strong></td>
        <td>${formatNaira(m.amount_naira)}</td>
        <td>${barCell(m.share_percent)}</td>
        <td>${m.payments.toLocaleString()}</td>
      </tr>`).join(''));

    fillTable('bd-cat-tbody', 'bd-cat-empty', r.by_category.map((k) => `
      <tr>
        <td><strong>${escapeHtml(k.category_name)}</strong></td>
        <td>${k.units.toLocaleString()}</td>
        <td>${formatNaira(k.revenue_naira)}</td>
        <td>${formatNaira(k.cost_naira)}</td>
        <td><strong>${formatNaira(k.profit_naira)}</strong></td>
        <td>${fmtPct(k.margin_percent)}</td>
      </tr>`).join(''));

    const maxDay = Math.max(0, ...r.by_weekday.map((d) => d.revenue_naira));
    fillTable('bd-day-tbody', 'bd-day-empty', r.by_weekday.some((d) => d.sales_count > 0)
      ? r.by_weekday.map((d) => `
      <tr>
        <td><strong>${WEEKDAY_NAMES[d.weekday]}</strong></td>
        <td>${d.sales_count.toLocaleString()}</td>
        <td>${formatNaira(d.revenue_naira)}</td>
        <td>${barCell(maxDay ? (d.revenue_naira / maxDay) * 100 : 0)}</td>
      </tr>`).join('') : '');

    const maxHour = Math.max(0, ...r.by_hour.map((h) => h.revenue_naira));
    const hourLabel = (h) => `${String(h).padStart(2, '0')}:00\u2013${String((h + 1) % 24).padStart(2, '0')}:00`;
    fillTable('bd-hour-tbody', 'bd-hour-empty', r.by_hour.filter((h) => h.sales_count > 0).map((h) => `
      <tr>
        <td><strong>${hourLabel(h.hour)}</strong></td>
        <td>${h.sales_count.toLocaleString()}</td>
        <td>${formatNaira(h.revenue_naira)}</td>
        <td>${barCell(maxHour ? (h.revenue_naira / maxHour) * 100 : 0)}</td>
      </tr>`).join(''));
  } catch (err) {
    showToast(err.message, true);
  }
}

let lastReturnProducts = [];
let lastSupplierRows = [];

async function loadReturnsReport() {
  if (Perms.isStaff() || !document.getElementById('returns-section')) return;
  const { days, from, to } = windowRange();
  try {
    const r = await Api.returnsReport({ branchId: currentBranchId(), dateFrom: from, dateTo: to });
    setText('rt-count', r.refund_count.toLocaleString());
    setText('rt-count-sub', `${formatNaira(r.refunded_naira)} refunded in ${days} days`);
    setText('rt-rate', fmtPct(r.rate_by_count_percent));
    setText('rt-rate-sub', `of ${r.sales_count.toLocaleString()} sales \u00B7 ${fmtPct(r.rate_by_value_percent)} of sales value`);
    setText('rt-pending', r.pending_count.toLocaleString());
    setText('rt-pending-sub', r.pending_count ? `${formatNaira(r.pending_naira)} still to pay out` : 'Nothing waiting');
    const top = r.by_reason[0];
    setText('rt-reason', top ? top.reason : dash);
    setText('rt-reason-sub', top ? `${top.count} refund${top.count === 1 ? '' : 's'}` : 'No refunds in this window');

    lastReturnProducts = r.by_product;
    fillTable('rt-prod-tbody', 'rt-prod-empty', r.by_product.map((p) => `
      <tr>
        <td><strong>${escapeHtml(p.product_name)}</strong></td>
        <td>${p.units_returned.toLocaleString()}</td>
        <td>${p.units_sold.toLocaleString()}</td>
        <td>${fmtPct(p.return_rate_percent)}</td>
        <td>${formatNaira(p.refund_value_naira)}</td>
        <td>${p.restocked_units.toLocaleString()}</td>
        <td>${p.spoiled_units.toLocaleString()}</td>
      </tr>`).join(''));

    fillTable('rt-reason-tbody', 'rt-reason-empty', r.by_reason.map((x) => `
      <tr><td>${escapeHtml(x.reason)}</td><td>${x.count}</td><td>${formatNaira(x.amount_naira)}</td></tr>`).join(''));
    fillTable('rt-staff-tbody', 'rt-staff-empty', r.by_staff.map((x) => `
      <tr><td><strong>${escapeHtml(x.staff_name)}</strong></td><td>${x.count}</td><td>${formatNaira(x.amount_naira)}</td></tr>`).join(''));
    fillTable('rt-action-tbody', 'rt-action-empty', r.by_action.map((x) => `
      <tr><td>${escapeHtml(methodLabel(x.action))}</td><td>${x.count}</td><td>${formatNaira(x.amount_naira)}</td></tr>`).join(''));
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadPurchasesReport() {
  if (Perms.isStaff() || !document.getElementById('purchases-section')) return;
  const { days, from, to } = windowRange();
  try {
    const r = await Api.purchasesReport({ branchId: currentBranchId(), dateFrom: from, dateTo: to });
    setText('pu-goods', formatNaira(r.goods_naira));
    setText('pu-goods-sub', `${r.invoice_count} invoice${r.invoice_count === 1 ? '' : 's'} in ${days} days`);
    setText('pu-extra', formatNaira(r.extra_cost_naira));
    setText('pu-extra-sub', r.goods_naira
      ? `${((r.extra_cost_naira / r.goods_naira) * 100).toFixed(1)}% on top of goods (waybill, transit, handling)`
      : 'Waybill, transit and handling');
    setText('pu-paid', formatNaira(r.paid_naira));
    setText('pu-paid-sub', r.adjustments_naira ? `${formatNaira(r.adjustments_naira)} adjusted for returns/damage` : 'Paid on these invoices');
    setText('pu-owed', formatNaira(r.balance_owed_naira));
    setText('pu-owed-sub', 'Still owed to suppliers');

    lastSupplierRows = r.by_supplier;
    fillTable('pu-supplier-tbody', 'pu-supplier-empty', r.by_supplier.map((s) => `
      <tr>
        <td><strong>${escapeHtml(s.supplier_name)}</strong></td>
        <td>${s.invoices}</td>
        <td>${formatNaira(s.goods_naira)}</td>
        <td>${formatNaira(s.extra_cost_naira)}${s.extra_cost_percent != null ? ` <span style="color:var(--ink-faint);font-size:12px;">(${s.extra_cost_percent}%)</span>` : ''}</td>
        <td><strong>${formatNaira(s.total_naira)}</strong></td>
        <td>${s.balance_owed_naira > 0 ? formatNaira(s.balance_owed_naira) : dash}</td>
        <td>${s.last_purchase_date || dash}</td>
      </tr>`).join(''));

    fillTable('pu-unpaid-tbody', 'pu-unpaid-empty', r.unpaid_invoices.map((u) => `
      <tr>
        <td><strong>${escapeHtml(u.supplier_name)}</strong></td>
        <td>${u.invoice_date}</td>
        <td>${formatNaira(u.amount_naira)}</td>
        <td>${formatNaira(u.paid_naira)}</td>
        <td><strong>${formatNaira(u.balance_naira)}</strong></td>
      </tr>`).join(''));

    fillTable('pu-month-tbody', 'pu-month-empty', r.by_month.map((m) => `
      <tr>
        <td><strong>${m.month}</strong></td>
        <td>${m.invoices}</td>
        <td>${formatNaira(m.goods_naira)}</td>
        <td>${formatNaira(m.extra_cost_naira)}</td>
        <td><strong>${formatNaira(m.total_naira)}</strong></td>
      </tr>`).join(''));

    fillTable('pu-trend-tbody', 'pu-trend-empty', r.cost_trends.map((t) => {
      const up = t.change_percent != null && t.change_percent > 0;
      const down = t.change_percent != null && t.change_percent < 0;
      return `
      <tr>
        <td><strong>${escapeHtml(t.product_name)}</strong></td>
        <td>${formatNaira(t.previous_cost_naira)}</td>
        <td>${formatNaira(t.latest_cost_naira)}</td>
        <td>${t.change_percent == null ? dash : `<strong>${up ? '+' : ''}${t.change_percent}%</strong>${up ? ' <span class="category-badge">Costlier</span>' : (down ? ' <span class="category-badge">Cheaper</span>' : '')}`}</td>
        <td>${t.purchases}</td>
        <td>${t.latest_date}${t.latest_supplier ? ` \u00B7 ${escapeHtml(t.latest_supplier)}` : ''}</td>
      </tr>`;
    }).join(''));
  } catch (err) {
    showToast(err.message, true);
  }
}

// ---- Patch 26c: stock movements, transfers & receiving, stock count variance ----
let movementLabelMap = {};
let lastMovementEvents = [];
let lastMovementName = '';
let lastVarianceProducts = [];

function fillMovementProductList() {
  const list = document.getElementById('mv-product-list');
  if (!list) return;
  movementLabelMap = {};
  list.innerHTML = (productCache || []).map((p) => {
    const label = p.sku ? `${p.name} (${p.sku})` : p.name;
    movementLabelMap[label.toLowerCase()] = p.id;
    if (!(p.name.toLowerCase() in movementLabelMap)) movementLabelMap[p.name.toLowerCase()] = p.id;
    return `<option value="${escapeHtml(label).replace(/"/g, '&quot;')}"></option>`;
  }).join('');
}

function qtyCell(e) {
  if (e.direction === 'moved' && e.quantity === 0) return '0';
  if (e.direction === 'moved') return `${e.quantity} moved`;
  return `<strong>${e.quantity > 0 ? '+' : ''}${e.quantity}</strong>`;
}

async function loadStockMovements() {
  if (Perms.isStaff() || !document.getElementById('movements-section')) return;
  const typed = (document.getElementById('mv-product')?.value || '').trim().toLowerCase();
  const productId = movementLabelMap[typed];
  if (!productId) {
    lastMovementEvents = [];
    setText('mv-title', 'Pick a product above to see everything that happened to its stock.');
    ['mv-received', 'mv-sold', 'mv-adjusted', 'mv-moved'].forEach((id) => { setText(id, dash); setText(`${id}-sub`, '\u00A0'); });
    fillTable('mv-tbody', 'mv-empty', '');
    const hint = document.getElementById('mv-empty');
    if (hint && hint.firstElementChild) hint.firstElementChild.textContent = 'Choose a product to see its history';
    return;
  }
  const { days, from, to } = windowRange();
  try {
    const r = await Api.stockMovements({ productId, branchId: currentBranchId(), dateFrom: from, dateTo: to });
    lastMovementName = r.product_name;
    setText('mv-title', `${r.product_name}${r.sku ? ` (${r.sku})` : ''} \u2014 last ${days} days`);
    setText('mv-received', r.received_units.toLocaleString());
    setText('mv-received-sub', 'Units received from suppliers');
    setText('mv-sold', r.sold_units.toLocaleString());
    setText('mv-sold-sub', r.restocked_units ? `${r.restocked_units} returned to stock` : 'Units sold');
    setText('mv-adjusted', `${r.adjusted_net_units > 0 ? '+' : ''}${r.adjusted_net_units}`);
    setText('mv-adjusted-sub', 'Net of damage, expiry, theft and counts');
    setText('mv-moved', r.transferred_units.toLocaleString());
    setText('mv-moved-sub', `${r.transfer_count} transfer${r.transfer_count === 1 ? '' : 's'}`);
    lastMovementEvents = r.events;
    fillTable('mv-tbody', 'mv-empty', r.events.map((e) => `
      <tr>
        <td>${escapeHtml(fmtDateTime(e.occurred_at))}</td>
        <td><strong>${escapeHtml(e.label)}</strong></td>
        <td>${e.location ? escapeHtml(e.location) : dash}</td>
        <td>${qtyCell(e)}</td>
        <td>${e.by_name ? escapeHtml(e.by_name) : dash}</td>
        <td>${e.detail ? escapeHtml(e.detail) : dash}</td>
      </tr>`).join('') + (r.truncated
      ? '<tr><td colspan="6" style="text-align:center;color:var(--ink-faint);">Showing the newest 400 events \u2014 choose a shorter window to see the rest.</td></tr>' : ''));
    const emptyEl = document.getElementById('mv-empty');
    if (emptyEl && emptyEl.firstElementChild && !r.events.length) {
      emptyEl.firstElementChild.textContent = 'No stock movement for this product in this window';
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadTransfersReport() {
  if (Perms.isStaff() || !document.getElementById('transfers-section')) return;
  const { days, from, to } = windowRange();
  try {
    const r = await Api.transfersReport({ branchId: currentBranchId(), dateFrom: from, dateTo: to });
    setText('tr-count', r.transfer_count.toLocaleString());
    setText('tr-count-sub', `${r.units_moved.toLocaleString()} units moved${r.value_naira ? ` \u00B7 ${formatNaira(r.value_naira)}` : ''}`);
    setText('tr-waiting', r.return_requests_waiting.toLocaleString());
    setText('tr-waiting-sub', r.return_requests_waiting ? `${formatNaira(r.return_requests_waiting_naira)} awaiting an answer` : 'No return requests waiting');
    setText('tr-short', r.short_units.toLocaleString());
    setText('tr-short-sub', r.short_lines ? `across ${r.short_lines} delivery line${r.short_lines === 1 ? '' : 's'} \u00B7 ${formatNaira(r.short_value_naira)}` : `Nothing received short in ${days} days`);
    setText('tr-intake', (r.intake_damaged_units + r.intake_missing_units).toLocaleString());
    setText('tr-intake-sub', `${r.intake_damaged_units} damaged \u00B7 ${r.intake_missing_units} missing on ${r.intake_receipts} waybill report${r.intake_receipts === 1 ? '' : 's'}`);

    fillTable('tr-route-tbody', 'tr-route-empty', r.by_route.map((x) => `
      <tr><td><strong>${escapeHtml(x.from_name)}</strong> \u2192 <strong>${escapeHtml(x.to_name)}</strong></td><td>${x.count}</td><td>${x.units.toLocaleString()}</td><td>${x.value_naira ? formatNaira(x.value_naira) : dash}</td></tr>`).join(''));
    fillTable('tr-type-tbody', 'tr-type-empty', r.by_type.map((x) => `
      <tr><td>${escapeHtml(x.label)}</td><td>${x.count}</td><td>${x.units.toLocaleString()}</td></tr>`).join(''));
    fillTable('tr-prod-tbody', 'tr-prod-empty', r.top_products.map((x) => `
      <tr><td><strong>${escapeHtml(x.product_name)}</strong></td><td>${x.transfers}</td><td>${x.units.toLocaleString()}</td></tr>`).join(''));
    fillTable('tr-recent-tbody', 'tr-recent-empty', r.recent.map((x) => `
      <tr>
        <td>${escapeHtml(fmtDateTime(x.occurred_at))}</td>
        <td><strong>${escapeHtml(x.product_name)}</strong></td>
        <td>${escapeHtml(x.from_name)} \u2192 ${escapeHtml(x.to_name)}</td>
        <td>${escapeHtml(x.label)}</td>
        <td>${x.units.toLocaleString()}</td>
        <td>${x.by_name ? escapeHtml(x.by_name) : dash}</td>
      </tr>`).join(''));
    fillTable('tr-wait-tbody', 'tr-wait-empty', r.waiting_returns.map((x) => `
      <tr>
        <td><strong>${escapeHtml(x.product_name)}</strong></td>
        <td>${escapeHtml(x.from_name)} \u2192 ${escapeHtml(x.to_name)}</td>
        <td>${x.units.toLocaleString()}</td>
        <td>${formatNaira(x.amount_naira)}</td>
        <td>${escapeHtml(fmtDateTime(x.requested_at))}</td>
        <td>${x.started_by_side === 'sender' ? 'Sending side' : 'Receiving side'}</td>
      </tr>`).join(''));
    fillTable('tr-short-tbody', 'tr-short-empty', r.short_deliveries.map((x) => `
      <tr>
        <td>${escapeHtml(fmtDateTime(x.occurred_at))}</td>
        <td><strong>${escapeHtml(x.product_name)}</strong></td>
        <td>${x.supplier_name ? escapeHtml(x.supplier_name) : dash}</td>
        <td>${x.branch_name ? escapeHtml(x.branch_name) : dash}</td>
        <td>${x.invoiced.toLocaleString()}</td>
        <td>${x.received.toLocaleString()}</td>
        <td><strong>${x.short.toLocaleString()}</strong></td>
        <td>${x.short_value_naira ? formatNaira(x.short_value_naira) : dash}</td>
      </tr>`).join(''));
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadCountVariance() {
  if (Perms.isStaff() || !document.getElementById('variance-section')) return;
  const { days, from, to } = windowRange();
  try {
    const r = await Api.countVariance({ branchId: currentBranchId(), dateFrom: from, dateTo: to });
    setText('cv-count', r.entries.toLocaleString());
    setText('cv-count-sub', r.entries ? `${r.matched_counts} matched exactly` : `No stock counts in ${days} days`);
    setText('cv-short', r.short_units.toLocaleString());
    setText('cv-short-sub', `${formatNaira(r.short_naira)} at cost`);
    setText('cv-surplus', r.surplus_units.toLocaleString());
    setText('cv-surplus-sub', `${formatNaira(r.surplus_naira)} at cost`);
    setText('cv-net', `${r.net_naira > 0 ? '+' : ''}${formatNaira(r.net_naira)}`);
    setText('cv-net-sub', r.net_naira < 0 ? 'Net shortage' : (r.net_naira > 0 ? 'Net surplus' : 'Balanced'));

    lastVarianceProducts = r.by_product;
    fillTable('cv-prod-tbody', 'cv-prod-empty', r.by_product.map((x) => `
      <tr>
        <td><strong>${escapeHtml(x.product_name)}</strong>${x.recounts >= 3 && x.short_units > 0 ? ' <span class="category-badge">Keeps coming up short</span>' : ''}</td>
        <td>${x.recounts}</td>
        <td>${x.short_units.toLocaleString()}</td>
        <td>${x.surplus_units.toLocaleString()}</td>
        <td>${x.short_naira ? formatNaira(x.short_naira) : dash}</td>
        <td><strong>${x.net_naira > 0 ? '+' : ''}${formatNaira(x.net_naira)}</strong></td>
      </tr>`).join(''));
    const groupRows = (rows) => rows.map((x) => `
      <tr><td><strong>${escapeHtml(x.name)}</strong></td><td>${x.entries}</td><td>${x.short_units.toLocaleString()}</td><td>${x.surplus_units.toLocaleString()}</td><td>${x.net_naira > 0 ? '+' : ''}${formatNaira(x.net_naira)}</td></tr>`).join('');
    fillTable('cv-branch-tbody', 'cv-branch-empty', groupRows(r.by_branch));
    fillTable('cv-staff-tbody', 'cv-staff-empty', groupRows(r.by_staff));
    fillTable('cv-recent-tbody', 'cv-recent-empty', r.recent.map((x) => `
      <tr>
        <td>${escapeHtml(fmtDateTime(x.occurred_at))}</td>
        <td><strong>${escapeHtml(x.product_name)}</strong></td>
        <td>${x.branch_name ? escapeHtml(x.branch_name) : dash}</td>
        <td><strong>${x.delta > 0 ? '+' : ''}${x.delta}</strong></td>
        <td>${x.value_naira ? `${x.value_naira > 0 ? '+' : ''}${formatNaira(x.value_naira)}` : dash}</td>
        <td>${x.by_name ? escapeHtml(x.by_name) : dash}</td>
        <td>${x.notes ? escapeHtml(x.notes) : dash}</td>
      </tr>`).join(''));
  } catch (err) {
    showToast(err.message, true);
  }
}

document.getElementById('mv-load-btn')?.addEventListener('click', () => loadStockMovements());
document.getElementById('mv-product')?.addEventListener('change', () => loadStockMovements());
document.getElementById('mv-export-btn')?.addEventListener('click', () => {
  if (!lastMovementEvents.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`stock-movements-${todayStr()}.csv`,
    ['Product', 'Date', 'Event', 'Where', 'Quantity', 'Direction', 'By', 'Details'],
    lastMovementEvents.map((e) => [lastMovementName, e.occurred_at, e.label, e.location || '', e.quantity, e.direction, e.by_name || '', e.detail || '']));
});
document.getElementById('cv-export-btn')?.addEventListener('click', () => {
  if (!lastVarianceProducts.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`stock-count-variance-${todayStr()}.csv`,
    ['Product', 'Counts', 'Short units', 'Surplus units', 'Short value (NGN)', 'Net value (NGN)'],
    lastVarianceProducts.map((x) => [x.product_name, x.recounts, x.short_units, x.surplus_units, x.short_naira, x.net_naira]));
});

document.getElementById('export-returns-btn')?.addEventListener('click', () => {
  if (!lastReturnProducts.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`returned-products-${todayStr()}.csv`,
    ['Product', 'Units returned', 'Units sold', 'Return rate %', 'Refund value (NGN)', 'Restocked', 'Spoiled'],
    lastReturnProducts.map((p) => [p.product_name, p.units_returned, p.units_sold,
      p.return_rate_percent == null ? '' : p.return_rate_percent, p.refund_value_naira, p.restocked_units, p.spoiled_units]));
});
document.getElementById('export-suppliers-btn')?.addEventListener('click', () => {
  if (!lastSupplierRows.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`supplier-spend-${todayStr()}.csv`,
    ['Supplier', 'Invoices', 'Goods (NGN)', 'Extra costs (NGN)', 'Total (NGN)', 'Paid (NGN)', 'Still owed (NGN)', 'Last purchase'],
    lastSupplierRows.map((s) => [s.supplier_name, s.invoices, s.goods_naira, s.extra_cost_naira, s.total_naira,
      s.paid_naira, s.balance_owed_naira, s.last_purchase_date || '']));
});

async function loadProfitSummary() {
  if (Perms.isStaff() || !document.getElementById('profit-section')) return;
  const periods = [
    { key: 'today', from: todayStr(), el: 'profit-today' },
    { key: 'week', from: startOfWeekStr(), el: 'profit-week' },
    { key: 'month', from: startOfMonthStr(), el: 'profit-month' },
    { key: 'alltime', from: '2000-01-01', el: 'profit-alltime' },
  ];
  let note = '';
  await Promise.all(periods.map(async (p) => {
    try {
      const s = await Api.profitSummary({ branchId: currentBranchId(), dateFrom: p.from });
      setText(p.el, formatNaira(s.gross_profit_naira));
      const margin = s.margin_percent == null ? '\u2014' : `${s.margin_percent}%`;
      setText(`${p.el}-sub`,
        `Margin ${margin} \u00B7 sales ${formatNaira(s.revenue_naira)}` +
        (s.losses_naira > 0 ? ` \u00B7 after losses ${formatNaira(s.net_profit_naira)}` : ''));
      if (p.key === 'alltime' && s.estimated_cost_lines > 0) {
        note = ` ${s.estimated_cost_lines} of ${s.total_lines} sale lines had no batch recorded, so today's cost price was used for them (an estimate).`;
      }
    } catch (err) {
      setText(p.el, '\u2014');
      setText(`${p.el}-sub`, 'Could not load');
    }
  }));
  setText('profit-note',
    'Gross profit = sales (after discounts) minus what the stock cost you. Cost comes from the batch each item was sold from.' + note);
}

async function loadBookingsReport() {
  if (Perms.isStaff() || !document.getElementById('bookings-section')) return;
  try {
    const r = await Api.bookingsReport(currentBranchId());
    setText('bk-open', r.open_count);
    setText('bk-open-sub', r.open_count ? `Oldest is ${r.oldest_age_days} day${r.oldest_age_days === 1 ? '' : 's'} old` : 'No open bookings');
    setText('bk-units', r.units_held);
    setText('bk-units-sub', r.customer_order_units
      ? `plus ${r.customer_order_units} held for customer orders`
      : 'Units reserved from sale');
    setText('bk-deposits', formatNaira(r.deposits_held_naira));
    setText('bk-deposits-sub', `Balance still owed ${formatNaira(r.balance_owed_naira)}`);
    setText('bk-refund', r.awaiting_refund_count);
    setText('bk-refund-sub', r.awaiting_refund_count
      ? `${formatNaira(r.awaiting_refund_naira)} to send back`
      : 'Nothing waiting');

    const dash = '\u2014';
    const who = (name, phone) => (name || phone)
      ? `<strong>${escapeHtml(name || 'Customer')}</strong>${phone ? `<br><span style="color:var(--ink-faint);font-size:12px;">${escapeHtml(phone)}</span>` : ''}`
      : 'Walk-in';

    const openBody = document.getElementById('bk-open-tbody');
    const openEmpty = document.getElementById('bk-open-empty');
    if (!r.open_bookings.length) {
      openBody.innerHTML = '';
      openEmpty.style.display = 'block';
    } else {
      openEmpty.style.display = 'none';
      openBody.innerHTML = r.open_bookings.map((b) => `
        <tr>
          <td>${who(b.customer_name, b.customer_phone)}</td>
          <td>${escapeHtml(b.items_summary)}</td>
          <td>${b.units}</td>
          <td>${formatNaira(b.total_naira)}</td>
          <td>${formatNaira(b.paid_naira)}</td>
          <td><strong>${formatNaira(b.balance_naira)}</strong></td>
          <td>${b.age_days} day${b.age_days === 1 ? '' : 's'}${b.age_days >= 30 ? ' <span class="category-badge">Follow up</span>' : ''}</td>
          <td>${b.pickup_note ? escapeHtml(b.pickup_note) : dash}</td>
          <td>${escapeHtml(b.branch_name || dash)}</td>
        </tr>`).join('');
    }

    const resBody = document.getElementById('bk-reserved-tbody');
    const resEmpty = document.getElementById('bk-reserved-empty');
    if (!r.reserved_stock.length) {
      resBody.innerHTML = '';
      resEmpty.style.display = 'block';
    } else {
      resEmpty.style.display = 'none';
      resBody.innerHTML = r.reserved_stock.map((s) => `
        <tr>
          <td><strong>${escapeHtml(s.product_name)}</strong></td>
          <td>${s.on_hand}</td>
          <td>${s.on_open_bookings}</td>
          <td>${s.on_customer_orders}</td>
          <td><strong>${s.sellable}</strong>${s.over_committed ? ' <span class="category-badge">Over-committed</span>' : ''}</td>
        </tr>`).join('');
    }

    const refBody = document.getElementById('bk-refund-tbody');
    const refEmpty = document.getElementById('bk-refund-empty');
    if (!r.awaiting_refund.length) {
      refBody.innerHTML = '';
      refEmpty.style.display = 'block';
    } else {
      refEmpty.style.display = 'none';
      refBody.innerHTML = r.awaiting_refund.map((c) => `
        <tr>
          <td>${who(c.customer_name, c.customer_phone)}</td>
          <td><strong>${formatNaira(c.amount_naira)}</strong></td>
          <td>${escapeHtml(fmtDateTime(c.cancelled_at))}</td>
          <td>${c.reason ? escapeHtml(c.reason) : dash}</td>
          <td>${escapeHtml(c.branch_name || dash)}</td>
        </tr>`).join('');
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

function categoryNameOf(p) {
  if (!p.category_id) return 'Uncategorized';
  return categoryNameById[p.category_id] || 'Unknown';
}

async function loadInventoryAnalytics() {
  try {
    applyChartTheme();
    const [allProducts, categories] = await Promise.all([
      Api.listProducts({ branchId: currentBranchId() }),
      Api.listCategories(),
    ]);
    categoryNameById = Object.fromEntries(categories.map((c) => [c.id, c.name]));
    const products = allProducts.filter(isCarried);
    productCache = products;

    // ---- Valuation: retail (selling price) and, where the API gives a
    // cost price, cost value and potential margin.
    let totalUnits = 0, retailValue = 0, costValue = 0, retailOfCosted = 0, costedCount = 0;
    products.forEach((p) => {
      const qty = p.quantity_available || 0;
      totalUnits += qty;
      retailValue += qty * (p.price_naira || 0);
      const cost = getCost(p);
      if (cost !== null) {
        costedCount += 1;
        costValue += qty * cost;
        retailOfCosted += qty * (p.price_naira || 0);
      }
    });
    const costNote = costedCount === 0 ? '' : (costedCount < products.length ? ` · cost ${formatNaira(costValue)} (${costedCount}/${products.length} products)` : ` · cost ${formatNaira(costValue)}`);
    document.getElementById('stock-overview-hint').textContent =
      `${products.length} products · ${totalUnits.toLocaleString()} units · retail ${formatNaira(retailValue)}${costNote}`;

    document.getElementById('inv-kpi-retail').textContent = formatNaira(retailValue);
    if (costedCount === 0) {
      document.getElementById('inv-kpi-cost').textContent = 'Not available';
      document.getElementById('inv-kpi-cost-sub').textContent = 'The products API isn\u2019t returning a cost price yet';
      document.getElementById('inv-kpi-margin').textContent = 'Not available';
      document.getElementById('inv-kpi-margin-sub').textContent = 'Needs cost price';
    } else {
      const margin = retailOfCosted - costValue;
      const pct = retailOfCosted > 0 ? Math.round((margin / retailOfCosted) * 100) : 0;
      document.getElementById('inv-kpi-cost').textContent = formatNaira(costValue);
      document.getElementById('inv-kpi-cost-sub').textContent = costedCount < products.length
        ? `Based on ${costedCount} of ${products.length} products with a cost price`
        : 'Units on hand × cost price';
      document.getElementById('inv-kpi-margin').textContent = formatNaira(margin);
      document.getElementById('inv-kpi-margin-sub').textContent = `${pct}% of retail value`;
    }

    // ---- Stock health
    let ok = 0, low = 0, out = 0;
    products.forEach((p) => {
      const qty = p.quantity_available || 0;
      const reorder = p.reorder_level || 0;
      if (qty <= 0) out++;
      else if (qty <= reorder) low++;
      else ok++;
    });
    document.getElementById('inv-kpi-reorder').textContent = String(low + out);
    document.getElementById('inv-kpi-reorder-sub').textContent = `${out} out of stock · ${low} low`;

    const healthCtx = document.getElementById('stock-health-chart');
    if (hasCharts() && healthCtx) {
      if (stockHealthChart) stockHealthChart.destroy();
      stockHealthChart = new Chart(healthCtx, {
        type: 'doughnut',
        data: {
          labels: ['In stock', 'Low stock', 'Out of stock'],
          datasets: [{ data: [ok, low, out], backgroundColor: ['#34d399', '#f2b84b', '#f56d63'], borderWidth: 0 }],
        },
        options: { responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { display: false } } },
      });
    } else if (!hasCharts()) {
      chartFallback('stock-health-chart', 'Chart library unavailable.');
    }
    document.getElementById('stock-health-legend').innerHTML = [
      ['In stock', '#34d399', ok], ['Low stock', '#f2b84b', low], ['Out of stock', '#f56d63', out],
    ].map(([label, color, count]) => `
      <div class="legend-row"><span class="dot" style="background:${color}"></span>${label} (${count})</div>`).join('');

    // ---- Units by category
    const byCategory = {};
    products.forEach((p) => {
      const key = p.category_id || 'uncategorized';
      if (!byCategory[key]) byCategory[key] = { name: categoryNameOf(p), units: 0, products: 0, value: 0 };
      byCategory[key].units += p.quantity_available || 0;
      byCategory[key].products += 1;
      byCategory[key].value += (p.quantity_available || 0) * (p.price_naira || 0);
    });
    const catRows = Object.values(byCategory).sort((a, b) => b.units - a.units);
    lastCatRows = catRows;

    const catCtx = document.getElementById('units-by-category-chart');
    if (hasCharts() && catCtx) {
      if (unitsByCategoryChart) unitsByCategoryChart.destroy();
      unitsByCategoryChart = new Chart(catCtx, {
        type: 'bar',
        data: {
          labels: catRows.map((c) => c.name),
          datasets: [{ data: catRows.map((c) => c.units), backgroundColor: '#4f8cff', borderRadius: 5, maxBarThickness: 42 }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true }, x: { grid: { display: false } } },
        },
      });
    } else if (!hasCharts()) {
      chartFallback('units-by-category-chart', 'Chart library unavailable — see the table below instead.');
    }

    const maxUnits = Math.max(...catRows.map((c) => c.units), 1);
    document.getElementById('category-breakdown-tbody').innerHTML = catRows.map((c) => `
      <tr>
        <td><strong>${escapeHtml(c.name)}</strong></td>
        <td>${c.products}</td>
        <td>${c.units}</td>
        <td>${formatNaira(c.value)}</td>
        <td>
          <div class="share-row">
            <div class="share-bar-track"><div class="share-bar-fill" style="width:${(c.units / maxUnits) * 100}%"></div></div>
            <span class="share-pct">${totalUnits ? Math.round((c.units / totalUnits) * 100) : 0}%</span>
          </div>
        </td>
      </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--ink-faint);">No categories yet</td></tr>';

    // ---- Top stock levels
    const topStock = products.slice().sort((a, b) => (b.quantity_available || 0) - (a.quantity_available || 0)).slice(0, 8);
    const stockCtx = document.getElementById('top-stock-chart');
    if (hasCharts() && stockCtx) {
      if (topStockChart) topStockChart.destroy();
      topStockChart = new Chart(stockCtx, {
        type: 'bar',
        data: {
          labels: topStock.map((p) => p.name),
          datasets: [{ data: topStock.map((p) => p.quantity_available || 0), backgroundColor: '#34d399', borderRadius: 5, maxBarThickness: 22 }],
        },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { x: { beginAtZero: true }, y: { grid: { display: false } } },
        },
      });
    } else if (!hasCharts()) {
      chartFallback('top-stock-chart', 'Chart library unavailable.');
    }

    renderReorderList();
    renderExpiryWatch();
  } catch (err) {
    showToast(err.message, true);
  }
}

// ------------------------------------------------------------ Reorder list

function renderReorderList() {
  const tbody = document.getElementById('reorder-tbody');
  const empty = document.getElementById('reorder-empty');
  if (!tbody) return;
  const cover = reorderCoverDays();
  const rows = productCache
    .map((p) => {
      const qty = p.quantity_available || 0;
      const level = p.reorder_level || 0;
      const perDay = perDaySales(p);
      const hasSales = perDay !== null && perDay > 0;
      const daysLeft = hasSales ? Math.floor(qty / perDay) : null;
      const out = qty <= 0;
      const low = qty <= level;
      const fast = !out && !low && daysLeft !== null && daysLeft <= FAST_RUNOUT_DAYS;
      const short = Math.max(level - qty, 0);
      // Order enough to reach `cover` days of stock at the recent selling
      // speed, and never less than what gets it back to the reorder level.
      let suggest = null;
      if (hasSales) suggest = Math.max(Math.ceil(perDay * cover) - qty, short, 0);
      else if (out || low) suggest = short;
      const statusLabel = out ? 'Out of stock' : (low ? 'Low stock' : 'Selling fast');
      return { p, qty, level, out, low, fast, short, daysLeft, hasSales, suggest, statusLabel };
    })
    .filter((r) => r.out || r.low || r.fast)
    .sort((a, b) =>
      (Number(b.out) - Number(a.out)) ||
      (Number(b.low) - Number(a.low)) ||
      (b.short - a.short) ||
      ((a.daysLeft === null ? 1e9 : a.daysLeft) - (b.daysLeft === null ? 1e9 : b.daysLeft)) ||
      a.p.name.localeCompare(b.p.name));

  lastReorderRows = rows;
  document.getElementById('reorder-sub').textContent = rows.length
    ? `${rows.length} product${rows.length === 1 ? '' : 's'} out of stock, at/below reorder level, or selling fast. Suggested order tops up to ${cover} days of stock at the recent selling speed.`
    : 'Products that are out of stock, at/below their reorder level, or about to run out.';

  if (!rows.length) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  const shown = rows.slice(0, 50);
  tbody.innerHTML = shown.map((r) => `
    <tr>
      <td><strong>${escapeHtml(r.p.name)}</strong></td>
      <td>${escapeHtml(categoryNameOf(r.p))}</td>
      <td><span class="category-badge">${r.statusLabel}</span></td>
      <td>${r.qty}</td>
      <td>${r.level}</td>
      <td>${r.short || '\u2014'}</td>
      <td>${r.hasSales ? r.daysLeft : '<span style="color:var(--ink-faint);">No recent sales</span>'}</td>
      <td>${r.suggest === null ? '\u2014' : `<strong>${r.suggest}</strong>`}</td>
    </tr>`).join('') + (rows.length > shown.length
    ? `<tr><td colspan="8" style="text-align:center;color:var(--ink-faint);">Showing ${shown.length} of ${rows.length} \u2014 export the CSV for the full list.</td></tr>` : '');
}

// ------------------------------------------------------------ Expiry watch

function renderExpiryWatch() {
  const tbody = document.getElementById('expiry-tbody');
  const empty = document.getElementById('expiry-empty');
  const emptyText = document.getElementById('expiry-empty-text');
  if (!tbody) return;

  const withExpiry = productCache.filter((p) => getExpiry(p));
  if (!withExpiry.length) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    emptyText.textContent = 'Expiry dates aren\u2019t coming through from the products API yet';
    return;
  }
  const today = todayStr();
  const rows = withExpiry
    .map((p) => ({ p, exp: getExpiry(p), days: daysBetween(today, getExpiry(p)), qty: p.quantity_available || 0 }))
    .filter((r) => r.qty > 0 && r.days <= EXPIRY_WATCH_DAYS)
    .sort((a, b) => a.days - b.days);

  if (!rows.length) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    emptyText.textContent = `Nothing expired or expiring in the next ${EXPIRY_WATCH_DAYS} days`;
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = rows.slice(0, 50).map((r) => {
    const status = r.days < 0 ? `Expired ${Math.abs(r.days)} day${Math.abs(r.days) === 1 ? '' : 's'} ago`
      : r.days === 0 ? 'Expires today' : `Expires in ${r.days} day${r.days === 1 ? '' : 's'}`;
    return `
    <tr>
      <td><strong>${escapeHtml(r.p.name)}</strong></td>
      <td>${escapeHtml(categoryNameOf(r.p))}</td>
      <td>${r.qty}</td>
      <td>${escapeHtml(r.exp)}</td>
      <td><span class="category-badge">${status}</span></td>
    </tr>`;
  }).join('');
}

// -------------------------------------------------------------- Top products

async function loadTopProducts() {
  const tbody = document.getElementById('top-products-tbody');
  const emptyState = document.getElementById('top-products-empty');
  try {
    const rows = await Api.productPerformance({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(windowDays()), limit: 10 });
    if (!rows.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = rows.map((r, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><strong>${escapeHtml(r.product_name)}</strong></td>
        <td>${r.category_name ? `<span class="category-badge">${escapeHtml(r.category_name)}</span>` : '—'}</td>
        <td>${r.quantity_sold}</td>
        <td><strong>${formatNaira(r.revenue_naira)}</strong></td>
        <td>${r.profit_naira == null ? '\u2014' : formatNaira(r.profit_naira)}</td>
        <td>${r.margin_percent == null ? '\u2014' : r.margin_percent + '%'}</td>
      </tr>`).join('');
  } catch (err) {
    showToast(err.message, true);
  }
}

// -------------------------------------------------------------- Slow movers

async function loadSlowMovers() {
  const tbody = document.getElementById('slow-tbody');
  const empty = document.getElementById('slow-empty');
  if (!tbody) return;
  const days = windowDays();
  try {
    // Pull sales for every product in the window (not just the top 10),
    // then compare against what's on the shelf.
    const sales = await Api.productPerformance({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(days), limit: 1000 });
    const soldById = {}, soldByName = {};
    sales.forEach((r) => {
      if (r.product_id != null) soldById[r.product_id] = (soldById[r.product_id] || 0) + (r.quantity_sold || 0);
      const k = String(r.product_name || '').trim().toLowerCase();
      soldByName[k] = (soldByName[k] || 0) + (r.quantity_sold || 0);
    });

    velocityCache = { days, soldById, soldByName };
    renderReorderList();

    const rows = productCache
      .filter((p) => (p.quantity_available || 0) > 0)
      .map((p) => {
        const qty = p.quantity_available || 0;
        const sold = soldById[p.id] != null ? soldById[p.id] : (soldByName[String(p.name || '').trim().toLowerCase()] || 0);
        const cover = sold > 0 ? Math.round(qty / (sold / days)) : null; // null = no sales at all
        const unitValue = getCost(p) !== null ? getCost(p) : (p.price_naira || 0);
        return { p, qty, sold, cover, value: qty * unitValue };
      })
      .filter((r) => r.sold === 0 || r.cover > SLOW_COVER_DAYS)
      .sort((a, b) => b.value - a.value);

    lastSlowRows = rows;
    document.getElementById('slow-sub').textContent =
      `In-stock products with no sales, or more than ${SLOW_COVER_DAYS} days of cover, in the last ${days} days. Value uses cost price where available, otherwise selling price.`;

    if (!rows.length) {
      tbody.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    const shown = rows.slice(0, 25);
    tbody.innerHTML = shown.map((r) => `
      <tr>
        <td><strong>${escapeHtml(r.p.name)}</strong></td>
        <td>${escapeHtml(categoryNameOf(r.p))}</td>
        <td>${r.qty}</td>
        <td>${r.sold}</td>
        <td>${r.cover === null ? '<span class="category-badge">No sales</span>' : r.cover.toLocaleString()}</td>
        <td>${formatNaira(r.value)}</td>
      </tr>`).join('') + (rows.length > shown.length
      ? `<tr><td colspan="6" style="text-align:center;color:var(--ink-faint);">Showing ${shown.length} of ${rows.length} — export the CSV for the full list.</td></tr>` : '');
  } catch (err) {
    showToast(err.message, true);
  }
}

// -------------------------------------------------------------- Branch comparison

async function loadBranchComparison() {
  const card = document.getElementById('branch-compare-card');
  const tbody = document.getElementById('branch-compare-tbody');
  if (!card || !tbody) return;
  // Only meaningful on "All branches" with more than one branch.
  if (Perms.isStaff() || currentBranchId() || branchCache.length < 2) {
    card.style.display = 'none';
    return;
  }
  try {
    const results = await Promise.all(branchCache.map(async (b) => {
      const products = (await Api.listProducts({ branchId: b.id })).filter(isCarried);
      let units = 0, value = 0, low = 0, out = 0;
      products.forEach((p) => {
        const qty = p.quantity_available || 0;
        units += qty;
        value += qty * (p.price_naira || 0);
        if (qty <= 0) out++;
        else if (qty <= (p.reorder_level || 0)) low++;
      });
      return { name: b.name, count: products.length, units, value, low, out };
    }));
    tbody.innerHTML = results.map((r) => `
      <tr>
        <td><strong>${escapeHtml(r.name)}</strong></td>
        <td>${r.count}</td>
        <td>${r.units.toLocaleString()}</td>
        <td>${formatNaira(r.value)}</td>
        <td>${r.low}</td>
        <td>${r.out}</td>
      </tr>`).join('');
    card.style.display = '';
  } catch (err) {
    card.style.display = 'none';
  }
}

// -------------------------------------------------------------------------- Boot

async function loadAll() {
  await loadSalesSummary();
  if (!Perms.isStaff()) {
    await loadInventoryAnalytics();
    await Promise.all([loadTopProducts(), loadSlowMovers()]);
    await loadTeamSales();
    await loadLossesTotal();
    await loadBranchComparison();
    await Promise.all([loadProfitSummary(), loadBookingsReport()]);
    await Promise.all([loadSalesBreakdown(), loadReturnsReport(), loadPurchasesReport()]);
    fillMovementProductList();
    await Promise.all([loadStockMovements(), loadTransfersReport(), loadCountVariance()]);
  }
}

// Everything that depends on the branch, including the two history
// tables that used to stay stale after a branch switch.
async function refreshAll() {
  await loadAll();
  if (!Perms.isStaff()) {
    await loadCouponHistory();
    await loadPriceChangeHistory();
  }
}
window.addEventListener('branch-context-changed', refreshAll);

document.getElementById('analysis-window')?.addEventListener('change', () => {
  loadTopProducts();
  loadSlowMovers();
  loadSalesBreakdown();
  loadReturnsReport();
  loadPurchasesReport();
  loadStockMovements();
  loadTransfersReport();
  loadCountVariance();
});

// Theme toggle: re-draw charts so axis/label colours follow the theme.
document.getElementById('theme-toggle-btn')?.addEventListener('click', () => {
  if (!Perms.isStaff()) setTimeout(loadInventoryAnalytics, 150);
});

document.getElementById('export-reorder-btn')?.addEventListener('click', () => {
  if (!lastReorderRows.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`reorder-list-${todayStr()}.csv`,
    ['Product', 'Category', 'Status', 'On hand', 'Reorder level', 'Below level by', 'Days left', 'Suggested order qty'],
    lastReorderRows.map((r) => [r.p.name, categoryNameOf(r.p), r.statusLabel, r.qty, r.level, r.short,
      r.daysLeft === null ? '' : r.daysLeft, r.suggest === null ? '' : r.suggest]));
});
document.getElementById('export-slow-btn')?.addEventListener('click', () => {
  if (!lastSlowRows.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`slow-moving-stock-${todayStr()}.csv`,
    ['Product', 'Category', 'On hand', `Sold in last ${windowDays()} days`, 'Days of cover', 'Tied-up value (NGN)'],
    lastSlowRows.map((r) => [r.p.name, categoryNameOf(r.p), r.qty, r.sold, r.cover === null ? 'No sales' : r.cover, r.value]));
});
document.getElementById('export-category-btn')?.addEventListener('click', () => {
  if (!lastCatRows.length) { showToast('Nothing to export.', true); return; }
  downloadCsv(`category-breakdown-${todayStr()}.csv`,
    ['Category', 'Products', 'Units', 'Retail value (NGN)'],
    lastCatRows.map((c) => [c.name, c.products, c.units, c.value]));
});

// September 2026 change spec section 17: Losses moved into Reports --
// this card's total is deliberately the overall all-time figure
// (Api.lossesSummary with no start/end date), not scoped to "this
// month", matching the same default already fixed on the Losses page
// itself.
async function loadLossesTotal() {
  const totalEl = document.getElementById('reports-losses-total');
  if (!totalEl) return;
  try {
    const summary = await Api.lossesSummary(currentBranchId());
    totalEl.textContent = formatNaira(summary.total_value_naira);
    document.getElementById('reports-losses-sub').textContent =
      `${summary.total_quantity} unit(s) across damaged, stolen, expired, and other write-offs`;
    renderLossBreakdown(summary);
  } catch (err) {
    totalEl.textContent = '—';
  }
}

// Breakdown by reason / by product. This only renders if the losses
// summary endpoint returns it (by_reason and/or by_product, as an array
// or an object keyed by name). Until the backend adds those, the card
// just shows the total as before.
function renderLossBreakdown(summary) {
  const el = document.getElementById('reports-losses-breakdown');
  if (!el) return;
  const normalise = (src, nameKeys) => {
    if (!src) return [];
    const list = Array.isArray(src)
      ? src.map((r) => ({ name: nameKeys.map((k) => r[k]).find(Boolean) || 'Other', value: r.total_value_naira ?? r.value_naira ?? r.value ?? 0, qty: r.total_quantity ?? r.quantity ?? null }))
      : Object.entries(src).map(([name, v]) => (typeof v === 'object'
        ? { name, value: v.total_value_naira ?? v.value_naira ?? v.value ?? 0, qty: v.total_quantity ?? v.quantity ?? null }
        : { name, value: Number(v) || 0, qty: null }));
    return list.sort((a, b) => b.value - a.value);
  };
  const block = (title, rows) => rows.length ? `
    <div style="margin-bottom:10px;">
      <div style="font-size:12px;color:var(--ink-muted);margin-bottom:4px;">${title}</div>
      ${rows.slice(0, 6).map((r) => `<div class="legend-row"><span style="flex:1;">${escapeHtml(String(r.name).replace(/_/g, ' '))}</span><strong>${formatNaira(r.value)}</strong>${r.qty != null ? `<span style="color:var(--ink-faint);margin-left:8px;">${r.qty} unit(s)</span>` : ''}</div>`).join('')}
    </div>` : '';
  el.innerHTML = block('By reason', normalise(summary.by_reason, ['reason', 'name']))
    + block('Biggest losses by product', normalise(summary.by_product, ['product_name', 'name']));
}

async function loadTeamSales() {
  try {
    const sales = await Api.listSales({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(365) });
    // Same calendar windows as the KPI cards at the top (this week =
    // since Sunday, this month = since the 1st), all in Lagos time. The
    // old rolling 7/30-day windows disagreed with those cards.
    const today = todayStr(), weekStart = startOfWeekStr(), monthStart = startOfMonthStr();
    const byWorker = {};
    sales.forEach((s) => {
      const name = s.cashier_name || 'Unknown';
      if (!byWorker[name]) byWorker[name] = { today: 0, week: 0, month: 0, all: 0, count: 0 };
      const total = s.total_naira || 0;
      const day = lagosDay(new Date(s.created_at));
      byWorker[name].all += total;
      byWorker[name].count += 1;
      if (day === today) byWorker[name].today += total;
      if (day >= weekStart) byWorker[name].week += total;
      if (day >= monthStart) byWorker[name].month += total;
    });
    const rows = Object.entries(byWorker).sort((a, b) => b[1].all - a[1].all);
    document.getElementById('worker-sales-tbody').innerHTML = rows.map(([name, w]) => `
      <tr>
        <td><strong>${escapeHtml(name)}</strong></td>
        <td>${formatNaira(w.today)}</td>
        <td>${formatNaira(w.week)}</td>
        <td>${formatNaira(w.month)}</td>
        <td><strong>${formatNaira(w.all)}</strong></td>
        <td>${w.count}</td>
      </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--ink-faint);">No sales recorded yet</td></tr>';
  } catch (err) {
    showToast(err.message, true);
  }
}

// -------------------------------------------------------------------------- Coupon traceability

function fmtDateTime(dateStr) {
  if (!dateStr) return '—';
  return new Date(dateStr).toLocaleString('en-NG', {
    month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: true, timeZone: 'Africa/Lagos',
  });
}

const couponStatusLabels = {
  active: 'Active',
  redeemed: 'Redeemed',
  voided: 'Voided',
  expired: 'Expired (unused)',
};

// Small "Refund" summary cell for the coupon traceability table --
// consistent with how refund status reads elsewhere (Losses, Invoice
// refunds section): a plain label, not a full receipt.
function couponRefundCellHtml(refunds) {
  if (!refunds || !refunds.length) return '—';
  const total = refunds.reduce((sum, r) => sum + r.amount_naira, 0);
  const anyPending = refunds.some((r) => r.status === 'pending');
  const label = anyPending ? 'Refund pending' : 'Refunded';
  return `<span class="category-badge">${label} · ${formatNaira(total)}</span>`;
}

async function loadCouponIssuerFilter() {
  const select = document.getElementById('coupon-issuer-filter');
  if (!select) return;
  try {
    const staff = await Api.listUsers({});
    select.innerHTML = '<option value="">All staff</option>' +
      staff.map((u) => `<option value="${u.id}">${escapeHtml(u.full_name)}</option>`).join('');
    select.addEventListener('change', () => loadCouponHistory());
  } catch (err) {
    // Non-fatal -- the history table still loads unfiltered.
  }
}

// Price change history -- per-branch price override feature spec item
// 5. Mirrors loadCouponHistory()'s shape (branch-scoped via
// BranchContext, same table-card/tbody/empty-state pattern). Both
// action types ('global_price_changed' and
// 'branch_price_override_changed') render in one table, distinguished
// by the Type column, since the backend already returns both together.
async function loadPriceChangeHistory() {
  const tbody = document.getElementById('price-change-history-tbody');
  const emptyState = document.getElementById('price-change-history-empty');
  if (!tbody) return;
  try {
    const rows = await Api.priceChangeHistory(BranchContext.get());
    if (!rows.length) {
      tbody.innerHTML = '';
      if (emptyState) emptyState.style.display = '';
      return;
    }
    if (emptyState) emptyState.style.display = 'none';
    tbody.innerHTML = rows.map((r) => `
      <tr>
        <td>${escapeHtml(r.product_name || '—')}</td>
        <td><span class="category-badge">${r.action === 'global_price_changed' ? 'Global' : 'Branch override'}</span></td>
        <td>${escapeHtml(r.branch_name || (r.action === 'global_price_changed' ? 'All branches' : '—'))}</td>
        <td>${escapeHtml(r.changed_by_name || '—')}</td>
        <td>${r.old_price_naira != null ? formatNaira(r.old_price_naira) : '—'}</td>
        <td>${r.new_price_naira != null ? formatNaira(r.new_price_naira) : '—'}</td>
        <td>${fmtDateTime(r.created_at)}</td>
      </tr>`).join('');
  } catch (err) {
    showToast(err.message, true);
  }
}

// PATCH7A-COUPON-CREDIT-LINE: small grey line under the code when the coupon
// was made from a store credit (CHG code). Hand-made coupons show nothing.
function couponCreditLineHtml(c) {
  if (!c || !c.credit_reference_code) return '';
  return `<div style="font-size:12px;color:#888;margin-top:2px;">From store credit ${escapeHtml(c.credit_reference_code)}</div>`;
}

async function loadCouponHistory() {
  const tbody = document.getElementById('coupon-history-tbody');
  const emptyState = document.getElementById('coupon-history-empty');
  if (!tbody) return;
  try {
    const issuedBy = document.getElementById('coupon-issuer-filter')?.value || undefined;
    // Spec section 2: branch-scoped when a specific branch is active
    // (via the shared BranchContext -- same pattern every other report
    // on this page already uses), full cross-branch trace when the
    // header is on "All branches".
    const rows = await Api.couponHistory(issuedBy, BranchContext.get());
    if (!rows.length) {
      tbody.innerHTML = '';
      if (emptyState) emptyState.style.display = '';
      return;
    }
    if (emptyState) emptyState.style.display = 'none';
    tbody.innerHTML = rows.map((c) => `
      <tr>
        <td><strong>${escapeHtml(c.code)}</strong>${couponCreditLineHtml(c)}</td>
        <td>${formatNaira(c.discount_amount_naira)}</td>
        <td><span class="category-badge">${couponStatusLabels[c.effective_status] || escapeHtml(c.effective_status)}</span></td>
        <td>${escapeHtml(c.issued_by_name || '—')}</td>
        <td>${fmtDateTime(c.created_at)}</td>
        <td>${escapeHtml(c.redeemed_by_name || '—')}</td>
        <td>${escapeHtml(c.redeemed_branch_name || '—')}</td>
        <td>${fmtDateTime(c.redeemed_at)}</td>
        <td>${
          c.redeemed_items && c.redeemed_items.length
            ? `<button type="button" class="btn btn-ghost btn-sm coupon-view-items-btn" data-code="${escapeHtml(c.code)}">View items</button>`
            : '—'
        }</td>
        <td>${couponRefundCellHtml(c.refunds)}</td>
      </tr>`).join('');
    tbody.dataset.rows = JSON.stringify(rows);
    tbody.querySelectorAll('.coupon-view-items-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const rowsData = JSON.parse(tbody.dataset.rows || '[]');
        const coupon = rowsData.find((r) => r.code === btn.dataset.code);
        openCouponTraceReceipt(coupon);
      });
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

// Shows the full transaction receipt for whichever sale this coupon
// was redeemed on -- same rendering used on Sales & Receipts, via the
// shared Receipt.render() (see js/receipt.js) -- rather than a bare
// item list, so a manager tracing a coupon sees exactly what the
// customer's receipt looked like.
const couponReceiptBackdrop = document.getElementById('receipt-modal-backdrop');
async function openCouponTraceReceipt(coupon) {
  if (!coupon || !coupon.redeemed_sale_id) {
    showToast('No transaction is linked to this coupon.', true);
    return;
  }
  try {
    const sale = await Api.getSale(coupon.redeemed_sale_id);
    Receipt.render(document.getElementById('receipt-sheet'), sale, StoreBranding.current());
    couponReceiptBackdrop.classList.add('visible');
  } catch (err) {
    showToast(err.message, true);
  }
}
document.getElementById('receipt-close')?.addEventListener('click', () => couponReceiptBackdrop.classList.remove('visible'));
document.getElementById('receipt-print')?.addEventListener('click', () => Receipt.print());
couponReceiptBackdrop?.addEventListener('click', (e) => { if (e.target === couponReceiptBackdrop) couponReceiptBackdrop.classList.remove('visible'); });

(async () => {
  await loadBranches();
  await loadAll();
  if (!Perms.isStaff()) {
    await loadCouponIssuerFilter();
    await loadCouponHistory();
    await loadPriceChangeHistory();
  }
  await initStatementsSection();
})();

// ---------------------------------------------------------------- Account statements

async function initStatementsSection() {
  // download_statements is a plain permission -- on by default for
  // super_admin/branch_manager, but grantable to any individual staff
  // account via a custom role on the Roles page, so gate on the
  // permission itself rather than role.
  if (!Perms.can('download_statements')) return;
  document.getElementById('statements-section-title').style.display = '';
  document.getElementById('statements-section-hint').style.display = '';
  document.getElementById('statements-card').style.display = '';

  try {
    const staff = await Api.listUsers({});
    document.getElementById('statement-staff-filter').innerHTML =
      '<option value="">All staff</option>' +
      staff.map((u) => `<option value="${u.id}">${escapeHtml(u.full_name)}</option>`).join('');
  } catch (err) { /* non-fatal -- "All staff" still works without the list */ }

  try {
    const branches = await Api.listBranches();
    document.getElementById('statement-branch-filter').innerHTML =
      '<option value="">All branches</option>' +
      branches.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
  } catch (err) { /* non-fatal */ }

  async function runDownload(btnId, format) {
    const btn = document.getElementById(btnId);
    btn.disabled = true;
    try {
      await Api.downloadStatement({
        staffId: document.getElementById('statement-staff-filter').value || undefined,
        branchId: document.getElementById('statement-branch-filter').value || undefined,
        dateFrom: document.getElementById('statement-date-from').value || undefined,
        dateTo: document.getElementById('statement-date-to').value || undefined,
        format,
      });
    } catch (err) {
      showToast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }
  document.getElementById('download-statement-btn').addEventListener('click', () => runDownload('download-statement-btn', 'csv'));
  document.getElementById('download-statement-pdf-btn').addEventListener('click', () => runDownload('download-statement-pdf-btn', 'pdf'));
}
