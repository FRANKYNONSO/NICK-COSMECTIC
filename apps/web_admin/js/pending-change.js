// Pending Change tab, folded into Transactions (platform update spec
// section 6). currentUser, roleLabels, toastTimer, showToast,
// escapeHtml, formatNaira are already declared by js/sales.js, which
// loads first on this shared page -- reused here rather than
// redeclared (let/const can't be redeclared in the same scope).
Auth.requireAuth();
Perms.applyNav();

function formatDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${d.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`;
}

if (BranchContext && typeof BranchContext.renderBadge === 'function') {
  BranchContext.renderBadge('pending-change-branch-badge');
}

// Platform update spec section 2: "Customer is owed change" -- a
// record is tied to the Sale/receipt it came from (never a bare name
// string, so two customers sharing a name never collide). Cash
// settles directly (money never left the till); a transfer/airtime
// request routes through Super Admin/Branch Manager approval first
// (section 2.4), same tier as a refund.
const canApproveChanges = Perms.can('view_all_reports'); // super_admin/branch_manager tier, mirrors refund mark-processed gating

const methodLabels = { cash: 'Cash', transfer: 'Bank transfer', airtime: 'Airtime', coupon: 'Discount code' }; // PATCH3D-COUPON-LABEL

function methodDetail(r) {
  if (r.settlement_method === 'transfer') {
    return `${escapeHtml(r.bank_name || '')} · ${escapeHtml(r.account_number || '')} (${escapeHtml(r.account_name || '')})`;
  }
  if (r.settlement_method === 'airtime') {
    return `${escapeHtml(r.airtime_phone_number || '')}${r.airtime_network ? ` · ${escapeHtml(r.airtime_network)}` : ''}`;
  }
  return '—';
}

// Sept 2026 dictated request: Outstanding, Pending Approval, and
// Settled are now three always-visible stacked tables instead of one
// table switched between by tabs (see sales.html) -- this renders one
// status "bucket" into whichever tbody it belongs to. actionCell is a
// function of (row) so each bucket can show its own action (Settle /
// Mark sent / a plain "settled by" readout).
function renderChangeRows(rows, tbodyId, emptyId, actionCell) {
  const tbody = document.getElementById(tbodyId);
  const emptyState = document.getElementById(emptyId);
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '';
    if (emptyState) emptyState.style.display = '';
    return;
  }
  if (emptyState) emptyState.style.display = 'none';
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td>${formatDateTime(r.created_at)}</td>
      <td class="mono">${escapeHtml(r.reference_code || '—')}</td>
      <td>${escapeHtml(r.customer_name)}${r.customer_phone ? `<div style="color:var(--ink-faint);font-size:12px;">${escapeHtml(r.customer_phone)}</div>` : ''}</td>
      <td>${formatNaira(r.amount_naira)}</td>
      <td>${methodLabels[r.settlement_method] || '—'}<div style="color:var(--ink-faint);font-size:12px;">${methodDetail(r)}</div></td>
      <td>${escapeHtml(r.branch_name || '—')}</td>
      <td>${escapeHtml(r.notes || '—')}</td>
      <td>${escapeHtml(r.created_by_name || 'Unknown')}</td>
      <td class="row-actions-links">${actionCell(r)}</td>
      <td class="row-actions-links"><a href="#" class="action-link action-link-view" data-view-receipt="${r.sale_id}">View</a></td>
    </tr>`).join('');
  tbody.querySelectorAll('[data-view-receipt]').forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      // openReceipt is declared in js/sales.js, which loads first on
      // this shared page (see the file-header note) -- reused here so
      // the full itemized purchase, and every settlement method tied
      // to it (cash/transfer/airtime, each with its own amount), shows
      // up in one place instead of just this one row's method.
      openReceipt(link.dataset.viewReceipt);
    });
  });
  tbody.querySelectorAll('[data-settle]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      openSettleChangeModal(btn.dataset.settle, rows.find((r) => r.id === btn.dataset.settle));
    });
  });
  tbody.querySelectorAll('[data-approve]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (!(await showConfirmModal('Has this transfer/airtime settlement actually been sent to the customer?', { title: 'Confirm settlement', confirmLabel: 'Confirm' }))) return;
      btn.style.pointerEvents = 'none';
      btn.textContent = 'Confirming…';
      try {
        await Api.markPendingChangeProcessed(btn.dataset.approve);
        showToast('Change settlement approved and marked sent.');
        loadPendingChanges();
        if (typeof refreshPendingChangeBadge === 'function') refreshPendingChangeBadge();
      } catch (err) {
        showToast(err.message, true);
        btn.style.pointerEvents = '';
        btn.textContent = 'Mark sent';
      }
    });
  });
}

