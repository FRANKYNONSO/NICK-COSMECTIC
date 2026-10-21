Auth.requireAuth();
Perms.applyNav();
// Section 15: Shifts now shares a page with Staff/Branches/Roles under
// one "Staff" nav item -- see roles.js for why a page-bouncing
// guardPage() here would be wrong now that this isn't its own page.
// In practice both super_admin and branch_manager have
// view_all_reports by default, so this only bites a branch_manager a
// super_admin has specifically revoked it from.
if (!Perms.can('view_all_reports')) {
  document.getElementById('tab-btn-shifts')?.remove();
}
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });


function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatDateTime(iso) {
  return iso ? new Date(iso).toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short', hour12: true, timeZone: 'Africa/Lagos' }) : '—';
}

let shiftsBranchCache = [];

async function loadBranches() {
  try {
    shiftsBranchCache = await Api.listBranches();
    BranchContext.renderBadge('branch-badge');
  } catch (err) {
    showToast(err.message, true);
    return;
  }
}

function branchName(branchId) {
  return shiftsBranchCache.find((b) => b.id === branchId)?.name || '—';
}

function renderRow(s) {
  const variance = s.status === 'closed' && s.closing_counted_naira != null
    ? s.closing_counted_naira - s.opening_cash_naira
    : null;
  const pendingConfirmation = s.auto_closed && s.closing_counted_naira == null;
  return `
    <tr>
      <td>${formatDateTime(s.opened_at)}</td>
      <td>${formatDateTime(s.closed_at)}</td>
      <td>${escapeHtml(s.opened_by_name || '')}</td>
      <td>${escapeHtml(branchName(s.branch_id))}</td>
      <td>${formatNaira(s.opening_cash_naira)}</td>
      <td>${s.closing_counted_naira != null ? formatNaira(s.closing_counted_naira) : '—'}</td>
      <td>
        <span class="stock-pill ${s.status === 'open' ? 'low' : 'ok'}">${pendingConfirmation ? 'Pending Review' : s.display_status}</span>
        ${s.auto_closed && !s.violation_exempt ? `<div style="color:var(--danger);font-size:11.5px;margin-top:2px;">⚠ Auto-closed — didn't close on time</div>` : ''}
        ${s.violation_exempt ? `<div style="color:var(--ink-faint);font-size:11.5px;margin-top:2px;">Exempted: ${escapeHtml(s.violation_exempt_reason || '')}</div>` : ''}
        ${variance != null ? `<div style="color:var(--ink-faint);font-size:11.5px;margin-top:2px;">Net movement: ${formatNaira(variance)}</div>` : ''}
      </td>
    </tr>`;
}

async function loadShifts() {
  const tbody = document.getElementById('shifts-tbody');
  const table = document.getElementById('shifts-table');
  const emptyState = document.getElementById('shifts-empty-state');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="7">Loading…</td></tr>';
  let shifts;
  try {
    shifts = await Api.listShifts({
      branchId: BranchContext.get(),
      status: document.getElementById('status-filter').value || undefined,
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }
  if (!shifts.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = shifts.map(renderRow).join('');
}

document.getElementById('filter-btn').addEventListener('click', loadShifts);
document.getElementById('status-filter').addEventListener('change', loadShifts);
window.addEventListener('branch-context-changed', () => { loadShifts(); loadUnclosedReport(); });

async function loadUnclosedReport() {
  const tbody = document.getElementById('unclosed-tbody');
  const table = document.getElementById('unclosed-table');
  const emptyState = document.getElementById('unclosed-empty-state');
  let rows;
  try {
    rows = await Api.unclosedShifts({ branchId: BranchContext.get() });
  } catch (err) {
    return; // non-fatal, e.g. staff account without view_all_reports
  }
  if (!rows.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = rows.map((s) => `
    <tr>
      <td>${escapeHtml(s.opened_by_name || '')}</td>
      <td>${escapeHtml(branchName(s.branch_id))}</td>
      <td>${formatDateTime(s.opened_at)}</td>
      <td>${formatDateTime(s.closed_at)}</td>
    </tr>`).join('');
}

loadBranches().then(() => { loadShifts(); loadUnclosedReport(); loadComplianceSummary(); loadComplianceEvents(); });
window.addEventListener('branch-context-changed', () => { loadComplianceSummary(); loadComplianceEvents(); });

// ---------------------------------------------------------- Shift Compliance

const STATUS_PILL_CLASS = {
  'Good Standing': 'ok',
  'Warning': 'low',
  'Management Review': 'low',
};

async function loadComplianceSummary() {
  const tbody = document.getElementById('compliance-tbody');
  const table = document.getElementById('compliance-table');
  const emptyState = document.getElementById('compliance-empty-state');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="10">Loading…</td></tr>';
  let rows;
  try {
    rows = await Api.complianceSummary({ branchId: BranchContext.get() });
  } catch (err) {
    tbody.innerHTML = '';
    return; // e.g. a staff account without view_all_reports -- non-fatal
  }
  const statusFilter = document.getElementById('compliance-status-filter').value;
  if (statusFilter) rows = rows.filter((r) => r.status === statusFilter);

  if (!rows.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td>${escapeHtml(r.user_name)}</td>
      <td>${escapeHtml(r.branch_name || '—')}</td>
      <td>${r.total_shifts}</td>
      <td>${r.manually_closed}</td>
      <td>${r.auto_closed}</td>
      <td>${r.late_closures}</td>
      <td>${r.warnings_issued}</td>
      <td>${r.pending_acknowledgements}</td>
      <td><span class="stock-pill ${STATUS_PILL_CLASS[r.status] || 'ok'}">${escapeHtml(r.status)}</span></td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-view-behavior="${r.user_id}" data-behavior-name="${escapeHtml(r.user_name)}">View history</button></td>
    </tr>`).join('');

  tbody.querySelectorAll('[data-view-behavior]').forEach((btn) => {
    btn.addEventListener('click', () => openShiftBehavior(btn.dataset.viewBehavior, btn.dataset.behaviorName));
  });
}
document.getElementById('compliance-status-filter').addEventListener('change', loadComplianceSummary);

