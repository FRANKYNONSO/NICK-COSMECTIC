// Website Orders tab (Transactions page).
// Shows orders placed on the customer storefront (port 8001) so staff can
// see who ordered what, which branches must send which pieces, merge the
// parts at one branch, and record the waybill. Everything here talks to
// GET/PATCH /api/v1/storefront/orders -- nothing here touches POS checkout,
// inventory, or stock. Loaded AFTER sales.js (uses its helpers:
// escapeHtml, formatNaira, formatDateTime, showToast, confirmAction,
// openReceipt, activateTab).

const WO_STATUSES = ['new', 'processing', 'waybilled', 'delivered', 'cancelled'];
const WO_STATUS_LABEL = { new: 'New', processing: 'Processing', waybilled: 'Waybilled', delivered: 'Delivered', cancelled: 'Cancelled' };

let woOrders = [];
let woLoaded = false;

(function injectWoStyle() {
  if (document.getElementById('wo-style')) return;
  const st = document.createElement('style');
  st.id = 'wo-style';
  st.textContent = `
    #website-orders-section td { vertical-align: top; font-size: 12.5px; line-height: 1.5; }
    .wo-pill { display:inline-block; padding:2px 9px; border-radius:100px; font-size:11px; font-weight:600; }
    .wo-pill.new { background: var(--warning-tint); color: var(--warning); }
    .wo-pill.processing { background: rgba(75,163,242,.15); color: #4ba3f2; }
    .wo-pill.waybilled { background: rgba(124,156,255,.16); color: #7c9cff; }
    .wo-pill.delivered { background: var(--success-tint); color: var(--success); }
    .wo-pill.cancelled { background: var(--danger-tint); color: var(--danger); }
    .wo-pill.unpaid { background: rgba(128,128,128,.16); color: var(--ink-muted); }
    .wo-pill.paid { background: var(--success-tint); color: var(--success); }
    .wo-muted { color: var(--ink-muted); font-size: 11.5px; }
    .wo-merge { display:inline-block; margin-top:6px; padding:4px 8px; border-radius:8px; background: var(--warning-tint); color: var(--warning); font-size: 11.5px; font-weight:600; }
    .wo-one { display:inline-block; margin-top:6px; color: var(--ink-muted); font-size: 11.5px; }
    .wo-ctrl { display:flex; flex-direction:column; gap:6px; min-width:170px; }
    .wo-ctrl select, .wo-ctrl input, .wo-ctrl textarea { width:100%; font-size:12px; }
    .wo-receipts { display:flex; flex-direction:column; gap:6px; }
    .wo-table-wrap { overflow-x:auto; }
  `;
  document.head.appendChild(st);
})();

