Auth.requireAuth();
StoreBranding.refresh();
Perms.applyNav();
// currentUser, roleLabels, toastTimer, and showToast are already
// declared by js/sales.js -- both scripts now share one page (the
// Credit List tab folded into Transactions, September 2026 change
// spec section 12), and js/sales.js loads first, so this file reuses
// its bindings instead of re-declaring them (let/const can't be
// redeclared in the same scope, unlike a plain function).
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short', hour12: true, timeZone: 'Africa/Lagos' });
}

const statusLabels = { outstanding: 'Outstanding', partially_paid: 'Partially paid', paid: 'Paid' };
function statusPill(status) {
  const cls = status === 'paid' ? 'ok' : status === 'partially_paid' ? 'low' : 'out';
  return `<span class="stock-pill ${cls}">${statusLabels[status] || status}</span>`;
}

let creditCache = [];
let branchCache = [];

async function loadBranches() {
  try {
    branchCache = await Api.listBranches();
  } catch (err) {
    showToast(err.message, true);
    return;
  }
  BranchContext.renderBadge('credit-list-branch-badge');
}

window.addEventListener('branch-context-changed', loadCredit);

async function loadCredit() {
  const tbody = document.getElementById('credit-tbody');
  const table = document.getElementById('credit-table');
  const emptyState = document.getElementById('credit-empty-state');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="9">Loading…</td></tr>';
  try {
    creditCache = await Api.listCredit({
      branchId: BranchContext.get(),
      status: document.getElementById('credit-status-filter').value || undefined,
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }
  if (!creditCache.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = creditCache.map(renderRow).join('');
}

function renderRow(c) {
  const itemLabel = c.product_name
    ? `${escapeHtml(c.product_name)}${c.quantity ? ` × ${c.quantity}` : ''}`
    : (c.description ? escapeHtml(c.description) : '');
  return `
    <tr>
      <td>${formatDateTime(c.opened_at)}</td>
      <td><strong>${escapeHtml(c.customer_full_name)}</strong>${itemLabel ? `<div style="color:var(--ink-faint);font-size:12.5px;">${itemLabel}</div>` : ''}</td>
      <td class="mono">${escapeHtml(c.customer_phone)}</td>
      <td>${escapeHtml(c.branch_name)}</td>
      <td>${escapeHtml(c.opened_by_name)}</td>
      <td>${formatNaira(c.amount_naira)}</td>
      <td>${formatNaira(c.balance_naira)}</td>
      <td>${statusPill(c.status)}</td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-view="${c.id}">${c.status === 'paid' ? 'View' : 'Take payment'}</button></td>
    </tr>`;
}

document.getElementById('credit-tbody').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-view]');
  if (btn) openDetail(btn.dataset.view);
});

document.getElementById('credit-filter-btn').addEventListener('click', loadCredit);
document.getElementById('credit-status-filter').addEventListener('change', loadCredit);

// ---------------------------------------------------------------- Receipt printing
// Shared between "just opened" and "just took a payment" -- spec:
// "they should be able to print the receipt of that particular open
// credit... though it should be questionable to the system if you
// want to print a receipt for it or not," so this always shows the
// receipt and lets the person choose Print or Close rather than
// forcing a print.

const creditReceiptBackdrop = document.getElementById('credit-receipt-backdrop');

async function printCreditReceipt(creditEntry, context) {
  let branding = { name: 'Nick Cosmetics' };
  try { branding = StoreBranding.current(); } catch (err) { /* falls back above */ }
  const sheet = document.getElementById('credit-receipt-sheet');
  // Every credit opened going forward has a real linked Sale (spec
  // section 12: "same level of detail as a normal sale receipt") --
  // reusing the exact same Receipt.render() a normal POS sale uses
  // means attendant, customer name/phone, and full item detail all
  // come for free instead of a second, thinner implementation. Older
  // credit entries opened before this existed have no sale_id, so
  // they fall back to the lightweight credit-only layout below.
  if (creditEntry.sale_id) {
    try {
      const sale = await Api.getSale(creditEntry.sale_id);
      Receipt.render(sheet, sale, branding);
      creditReceiptBackdrop.classList.add('visible');
      return;
    } catch (err) {
      // fall through to the lightweight layout rather than leaving
      // the person with no receipt at all
    }
  }
  Receipt.renderCredit(sheet, creditEntry, branding, context);
  creditReceiptBackdrop.classList.add('visible');
}

document.getElementById('credit-receipt-close').addEventListener('click', () => creditReceiptBackdrop.classList.remove('visible'));
document.getElementById('credit-receipt-print').addEventListener('click', () => Receipt.print());
creditReceiptBackdrop.addEventListener('click', (e) => { if (e.target === creditReceiptBackdrop) creditReceiptBackdrop.classList.remove('visible'); });

