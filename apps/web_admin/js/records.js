
Auth.requireAuth();
Perms.applyNav();
// Belt-and-braces redirect for a typed URL/stale bookmark -- the nav
// link itself is already hidden by Perms.applyNav() via
// NAV_REQUIREMENTS.records in permissions.js, same tier as GET
// /reports/performance-records's own view_all_reports check server-side.
Perms.guardPage('view_all_reports');
if (typeof StoreBranding !== 'undefined') StoreBranding.refresh();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier', store_assistant: 'Store assistant' }; // PATCHP3C-ROLE-LABEL
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

BranchContext.renderBadge('branch-badge');

let toastTimer = null;
function showToast(message, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 0 })}`;
}

// "Sales" is a plain count (number of completed sales that day), not a
// naira figure -- see MetricSnapshot's docstring and the daily rollup
// job (tasks/metric_snapshots.py), which stores it unconverted.
const METRIC_LABELS = {
  revenue: 'Revenue', inventory_investment: 'Inventory investment', sales: 'Sales', losses: 'Losses',
};
const PERIOD_LABELS = { daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly', yearly: 'Yearly', custom: 'Custom' };

function formatMetricValue(metric, value) {
  return metric === 'sales' ? Number(value || 0).toLocaleString('en-NG') : formatNaira(value);
}

let activeMetric = 'revenue';
let activePeriod = 'daily';
let recordsChart = null;

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}
function daysAgoIso(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

async function loadRecords() {
  const tbody = document.getElementById('records-tbody');
  const emptyState = document.getElementById('records-empty');
  const totalEl = document.getElementById('records-all-time-total');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="2">Loading…</td></tr>';
  emptyState.style.display = 'none';

  const params = {
    metric: activeMetric,
    // A custom range still buckets by day underneath -- there's no
    // separate "custom" bucket size on the backend, just a bounded
    // date window over the daily rollups.
    period: activePeriod === 'custom' ? 'daily' : activePeriod,
    branchId: BranchContext.get(),
  };

  if (activePeriod === 'custom') {
    const from = document.getElementById('records-custom-from').value;
    const to = document.getElementById('records-custom-to').value;
    if (!from || !to) {
      tbody.innerHTML = '';
      emptyState.style.display = '';
      renderRecordsChart([]);
      renderRecordsList([]);
      return;
    }
    params.dateFrom = from;
    params.dateTo = to;
    document.getElementById('records-period-label').textContent = `${from} \u2013 ${to} \u00b7 custom range`;
  } else {
    document.getElementById('records-period-label').textContent = PERIOD_LABELS[activePeriod];
  }

  let data;
  try {
    // NOTE: this passes dateFrom/dateTo through to Api.performanceRecords()
    // for a custom range. That requires js/api.js's performanceRecords()
    // to forward those two params as query params, and the backend route
    // (GET /reports/performance-records) to accept them as an optional
    // bounding window on top of period=daily. If either side doesn't
    // support that yet, a custom-range request will silently ignore the
    // dates and return the same thing as period=daily unbounded -- worth
    // a quick check against api.js and the router before shipping this.
    data = await Api.performanceRecords(params);
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }

  totalEl.textContent = formatMetricValue(activeMetric, data.all_time_total_naira);

  if (!data.rows.length) {
    tbody.innerHTML = '';
    emptyState.style.display = '';
    renderRecordsChart([]);
    renderRecordsList([]);
    return;
  }
  tbody.innerHTML = data.rows.map((r) => `
    <tr${r.in_progress ? ' style="font-style:italic;color:var(--ink-muted);"' : ''}>
      <td>${escapeHtml(r.period_label)}${r.in_progress ? ' (in progress)' : ''}</td>
      <td>${formatMetricValue(activeMetric, r.value_naira)}</td>
    </tr>`).join('');
  renderRecordsChart(data.rows);
  renderRecordsList(data.rows);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Animates a number from 0 up to endValue over `duration` ms -- the
// "My Proficiency"-style count-up, run fresh every time a tab is
// switched, not a perpetual live ticker.
function animateValue(el, endValue, formatFn, duration = 800) {
  if (!el) return;
  const start = performance.now();
  function step(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = formatFn(endValue * eased);
    if (t < 1) requestAnimationFrame(step);
    else el.textContent = formatFn(endValue);
  }
  requestAnimationFrame(step);
}

// Each row's fill bar is proportional to that row's share of the max
// value currently on screen, not a fixed scale -- so it stays meaningful
// whether today's biggest number is 500 or 500,000. A real nonzero value
// gets a minimum 4% sliver so it's never indistinguishable from a
// genuine zero.
function renderRecordsList(rows) {
  const container = document.getElementById('records-list');
  if (!container) return;
  if (!rows.length) {
    container.innerHTML = '';
    return;
  }

  const maxValue = Math.max(...rows.map((r) => r.value_naira), 1);
  const fillColor = activeMetric === 'losses' ? 'rgba(227,73,72,0.35)' : 'rgba(42,120,214,0.35)';

  container.innerHTML = rows.map((r, i) => `
    <div style="position:relative;background:var(--surface-1);border-radius:8px;overflow:hidden;margin-bottom:6px;">
      <div class="record-row-fill" data-row-index="${i}" style="position:absolute;inset:0;width:0%;background:${fillColor};transition:width 700ms ease-out;"></div>
      <div style="position:relative;display:flex;justify-content:space-between;align-items:center;padding:10px 14px;font-size:13px;${r.in_progress ? 'font-style:italic;color:var(--ink-muted);' : ''}">
        <span>${escapeHtml(r.period_label)}${r.in_progress ? ' (in progress)' : ''}</span>
        <span class="record-row-value" data-row-index="${i}">${formatMetricValue(activeMetric, 0)}</span>
      </div>
    </div>`).join('');

  requestAnimationFrame(() => {
    rows.forEach((r, i) => {
      const fillEl = container.querySelector(`.record-row-fill[data-row-index="${i}"]`);
      if (fillEl) {
        const pct = r.value_naira > 0 ? Math.max((r.value_naira / maxValue) * 100, 4) : 0;
        fillEl.style.width = pct + '%';
      }
      const valueEl = container.querySelector(`.record-row-value[data-row-index="${i}"]`);
      animateValue(valueEl, r.value_naira, (v) => formatMetricValue(activeMetric, v));
    });
  });
}

// Log scale can't plot a true zero -- substitute a tiny floor value for
// plotting only. Tooltips below always read the real value straight from
// `chronological`, never from this substituted number, so labels stay
// accurate even for genuinely-zero days.
const CHART_LOG_FLOOR = 1;

// How many recent periods to show by default before the person has to
// drag left to see older history -- keeps the chart from opening on a
// long stretch of inactive days. A custom range shows everything in
// the window (it's already bounded by the date pickers), so it isn't
// windowed further.
const CHART_WINDOW_SIZE = { daily: 14, weekly: 12, monthly: 12, yearly: 20, custom: Infinity };

if (typeof ChartDataLabels !== 'undefined') {
  Chart.register(ChartDataLabels);
}

function renderRecordsChart(rows) {
  const canvas = document.getElementById('records-chart');
  if (!canvas) return;
  if (recordsChart) {
    recordsChart.destroy();
    recordsChart = null;
  }
  if (!rows.length) return;

  const chronological = [...rows].reverse();
  const color = activeMetric === 'losses' ? '#e34948' : '#2a78d6';
  const fillColor = activeMetric === 'losses' ? 'rgba(227,73,72,0.12)' : 'rgba(42,120,214,0.12)';
  const windowSize = CHART_WINDOW_SIZE[activePeriod] || chronological.length;
  const minIndex = Math.max(0, chronological.length - windowSize);
  const maxIndex = chronological.length - 1;

  recordsChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: chronological.map((r) => r.period_label),
      datasets: [{
        label: METRIC_LABELS[activeMetric],
        data: chronological.map((r) => (r.value_naira > 0 ? r.value_naira : CHART_LOG_FLOOR)),
        borderColor: color,
        backgroundColor: fillColor,
        fill: true,
        tension: 0.3,
        pointRadius: 4,
        pointBackgroundColor: color,
        borderWidth: 2.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 26 } },
      animation: {
        duration: 800,
        easing: 'easeOutQuart',
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => formatMetricValue(activeMetric, chronological[ctx.dataIndex].value_naira),
          },
        },
        datalabels: {
          align: 'top',
          color: '#8b8f98',
          font: { size: 10 },
          formatter: (_v, ctx) => formatMetricValue(activeMetric, chronological[ctx.dataIndex].value_naira),
        },
        zoom: {
          pan: { enabled: true, mode: 'x', modifierKey: null },
          zoom: { wheel: { enabled: false }, pinch: { enabled: false } },
          limits: { x: { min: 0, max: maxIndex, minRange: Math.min(4, chronological.length) } },
        },
      },
      scales: {
        x: {
          min: minIndex,
          max: maxIndex,
          grid: { display: false },
        },
        y: {
          type: 'logarithmic',
          min: CHART_LOG_FLOOR,
          display: false,
        },
      },
    },
  });
}

document.getElementById('records-metric-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-metric]');
  if (!btn) return;
  activeMetric = btn.dataset.metric;
  document.querySelectorAll('#records-metric-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
  document.getElementById('records-metric-title').textContent = METRIC_LABELS[activeMetric];
  loadRecords();
});

document.getElementById('records-period-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-period]');
  if (!btn) return;
  activePeriod = btn.dataset.period;
  document.querySelectorAll('#records-period-tabs button').forEach((b) => b.classList.toggle('active', b === btn));

  const customRow = document.getElementById('records-custom-range');
  if (activePeriod === 'custom') {
    customRow.style.display = 'flex';
    // First time opening Custom this session, default to the last 7
    // days rather than leaving both fields blank -- after that, keep
    // whatever the person last picked.
    const fromInput = document.getElementById('records-custom-from');
    const toInput = document.getElementById('records-custom-to');
    if (!fromInput.value) fromInput.value = daysAgoIso(6);
    if (!toInput.value) toInput.value = todayIso();
  } else {
    customRow.style.display = 'none';
    document.getElementById('records-period-label').textContent = PERIOD_LABELS[activePeriod];
  }
  loadRecords();
});

document.getElementById('records-custom-apply').addEventListener('click', () => {
  loadRecords();
});

loadRecords();