// Also escapes quotes: some values (waybill ref, ids) are placed inside HTML
// attributes, where the shared escapeHtml() (which leaves quotes alone) is not enough.
function woEsc(v) {
  return escapeHtml(v === null || v === undefined ? '' : String(v)).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function woShortId(id) { return String(id || '').slice(0, 8).toUpperCase(); }

// ---- Pure rendering helpers (no DOM access, so they can be tested alone)

function woPickListHtml(order) {
  const lines = (order.pick_list || []).map((p) => {
    const perBranch = (p.branches || []).map((b) => `${woEsc(b.branch_name)} <strong>${woEsc(b.quantity)}</strong>`).join(' &middot; ');
    return `<div style="margin-bottom:6px;"><strong>${woEsc(p.product_name)}</strong> &times; ${woEsc(p.total_quantity)}<div class="wo-muted">${perBranch}</div></div>`;
  }).join('');
  const branchCount = (order.sales || []).length;
  const merge = branchCount > 1
    ? `<div class="wo-merge">Parts come from ${branchCount} branches &mdash; gather them at ${woEsc(order.merge_at_branch_name || 'one branch')} and send as ONE parcel</div>`
    : `<div class="wo-one">Single branch${order.merge_at_branch_name ? ': ' + woEsc(order.merge_at_branch_name) : ''}</div>`;
  return lines + merge;
}

function woMoneyHtml(order) {
  const rows = [];
  rows.push(`Subtotal: ${formatNaira(order.subtotal_naira)}`);
  if (Number(order.discount_naira) > 0) {
    rows.push(`Discount: &minus;${formatNaira(order.discount_naira)}${order.coupon_code ? ' <span class="wo-muted">(' + woEsc(order.coupon_code) + ')</span>' : ''}`);
  }
  rows.push(`Total (sales): ${formatNaira(order.total_naira)}`);
  rows.push(`Shipping: ${Number(order.shipping_fee_naira) > 0 ? formatNaira(order.shipping_fee_naira) : '<span class="wo-muted">none set</span>'}`);
  if (order.shipping_note) rows.push(`<div class="wo-muted">${woEsc(order.shipping_note)}</div>`);
  rows.push(`<strong>Payable: ${formatNaira(order.payable_naira)}</strong>`);
  return rows.map((r) => `<div>${r}</div>`).join('');
}

function woReceiptsHtml(order) {
  return '<div class="wo-receipts">' + (order.sales || []).map((s) =>
    `<button type="button" class="btn btn-ghost btn-sm" data-wo-receipt="${woEsc(s.sale_id)}">Receipt ${woEsc(s.ticket)}<br><span class="wo-muted">${woEsc(s.branch_name)} &middot; ${formatNaira(s.total_naira)}</span></button>`
  ).join('') + '</div>';
}

// PATCH19C-PAYMENT-SCREEN
// Payment pill, "Mark as paid" button and the "Record payment" box.
// Talks only to POST /api/v1/storefront/orders/{id}/payment. It records that
// the customer paid; it changes no stock, sale or revenue figure.
const WO_PAY_METHOD_LABEL = { bank_transfer: 'Bank transfer', cash_on_delivery: 'Cash on delivery' };
const WO_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
let woPayOverlay = null;

function woPayDate(iso) {
  if (!iso) return '';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '';
  // Nigeria is UTC+1 all year (no daylight saving).
  const d = new Date(ms + 60 * 60 * 1000);
  return d.getUTCDate() + ' ' + WO_MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
}

function woIsPaid(order) {
  return !!order && order.payment_status === 'paid';
}

function woPaymentPillHtml(order) {
  if (!woIsPaid(order)) {
    return '<div style="margin-top:6px;"><span class="wo-pill unpaid">Not paid</span></div>';
  }
  const how = WO_PAY_METHOD_LABEL[order.payment_method] || '';
  const when = woPayDate(order.paid_at);
  const text = 'Paid ' + formatNaira(order.amount_received_naira) + (how ? ' by ' + how : '') + (when ? ', ' + when : '');
  return '<div style="margin-top:6px;"><span class="wo-pill paid">' + woEsc(text) + '</span></div>';
}

function woCanMarkPaid(order) {
  return !!order && order.status !== 'cancelled' && !woIsPaid(order);
}

function woPayButtonHtml(order) {
  if (!woCanMarkPaid(order)) return '';
  return '<button type="button" class="btn btn-ghost btn-sm" data-wo-pay="' + woEsc(order.id) + '">Mark as paid</button>';
}

function woPaidReminderHtml(order) {
  if (!woIsPaid(order)) return '';
  return '<div style="margin-top:8px;"><strong>This order was paid ' + formatNaira(order.amount_received_naira) + '. Remember to return the money to the customer.</strong></div>';
}

function woAmountDiffers(entered, payable) {
  if (entered === '' || entered === null || entered === undefined) return false;
  const a = Math.round(Number(entered) * 100);
  const p = Math.round(Number(payable) * 100);
  if (!Number.isFinite(a) || !Number.isFinite(p)) return false;
  return a !== p;
}

function woEl(tag, props, children) {
  const e = document.createElement(tag);
  Object.keys(props || {}).forEach((k) => {
    if (k === 'text') e.textContent = props[k];
    else if (k === 'className') e.className = props[k];
    else if (k === 'style') e.setAttribute('style', props[k]);
    else e[k] = props[k];
  });
  (children || []).forEach((c) => e.appendChild(c));
  return e;
}

function woPayField(label, input) {
  return woEl('div', { className: 'field' }, [woEl('label', { text: label, htmlFor: input.id }), input]);
}

function woClosePayBox() {
  if (woPayOverlay && woPayOverlay.parentNode) woPayOverlay.parentNode.removeChild(woPayOverlay);
  woPayOverlay = null;
}

function woOpenPayBox(orderId) {
  const order = woOrders.find((o) => o.id === orderId);
  if (!order) return;
  if (!woCanMarkPaid(order)) {
    showToast('This order cannot be marked as paid', true);
    return;
  }
  woClosePayBox();
  const payable = Number(order.payable_naira);

  const overlay = woEl('div', { id: 'wo-pay-overlay', style: 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.55);padding:12px;' });
  woPayOverlay = overlay;
  const box = woEl('div', { className: 'modal', style: 'position:relative;display:block;opacity:1;visibility:visible;transform:none;width:100%;max-width:440px;max-height:92vh;overflow:auto;' });
  overlay.appendChild(box);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) woClosePayBox(); });

  const bank = woEl('input', { type: 'radio', name: 'wo-pay-method', value: 'bank_transfer', id: 'wo-pay-bank' });
  const cash = woEl('input', { type: 'radio', name: 'wo-pay-method', value: 'cash_on_delivery', id: 'wo-pay-cash' });
  const methodRow = woEl('div', { className: 'field' }, [
    woEl('label', { text: 'How did the customer pay?' }),
    woEl('div', {}, [
      woEl('label', { style: 'margin-right:16px;cursor:pointer;' }, [bank, woEl('span', { text: ' Bank transfer' })]),
      woEl('label', { style: 'cursor:pointer;' }, [cash, woEl('span', { text: ' Cash on delivery' })]),
    ]),
  ]);
  const amount = woEl('input', { type: 'number', id: 'wo-pay-amount', min: '0', step: 'any', value: String(payable) });
  const note = woEl('input', { type: 'text', id: 'wo-pay-note', maxLength: 500 });
  const warn = woEl('div', { id: 'wo-pay-warn', style: 'color:var(--warning,#b7791f);background:var(--warning-tint,rgba(255,193,7,.15));font-size:13px;margin:6px 0;padding:6px 8px;border-radius:8px;', text: 'This is not the order total. Check the amount before saving.' });
  warn.style.display = 'none';
  const err = woEl('div', { id: 'wo-pay-err', style: 'color:var(--danger,#d33);font-size:13px;margin:6px 0;' });
  err.style.display = 'none';
  const cancel = woEl('button', { type: 'button', className: 'btn btn-ghost', text: 'Cancel' });
  const submit = woEl('button', { type: 'submit', className: 'btn btn-primary', text: 'Save payment' });
  const form = woEl('form', {}, [
    methodRow,
    woPayField('Amount received (\u20a6)', amount),
    woPayField('Note (optional)', note),
    warn,
    err,
    woEl('div', { className: 'modal-actions' }, [cancel, submit]),
  ]);
  box.appendChild(woEl('h3', { text: 'Record payment' }));
  box.appendChild(woEl('p', { className: 'modal-sub', text: 'Order total: ' + formatNaira(payable) }));
  box.appendChild(form);

  cancel.addEventListener('click', woClosePayBox);
  amount.addEventListener('input', () => {
    warn.style.display = woAmountDiffers(amount.value, payable) ? '' : 'none';
  });

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    err.style.display = 'none';
    const method = bank.checked ? 'bank_transfer' : (cash.checked ? 'cash_on_delivery' : '');
    const value = Number(amount.value);
    let msg = '';
    if (!method) msg = 'Choose how the customer paid.';
    else if (amount.value === '' || !(value > 0)) msg = 'The amount must be more than 0.';
    if (msg) {
      err.textContent = msg;
      err.style.display = '';
      return;
    }
    const payload = { method: method, amount_naira: value };
    if (note.value.trim()) payload.note = note.value.trim();
    submit.disabled = true;
    try {
      await Api.recordStorefrontPayment(orderId, payload);
      woClosePayBox();
      showToast('Payment recorded');
      await loadWebsiteOrders();
    } catch (e) {
      let text;
      if (e && e.status === 403) text = 'Only a super admin or a branch manager can record a payment.';
      else text = (e && e.message) || 'Could not record the payment.';
      err.textContent = text;
      err.style.display = '';
      submit.disabled = false;
      if (e && e.status === 409) await loadWebsiteOrders();
    }
  });

  document.body.appendChild(overlay);
  amount.focus();
}

// PATCH19D-UNDO-PAYMENT
// "Undo payment" button and box. Talks only to
// POST /api/v1/storefront/orders/{id}/payment/undo. It puts the payment back
// to "Not paid"; it changes no stock, sale or revenue figure.
let woUnpayOverlay = null;

function woCanUndoPayment(order) {
  return !!order && order.status !== 'cancelled' && woIsPaid(order);
}