// ---------------------------------------------------------------- Detail / payment

const creditDetailBackdrop = document.getElementById('credit-detail-backdrop');
const creditDetailError = document.getElementById('credit-detail-error');
let activeCreditId = null;

function renderDetail(c) {
  const itemLabel = c.product_name
    ? ` · Buying: ${c.product_name}${c.quantity ? ` × ${c.quantity}` : ''}`
    : '';
  document.getElementById('credit-detail-readout').textContent =
    `${c.customer_full_name} · ${c.customer_phone} · Opened ${formatDateTime(c.opened_at)} by ${c.opened_by_name}${itemLabel}`;
  // Payment history as an actual table (cashier, amount, date, time,
  // method each get their own column) -- was previously one plain
  // text line per payment, which is hard to scan once a credit has
  // more than a couple of top-ups from different cashiers.
  const historyTable = c.payments.length
    ? `<table style="width:100%;border-collapse:collapse;font-size:12.5px;">
        <thead>
          <tr style="text-align:left;border-bottom:1px solid var(--border);">
            <th style="padding:6px 4px;">Date</th>
            <th style="padding:6px 4px;">Time</th>
            <th style="padding:6px 4px;">Cashier</th>
            <th style="padding:6px 4px;">Method</th>
            <th style="padding:6px 4px;text-align:right;">Amount</th>
          </tr>
        </thead>
        <tbody>
          ${c.payments.map((p) => {
            const paidAt = p.paid_at ? new Date(p.paid_at) : null;
            const dateStr = paidAt ? paidAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' }) : '—';
            const timeStr = paidAt ? paidAt.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' }) : '—';
            return `<tr style="border-bottom:1px solid var(--border);">
              <td style="padding:6px 4px;">${escapeHtml(dateStr)}</td>
              <td style="padding:6px 4px;">${escapeHtml(timeStr)}</td>
              <td style="padding:6px 4px;">${escapeHtml(p.received_by_name || '—')}</td>
              <td style="padding:6px 4px;text-transform:capitalize;">${escapeHtml(p.method || '—')}</td>
              <td style="padding:6px 4px;text-align:right;">${formatNaira(p.amount_naira)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`
    : '<div style="color:var(--ink-faint);">No payments yet.</div>';
  document.getElementById('credit-payment-history').innerHTML =
    `<div style="margin-bottom:10px;"><strong>Total owed:</strong> ${formatNaira(c.amount_naira)} · <strong>Paid:</strong> ${formatNaira(c.amount_paid_naira)} · <strong>Balance:</strong> ${formatNaira(c.balance_naira)}</div>` + historyTable;
  const paymentForm = document.getElementById('credit-payment-form');
  if (c.status === 'paid' || !BranchContext.get()) {
    paymentForm.style.display = 'none';
  } else {
    paymentForm.style.display = '';
    document.getElementById('credit-payment-amount').max = c.balance_naira;
    document.getElementById('credit-payment-amount').value = '';
  }
}

async function openDetail(creditId) {
  activeCreditId = creditId;
  const c = creditCache.find((row) => row.id === creditId);
  if (c) renderDetail(c);
  creditDetailError.classList.remove('visible');
  creditDetailBackdrop.classList.add('visible');
}

document.getElementById('credit-detail-close').addEventListener('click', () => creditDetailBackdrop.classList.remove('visible'));
document.getElementById('credit-detail-print').addEventListener('click', () => {
  const c = creditCache.find((row) => row.id === activeCreditId);
  if (c) printCreditReceipt(c, c.status === 'paid' ? 'completed' : 'payment');
});
creditDetailBackdrop.addEventListener('click', (e) => { if (e.target === creditDetailBackdrop) creditDetailBackdrop.classList.remove('visible'); });

document.getElementById('credit-payment-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!activeCreditId) return;
  const submitBtn = document.getElementById('credit-payment-submit');
  submitBtn.disabled = true;
  creditDetailError.classList.remove('visible');
  try {
    const updated = await Api.addCreditPayment(activeCreditId, {
      amount_naira: Number(document.getElementById('credit-payment-amount').value),
      method: document.getElementById('credit-payment-method').value,
    });
    showToast(updated.status === 'paid' ? 'Payment recorded — credit fully paid off.' : 'Payment recorded.');
    renderDetail(updated);
    await loadCredit();
    printCreditReceipt(updated, updated.status === 'paid' ? 'completed' : 'payment');
  } catch (err) {
    creditDetailError.textContent = err.message;
    creditDetailError.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});

loadBranches().then(loadCredit);
if (window.location.hash === '#credit') activateTab('credit');
