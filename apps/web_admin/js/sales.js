Auth.requireAuth();
StoreBranding.refresh();
Perms.applyNav();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

// Staff only ever see their own sales (server-enforced regardless of what
// filters are sent -- see GET /sales), so a "which cashier?" picker makes
// no sense for them and, worse, calls GET /users, which staff can't access
// at all (manager/admin only) -- that's what was breaking Filter for them.
if (Perms.isStaff()) {
  document.getElementById('cashier-filter').style.display = 'none';
  document.getElementById('branch-badge')?.remove();
}

let toastTimer = null;

// -------------------------------------------------- Generic confirm step
// Shows a summary and resolves true/false based on which button the user
// taps. Used to force an explicit confirmation before an action that
// can't be undone (adding a payment, changing a booking's item) so a
// stray click can't silently apply it.
const genericConfirmBackdrop = document.getElementById('generic-confirm-backdrop');
let genericConfirmResolve = null;

function confirmAction({ title = 'Confirm', sub = 'Review before you continue.', bodyHtml = '', confirmLabel = 'Confirm' } = {}) {
  document.getElementById('generic-confirm-title').textContent = title;
  document.getElementById('generic-confirm-sub').textContent = sub;
  document.getElementById('generic-confirm-body').innerHTML = bodyHtml;
  document.getElementById('generic-confirm-ok').textContent = confirmLabel;
  genericConfirmBackdrop.classList.add('visible');
  return new Promise((resolve) => { genericConfirmResolve = resolve; });
}
document.getElementById('generic-confirm-cancel').addEventListener('click', () => {
  genericConfirmBackdrop.classList.remove('visible');
  if (genericConfirmResolve) genericConfirmResolve(false);
  genericConfirmResolve = null;
});
document.getElementById('generic-confirm-ok').addEventListener('click', () => {
  genericConfirmBackdrop.classList.remove('visible');
  if (genericConfirmResolve) genericConfirmResolve(true);
  genericConfirmResolve = null;
});

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
  return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 2 })}`;
}

function formatDateTime(dateStr) {
  return new Date(dateStr).toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short', hour12: true, timeZone: 'Africa/Lagos' });
}

// Compact "Change: Pending / Settled" indicator for the Sales table
// (platform update spec section 2.5) -- SaleOut.pending_changes is the
// list of PendingChangeSummary rows the API already embeds per-sale
// specifically for this, so no extra round-trip is needed to know a
// sale still has money owed on it. Full detail (method, reference
// code, approval) lives on the Transactions > Pending Change tab;
// this is just the at-a-glance flag. A sale can have more than one row
// (change owed split across settlement methods) -- the badge shows
// the least-resolved state: any row still open outranks a settled one.
function changeStatusBadge(pendingChanges) {
  const rows = pendingChanges || [];
  if (!rows.length) return '—';
  const openRows = rows.filter((r) => r.status !== 'settled');
  if (!openRows.length) {
    const count = rows.length > 1 ? ` (${rows.length})` : '';
    return `<span style="color:var(--ink-faint);font-size:12px;">Settled${count}</span>`;
  }
  const anyPending = openRows.some((r) => r.status === 'pending');
  const label = (anyPending ? 'Pending approval' : 'Pending') + (rows.length > 1 ? ` (${openRows.length}/${rows.length})` : '');
  return `<span style="color:var(--danger);font-weight:600;font-size:12px;">${escapeHtml(label)}</span>`;
}

async function loadBranchFilter() {
  try {
    await Api.listBranches();
    BranchContext.renderBadge('branch-badge');
  } catch (err) { /* non-fatal */ }
}

// Cashiers are just staff/users — lets you narrow a branch's sales down to
// one worker (e.g. "did James's till balance today?").
async function loadCashierFilter() {
  try {
    const users = await Api.listUsers();
    const select = document.getElementById('cashier-filter');
    select.innerHTML = '<option value="">All cashiers</option>' +
      users.map((u) => `<option value="${u.id}">${escapeHtml(u.full_name || u.email)}</option>`).join('');
  } catch (err) { /* non-fatal */ }
}

let salesCache = [];

async function loadSales() {
  const branchId = BranchContext.get();
  const cashierId = document.getElementById('cashier-filter').value || undefined;
  const dateFrom = document.getElementById('from-date').value || undefined;
  const dateTo = document.getElementById('to-date').value || undefined;

  const tbody = document.getElementById('sales-tbody');
  const table = document.getElementById('sales-table');
  const emptyState = document.getElementById('empty-state');
  emptyState.style.display = 'none';
  table.style.display = '';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="7">Loading sales…</td></tr>';

  try {
    salesCache = await Api.listSales({ branchId, dateFrom, dateTo, cashierId });
    if (!salesCache.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
      return;
    }
    tbody.innerHTML = salesCache.map((s) => `
      <tr>
        <td class="mono">${escapeHtml((s.receipt_number || s.id).slice(0, 8))}…</td>
        <td><strong>${formatDateTime(s.created_at)}</strong></td>
        <td><strong>${escapeHtml(s.cashier_name || '—')}</strong></td>
        <td>${s.discount_naira ? '−' + formatNaira(s.discount_naira) : '—'}</td>
        <td><strong>${formatNaira(s.total_naira)}</strong></td>
        <td>${changeStatusBadge(s.pending_changes)}</td>
        <td class="row-actions-links">
          <a href="#" class="action-link action-link-view" data-view="${s.id}">View</a>
          <a href="#" class="action-link action-link-refund" data-refund="${s.id}">Refund</a>
        </td>
      </tr>`).join('');

    salesCache.forEach((s) => {
      document.querySelector(`[data-view="${s.id}"]`)?.addEventListener('click', (e) => {
        e.preventDefault();
        openReceipt(s.id);
      });
      // Refund is its own first-class action straight from the sales
      // table -- not something you have to already be inside "View
      // Receipt" to discover (spec: "it shouldn't just be standalone
      // [inside view receipt]. It should have its own section").
      document.querySelector(`[data-refund="${s.id}"]`)?.addEventListener('click', async (e) => {
        e.preventDefault();
        await openReceipt(s.id);
        if (refundToggle.style.display !== 'none') {
          refundPanel.style.display = 'block';
          refundPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

document.getElementById('filter-btn').addEventListener('click', loadSales);
document.getElementById('clear-btn').addEventListener('click', () => {
  document.getElementById('from-date').value = '';
  document.getElementById('to-date').value = '';
  document.getElementById('cashier-filter').value = '';
  loadSales();
});

// ------------------------------------------------------------------ Receipt

const receiptBackdrop = document.getElementById('receipt-modal-backdrop');
const refundPanel = document.getElementById('refund-panel');
const refundToggle = document.getElementById('receipt-refund-toggle');
let currentReceiptSale = null;

async function openReceipt(saleId) {
  try {
    const sale = await Api.getSale(saleId);
    currentReceiptSale = sale;
    const created = new Date(sale.created_at);
    const receiptNumber = (sale.receipt_number || sale.id || '').toString();

    Receipt.render(document.getElementById('receipt-sheet'), sale, StoreBranding.current());

    // Refund is only possible against a sale that's still (partially)
    // active, or an open booking (spec section 13) -- matches the
    // server's own check in POST /sales/{id}/refund.
    const refundable = ['completed', 'partially_refunded', 'booking'].includes(sale.status || 'completed');
    refundToggle.style.display = refundable ? '' : 'none';
    refundPanel.style.display = 'none';
    document.getElementById('refund-error').classList.remove('visible');
    // Reference back to who rang up the original sale and when, so
    // whoever processes the refund can confirm against what the
    // customer is telling them (spec: "this is the day you bought it,
    // and it was settled to you by Godwin") before picking which item(s)
    // to refund.
    document.getElementById('refund-original-sale-info').textContent =
      `Originally sold ${created.toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos' })} at ${created.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })} by ${sale.cashier_name || 'unknown cashier'} · Receipt ${receiptNumber.slice(0, 8)}…`;
    document.getElementById('refund-tbody').innerHTML = (sale.items || []).map((it) => `
      <tr>
        <td>${escapeHtml(it.product_name)}</td>
        <td>${it.quantity}</td>
        <td><input type="number" min="0" max="${it.quantity}" value="0" style="width:70px;" data-refund-qty="${it.id}" /></td>
        <td><input type="checkbox" checked data-refund-restock="${it.id}" /></td>
        <td><input type="checkbox" data-refund-spoiled="${it.id}" /></td>
      </tr>`).join('');
    document.getElementById('refund-photo').value = '';
    document.getElementById('refund-want').value = 'refund';
    // September 2026 change spec section 10: "All refunds, system-
    // wide, must go through bank transfer -- remove cash as a refund
    // method entirely" -- refund-method is now a fixed, disabled
    // 'transfer' (see sales.html), regardless of how the original
    // sale was paid.
    document.getElementById('refund-method').value = 'transfer';
    document.getElementById('refund-method-hint').textContent = 'Refunds are always paid out by bank transfer.';
    populateBankSelect(document.getElementById('refund-bank-name'));
    populateBankSelect(document.getElementById('refund-settlement-bank-name'));
    document.getElementById('refund-account-number').value = '';
    document.getElementById('refund-account-name').value = '';
    document.getElementById('refund-settlement-account-number').value = '';
    document.getElementById('refund-settlement-account-name').value = '';
    updateRefundMethodFields();
    document.getElementById('refund-exchange-fields').style.display = 'none';
    document.getElementById('refund-exchange-search').value = '';
    document.getElementById('refund-exchange-variant').innerHTML = '';
    document.getElementById('refund-exchange-price-preview').textContent = '';
    document.getElementById('refund-exchange-topup-fields').style.display = 'none';
    document.getElementById('refund-exchange-settlement-fields').style.display = 'none';
    document.getElementById('refund-settlement-bank-fields').style.display = 'none';
    document.getElementById('refund-settlement-note').textContent = '';

    receiptBackdrop.classList.add('visible');
  } catch (err) {
    showToast(err.message, true);
  }
}