async function loadComplianceEvents() {
  const tbody = document.getElementById('compliance-events-tbody');
  const table = document.getElementById('compliance-events-table');
  const emptyState = document.getElementById('compliance-events-empty-state');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="8">Loading…</td></tr>';
  let events;
  try {
    events = await Api.complianceEvents({
      branchId: BranchContext.get(),
      status: document.getElementById('compliance-events-filter').value || undefined,
    });
  } catch (err) {
    tbody.innerHTML = '';
    return;
  }
  if (!events.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = events.map((e) => `
    <tr>
      <td>${formatDateTime(e.created_at)}</td>
      <td>${escapeHtml(e.user_name || '')}</td>
      <td>${escapeHtml(e.branch_name || '—')}</td>
      <td>${e.occurrence_number}</td>
      <td>${escapeHtml(e.level_label)}</td>
      <td>${e.acknowledged_at ? formatDateTime(e.acknowledged_at) : '<span style="color:var(--danger);">Pending</span>'}</td>
      <td>${
        e.admin_acknowledged_at
          ? `<span title="${escapeHtml(e.admin_note || '')}">Acknowledged by ${escapeHtml(e.admin_acknowledged_by_name || 'admin')}${e.admin_note ? ' · has a note' : ''}</span>`
          : `<button type="button" class="btn btn-ghost btn-sm" data-admin-ack="${e.id}">Acknowledge</button>`
      }</td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-exempt-shift="${e.shift_id}">Mark not staff's fault</button></td>
    </tr>`).join('');

  tbody.querySelectorAll('[data-exempt-shift]').forEach((btn) => {
    btn.addEventListener('click', () => openShiftExempt(btn.dataset.exemptShift));
  });
  // Item 15: admin "I've informed this staff member" follow-up --
  // separate from the staff member's own Acknowledged column above.
  // A plain prompt() for the optional corrective note keeps this a
  // one-click action from the Shift report, same weight as "Mark not
  // staff's fault" next to it.
  tbody.querySelectorAll('[data-admin-ack]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const note = (await showPromptModal('', { title: 'Optional note', placeholder: 'e.g. "Spoke with staff — won\'t happen again"', confirmLabel: 'Save' })) || '';
      try {
        await Api.adminAcknowledgeComplianceEvent(btn.dataset.adminAck, note.trim());
        showToast('Marked as followed up.');
        loadComplianceEvents();
      } catch (err) {
        showToast(err.message, true);
      }
    });
  });
}
document.getElementById('compliance-events-filter').addEventListener('change', loadComplianceEvents);

// -------------------------------------------------- Shift behavior detail
const shiftBehaviorBackdrop = document.getElementById('shift-behavior-backdrop');
async function openShiftBehavior(userId, name) {
  document.getElementById('shift-behavior-title').textContent = `Shift behavior — ${name}`;
  const tbody = document.getElementById('shift-behavior-tbody');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="8">Loading…</td></tr>';
  shiftBehaviorBackdrop.classList.add('visible');
  try {
    const rows = await Api.staffShiftBehavior(userId);
    tbody.innerHTML = rows.length ? rows.map((r) => `
      <tr>
        <td>${escapeHtml(r.date)}</td>
        <td>${escapeHtml(r.branch_name || '—')}</td>
        <td>${formatDateTime(r.opened_at)}</td>
        <td>${r.closed_at ? formatDateTime(r.closed_at) : '—'}</td>
        <td>${formatNaira(r.opening_cash_naira)}</td>
        <td>${r.closing_counted_naira != null ? formatNaira(r.closing_counted_naira) : '—'}</td>
        <td>${escapeHtml(r.display_status)}</td>
        <td>${r.violation ? 'Yes' : 'None'}</td>
      </tr>`).join('') : '<tr><td colspan="8">No shift history yet.</td></tr>';
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8">${escapeHtml(err.message)}</td></tr>`;
  }
}
document.getElementById('shift-behavior-close').addEventListener('click', () => {
  shiftBehaviorBackdrop.classList.remove('visible');
});

// ---------------------------------------------------- Exempt a violation
const shiftExemptBackdrop = document.getElementById('shift-exempt-backdrop');
let pendingExemptShiftId = null;
function openShiftExempt(shiftId) {
  pendingExemptShiftId = shiftId;
  document.getElementById('shift-exempt-reason').value = '';
  document.getElementById('shift-exempt-error').classList.remove('visible');
  shiftExemptBackdrop.classList.add('visible');
}
document.getElementById('shift-exempt-cancel').addEventListener('click', () => {
  shiftExemptBackdrop.classList.remove('visible');
});
document.getElementById('shift-exempt-confirm').addEventListener('click', async () => {
  const errorEl = document.getElementById('shift-exempt-error');
  const reason = document.getElementById('shift-exempt-reason').value.trim();
  if (!reason) {
    errorEl.textContent = 'Enter a reason.';
    errorEl.classList.add('visible');
    return;
  }
  try {
    await Api.exemptShiftViolation(pendingExemptShiftId, reason);
    showToast('Marked as a system/administrative closure — violation removed.');
    shiftExemptBackdrop.classList.remove('visible');
    loadComplianceSummary();
    loadComplianceEvents();
    loadShifts();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  }
});