function woUnpayButtonHtml(order) {
  if (!woCanUndoPayment(order)) return '';
  return '<button type="button" class="btn btn-ghost btn-sm" data-wo-unpay="' + woEsc(order.id) + '">Undo payment</button>';
}

function woRecordedText(order) {
  const how = WO_PAY_METHOD_LABEL[order.payment_method] || '';
  const when = woPayDate(order.paid_at);
  return 'Recorded: ' + formatNaira(order.amount_received_naira) + (how ? ' by ' + how : '') + (when ? ', ' + when : '') + '.';
}

function woCloseUnpayBox() {
  if (woUnpayOverlay && woUnpayOverlay.parentNode) woUnpayOverlay.parentNode.removeChild(woUnpayOverlay);
  woUnpayOverlay = null;
}

function woOpenUnpayBox(orderId) {
  const order = woOrders.find((o) => o.id === orderId);
  if (!order) return;
  if (!woCanUndoPayment(order)) {
    showToast('This payment cannot be undone', true);
    return;
  }
  woCloseUnpayBox();

  const overlay = woEl('div', { id: 'wo-unpay-overlay', style: 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.55);padding:12px;' });
  woUnpayOverlay = overlay;
  const box = woEl('div', { className: 'modal', style: 'position:relative;display:block;opacity:1;visibility:visible;transform:none;width:100%;max-width:440px;max-height:92vh;overflow:auto;' });
  overlay.appendChild(box);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) woCloseUnpayBox(); });

  const reason = woEl('input', { type: 'text', id: 'wo-unpay-reason', maxLength: 500 });
  const err = woEl('div', { id: 'wo-unpay-err', style: 'color:var(--danger,#d33);font-size:13px;margin:6px 0;' });
  err.style.display = 'none';
  const keep = woEl('button', { type: 'button', className: 'btn btn-ghost', text: 'Keep payment' });
  const submit = woEl('button', { type: 'submit', className: 'btn btn-primary', text: 'Undo payment' });
  const form = woEl('form', {}, [
    woPayField('Reason (required)', reason),
    err,
    woEl('div', { className: 'modal-actions' }, [keep, submit]),
  ]);
  box.appendChild(woEl('h3', { text: 'Undo payment' }));
  box.appendChild(woEl('p', { className: 'modal-sub', text: woRecordedText(order) }));
  box.appendChild(form);

  keep.addEventListener('click', woCloseUnpayBox);

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    err.style.display = 'none';
    const why = reason.value.trim();
    if (!why) {
      err.textContent = 'Write why the payment is being undone.';
      err.style.display = '';
      return;
    }
    submit.disabled = true;
    try {
      await Api.undoStorefrontPayment(orderId, { reason: why });
      woCloseUnpayBox();
      showToast('Payment undone');
      await loadWebsiteOrders();
    } catch (e) {
      let text;
      if (e && e.status === 403) text = 'Only a super admin or a branch manager can undo a payment.';
      else text = (e && e.message) || 'Could not undo the payment.';
      err.textContent = text;
      err.style.display = '';
      submit.disabled = false;
      if (e && e.status === 409) await loadWebsiteOrders();
    }
  });

  document.body.appendChild(overlay);
  reason.focus();
}

function woControlsHtml(order) {
  const opts = WO_STATUSES.map((s) => `<option value="${s}"${s === order.status ? ' selected' : ''}>${WO_STATUS_LABEL[s]}</option>`).join('');
  return `<div class="wo-ctrl" data-wo-row="${woEsc(order.id)}">
    <select data-wo-status>${opts}</select>
    <input type="text" data-wo-waybill placeholder="Waybill reference" maxlength="120" value="${woEsc(order.waybill_ref)}" />
    <textarea data-wo-note rows="2" placeholder="Admin note" maxlength="500">${woEsc(order.admin_note)}</textarea>
    <button type="button" class="btn btn-primary btn-sm" data-wo-save="${woEsc(order.id)}">Save</button>
    ${woPayButtonHtml(order)}
    ${woUnpayButtonHtml(order)}
  </div>`;
}

function woRowHtml(order) {
  const status = WO_STATUSES.includes(order.status) ? order.status : 'new';
  const place = [order.delivery_address, order.delivery_city, order.delivery_lga, order.delivery_state].filter(Boolean).map(woEsc).join(', ');
  return `<tr>
    <td><strong>${woShortId(order.id)}</strong><div class="wo-muted">${order.created_at ? woEsc(formatDateTime(order.created_at)) : ''}</div><div style="margin-top:6px;"><span class="wo-pill ${status}">${woEsc(WO_STATUS_LABEL[status])}</span></div>${woPaymentPillHtml(order)}</td>
    <td><strong>${woEsc(order.customer_name)}</strong><div>${woEsc(order.customer_phone)}</div><div class="wo-muted">${woEsc(order.customer_email)}</div></td>
    <td>${place}</td>
    <td>${woPickListHtml(order)}</td>
    <td>${woMoneyHtml(order)}</td>
    <td>${woReceiptsHtml(order)}</td>
    <td>${woControlsHtml(order)}</td>
  </tr>`;
}

// ---- Data + DOM

function woNormalise(res) {
  if (Array.isArray(res)) return res;
  if (res && Array.isArray(res.items)) return res.items;
  if (res && Array.isArray(res.orders)) return res.orders;
  return [];
}

function woVisibleOrders() {
  const statusEl = document.getElementById('wo-status-filter');
  const wanted = statusEl ? statusEl.value : '';
  const activeBranch = (typeof BranchContext !== 'undefined') ? BranchContext.get() : undefined;
  return woOrders.filter((o) => {
    if (wanted && o.status !== wanted) return false;
    if (activeBranch && !(o.sales || []).some((s) => s.branch_id === activeBranch)) return false;
    return true;
  });
}

function woUpdateTabBadge() {
  const btn = document.querySelector('#main-tabs .tab-btn[data-tab="website-orders"]');
  if (!btn) return;
  const n = woOrders.filter((o) => o.status === 'new').length;
  btn.innerHTML = 'Website Orders' + (n > 0 ? ` <span class="badge-count">${n}</span>` : '');
}

function woRender() {
  const tbody = document.getElementById('wo-tbody');
  const empty = document.getElementById('wo-empty');
  if (!tbody) return;
  const list = woVisibleOrders();
  tbody.innerHTML = list.map(woRowHtml).join('');
  if (empty) empty.style.display = list.length ? 'none' : '';
  const count = document.getElementById('wo-count');
  if (count) count.textContent = `${list.length} order${list.length === 1 ? '' : 's'}`;
}