refundToggle.addEventListener('click', () => {
  refundPanel.style.display = refundPanel.style.display === 'none' ? 'block' : 'none';
});

// Section 7: the first question is "Refund or Exchange?" -- 'refund'
// shows the cash/transfer method picker, 'exchange' shows the
// exchange-product picker instead.
function updateRefundMethodFields() {
  const wantsExchange = document.getElementById('refund-want').value === 'exchange';
  document.getElementById('refund-method-fields').style.display = wantsExchange ? 'none' : 'block';
  document.getElementById('refund-exchange-fields').style.display = wantsExchange ? 'block' : 'none';
  updateRefundBankFields();
}
document.getElementById('refund-want').addEventListener('change', updateRefundMethodFields);

function updateRefundBankFields() {
  const wantsExchange = document.getElementById('refund-want').value === 'exchange';
  // Section 10: refund-method is now fixed to 'transfer' -- bank
  // fields always show for a plain refund now, not conditionally.
  const isTransfer = !wantsExchange;
  document.getElementById('refund-bank-fields').style.display = isTransfer ? 'block' : 'none';
}
document.getElementById('refund-method').addEventListener('change', updateRefundBankFields);

let refundExchangeSearchTimer = null;
document.getElementById('refund-exchange-search').addEventListener('input', (e) => {
  clearTimeout(refundExchangeSearchTimer);
  const search = e.target.value.trim();
  refundExchangeSearchTimer = setTimeout(async () => {
    const select = document.getElementById('refund-exchange-variant');
    if (!search) { select.innerHTML = ''; return; }
    try {
      const products = await Api.listProducts({ search });
      exchangeProductCache = products;
      // Bug fix (spec section 9): this is a plain, always-clickable
      // <select> populated from the search results -- not a custom
      // dropdown div layered over the page, which is what made a
      // matching suggestion visible but unselectable before.
      select.innerHTML = products.slice(0, 15)
        .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}${p.size ? ` (${escapeHtml(p.size)})` : ''} — ${escapeHtml(p.sku)}</option>`)
        .join('');
      updateExchangePricePreview();
    } catch (err) {
      showToast(err.message, true);
    }
  }, 300);
});

// Section 9: "Once selected, show quantity and pull the price from
// inventory automatically" + compare to what was originally paid so
// the cashier knows up front whether this is a top-up or a refund.
let exchangeProductCache = [];
function updateExchangePricePreview() {
  const productId = document.getElementById('refund-exchange-variant').value;
  const product = exchangeProductCache.find((p) => p.id === productId);
  const preview = document.getElementById('refund-exchange-price-preview');
  const topupFields = document.getElementById('refund-exchange-topup-fields');
  const settlementFields = document.getElementById('refund-exchange-settlement-fields');
  topupFields.style.display = 'none';
  settlementFields.style.display = 'none';
  if (!product || !currentReceiptSale) {
    preview.textContent = '';
    return;
  }
  const qty = Math.max(1, Number(document.getElementById('refund-exchange-qty').value) || 1);
  const exchangeTotal = product.selling_price_naira * qty;
  const refundedAmount = refundableAmountForSelectedLines();
  preview.textContent = `${formatNaira(product.selling_price_naira)} × ${qty} = ${formatNaira(exchangeTotal)} (refunding ${formatNaira(refundedAmount)} worth of the original item)`;
  if (exchangeTotal > refundedAmount) {
    document.getElementById('refund-exchange-topup-amount').value = (exchangeTotal - refundedAmount).toFixed(2);
    topupFields.style.display = 'block';
  } else if (exchangeTotal < refundedAmount) {
    settlementFields.style.display = 'block';
    updateSettlementBankFields();
  }
}

function refundableAmountForSelectedLines() {
  if (!currentReceiptSale) return 0;
  return (currentReceiptSale.items || []).reduce((sum, it) => {
    const qtyInput = document.querySelector(`[data-refund-qty="${it.id}"]`);
    const qty = Number(qtyInput?.value || 0);
    return sum + qty * (it.unit_price_naira || 0);
  }, 0);
}

document.getElementById('refund-exchange-variant').addEventListener('change', updateExchangePricePreview);
document.getElementById('refund-exchange-qty').addEventListener('input', updateExchangePricePreview);
document.getElementById('refund-tbody').addEventListener('input', updateExchangePricePreview);

function updateSettlementBankFields() {
  const isTransfer = document.getElementById('refund-settlement-method').value === 'transfer';
  document.getElementById('refund-settlement-bank-fields').style.display = isTransfer ? 'block' : 'none';
}
document.getElementById('refund-settlement-method').addEventListener('change', updateSettlementBankFields);

document.getElementById('refund-submit').addEventListener('click', async () => {
  if (!currentReceiptSale) return;
  const errorEl = document.getElementById('refund-error');
  errorEl.classList.remove('visible');

  const lines = (currentReceiptSale.items || [])
    .map((it) => {
      const qtyInput = document.querySelector(`[data-refund-qty="${it.id}"]`);
      const restockInput = document.querySelector(`[data-refund-restock="${it.id}"]`);
      const spoiledInput = document.querySelector(`[data-refund-spoiled="${it.id}"]`);
      const quantity = Number(qtyInput?.value || 0);
      return quantity > 0
        ? { sale_item_id: it.id, quantity, restock: !!restockInput?.checked, spoiled: !!spoiledInput?.checked }
        : null;
    })
    .filter(Boolean);

  if (!lines.length) {
    errorEl.textContent = 'Enter a refund quantity for at least one item.';
    errorEl.classList.add('visible');
    return;
  }

  const reasonValue = document.getElementById('refund-reason').value.trim();
  if (!reasonValue) {
    errorEl.textContent = 'A reason for the refund is required.';
    errorEl.classList.add('visible');
    return;
  }
  const photoFileCheck = document.getElementById('refund-photo').files[0];
  if (!photoFileCheck) {
    errorEl.textContent = 'A photo of the returned/damaged item is required before a refund can be processed.';
    errorEl.classList.add('visible');
    return;
  }

  const wantsExchange = document.getElementById('refund-want').value === 'exchange';
  const payload = { lines, reason: reasonValue };

  if (wantsExchange) {
    payload.action = 'exchange';
    const productId = document.getElementById('refund-exchange-variant').value;
    if (!productId) {
      errorEl.textContent = 'Pick the item to swap for.';
      errorEl.classList.add('visible');
      return;
    }
    payload.exchange_product_id = productId;
    payload.exchange_quantity = Number(document.getElementById('refund-exchange-qty').value || 1);
    payload.exchange_payment_method = document.getElementById('refund-exchange-method').value;
    const settlementFieldsVisible = document.getElementById('refund-exchange-settlement-fields').style.display !== 'none';
    if (settlementFieldsVisible) {
      payload.settlement_method = document.getElementById('refund-settlement-method').value;
      if (payload.settlement_method === 'transfer') {
        payload.bank_name = document.getElementById('refund-settlement-bank-name').value;
        payload.account_number = document.getElementById('refund-settlement-account-number').value.trim();
        payload.account_name = document.getElementById('refund-settlement-account-name').value.trim();
        if (!(payload.bank_name && payload.account_number && payload.account_name)) {
          errorEl.textContent = 'Bank name, account number, and account name are required for the transfer.';
          errorEl.classList.add('visible');
          return;
        }
      }
    }
  } else {
    payload.action = document.getElementById('refund-method').value; // always 'transfer' now (section 10)
    if (payload.action === 'transfer') {
      payload.bank_name = document.getElementById('refund-bank-name').value;
      payload.account_number = document.getElementById('refund-account-number').value.trim();
      payload.account_name = document.getElementById('refund-account-name').value.trim();
      if (!(payload.bank_name && payload.account_number && payload.account_name)) {
        errorEl.textContent = 'Bank name, account number, and account name are required for a transfer refund.';
        errorEl.classList.add('visible');
        return;
      }
    }
  }

  const btn = document.getElementById('refund-submit');
  btn.disabled = true;
  try {
    const photoFile = document.getElementById('refund-photo').files[0];
    if (photoFile) {
      const uploaded = await Api.uploadRefundPhoto(photoFile);
      payload.image_url = uploaded.url;
    }
    const refund = await Api.refundSale(currentReceiptSale.id, payload);
    let message = 'Refund processed.';
    if (refund.action === 'transfer' || refund.status === 'pending') {
      message = 'Refund submitted — tell the customer management will process this within 24 hours.';
    } else if (refund.settlement === 'store_paid_customer') {
      message = refund.settlement_method === 'transfer'
        ? `Refund submitted — the ₦${refund.settlement_amount_naira} difference will be sent within 24 hours.`
        : `Refund processed. Store paid the customer ₦${refund.settlement_amount_naira} back.`;
    } else if (refund.settlement === 'customer_topped_up') {
      message = `Refund processed. Customer topped up ₦${refund.settlement_amount_naira}.`;
    } else if (refund.settlement === 'even') {
      message = 'Refund processed. Even swap — no money changed hands.';
    }
    showToast(message);
    receiptBackdrop.classList.remove('visible');
    loadSales();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('receipt-close').addEventListener('click', () => receiptBackdrop.classList.remove('visible'));
document.getElementById('receipt-print').addEventListener('click', () => Receipt.print());

// ------------------------------------------------------------------ Bookings

let currentBookingId = null;
let currentBookingAction = null; // 'complete' | 'cancel'
let currentBookingCache = {}; // id -> booking object, so the modal can read balance/history
const bookingActionBackdrop = document.getElementById('booking-action-backdrop');

function renderBookingHistory(booking) {
  const list = document.getElementById('booking-history-list');
  const payments = booking.payments || [];
  if (!payments.length) {
    list.innerHTML = '<span style="color:var(--ink-faint);">No payments recorded yet.</span>';
    return;
  }
  list.innerHTML = payments.map((p) => `
    <div style="display:flex;justify-content:space-between;gap:8px;padding:3px 0;">
      <span>${formatNaira(p.amount_naira)} · ${escapeHtml(p.method)}</span>
      <span style="color:var(--ink-faint);">${escapeHtml(p.recorded_by_name || 'Unknown')}${p.created_at ? ` · ${new Date(p.created_at).toLocaleString('en-NG', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}` : ''}</span>
    </div>`).join('');
}

async function loadBookings() {
  const tbody = document.getElementById('bookings-tbody');
  const emptyState = document.getElementById('bookings-empty');
  try {
    const branchId = BranchContext.get();
    const bookings = await Api.listBookings(branchId);
    currentBookingCache = {};
    bookings.forEach((b) => { currentBookingCache[b.id] = b; });
    if (!bookings.length) {
      tbody.innerHTML = '';
      emptyState.style.display = '';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = bookings.map((b) => {
      const itemsSummary = b.items.map((it) => `${it.quantity}× ${escapeHtml(it.product_name || 'item')}`).join(', ');
      return `
      <tr>
        <td>${new Date(b.created_at).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
        <td>${escapeHtml(b.cashier_name || 'Unknown')}</td>
        <td>${itemsSummary}</td>
        <td>${formatNaira(b.total_naira)}</td>
        <td>${formatNaira(b.paid_naira)}</td>
        <td><strong>${formatNaira(Math.max(b.total_naira + (b.storage_fee_naira || 0) - b.paid_naira, 0))}</strong>${b.storage_fee_naira > 0 ? ' <span style="color:var(--danger);font-size:11px;">incl. storage fee</span>' : ''}</td>
        <td>${b.notes ? escapeHtml(b.notes) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td class="row-actions-links">
          <a href="#" class="action-link action-link-payment" data-booking-complete="${b.id}">Add payment</a>
          <a href="#" class="action-link action-link-change" data-booking-change-item="${b.id}">Change item</a>
          <a href="#" class="action-link action-link-receipt" data-booking-receipt="${b.id}">Receipt</a>
          <a href="#" class="action-link action-link-cancel" data-booking-cancel="${b.id}">Cancel</a>
        </td>
      </tr>`;
    }).join('');

    tbody.querySelectorAll('[data-booking-complete]').forEach((btn) => {
      btn.addEventListener('click', (e) => { e.preventDefault(); openBookingAction(btn.dataset.bookingComplete, 'complete'); });
    });
    tbody.querySelectorAll('[data-booking-receipt]').forEach((btn) => {
      // Every booking -- first deposit, and each later top-up -- is
      // printable from here, not just the final completed sale (spec:
      // "even in their first booking, second booking, third booking,
      // they should be able to print receipt for each of the bookings").
      btn.addEventListener('click', (e) => { e.preventDefault(); openReceipt(btn.dataset.bookingReceipt); });
    });
    tbody.querySelectorAll('[data-booking-cancel]').forEach((btn) => {
      btn.addEventListener('click', (e) => { e.preventDefault(); openBookingAction(btn.dataset.bookingCancel, 'cancel'); });
    });
    tbody.querySelectorAll('[data-booking-change-item]').forEach((btn) => {
      btn.addEventListener('click', (e) => { e.preventDefault(); openChangeItemModal(btn.dataset.bookingChangeItem); });
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

// September 2026 change spec sections 9/10: shared same-day-cash /
// bank-transfer eligibility check for both booking cancellation and
// change-item refunds -- mirrors the backend's own logic exactly
// (same_day = within 24h of booking creation; paid_in_cash = the
// booking's payment_method is 'cash') so the UI defaults to whichever
// option the server will actually accept, though the server has the
// final say (a stale client clock/cache just means a clearer error
// message, not a security check bypassed).
function isBookingRefundCashEligible(booking) {
  if (!booking || !booking.created_at) return false;
  const createdAt = new Date(booking.created_at);
  const sameDay = (Date.now() - createdAt.getTime()) <= 24 * 60 * 60 * 1000;
  return sameDay && booking.payment_method === 'cash';
}

function updateBookingCancelRefundFields() {
  const booking = currentBookingCache[currentBookingId];
  const depositPaid = booking ? booking.paid_naira > 0 : false;
  const fieldsWrap = document.getElementById('booking-cancel-refund-fields');
  fieldsWrap.style.display = depositPaid ? '' : 'none';
  if (!depositPaid) return;
  const methodSelect = document.getElementById('booking-cancel-refund-method');
  const cashEligible = isBookingRefundCashEligible(booking);
  methodSelect.querySelector('option[value="cash"]').disabled = !cashEligible;
  methodSelect.value = cashEligible ? 'cash' : 'transfer';
  document.getElementById('booking-cancel-refund-hint').textContent = cashEligible
    ? `${formatNaira(booking.paid_naira)} deposit paid in cash today — hand it back directly, or switch to bank transfer.`
    : `${formatNaira(booking.paid_naira)} deposit paid — this can no longer be refunded in cash on the spot (either it wasn't cash originally, or it's been more than 24 hours), so it goes by bank transfer.`;
  updateBookingCancelRefundBankFields();
  populateBankSelect(document.getElementById('booking-cancel-refund-bank-name'));
}
document.getElementById('booking-cancel-refund-method').addEventListener('change', updateBookingCancelRefundBankFields);
// September 2026 change spec section 10 (cancellation reason presets):
// one-click reasons for the free-text cancel field -- e.g. "Already
// sold out" when staff cancel specifically to resell the item to a
// walk-in customer at the POS.
document.querySelectorAll('[data-reason-preset]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.getElementById('booking-cancel-reason').value = btn.dataset.reasonPreset;
  });
});
function updateBookingCancelRefundBankFields() {
  const isTransfer = document.getElementById('booking-cancel-refund-method').value === 'transfer';
  document.getElementById('booking-cancel-refund-bank-fields').style.display = isTransfer ? '' : 'none';
}

function openBookingAction(bookingId, action) {
  currentBookingId = bookingId;
  currentBookingAction = action;
  const booking = currentBookingCache[bookingId];
  document.getElementById('booking-action-error').classList.remove('visible');
  document.getElementById('booking-action-title').textContent =
    action === 'complete' ? 'Add payment' : 'Cancel booking';
  document.getElementById('booking-action-complete-fields').style.display = action === 'complete' ? '' : 'none';
  document.getElementById('booking-action-cancel-fields').style.display = action === 'cancel' ? '' : 'none';
  document.getElementById('booking-history-section').style.display = action === 'complete' ? '' : 'none';
  document.getElementById('booking-cancel-reason').value = '';
  document.getElementById('booking-cancel-refund-account-number').value = '';
  document.getElementById('booking-cancel-refund-account-name').value = '';
  if (action === 'cancel') updateBookingCancelRefundFields();
  if (booking) {
    renderBookingHistory(booking);
    const balance = Math.max(booking.total_naira + (booking.storage_fee_naira || 0) - booking.paid_naira, 0);
    document.getElementById('booking-payment-amount').value = balance;
    document.getElementById('booking-payment-amount').max = balance;
    document.getElementById('booking-pickup-date').value = booking.pickup_date ? booking.pickup_date.slice(0, 10) : '';
    const feeNote = document.getElementById('booking-storage-fee-note');
    if (booking.storage_fee_naira > 0) {
      feeNote.textContent = `This deposit has passed 1 year — a storage/security fee of ${formatNaira(booking.storage_fee_naira)} has been added to the balance.`;
      feeNote.style.display = '';
    } else {
      feeNote.style.display = 'none';
    }
  }
  bookingActionBackdrop.classList.add('visible');
}

document.getElementById('booking-action-close').addEventListener('click', () => {
  bookingActionBackdrop.classList.remove('visible');
});

document.getElementById('booking-action-confirm').addEventListener('click', async () => {
  const errorEl = document.getElementById('booking-action-error');
  errorEl.classList.remove('visible');
  const btn = document.getElementById('booking-action-confirm');
  btn.disabled = true;
  try {
    if (currentBookingAction === 'complete') {
      const amount = Number(document.getElementById('booking-payment-amount').value || 0);
      if (amount <= 0) {
        errorEl.textContent = 'Enter an amount greater than zero.';
        errorEl.classList.add('visible');
        btn.disabled = false;
        return;
      }
      const method = document.getElementById('booking-complete-method').value;
      const pickupDate = document.getElementById('booking-pickup-date').value || null;

      const ok = await confirmAction({
        title: 'Confirm this payment',
        sub: 'This will be recorded against the booking and cannot be undone.',
        confirmLabel: 'Confirm payment',
        bodyHtml: `
          <div style="display:flex;justify-content:space-between;"><span>Amount</span><strong>${formatNaira(amount)}</strong></div>
          <div style="display:flex;justify-content:space-between;"><span>Method</span><strong>${escapeHtml(method)}</strong></div>
          ${pickupDate ? `<div style="display:flex;justify-content:space-between;"><span>Pickup date</span><strong>${escapeHtml(pickupDate)}</strong></div>` : ''}
        `,
      });
      btn.disabled = false;
      if (!ok) return;
      btn.disabled = true;

      const sale = await Api.addBookingPayment(currentBookingId, {
        amount_naira: amount,
        payment_method: method,
        pickup_date: pickupDate,
      });
      bookingActionBackdrop.classList.remove('visible');
      if (sale.status === 'completed') {
        showToast('Payment recorded — booking completed.');
        openReceipt(sale.id);
      } else {
        showToast(`Payment recorded — ${formatNaira(Math.max(sale.total_naira + (sale.storage_fee_naira || 0) - sale.paid_naira, 0))} still owed.`);
      }
    } else {
      const reason = document.getElementById('booking-cancel-reason').value.trim();
      if (!reason) {
        errorEl.textContent = 'A cancellation reason is required.';
        errorEl.classList.add('visible');
        btn.disabled = false;
        return;
      }
      const payload = { reason };
      const booking = currentBookingCache[currentBookingId];
      // September 2026 change spec section 10: a deposit already paid
      // now actively triggers a refund on cancellation -- same-day
      // cash if that's how it was paid, bank transfer otherwise.
      if (booking && booking.paid_naira > 0) {
        payload.refund_method = document.getElementById('booking-cancel-refund-method').value;
        if (payload.refund_method === 'transfer') {
          payload.refund_bank_name = document.getElementById('booking-cancel-refund-bank-name').value;
          payload.refund_account_number = document.getElementById('booking-cancel-refund-account-number').value.trim();
          payload.refund_account_name = document.getElementById('booking-cancel-refund-account-name').value.trim();
          if (!(payload.refund_bank_name && payload.refund_account_number && payload.refund_account_name)) {
            errorEl.textContent = 'Bank name, account number, and account name are required for the refund.';
            errorEl.classList.add('visible');
            btn.disabled = false;
            return;
          }
        }
      }
      await Api.cancelBooking(currentBookingId, payload);
      showToast(
        booking && booking.paid_naira > 0
          ? (payload.refund_method === 'cash'
              ? `Booking cancelled — ${formatNaira(booking.paid_naira)} handed back in cash.`
              : `Booking cancelled — ${formatNaira(booking.paid_naira)} refund submitted by bank transfer.`)
          : 'Booking cancelled.'
      );
      bookingActionBackdrop.classList.remove('visible');
    }
    await loadBookings();
    await loadSales();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
  }
});

// -------------------------------------------------------- Change item
//
// The product picker is a plain native <select> (#change-item-product),
// same pattern as the Product Categories page's own "Product" picker
// (js/category-variants.js, #pc-product): every product for the chosen
// category (or the whole catalog, if no category is set yet) is loaded
// into it up front, sorted alphabetically. Using a real <select> means
// the browser handles opening/closing, scrolling, and type-ahead itself
// (type a letter and it jumps to the first option starting with it) --
// no custom JS needed for any of that, and since the browser renders the
// open list in its own top layer, it can never end up covering the
// Category field or anything else on the page the way an absolutely
// positioned custom dropdown could.

let currentChangeItemBookingId = null;
const changeItemBackdrop = document.getElementById('change-item-backdrop');
const changeItemProductSelect = document.getElementById('change-item-product');
const changeItemCategorySelect = document.getElementById('change-item-category');
const changeItemQuantityInput = document.getElementById('change-item-quantity');
const changeItemPriceInput = document.getElementById('change-item-price');
const changeItemHint = document.getElementById('change-item-hint');
// product id -> full product object, refreshed every time the list reloads.
let changeItemProductMap = {};
let changeItemSelectedProduct = null;
let changeItemCategoryCache = [];

async function openChangeItemModal(bookingId) {
  currentChangeItemBookingId = bookingId;
  document.getElementById('change-item-error').classList.remove('visible');
  changeItemSelectedProduct = null;
  changeItemQuantityInput.value = '1';
  changeItemPriceInput.value = '';
  changeItemHint.textContent = '';
  document.getElementById('change-item-refund-fields').style.display = 'none';
  document.getElementById('change-item-refund-account-number').value = '';
  document.getElementById('change-item-refund-account-name').value = '';
  if (!changeItemCategoryCache.length) {
    try {
      changeItemCategoryCache = await Api.listCategories();
    } catch (err) {
      // Non-fatal -- category stays as just "All categories", product list still loads
    }
  }
  changeItemCategorySelect.innerHTML = '<option value="">All categories</option>' +
    changeItemCategoryCache.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  changeItemBackdrop.classList.add('visible');
  await loadChangeItemProducts();
  changeItemProductSelect.focus();
}

// Loads every product for the given category (or the whole catalog when
// categoryId is empty) into the Product select, sorted alphabetically by
// name so the browser's own type-ahead jumps to the right spot as soon
// as a letter is typed -- same as the Product Categories page.
let changeItemLoadToken = 0;
async function loadChangeItemProducts(categoryId) {
  const myToken = ++changeItemLoadToken;
  const previousProductId = changeItemSelectedProduct?.id;
  changeItemProductSelect.innerHTML = '<option value="">Loading…</option>';
  changeItemProductSelect.disabled = true;
  try {
    const products = await Api.listProducts({
      branchId: BranchContext.get(),
      categoryId: categoryId || undefined,
    });
    if (myToken !== changeItemLoadToken) return; // a newer load has since started -- discard this stale result
    products.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
    changeItemProductMap = {};
    products.forEach((p) => { changeItemProductMap[p.id] = p; });
    changeItemProductSelect.innerHTML = '<option value="">Select a product…</option>' +
      products.map((p) => `<option value="${p.id}">${escapeHtml(p.name)} — ${formatNaira(p.selling_price_naira)}</option>`).join('');
    // Keep the current selection if it's still in the (possibly narrowed) list.
    if (previousProductId && changeItemProductMap[previousProductId]) {
      changeItemProductSelect.value = previousProductId;
    } else if (previousProductId) {
      changeItemSelectedProduct = null;
      changeItemPriceInput.value = '';
      changeItemHint.textContent = '';
    }
  } catch (err) {
    changeItemProductSelect.innerHTML = '<option value="">Couldn\'t load products</option>';
    showToast(err.message, true);
  } finally {
    changeItemProductSelect.disabled = false;
  }
}

changeItemProductSelect.addEventListener('change', () => {
  changeItemSelectedProduct = changeItemProductMap[changeItemProductSelect.value] || null;
  if (!changeItemSelectedProduct) {
    changeItemPriceInput.value = '';
    changeItemHint.textContent = '';
    return;
  }
  // Setting .value programmatically doesn't fire this select's own
  // 'change' handler, so this can't loop back into reloading the list.
  if (changeItemSelectedProduct.category_id) {
    changeItemCategorySelect.value = changeItemSelectedProduct.category_id;
  }
  fillChangeItemPrice();
});

// Picking a category reloads the Product select down to just that
// category's items -- the whole list, ready to click through or jump
// into with the keyboard, not something you have to type first to see.
changeItemCategorySelect.addEventListener('change', () => {
  loadChangeItemProducts(changeItemCategorySelect.value);
});

function fillChangeItemPrice() {
  const product = changeItemSelectedProduct;
  if (!product) return;
  const qty = Math.max(1, Number(changeItemQuantityInput.value) || 1);
  changeItemPriceInput.value = (product.selling_price_naira * qty).toFixed(2);
  changeItemHint.textContent = `Auto-filled: ${formatNaira(product.selling_price_naira)} × ${qty}. Leave as-is to use the current selling price.`;
  updateChangeItemRefundFields();
}
changeItemQuantityInput.oninput = fillChangeItemPrice;
changeItemPriceInput.oninput = updateChangeItemRefundFields;

// September 2026 change spec section 9: only relevant when the new
// item's total is less than the deposit already paid on this booking
// -- same same-day-cash / bank-transfer routing as booking
// cancellation (section 10), via the shared isBookingRefundCashEligible().
function updateChangeItemRefundFields() {
  const fieldsWrap = document.getElementById('change-item-refund-fields');
  const booking = currentBookingCache[currentChangeItemBookingId];
  const newTotal = changeItemPriceInput.value ? Number(changeItemPriceInput.value) : null;
  const ownesRefund = booking && newTotal !== null && booking.paid_naira > newTotal;
  fieldsWrap.style.display = ownesRefund ? '' : 'none';
  if (!ownesRefund) return;
  const difference = booking.paid_naira - newTotal;
  const methodSelect = document.getElementById('change-item-refund-method');
  const cashEligible = isBookingRefundCashEligible(booking);
  methodSelect.querySelector('option[value="cash"]').disabled = !cashEligible;
  methodSelect.value = cashEligible ? 'cash' : 'transfer';
  document.getElementById('change-item-refund-hint').textContent = cashEligible
    ? `${formatNaira(difference)} is owed back — paid in cash today, so hand it back directly, or switch to bank transfer.`
    : `${formatNaira(difference)} is owed back — this can no longer be refunded in cash on the spot (either the deposit wasn't cash originally, or it's been more than 24 hours), so it goes by bank transfer.`;
  updateChangeItemRefundBankFields();
  populateBankSelect(document.getElementById('change-item-refund-bank-name'));
}
document.getElementById('change-item-refund-method').addEventListener('change', updateChangeItemRefundBankFields);
function updateChangeItemRefundBankFields() {
  const isTransfer = document.getElementById('change-item-refund-method').value === 'transfer';
  document.getElementById('change-item-refund-bank-fields').style.display = isTransfer ? '' : 'none';
}

document.getElementById('change-item-close').addEventListener('click', () => changeItemBackdrop.classList.remove('visible'));
changeItemBackdrop.addEventListener('click', (e) => { if (e.target === changeItemBackdrop) changeItemBackdrop.classList.remove('visible'); });

document.getElementById('change-item-confirm').addEventListener('click', async () => {
  const errorEl = document.getElementById('change-item-error');
  errorEl.classList.remove('visible');
  if (!changeItemSelectedProduct) {
    errorEl.textContent = 'Select the new product.';
    errorEl.classList.add('visible');
    return;
  }
  const btn = document.getElementById('change-item-confirm');
  btn.disabled = true;
  try {
    const priceNaira = changeItemPriceInput.value ? Number(changeItemPriceInput.value) : null;
    const quantity = Math.max(1, Number(changeItemQuantityInput.value) || 1);
    const productLabel = changeItemSelectedProduct.name;

    const ok = await confirmAction({
      title: 'Confirm item change',
      sub: 'This replaces the booking\'s item and updates its total. It cannot be undone.',
      confirmLabel: 'Change item',
      bodyHtml: `
        <div style="display:flex;justify-content:space-between;"><span>New item</span><strong>${escapeHtml(productLabel)}</strong></div>
        <div style="display:flex;justify-content:space-between;"><span>Quantity</span><strong>${quantity}</strong></div>
        ${priceNaira !== null ? `<div style="display:flex;justify-content:space-between;"><span>New total</span><strong>${formatNaira(priceNaira)}</strong></div>` : ''}
      `,
    });
    if (!ok) { btn.disabled = false; return; }

    const payload = {
      product_id: changeItemSelectedProduct.id,
      quantity,
      // Api sends a total; convert back to a per-unit price for the
      // backend, which expects unit_price_naira (it multiplies by
      // quantity itself so the total always matches quantity × price).
      unit_price_naira: priceNaira !== null ? priceNaira / quantity : null,
    };
    const refundFieldsVisible = document.getElementById('change-item-refund-fields').style.display !== 'none';
    if (refundFieldsVisible) {
      payload.refund_method = document.getElementById('change-item-refund-method').value;
      if (payload.refund_method === 'transfer') {
        payload.refund_bank_name = document.getElementById('change-item-refund-bank-name').value;
        payload.refund_account_number = document.getElementById('change-item-refund-account-number').value.trim();
        payload.refund_account_name = document.getElementById('change-item-refund-account-name').value.trim();
        if (!(payload.refund_bank_name && payload.refund_account_number && payload.refund_account_name)) {
          errorEl.textContent = 'Bank name, account number, and account name are required for the refund.';
          errorEl.classList.add('visible');
          btn.disabled = false;
          return;
        }
      }
    }
    await Api.changeBookingItem(currentChangeItemBookingId, payload);
    showToast('Item changed — total and balance updated.');
    changeItemBackdrop.classList.remove('visible');
    await loadBookings();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------- Tabs

let activeTab = 'sales';
// Keeps the URL hash in sync with whichever tab is active, so a page
// refresh (or a copied/bookmarked link) lands back on the same tab
// instead of always resetting to Sales. Uses replaceState rather than
// a plain hash assignment so clicking between tabs doesn't fill up the
// browser's back-button history with one entry per tab click.
function setTabHash(tabName) {
  const hashMap = {
    sales: '',
    bookings: '#bookings',
    refunds: '#refunds',
    credit: '#credit',
    'pending-change': '#pending-change',
    losses: '#losses',
    'website-orders': '#website-orders',
  };
  const newHash = hashMap.hasOwnProperty(tabName) ? hashMap[tabName] : '';
  const newUrl = window.location.pathname + window.location.search + newHash;
  history.replaceState(null, '', newUrl);
}
function activateTab(tabName) {
  const btn = document.querySelector(`#main-tabs .tab-btn[data-tab="${tabName}"]`);
  if (!btn) return;
  activeTab = tabName;
  setTabHash(tabName);
  // Scoped to #main-tabs so this never touches the Pending Change/
  // Losses tabs' own internal sub-tab buttons, which share the same
  // .tab-btn class for styling but live in their own namespaced
  // attribute (data-change-tab / data-loss-tab) -- see
  // js/pending-change.js and js/losses.js.
  document.querySelectorAll('#main-tabs .tab-btn').forEach((b) => b.classList.toggle('active', b === btn));
  document.getElementById('sales-section').style.display = activeTab === 'sales' ? '' : 'none';
  document.getElementById('bookings-section').style.display = activeTab === 'bookings' ? '' : 'none';
  document.getElementById('refunds-section').style.display = activeTab === 'refunds' ? '' : 'none';
  document.getElementById('credit-section').style.display = activeTab === 'credit' ? '' : 'none';
  document.getElementById('pending-change-section').style.display = activeTab === 'pending-change' ? '' : 'none';
  document.getElementById('losses-section').style.display = activeTab === 'losses' ? '' : 'none';
  document.getElementById('website-orders-section').style.display = activeTab === 'website-orders' ? '' : 'none';
  if (activeTab === 'bookings') { loadBookings(); loadBookingHistory(); }
  if (activeTab === 'refunds') loadRefundHistory();
  if (activeTab === 'credit') loadCredit();
  if (activeTab === 'pending-change') loadPendingChanges();
  if (activeTab === 'losses') loadLossesHistory();
  if (activeTab === 'website-orders' && typeof loadWebsiteOrders === 'function') loadWebsiteOrders();
}
document.querySelectorAll('#main-tabs .tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
});
// Losses is gated the same as the old standalone nav item was
// (view_all_reports -- super_admin/branch_manager tier); a cashier
// has no Losses tab at all, same as they never had the sidebar link.
if (!Perms.can('view_all_reports')) {
  document.querySelector('#main-tabs .tab-btn[data-tab="losses"]')?.remove();
}
if (window.location.hash === '#refunds') activateTab('refunds');
if (window.location.hash === '#bookings') activateTab('bookings');
// #credit / #pending-change / #losses are each deferred to their own
// script's init (below) since their load functions aren't defined yet
// at this point in script load order.

// The audit log's `action` field for every booking-related event, and
// how to label each in the history table.
const BOOKING_HISTORY_LABELS = {
  booking_created: 'Booking opened (deposit taken)',
  booking_payment_added: 'Payment / top-up added',
  booking_completed: 'Completed (balance paid, picked up)',
  booking_item_changed: 'Item swapped',
  booking_cancelled: 'Cancelled',
};

function describeBookingHistoryEntry(entry) {
  const m = entry.metadata || {};
  switch (entry.action) {
    case 'booking_item_changed':
      return `${escapeHtml(m.old_product || '?')} → ` +
        `${escapeHtml(m.new_product || '?')}. ` +
        `New total ${escapeHtml(m.new_total_naira != null ? formatNaira(m.new_total_naira) : '—')}, ` +
        `balance now ${escapeHtml(m.new_balance_naira != null ? formatNaira(m.new_balance_naira) : '—')}.`;
    case 'booking_cancelled':
      return `Reason: ${escapeHtml(m.reason || '—')}. ${m.restocked ? 'Item restocked.' : 'Item not restocked.'} ` +
        `Deposit paid: ${escapeHtml(m.deposit_paid_naira != null ? formatNaira(m.deposit_paid_naira) : '—')}.`;
    case 'booking_completed':
    case 'booking_payment_added':
      return `Amount: ${escapeHtml(m.amount_naira != null ? formatNaira(m.amount_naira) : '—')}, method: ${escapeHtml(m.method || '—')}.`;
    case 'booking_created':
      return `Deposit: ${escapeHtml(m.deposit_naira != null ? formatNaira(m.deposit_naira) : '—')}, total: ${escapeHtml(m.total_naira != null ? formatNaira(m.total_naira) : '—')}.`;
    default:
      return Object.keys(m).length ? escapeHtml(JSON.stringify(m)) : '—';
  }
}

async function loadBookingHistory() {
  const tbody = document.getElementById('booking-history-tbody');
  const table = document.getElementById('booking-history-table');
  const emptyState = document.getElementById('booking-history-empty');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading…</td></tr>';
  let entries;
  try {
    entries = await Api.auditLog({ entityType: 'sale', limit: 300 });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }
  entries = (entries || []).filter((e) => e.action && e.action.startsWith('booking_'));
  if (!entries.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = entries.map((e) => `
    <tr>
      <td>${formatDateTime(e.created_at)}</td>
      <td>${escapeHtml(BOOKING_HISTORY_LABELS[e.action] || e.action)}</td>
      <td>${escapeHtml(e.user_name || '—')}</td>
      <td style="font-size:12.5px;color:var(--ink-muted);">${describeBookingHistoryEntry(e)}</td>
      <td class="row-actions-links">${e.entity_id ? `
        <a href="#" class="action-link action-link-view" data-booking-history-view="${e.entity_id}">View</a>
        <a href="#" class="action-link action-link-refund" data-booking-history-refund="${e.entity_id}">Refund…</a>` : ''}</td>
    </tr>`).join('');

  // Opens the same printable receipt sheet used for an ordinary sale --
  // shows the logo, the attending cashier, the items, and (since this is
  // a booking) the full deposit/payment history, with printing optional
  // (spec: "it should now be optional for you to print or not").
  tbody.querySelectorAll('[data-booking-history-view]').forEach((btn) => {
    btn.addEventListener('click', (e) => { e.preventDefault(); openReceipt(btn.dataset.bookingHistoryView); });
  });
  tbody.querySelectorAll('[data-booking-history-refund]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      await openReceipt(btn.dataset.bookingHistoryRefund);
      if (refundToggle.style.display !== 'none') {
        refundPanel.style.display = 'block';
      }
    });
  });
}

let refundHistoryCache = [];

async function loadRefundHistory() {
  const tbody = document.getElementById('refunds-tbody');
  const table = document.getElementById('refunds-table');
  const emptyState = document.getElementById('refunds-empty');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="9">Loading…</td></tr>';
  try {
    refundHistoryCache = await Api.listRefunds({ branchId: BranchContext.get() });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }
  if (!refundHistoryCache.length) {
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  const sourceLabels = { sale: 'Sale', credit: 'Credit', booking: 'Booking' };
  const canApprove = ['super_admin', 'branch_manager'].includes(currentUser?.role);
  tbody.innerHTML = refundHistoryCache.map((r) => `
    <tr>
      <td>${formatDateTime(r.created_at)}</td>
      <td><span class="category-badge">${escapeHtml(sourceLabels[r.source] || 'Sale')}</span></td>
      <td>${formatDateTime(r.original_sale_at)}${r.receipt_number ? ` <span class="mono" style="color:var(--ink-faint);">(${escapeHtml(r.receipt_number.slice(0, 8))}…)</span>` : ''}</td>
      <td>${escapeHtml(r.original_cashier_name || '—')}</td>
      <td>${escapeHtml(r.processed_by_name || '—')}</td>
      <td>${escapeHtml(r.reason || '—')}</td>
      <td>${formatNaira(r.amount_naira)}</td>
      <td>${r.status === 'pending'
        ? '<span class="stock-pill low">Pending</span>'
        : '<span class="stock-pill">Completed</span>'}</td>
      <td class="row-actions-links">
        <a href="#" class="action-link action-link-view" data-refund-detail="${r.id}">Details</a>
        ${r.status === 'pending' && canApprove ? `<a href="#" class="action-link action-link-payment" data-mark-processed="${r.id}">Mark processed</a>` : ''}
      </td>
    </tr>`).join('');

  tbody.querySelectorAll('[data-refund-detail]').forEach((btn) => {
    btn.addEventListener('click', (e) => { e.preventDefault(); openRefundHistoryDetail(btn.dataset.refundDetail); });
  });
  tbody.querySelectorAll('[data-mark-processed]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      btn.style.pointerEvents = 'none';
      try {
        await Api.markRefundProcessed(btn.dataset.markProcessed);
        showToast('Refund marked as processed.');
        await loadRefundHistory();
        if (typeof refreshPendingRefundBadge === 'function') refreshPendingRefundBadge();
      } catch (err) {
        showToast(err.message, true);
        btn.style.pointerEvents = '';
      }
    });
  });
}