async function loadPendingChanges() {
  const branchId = BranchContext.get();
  ['pending-change-tbody', 'pending-approval-tbody', 'settled-change-tbody'].forEach((id) => {
    const tbody = document.getElementById(id);
    if (tbody) tbody.innerHTML = '<tr class="loading-row"><td colspan="10">Loading…</td></tr>';
  });

  let outstandingRows = [];
  let pendingRows = [];
  let settledRows = [];
  try {
    [outstandingRows, pendingRows, settledRows] = await Promise.all([
      Api.listPendingChanges({ status: 'outstanding', branchId }),
      canApproveChanges ? Api.listPendingChanges({ status: 'pending', branchId }) : Promise.resolve([]),
      Api.listPendingChanges({ status: 'settled', branchId }),
    ]);
  } catch (err) {
    showToast(err.message, true);
    ['pending-change-tbody', 'pending-approval-tbody', 'settled-change-tbody'].forEach((id) => {
      const tbody = document.getElementById(id);
      if (tbody) tbody.innerHTML = '';
    });
    return;
  }

  renderChangeRows(outstandingRows, 'pending-change-tbody', 'pending-change-empty',
    (r) => `<a href="#" class="action-link action-link-payment" data-settle="${r.id}">Settle</a>`);

  renderChangeRows(pendingRows, 'pending-approval-tbody', 'pending-approval-empty',
    (r) => canApproveChanges
      ? `<a href="#" class="action-link action-link-payment" data-approve="${r.id}">Mark sent</a>`
      : `<span style="color:var(--ink-faint);font-size:12px;">Awaiting Branch Manager/Super Admin approval</span>`);

  renderChangeRows(settledRows, 'settled-change-tbody', 'settled-change-empty',
    (r) => `<span style="color:var(--ink-faint);font-size:12px;">Settled ${formatDateTime(r.settled_at)}${r.settled_by_name ? ` by ${escapeHtml(r.settled_by_name)}` : ''}</span>`);
}

// Approving change settlements is a Branch Manager/Super Admin job
// (spec 2.4) -- a cashier has no use for the whole Pending Approval
// table, so the section is hidden entirely rather than just its
// action buttons (see the #pending-approval-section wrapper in
// sales.html).
if (!canApproveChanges) {
  const section = document.getElementById('pending-approval-section');
  if (section) section.style.display = 'none';
  const title = section?.previousElementSibling; // .section-hint
  const heading = title?.previousElementSibling; // .section-title
  if (title) title.style.display = 'none';
  if (heading) heading.style.display = 'none';
}