async function loadWebsiteOrders() {
  try {
    woOrders = woNormalise(await Api.listStorefrontOrders());
    woLoaded = true;
    woUpdateTabBadge();
    woRender();
  } catch (err) {
    showToast(err.message || 'Could not load website orders', true);
  }
}

async function woSave(orderId, row) {
  const order = woOrders.find((o) => o.id === orderId);
  if (!order) return;
  const status = row.querySelector('[data-wo-status]').value;
  const waybill = row.querySelector('[data-wo-waybill]').value.trim();
  const note = row.querySelector('[data-wo-note]').value.trim();

  const payload = {};
  if (status !== order.status) payload.status = status;
  if (waybill !== (order.waybill_ref || '')) payload.waybill_ref = waybill;
  if (note !== (order.admin_note || '')) payload.admin_note = note;
  if (!Object.keys(payload).length) { showToast('Nothing to save'); return; }

  if (status === 'waybilled' && !waybill) {
    showToast('Enter the waybill reference before marking this order as waybilled', true);
    return;
  }
  if (payload.status === 'cancelled') {
    const ok = await confirmAction({
      title: 'Cancel this website order?',
      // PATCH11A-WEBSITE-CANCEL
      sub: 'The sale is cancelled and the items go back on the shelf.',
      bodyHtml: 'Stock goes back to the branch it was taken from. The sale will no longer count as revenue. Loyalty points for this order are taken back. This cannot be undone.' + woPaidReminderHtml(order),
      confirmLabel: 'Cancel order',
    });
    if (!ok) return;
  }
  const btn = row.querySelector('[data-wo-save]');
  btn.disabled = true;
  try {
    await Api.updateStorefrontOrder(orderId, payload);
    showToast('Order updated');
    await loadWebsiteOrders();
  } catch (err) {
    showToast(err.message || 'Could not update the order', true);
    btn.disabled = false;
  }
}

(function initWebsiteOrders() {
  // Same gate as the Losses tab: managers/admins only.
  if (typeof Perms !== 'undefined' && !Perms.can('view_all_reports')) {
    document.querySelector('#main-tabs .tab-btn[data-tab="website-orders"]')?.remove();
    return;
  }
  const tbody = document.getElementById('wo-tbody');
  if (!tbody) return;

  tbody.addEventListener('click', (e) => {
    const rec = e.target.closest('[data-wo-receipt]');
    if (rec) { openReceipt(rec.dataset.woReceipt); return; }
    const pay = e.target.closest('[data-wo-pay]');
    if (pay) { woOpenPayBox(pay.dataset.woPay); return; }
    const unpay = e.target.closest('[data-wo-unpay]');
    if (unpay) { woOpenUnpayBox(unpay.dataset.woUnpay); return; }
    const save = e.target.closest('[data-wo-save]');
    if (save) woSave(save.dataset.woSave, save.closest('[data-wo-row]'));
  });
  document.getElementById('wo-status-filter')?.addEventListener('change', woRender);
  document.getElementById('wo-refresh-btn')?.addEventListener('click', loadWebsiteOrders);
  window.addEventListener('branch-context-changed', woRender);

  // Load once so the tab shows a "new orders" count even before it is opened.
  loadWebsiteOrders();
  if (window.location.hash === '#website-orders') activateTab('website-orders');
})();

// ---- Website coupons panel (begin)
// Super admin only (the API enforces it: non-super-admins get 403 and the
// panel removes itself). Talks to /api/v1/storefront/coupons only.
const WOC_STATUS_LABEL = { waiting: 'Hidden, not revealed', active: 'Active', redeemed: 'Redeemed', voided: 'Voided', expired: 'Expired' };
let wocCoupons = [];

(function injectWocStyle() {
  if (document.getElementById('woc-style')) return;
  const st = document.createElement('style');
  st.id = 'woc-style';
  st.textContent = `
    .woc-panel { border:1px solid var(--border, rgba(128,128,128,.3)); border-radius:12px; padding:14px; margin-bottom:16px; }
    .woc-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
    .woc-head .woc-spacer { flex:1; }
    .woc-form { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin-bottom:12px; }
    .woc-form label { display:flex; flex-direction:column; gap:4px; font-size:11.5px; color: var(--ink-muted); }
    .woc-form input { padding:6px 8px; font-size:13px; min-width:110px; }
    .woc-form input.woc-note { min-width:200px; }
    .woc-new { margin-bottom:12px; padding:10px 12px; border-radius:10px; background: var(--success-tint); color: var(--success); font-size:13px; }
    .woc-new .woc-codes { margin-top:6px; font-family: monospace; font-size:14px; line-height:1.7; }
    .woc-table { width:100%; border-collapse:collapse; }
    .woc-table th, .woc-table td { padding:8px 10px; text-align:left; vertical-align:top; border-bottom:1px solid var(--border, rgba(128,128,128,.25)); font-size:12.5px; }
    .woc-code { font-family: monospace; font-size:13px; }
    .wo-pill.woc-active { background: var(--success-tint); color: var(--success); }
    .wo-pill.woc-redeemed { background: rgba(75,163,242,.15); color: #4ba3f2; }
    .wo-pill.woc-voided { background: var(--danger-tint); color: var(--danger); }
    .wo-pill.woc-expired { background: var(--warning-tint); color: var(--warning); }
    .wo-pill.woc-waiting { background: rgba(128,128,128,.18); color: var(--ink-muted); }
  `;
  document.head.appendChild(st);
})();