const refundHistoryDetailBackdrop = document.getElementById('refund-history-detail-backdrop');
async function openRefundHistoryDetail(refundId) {
  const sheet = document.getElementById('refund-history-detail-sheet');
  sheet.innerHTML = '<p class="hint">Loading refund receipt…</p>';
  refundHistoryDetailBackdrop.classList.add('visible');
  try {
    const detail = await Api.getRefundDetail(refundId);
    Receipt.renderRefund(sheet, detail, StoreBranding.current());
  } catch (err) {
    sheet.innerHTML = '';
    refundHistoryDetailBackdrop.classList.remove('visible');
    showToast(err.message, true);
  }
}
document.getElementById('refund-history-detail-close').addEventListener('click', () => refundHistoryDetailBackdrop.classList.remove('visible'));
document.getElementById('refund-history-detail-print').addEventListener('click', () => Receipt.print());
refundHistoryDetailBackdrop.addEventListener('click', (e) => { if (e.target === refundHistoryDetailBackdrop) refundHistoryDetailBackdrop.classList.remove('visible'); });

// ------------------------------------------------------------------ Init

Promise.all([loadBranchFilter(), Perms.isStaff() ? Promise.resolve() : loadCashierFilter()]).then(() => {
  loadSales();
  loadBookings();
  // Item 10: Losses' "View sale" link now points here with
  // ?sale_id=<id> instead of the bare page -- open that specific
  // sale's receipt straight away instead of leaving the admin to
  // find it themselves in the list.
  const requestedSaleId = new URLSearchParams(window.location.search).get('sale_id');
  if (requestedSaleId) openReceipt(requestedSaleId);
});

window.addEventListener('branch-context-changed', () => {
  loadSales();
  loadBookings();
  if (activeTab === 'refunds') loadRefundHistory();
});
// PATCH3E-SWAP-LABELS
