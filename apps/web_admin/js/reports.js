
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

function todayStr() { return new Date().toISOString().slice(0, 10); }
function startOfWeekStr() {
  const d = new Date();
  const day = d.getDay(); // 0 = Sunday
  d.setDate(d.getDate() - day);
  return d.toISOString().slice(0, 10);
}
function startOfMonthStr() {
  const d = new Date();
  d.setDate(1);
  return d.toISOString().slice(0, 10);
}

const hasCharts = () => typeof Chart !== 'undefined';
if (hasCharts()) {
  Chart.defaults.color = '#97a1b8';
  Chart.defaults.borderColor = '#232f49';
  Chart.defaults.font.family = "'Inter', sans-serif";
}

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
window.addEventListener('branch-context-changed', loadAll);

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
      document.getElementById(p.valueEl).textContent = formatNaira(summary.grand_total_naira);
      const count = summary.grand_sales_count || 0;
      document.getElementById(p.subEl).textContent = `${count} transaction${count === 1 ? '' : 's'}`;
    } catch (err) {
      document.getElementById(p.valueEl).textContent = '—';
    }
  }));
}

// ------------------------------------------------------------ Inventory analytics

let stockHealthChart = null, unitsByCategoryChart = null, topStockChart = null;

async function loadInventoryAnalytics() {
  try {
    const [products, categories] = await Promise.all([
      Api.listProducts({ branchId: currentBranchId() }),
      Api.listCategories(),
    ]);

    const totalUnits = products.reduce((s, p) => s + (p.quantity_available || 0), 0);
    const totalValue = products.reduce((s, p) => s + (p.quantity_available || 0) * (p.price_naira || 0), 0);
    document.getElementById('stock-overview-hint').textContent =
      `${products.length} products · ${totalUnits.toLocaleString()} units · ${formatNaira(totalValue)} value`;

    let ok = 0, low = 0, out = 0;
    products.forEach((p) => {
      const qty = p.quantity_available || 0;
      const reorder = p.reorder_level || 0;
      if (qty <= 0) out++;
      else if (qty <= reorder) low++;
      else ok++;
    });
    const healthCtx = document.getElementById('stock-health-chart');
    if (hasCharts()) {
      if (stockHealthChart) stockHealthChart.destroy();
      stockHealthChart = new Chart(healthCtx, {
        type: 'doughnut',
        data: {
          labels: ['In stock', 'Low stock', 'Out of stock'],
          datasets: [{ data: [ok, low, out], backgroundColor: ['#34d399', '#f2b84b', '#f56d63'], borderWidth: 0 }],
        },
        options: { responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { display: false } } },
      });
    } else {
      chartFallback('stock-health-chart', 'Chart library unavailable.');
    }
    document.getElementById('stock-health-legend').innerHTML = [
      ['In stock', '#34d399', ok], ['Low stock', '#f2b84b', low], ['Out of stock', '#f56d63', out],
    ].map(([label, color, count]) => `
      <div class="legend-row"><span class="dot" style="background:${color}"></span>${label} (${count})</div>`).join('');

    const catNameById = Object.fromEntries(categories.map((c) => [c.id, c.name]));
    const byCategory = {};
    products.forEach((p) => {
      const key = p.category_id || 'uncategorized';
      const name = p.category_id ? (catNameById[p.category_id] || 'Unknown') : 'Uncategorized';
      if (!byCategory[key]) byCategory[key] = { name, units: 0, products: 0, value: 0 };
      byCategory[key].units += p.quantity_available || 0;
      byCategory[key].products += 1;
      byCategory[key].value += (p.quantity_available || 0) * (p.price_naira || 0);
    });
    const catRows = Object.values(byCategory).sort((a, b) => b.units - a.units);

    const catCtx = document.getElementById('units-by-category-chart');
    if (hasCharts()) {
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
    } else {
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
            <span class="share-pct">${Math.round((c.units / totalUnits) * 100) || 0}%</span>
          </div>
        </td>
      </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--ink-faint);">No categories yet</td></tr>';

    const topStock = products.slice().sort((a, b) => (b.quantity_available || 0) - (a.quantity_available || 0)).slice(0, 8);
    const stockCtx = document.getElementById('top-stock-chart');
    if (hasCharts()) {
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
    } else {
      chartFallback('top-stock-chart', 'Chart library unavailable.');
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

// -------------------------------------------------------------- Top products

async function loadTopProducts() {
  const tbody = document.getElementById('top-products-tbody');
  const emptyState = document.getElementById('top-products-empty');
  try {
    const rows = await Api.productPerformance({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(365), limit: 10 });
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
      </tr>`).join('');
  } catch (err) {
    showToast(err.message, true);
  }
}

// -------------------------------------------------------------------------- Boot

async function loadAll() {
  await loadSalesSummary();
  if (!Perms.isStaff()) {
    await loadInventoryAnalytics();
    await loadTopProducts();
    await loadTeamSales();
    await loadLossesTotal();
  }
}

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
  } catch (err) {
    totalEl.textContent = '—';
  }
}

function inRange(dateStr, days) {
  const d = new Date(dateStr);
  const now = new Date();
  if (days === 0) return d.toDateString() === now.toDateString();
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  return d >= cutoff;
}

async function loadTeamSales() {
  try {
    const sales = await Api.listSales({ branchId: currentBranchId(), dateFrom: dateNDaysAgo(365) });
    const byWorker = {};
    sales.forEach((s) => {
      const name = s.cashier_name || 'Unknown';
      if (!byWorker[name]) byWorker[name] = { today: 0, week: 0, month: 0, all: 0, count: 0 };
      const total = s.total_naira || 0;
      byWorker[name].all += total;
      byWorker[name].count += 1;
      if (inRange(s.created_at, 0)) byWorker[name].today += total;
      if (inRange(s.created_at, 7)) byWorker[name].week += total;
      if (inRange(s.created_at, 30)) byWorker[name].month += total;
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

  document.getElementById('download-statement-btn').addEventListener('click', async () => {
    const btn = document.getElementById('download-statement-btn');
    btn.disabled = true;
    try {
      await Api.downloadStatement({
        staffId: document.getElementById('statement-staff-filter').value || undefined,
        branchId: document.getElementById('statement-branch-filter').value || undefined,
        dateFrom: document.getElementById('statement-date-from').value || undefined,
        dateTo: document.getElementById('statement-date-to').value || undefined,
        format: 'csv',
      });
    } catch (err) {
      showToast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('download-statement-pdf-btn').addEventListener('click', async () => {
    const btn = document.getElementById('download-statement-pdf-btn');
    btn.disabled = true;
    try {
      await Api.downloadStatement({
        staffId: document.getElementById('statement-staff-filter').value || undefined,
        branchId: document.getElementById('statement-branch-filter').value || undefined,
        dateFrom: document.getElementById('statement-date-from').value || undefined,
        dateTo: document.getElementById('statement-date-to').value || undefined,
        format: 'pdf',
      });
    } catch (err) {
      showToast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
}