function wocCouponRowHtml(c) {
  // Not revealed yet: active with no expiry (old coupons always have an expiry).
  // The server sends a masked code for these.
  const waiting = c.status === 'active' && !c.expires_at;
  const shown = waiting ? 'waiting' : c.status;
  const known = Object.prototype.hasOwnProperty.call(WOC_STATUS_LABEL, shown);
  const cls = known ? shown : 'expired';
  const label = known ? WOC_STATUS_LABEL[shown] : String(c.status || '');
  const codeCell = waiting
    ? `<strong class="woc-code">${woEsc(c.code)}</strong> <button type="button" class="btn btn-ghost btn-sm" data-woc-reveal="${woEsc(c.id)}">Reveal (starts countdown)</button>`
    : `<strong class="woc-code">${woEsc(c.code)}</strong> <button type="button" class="btn btn-ghost btn-sm" data-woc-copy="${woEsc(c.code)}">Copy</button>`;
  const redeemed = c.redeemed_order_id
    ? `Order ${woEsc(woShortId(c.redeemed_order_id))}<div class="wo-muted">${c.redeemed_at ? woEsc(formatDateTime(c.redeemed_at)) : ''}</div>`
    : '<span class="wo-muted">-</span>';
  const action = c.status === 'active'
    ? `<button type="button" class="btn btn-ghost btn-sm" data-woc-void="${woEsc(c.id)}">Void</button>`
    : '';
  return `<tr>
    <td>${codeCell}</td>
    <td>${formatNaira(c.discount_amount_naira)}</td>
    <td><span class="wo-pill woc-${cls}">${woEsc(label)}</span></td>
    <td>${waiting ? '<span class="wo-muted">Starts when revealed (' + woEsc(c.valid_hours || 24) + ' h)</span>' : (c.expires_at ? woEsc(formatDateTime(c.expires_at)) : '')}</td>
    <td>${c.note ? woEsc(c.note) : '<span class="wo-muted">-</span>'}</td>
    <td>${redeemed}</td>
    <td>${action}</td>
  </tr>`;
}

function wocPanelHtml() {
  return `
    <div class="woc-head">
      <strong>Website coupons</strong>
      <span class="wo-muted">Single-use website discount codes and where they were used</span>
      <span class="woc-spacer"></span>
      <button type="button" class="btn btn-ghost btn-sm" data-woc-refresh>Refresh</button>
      <button type="button" class="btn btn-ghost btn-sm" data-woc-toggle>Hide</button>
    </div>
    <div id="woc-body">
      <div class="wo-muted" style="padding:2px 0 12px;">New codes are created on the POS page (Website coupon button, super admin). A new code stays hidden until you press Reveal; revealing it starts its countdown.</div>
      <div class="wo-table-wrap">
        <table class="woc-table">
          <thead><tr><th>Code</th><th>Discount</th><th>Status</th><th>Expires</th><th>Note</th><th>Used on</th><th></th></tr></thead>
          <tbody id="woc-tbody"></tbody>
        </table>
      </div>
      <div id="woc-empty" class="wo-muted" style="display:none; padding:8px 0;">No coupons yet.</div>
    </div>`;
}

function wocRender() {
  const tbody = document.getElementById('woc-tbody');
  if (!tbody) return;
  tbody.innerHTML = wocCoupons.map(wocCouponRowHtml).join('');
  const empty = document.getElementById('woc-empty');
  if (empty) empty.style.display = wocCoupons.length ? 'none' : '';
}

function wocRemovePanel() {
  document.getElementById('wo-coupons-panel')?.remove();
}

async function wocLoad() {
  try {
    const res = await Api.listStorefrontCoupons();
    wocCoupons = Array.isArray(res) ? res : [];
    wocRender();
  } catch (err) {
    if (err && err.status === 403) { wocRemovePanel(); return; }
    showToast((err && err.message) || 'Could not load coupons', true);
  }
}

async function wocCopy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e2) { /* ignore */ }
    ta.remove();
  }
  showToast('Copied');
}

async function wocGenerate(btn) {
  const amount = Number(document.getElementById('woc-amount').value);
  const hours = Number(document.getElementById('woc-hours').value);
  const count = Number(document.getElementById('woc-count').value);
  const note = document.getElementById('woc-note').value.trim();
  if (!(amount > 0)) { showToast('Enter a discount amount in naira', true); return; }
  if (!Number.isInteger(hours) || hours < 1 || hours > 2160) { showToast('Valid-for hours must be a whole number from 1 to 2160', true); return; }
  if (!Number.isInteger(count) || count < 1 || count > 50) { showToast('Number of codes must be a whole number from 1 to 50', true); return; }
  const payload = { discount_amount_naira: amount, expires_in_hours: hours, count };
  if (note) payload.note = note;
  btn.disabled = true;
  try {
    const made = await Api.generateStorefrontCoupons(payload);
    const list = Array.isArray(made) ? made : [];
    const box = document.getElementById('woc-new');
    if (box) {
      box.style.display = '';
      box.innerHTML = `<strong>${list.length} new code${list.length === 1 ? '' : 's'} (${formatNaira(amount)} each)</strong>
        <div class="woc-codes">${list.map((c) => woEsc(c.code)).join('<br>')}</div>
        <button type="button" class="btn btn-ghost btn-sm" style="margin-top:8px;" data-woc-copy-all="${woEsc(list.map((c) => c.code).join('\n'))}">Copy all</button>`;
    }
    showToast('Coupons created');
    await wocLoad();
  } catch (err) {
    showToast((err && err.message) || 'Could not create coupons', true);
  } finally {
    btn.disabled = false;
  }
}

function wocRevealApi(ids) {
  if (typeof Api !== 'undefined' && typeof Api.revealStorefrontCoupons === 'function') {
    return Api.revealStorefrontCoupons(ids);
  }
  return apiRequest('/storefront/coupons/reveal', { method: 'POST', body: { ids } });
}

// Reveal = the server starts the countdown and the list then shows the real code.
async function wocReveal(btn) {
  const id = btn.dataset.wocReveal;
  btn.disabled = true;
  try {
    await wocRevealApi([id]);
    showToast('Code revealed. The countdown has started.');
  } catch (err) {
    btn.disabled = false;
    showToast('Could not reveal the code: ' + ((err && err.message) || 'unknown error'), true);
    return;
  }
  await wocLoad();
}

async function wocVoid(couponId) {
  const c = wocCoupons.find((x) => x.id === couponId);
  const ok = await confirmAction({
    title: 'Void this coupon?',
    sub: c ? c.code : '',
    bodyHtml: 'A voided coupon can no longer be used. This cannot be undone.',
    confirmLabel: 'Void coupon',
  });
  if (!ok) return;
  try {
    await Api.voidStorefrontCoupon(couponId);
    showToast('Coupon voided');
    await wocLoad();
  } catch (err) {
    showToast((err && err.message) || 'Could not void the coupon', true);
  }
}