window.addEventListener('branch-context-changed', () => { if (activeTab === 'pending-change') loadPendingChanges(); });
// Two dashboard cards deep-link here now (spec clarification): the
// everyone-visible "Pending change" card just opens the tab; the
// admin-only "Change approvals" card appends ":pending" to also
// scroll straight to the Pending Approval table instead of leaving
// the admin to find it themselves further down the page.
if (window.location.hash.startsWith('#pending-change')) {
  activateTab('pending-change');
  const [, subTab] = window.location.hash.split(':');
  if (subTab === 'pending') {
    document.getElementById('pending-approval-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// ---------------------------------------------------- Settle modal (spec item 5)
const settleChangeBackdrop = document.getElementById('settle-change-backdrop');
let settleChangeId = null;
let settleChangeAmountNaira = 0;

function openSettleChangeModal(id, record) {
  settleChangeId = id;
  settleChangeAmountNaira = record ? record.amount_naira : 0;
  document.getElementById('settle-change-form').reset();
  document.getElementById('settle-change-error').classList.remove('visible');
  document.getElementById('settle-change-sub').textContent = `Owed: ${formatNaira(settleChangeAmountNaira)}`;
  const amountInput = document.getElementById('settle-change-amount');
  amountInput.value = settleChangeAmountNaira.toFixed(2);
  amountInput.max = settleChangeAmountNaira;
  updateSettleChangeMethodFields();
  settleChangeBackdrop.classList.add('visible');
}

function updateSettleChangeMethodFields() {
  const method = document.getElementById('settle-change-method').value;
  document.getElementById('settle-change-transfer-fields').style.display = method === 'transfer' ? '' : 'none';
  document.getElementById('settle-change-airtime-fields').style.display = method === 'airtime' ? '' : 'none';
  if (method === 'transfer') populateBankSelect(document.getElementById('settle-change-bank-name'));
}
document.getElementById('settle-change-method').addEventListener('change', updateSettleChangeMethodFields);

document.getElementById('settle-change-cancel').addEventListener('click', () => {
  settleChangeBackdrop.classList.remove('visible');
});

document.getElementById('settle-change-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('settle-change-error');
  const submitBtn = document.getElementById('settle-change-submit');
  const method = document.getElementById('settle-change-method').value;
  const amount = Number(document.getElementById('settle-change-amount').value || 0);

  if (Math.abs(amount - settleChangeAmountNaira) > 0.5) {
    errorEl.textContent = `Amount given back must match the outstanding balance of ${formatNaira(settleChangeAmountNaira)}.`;
    errorEl.classList.add('visible');
    return;
  }

  const payload = { amount_naira: amount, settlement_method: method };
  if (method === 'transfer') {
    payload.bank_name = document.getElementById('settle-change-bank-name').value;
    payload.account_number = document.getElementById('settle-change-account-number').value.trim();
    payload.account_name = document.getElementById('settle-change-account-name').value.trim();
    if (!(payload.bank_name && payload.account_number && payload.account_name)) {
      errorEl.textContent = 'Bank name, account number, and account name are required for a transfer.';
      errorEl.classList.add('visible');
      return;
    }
  } else if (method === 'airtime') {
    payload.airtime_phone_number = document.getElementById('settle-change-airtime-phone').value.trim();
    payload.airtime_network = document.getElementById('settle-change-airtime-network').value || null;
    if (!payload.airtime_phone_number) {
      errorEl.textContent = 'A phone number is required for an airtime top-up.';
      errorEl.classList.add('visible');
      return;
    }
  }

  submitBtn.disabled = true;
  errorEl.classList.remove('visible');
  try {
    const result = await Api.settlePendingChange(settleChangeId, payload);
    // Choosing cash settles immediately; choosing transfer/airtime here
    // routes it into the same approval queue a transfer/airtime request
    // gets at creation (spec 2.4) -- it's real money moving now, so the
    // toast needs to reflect whichever actually happened.
    const msg = result.status === 'settled'
      ? 'Marked as settled.'
      : `Submitted for approval (ref ${result.reference_code}) — awaiting Branch Manager/Super Admin confirmation.`;
    showToast(msg);
    settleChangeBackdrop.classList.remove('visible');
    loadPendingChanges();
    // This record just left 'outstanding' either way (settled, or
    // moved into the approval queue above) -- refresh both dashboard
    // cards, not just the admin one.
    if (typeof refreshOutstandingChangeBadge === 'function') refreshOutstandingChangeBadge();
    if (typeof refreshPendingChangeBadge === 'function') refreshPendingChangeBadge();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});