(function initWebsiteCoupons() {
  const section = document.getElementById('website-orders-section');
  if (!section) return;
  if (document.getElementById('wo-coupons-panel')) return;
  const panel = document.createElement('div');
  panel.id = 'wo-coupons-panel';
  panel.className = 'woc-panel';
  panel.innerHTML = wocPanelHtml();
  section.insertBefore(panel, section.firstChild);

  panel.addEventListener('click', (e) => {
    const gen = e.target.closest('[data-woc-generate]');
    if (gen) { wocGenerate(gen); return; }
    const voidBtn = e.target.closest('[data-woc-void]');
    if (voidBtn) { wocVoid(voidBtn.dataset.wocVoid); return; }
    const rev = e.target.closest('[data-woc-reveal]');
    if (rev) { wocReveal(rev); return; }
    const copy = e.target.closest('[data-woc-copy]');
    if (copy) { wocCopy(copy.dataset.wocCopy); return; }
    const copyAll = e.target.closest('[data-woc-copy-all]');
    if (copyAll) { wocCopy(copyAll.dataset.wocCopyAll); return; }
    if (e.target.closest('[data-woc-refresh]')) { wocLoad(); return; }
    const tog = e.target.closest('[data-woc-toggle]');
    if (tog) {
      const body = document.getElementById('woc-body');
      const hide = body.style.display !== 'none';
      body.style.display = hide ? 'none' : '';
      tog.textContent = hide ? 'Show' : 'Hide';
    }
  });
  wocLoad();
})();
// ---- Website coupons panel (end)

// ---- Website history panels (begin)
// Two read-only history tables built from GET /storefront/orders (which
// already keeps every order, its coupon and the branch each item came from):
//   1. Online purchase history: one row per item bought online, with the
//      branch that supplied it.
//   2. Coupon usage history: one row per order that used a coupon.
// Loaded lazily (first time a panel is opened), 100 orders per page.
const WOH_PAGE = 100;
const woh = { orders: [], seen: {}, offset: 0, done: false, loading: false, started: false };

(function injectWohStyle() {
  if (document.getElementById('woh-style')) return;
  const st = document.createElement('style');
  st.id = 'woh-style';
  st.textContent = `
    .woh-panel { border:1px solid var(--border, rgba(128,128,128,.3)); border-radius:12px; padding:14px; margin-top:16px; }
    .woh-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .woh-head .woh-spacer { flex:1; }
    .woh-filters { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin:12px 0; }
    .woh-filters label { display:flex; flex-direction:column; gap:4px; font-size:11.5px; color: var(--ink-muted); }
    .woh-filters input, .woh-filters select { padding:6px 8px; font-size:13px; min-width:120px; }
    .woh-filters input.woh-q { min-width:220px; }
    .woh-summary { font-size:12.5px; margin-bottom:8px; }
    .woh-table { width:100%; border-collapse:collapse; }
    .woh-table th, .woh-table td { padding:8px 10px; text-align:left; vertical-align:top; border-bottom:1px solid var(--border, rgba(128,128,128,.25)); font-size:12.5px; }
    .woh-num { text-align:right !important; white-space:nowrap; }
    .woh-more { margin-top:10px; display:flex; gap:10px; align-items:center; }
  `;
  document.head.appendChild(st);
})();

// ---- Pure helpers (no DOM access)

function wohDateKey(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function wohItemRows(orders, activeBranch) {
  const rows = [];
  (orders || []).forEach((o) => {
    (o.sales || []).forEach((s) => {
      if (activeBranch && s.branch_id !== activeBranch) return;
      (s.items || []).forEach((it) => rows.push({ order: o, sale: s, item: it }));
    });
  });
  return rows;
}

function wohCouponRows(orders) {
  return (orders || []).filter((o) => o.coupon_code);
}

function wohInRange(order, f) {
  const k = wohDateKey(order.created_at);
  if (f.from && (!k || k < f.from)) return false;
  if (f.to && (!k || k > f.to)) return false;
  return true;
}

function wohFilterItems(rows, f) {
  const q = (f.q || '').trim().toLowerCase();
  return rows.filter((r) => {
    if (f.branch && r.sale.branch_id !== f.branch) return false;
    if (f.status && r.order.status !== f.status) return false;
    if (!wohInRange(r.order, f)) return false;
    if (q) {
      const hay = [r.item.product_name, r.order.customer_name, r.order.customer_phone, woShortId(r.order.id), r.sale.ticket, r.sale.branch_name].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function wohFilterCoupons(orders, f) {
  const q = (f.q || '').trim().toLowerCase();
  return orders.filter((o) => {
    if (!wohInRange(o, f)) return false;
    if (q) {
      const hay = [o.coupon_code, o.customer_name, o.customer_phone, woShortId(o.id)].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function wohStatusPill(status) {
  const st = WO_STATUSES.includes(status) ? status : 'new';
  return `<span class="wo-pill ${st}">${woEsc(WO_STATUS_LABEL[st])}</span>`;
}

function wohItemRowHtml(r) {
  const o = r.order, s = r.sale, it = r.item;
  return `<tr>
    <td>${o.created_at ? woEsc(formatDateTime(o.created_at)) : ''}</td>
    <td><strong>${woEsc(woShortId(o.id))}</strong><div style="margin-top:4px;">${wohStatusPill(o.status)}</div></td>
    <td><strong>${woEsc(o.customer_name)}</strong><div class="wo-muted">${woEsc(o.customer_phone)}</div></td>
    <td>${woEsc(it.product_name)}</td>
    <td class="woh-num">${woEsc(it.quantity)}</td>
    <td class="woh-num">${formatNaira(it.unit_price_naira)}</td>
    <td class="woh-num">${formatNaira(it.line_total_naira)}</td>
    <td><strong>${woEsc(s.branch_name)}</strong></td>
    <td><button type="button" class="btn btn-ghost btn-sm" data-woh-receipt="${woEsc(s.sale_id)}">Receipt ${woEsc(s.ticket)}</button></td>
  </tr>`;
}

function wohCouponRowHtml(o) {
  const sales = (o.sales || []).map((s) =>
    `<div><button type="button" class="btn btn-ghost btn-sm" data-woh-receipt="${woEsc(s.sale_id)}">${woEsc(s.ticket)}</button> ${woEsc(s.branch_name)} <span class="wo-muted">${formatNaira(s.total_naira)}</span></div>`
  ).join('');
  return `<tr>
    <td>${o.created_at ? woEsc(formatDateTime(o.created_at)) : ''}</td>
    <td><strong>${woEsc(o.coupon_code)}</strong></td>
    <td class="woh-num">${formatNaira(o.discount_naira)}</td>
    <td><strong>${woEsc(woShortId(o.id))}</strong><div style="margin-top:4px;">${wohStatusPill(o.status)}</div></td>
    <td><strong>${woEsc(o.customer_name)}</strong><div class="wo-muted">${woEsc(o.customer_phone)}</div></td>
    <td class="woh-num">${formatNaira(o.subtotal_naira)}</td>
    <td class="woh-num">${formatNaira(o.payable_naira)}</td>
    <td>${sales}</td>
  </tr>`;
}

function wohItemSummary(rows) {
  const live = rows.filter((r) => r.order.status !== 'cancelled');
  const units = live.reduce((n, r) => n + Number(r.item.quantity || 0), 0);
  const value = live.reduce((n, r) => n + Number(r.item.line_total_naira || 0), 0);
  return { lines: rows.length, units, value };
}

function wohCouponSummary(list) {
  const live = list.filter((o) => o.status !== 'cancelled');
  return { used: list.length, discount: live.reduce((n, o) => n + Number(o.discount_naira || 0), 0) };
}

// ---- Data + DOM

function wohFilters(prefix) {
  const val = (id) => { const el = document.getElementById(prefix + id); return el ? el.value : ''; };
  return { q: val('-q'), branch: val('-branch'), status: val('-status'), from: val('-from'), to: val('-to') };
}

function wohMoreHtml() {
  const label = woh.done ? 'All orders loaded' : 'Load older orders';
  return `<span class="wo-muted">${woh.orders.length} order${woh.orders.length === 1 ? '' : 's'} loaded${woh.done ? '' : ' (more available)'}</span>` +
    (woh.done ? '' : ` <button type="button" class="btn btn-ghost btn-sm" data-woh-more ${woh.loading ? 'disabled' : ''}>${label}</button>`);
}

function wohRenderPurchases() {
  const tbody = document.getElementById('whp-tbody');
  if (!tbody) return;
  const active = (typeof BranchContext !== 'undefined') ? BranchContext.get() : undefined;
  const all = wohItemRows(woh.orders, active);

  // Branch dropdown from the data, keeping the current choice.
  const sel = document.getElementById('whp-branch');
  if (sel) {
    const keep = sel.value;
    const seen = {};
    all.forEach((r) => { seen[r.sale.branch_id] = r.sale.branch_name; });
    sel.innerHTML = '<option value="">All branches</option>' + Object.keys(seen)
      .sort((a, b) => String(seen[a]).localeCompare(String(seen[b])))
      .map((id) => `<option value="${woEsc(id)}"${id === keep ? ' selected' : ''}>${woEsc(seen[id])}</option>`).join('');
  }

  const rows = wohFilterItems(all, wohFilters('whp'));
  tbody.innerHTML = rows.map(wohItemRowHtml).join('');
  const sm = wohItemSummary(rows);
  document.getElementById('whp-summary').innerHTML =
    `${sm.lines} item line${sm.lines === 1 ? '' : 's'} &middot; <strong>${sm.units}</strong> units &middot; <strong>${formatNaira(sm.value)}</strong> <span class="wo-muted">(units and value exclude cancelled orders)</span>`;
  document.getElementById('whp-empty').style.display = rows.length ? 'none' : '';
  document.getElementById('whp-more').innerHTML = wohMoreHtml();
}

function wohRenderCoupons() {
  const tbody = document.getElementById('whc-tbody');
  if (!tbody) return;
  const list = wohFilterCoupons(wohCouponRows(woh.orders), wohFilters('whc'));
  tbody.innerHTML = list.map(wohCouponRowHtml).join('');
  const sm = wohCouponSummary(list);
  document.getElementById('whc-summary').innerHTML =
    `${sm.used} coupon${sm.used === 1 ? '' : 's'} used &middot; <strong>${formatNaira(sm.discount)}</strong> given in discounts <span class="wo-muted">(excludes cancelled orders)</span>`;
  document.getElementById('whc-empty').style.display = list.length ? 'none' : '';
  document.getElementById('whc-more').innerHTML = wohMoreHtml();
}

function wohRenderAll() { wohRenderPurchases(); wohRenderCoupons(); }

async function wohLoadMore() {
  if (woh.loading || woh.done) return;
  woh.loading = true;
  wohRenderAll();
  try {
    const res = await Api.listStorefrontOrdersPage(WOH_PAGE, woh.offset);
    const page = woNormalise(res);
    let fresh = 0;
    page.forEach((o) => { if (!woh.seen[o.id]) { woh.seen[o.id] = true; woh.orders.push(o); fresh += 1; } });
    woh.offset += page.length;
    // Fewer than a full page, or nothing new (server ignored the offset): no more to fetch.
    if (page.length < WOH_PAGE || fresh === 0) woh.done = true;
  } catch (err) {
    showToast((err && err.message) || 'Could not load order history', true);
  } finally {
    woh.loading = false;
    wohRenderAll();
  }
}

function wohEnsureLoaded() {
  if (woh.started) return;
  woh.started = true;
  wohLoadMore();
}

function wohPanelShell(id, title, desc, filtersHtml, theadHtml, prefix) {
  return `
    <div class="woh-head">
      <strong>${title}</strong>
      <span class="wo-muted">${desc}</span>
      <span class="woh-spacer"></span>
      <button type="button" class="btn btn-ghost btn-sm" data-woh-toggle="${id}">Show</button>
    </div>
    <div id="${id}-body" style="display:none;">
      <div class="woh-filters">${filtersHtml}</div>
      <div id="${prefix}-summary" class="woh-summary"></div>
      <div class="wo-table-wrap"><table class="woh-table"><thead>${theadHtml}</thead><tbody id="${prefix}-tbody"></tbody></table></div>
      <div id="${prefix}-empty" class="wo-muted" style="display:none; padding:8px 0;">Nothing to show for these filters.</div>
      <div id="${prefix}-more" class="woh-more"></div>
    </div>`;
}

(function initWebsiteHistory() {
  const section = document.getElementById('website-orders-section');
  if (!section || document.getElementById('wo-history-purchases')) return;

  const statusOpts = '<option value="">All statuses</option>' + WO_STATUSES.map((s) => `<option value="${s}">${woEsc(WO_STATUS_LABEL[s])}</option>`).join('');

  const purchases = document.createElement('div');
  purchases.id = 'wo-history-purchases';
  purchases.className = 'woh-panel';
  purchases.innerHTML = wohPanelShell('whp', 'Online purchase history',
    'Every item bought on the website and the branch it came from',
    `<label>Search<input type="text" id="whp-q" class="woh-q" placeholder="Product, customer, phone, order no." /></label>
     <label>Branch<select id="whp-branch"><option value="">All branches</option></select></label>
     <label>Status<select id="whp-status">${statusOpts}</select></label>
     <label>From<input type="date" id="whp-from" /></label>
     <label>To<input type="date" id="whp-to" /></label>`,
    '<tr><th>Date</th><th>Order</th><th>Customer</th><th>Product</th><th class="woh-num">Qty</th><th class="woh-num">Unit price</th><th class="woh-num">Line total</th><th>Bought from</th><th>Receipt</th></tr>',
    'whp');

  const coupons = document.createElement('div');
  coupons.id = 'wo-history-coupons';
  coupons.className = 'woh-panel';
  coupons.innerHTML = wohPanelShell('whc', 'Coupon usage history',
    'Every website order that used a coupon, with its sales',
    `<label>Search<input type="text" id="whc-q" class="woh-q" placeholder="Code, customer, phone, order no." /></label>
     <label>From<input type="date" id="whc-from" /></label>
     <label>To<input type="date" id="whc-to" /></label>`,
    '<tr><th>Used on</th><th>Code</th><th class="woh-num">Discount</th><th>Order</th><th>Customer</th><th class="woh-num">Order subtotal</th><th class="woh-num">Payable</th><th>Sales (receipt, branch, amount)</th></tr>',
    'whc');

  section.appendChild(purchases);
  section.appendChild(coupons);

  [purchases, coupons].forEach((panel) => {
    panel.addEventListener('click', (e) => {
      const rec = e.target.closest('[data-woh-receipt]');
      if (rec) { openReceipt(rec.dataset.wohReceipt); return; }
      if (e.target.closest('[data-woh-more]')) { wohLoadMore(); return; }
      const tog = e.target.closest('[data-woh-toggle]');
      if (tog) {
        const body = document.getElementById(tog.dataset.wohToggle + '-body');
        const show = body.style.display === 'none';
        body.style.display = show ? '' : 'none';
        tog.textContent = show ? 'Hide' : 'Show';
        if (show) { wohEnsureLoaded(); wohRenderAll(); }
      }
    });
  });
  purchases.addEventListener('input', wohRenderPurchases);
  purchases.addEventListener('change', wohRenderPurchases);
  coupons.addEventListener('input', wohRenderCoupons);
  coupons.addEventListener('change', wohRenderCoupons);
  window.addEventListener('branch-context-changed', () => { if (woh.started) wohRenderAll(); });
})();
// ---- Website history panels (end)

// ---- Website view switcher (begin)
// Puts a row of buttons at the top of the Website Orders tab so staff pick
// what to look at: Orders, Purchase history, Coupon history, Coupon generator.
// Only the chosen view is shown, and every table is visible straight away
// (no Show/Hide clicks). Works with whichever panels exist on the page.
(function initWebsiteViews() {
  const section = document.getElementById('website-orders-section');
  if (!section || document.getElementById('wo-view-bar')) return;

  (function injectStyle() {
    if (document.getElementById('wov-style')) return;
    const st = document.createElement('style');
    st.id = 'wov-style';
    st.textContent = `
      .wov-bar { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:14px; border-bottom:1px solid var(--border, rgba(128,128,128,.3)); }
      .wov-btn { border:none; background:none; padding:9px 14px; cursor:pointer; font-size:13px; font-weight:600; color: var(--ink-muted); border-bottom:2px solid transparent; margin-bottom:-1px; }
      .wov-btn:hover { color: inherit; }
      .wov-btn.active { color: inherit; border-bottom-color: currentColor; }
    `;
    document.head.appendChild(st);
  })();

  // Remove the old Show/Hide and Hide buttons and open every panel body.
  section.querySelectorAll('[data-woh-toggle], [data-woc-toggle]').forEach((b) => b.remove());
  ['whp-body', 'whc-body', 'woc-body'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = '';
  });
  // Panels sit flush when they are the only thing on screen.
  ['wo-coupons-panel', 'wo-history-purchases', 'wo-history-coupons'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.marginTop = '0';
  });

  // Everything that was on the tab before the new panels = the Orders view.
  const panelIds = ['wo-coupons-panel', 'wo-history-purchases', 'wo-history-coupons'];
  const wrapper = document.createElement('div');
  wrapper.id = 'wo-view-orders';
  Array.from(section.children).forEach((el) => {
    if (panelIds.indexOf(el.id) === -1) wrapper.appendChild(el);
  });
  section.appendChild(wrapper);

  const views = [
    { key: 'orders', label: 'Orders', id: 'wo-view-orders' },
    { key: 'purchases', label: 'Purchase history', id: 'wo-history-purchases' },
    { key: 'couponhist', label: 'Coupon history', id: 'wo-history-coupons' },
    { key: 'coupons', label: 'Generated coupons', id: 'wo-coupons-panel' },
  ];

  const bar = document.createElement('div');
  bar.id = 'wo-view-bar';
  bar.className = 'wov-bar';
  section.insertBefore(bar, section.firstChild);

  let current = 'orders';

  function exists(v) { return !!document.getElementById(v.id); }

  function select(key) {
    let v = views.find((x) => x.key === key);
    if (!v || !exists(v)) { v = views[0]; }
    current = v.key;
    views.forEach((x) => {
      const el = document.getElementById(x.id);
      if (el) el.style.display = (x.key === current) ? '' : 'none';
    });
    bar.querySelectorAll('.wov-btn').forEach((b) => b.classList.toggle('active', b.dataset.wovKey === current));
    if (current === 'purchases' || current === 'couponhist') {
      if (typeof wohEnsureLoaded === 'function') wohEnsureLoaded();
      if (typeof wohRenderAll === 'function') wohRenderAll();
    }
    if (current === 'coupons' && typeof wocLoad === 'function') wocLoad();
  }

  function buildBar() {
    bar.innerHTML = views.filter(exists).map((v) =>
      `<button type="button" class="wov-btn" data-wov-key="${v.key}">${v.label}</button>`).join('');
    bar.querySelectorAll('.wov-btn').forEach((b) => b.classList.toggle('active', b.dataset.wovKey === current));
  }

  bar.addEventListener('click', (e) => {
    const b = e.target.closest('.wov-btn');
    if (b) select(b.dataset.wovKey);
  });

  // The coupon generator removes itself for non-super-admins (the API says
  // 403); drop its button too and fall back to Orders if it was open.
  new MutationObserver(() => {
    const had = bar.querySelectorAll('.wov-btn').length;
    const now = views.filter(exists).length;
    if (had !== now) {
      buildBar();
      if (!exists(views.find((x) => x.key === current))) select('orders');
    }
  }).observe(section, { childList: true });

  buildBar();
  select('orders');
})();
// ---- Website view switcher (end)
