// ---------------------------------------------------------------- Receipt
// Single source of truth for rendering a printable sales receipt. Used by
// both js/pos.js (receipt shown immediately after checkout) and js/sales.js
// (reprinting a past receipt from Sales & Receipts). Deliberately narrow
// and compact -- sized to fit standard 80mm thermal receipt paper without
// overflowing onto a second sheet, matching the store's existing paper
// stock rather than a full A4 page.

const Receipt = (() => {
  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function formatNaira(amount) {
    return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 2 })}`;
  }

  const paymentLabels = {
    cash: 'Cash',
    card: 'Card',
    transfer: 'Transfer',
    // PATCH6E-ONLINE-LABEL: same word as the deposit dropdown in restock-list.js
    online: 'Online',
    split: 'Split payment',
  };

  // `store` carries the branding fields the store can customise (logo,
  // name, email/phone, and the base URL used to build the QR code's
  // "view online invoice" link). All optional -- everything degrades
  // gracefully to plain text if a field is missing.
  function render(container, sale, store = {}) {
    if (!container || !sale) return;

    const created = new Date(sale.created_at);
    const receiptNumber = (sale.receipt_number || sale.id || '').toString();
    const shortId = receiptNumber.slice(0, 8).toUpperCase();
    const items = sale.items || [];

    const logoBlock = store.logoUrl
      ? `<img src="${escapeHtml(store.logoUrl)}" alt="${escapeHtml(store.name || 'Store logo')}" class="receipt-logo-img" />`
      : `<div class="receipt-logo-placeholder">📷 Your logo</div>`;

    const itemsHtml = items.map((it) => `
      <div class="receipt-line-item">
        <div class="receipt-line-item-row">
          <span class="receipt-line-qty">${it.quantity}</span>
          <span class="receipt-line-name">${escapeHtml(it.product_name)}</span>
          <span class="receipt-line-amount">${formatNaira(it.line_total_naira)}</span>
        </div>
        ${it.unit_price_naira != null ? `<div class="receipt-line-unit">${formatNaira(it.unit_price_naira)} / ${escapeHtml(it.unit_label || 'unit')}</div>` : ''}
      </div>`).join('');

    const paymentMethod = paymentLabels[sale.payment_method] || escapeHtml(sale.payment_method || '');
    const isPartPayment = sale.status === 'booking';
    const paidNaira = sale.paid_naira != null ? sale.paid_naira : sale.total_naira;
    const balanceNaira = Math.max(sale.total_naira - paidNaira, 0);
    const payments = sale.payments || [];

    // Itemized payment history -- every deposit/top-up with its own
    // date, time and method (spec: "in their receipts... they should
    // also be able to see how they made the payment based on the
    // days, the time, and the dates"). Falls back to the single
    // payment_method/total line above when there's no per-payment
    // history (an ordinary single-tender sale).
    const paymentHistoryHtml = payments.length ? `
      <div class="receipt-payment-history">
        <div class="receipt-payment-history-title">Payment history</div>
        ${payments.map((p) => {
          const paidAt = p.created_at ? new Date(p.created_at) : null;
          const when = paidAt
            ? `${paidAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${paidAt.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`
            : '';
          return `<div class="receipt-payment-history-entry">
            <div class="receipt-payment-history-row">
              <span>${when}</span>
              <span>${paymentLabels[p.method] || escapeHtml(p.method || '')}</span>
              <span>${formatNaira(p.amount_naira)}</span>
            </div>
            ${p.recorded_by_name ? `<div class="receipt-payment-history-staff">Received by: ${escapeHtml(p.recorded_by_name)}</div>` : ''}
          </div>`;
        }).join('')}
      </div>` : '';

    const storageFeeNote = sale.storage_fee_naira > 0 ? `
      <div class="receipt-balance-note">
        This booking has passed 365 days and has accrued ${formatNaira(sale.storage_fee_naira)} in storage/security fees at ₦100 per day beyond the 1-year mark, added to the balance due.
      </div>` : '';

    // September 2026 change spec section 12: this is the single
    // evolving receipt for the sale -- it must show the full
    // sold -> refunded -> remaining history in place, rather than a
    // separate refund-specific receipt/document ever being generated.
    // Bug fix note: this used to be entirely absent from render(), so
    // re-opening "View" on an already-refunded sale looked identical
    // to a never-refunded one -- no refund shows up here at all.
    const refunds = sale.refunds || [];
    const actionLabels = { cash: 'Cash refund', transfer: 'Bank transfer refund', exchange: 'Swap Item(s)' };
    const totalRefundedNaira = refunds.reduce((sum, r) => sum + (r.amount_naira || 0), 0);
    const refundHistoryHtml = refunds.length ? `
      <div class="receipt-payment-history">
        <div class="receipt-payment-history-title">Refund history</div>
        ${refunds.map((r) => {
          const refundedAt = r.created_at ? new Date(r.created_at) : null;
          const when = refundedAt
            ? `${refundedAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${refundedAt.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`
            : '';
          const label = actionLabels[r.action] || 'Refund';
          const statusSuffix = r.status === 'pending' ? ' (pending — within 24 hours)' : '';
          // Itemized lines for THIS refund: which product(s), how many,
          // and what happened to the stock. Accepts either r.items or
          // r.lines from the API, and falls back to the sale's own item
          // list (by sale_item_id) when a line has no product name.
          const refundLines = r.items || r.lines || [];
          const saleItems = sale.items || [];
          const refundItemsHtml = refundLines.map((it) => {
            const original = saleItems.find((si) => String(si.id) === String(it.sale_item_id || it.id)) || {};
            const name = it.product_name || original.product_name || 'Item';
            const unitPrice = it.unit_price_naira != null ? it.unit_price_naira : original.unit_price_naira;
            const lineTotal = it.line_total_naira != null
              ? it.line_total_naira
              : (unitPrice != null ? unitPrice * it.quantity : null);
            const stockNote = it.spoiled
              ? 'spoiled, written off'
              : ((it.restocked != null ? it.restocked : it.restock) ? 'restocked' : 'not restocked');
            return `<div class="receipt-payment-history-staff" style="display:flex;justify-content:space-between;gap:8px;">
              <span>${it.quantity} × ${escapeHtml(name)} · ${stockNote}</span>
              ${lineTotal != null ? `<span>-${formatNaira(lineTotal)}</span>` : ''}
            </div>`;
          }).join('');
          return `<div class="receipt-payment-history-entry">
            <div class="receipt-payment-history-row">
              <span>${when}</span>
              <span>${escapeHtml(label)}${statusSuffix}</span>
              <span>-${formatNaira(r.amount_naira)}</span>
            </div>
            ${refundItemsHtml ? `<div class="receipt-refund-items" style="margin:4px 0;">${refundItemsHtml}</div>` : ''}
            ${r.reason ? `<div class="receipt-payment-history-staff">Reason: ${escapeHtml(r.reason)}</div>` : ''}
          </div>`;
        }).join('')}
      </div>
      <div class="receipt-totals-row"><span>Total refunded</span><span>-${formatNaira(totalRefundedNaira)}</span></div>
      <div class="receipt-totals-row" style="font-weight:600;"><span>Remaining after refunds</span><span>${formatNaira(Math.max(0, sale.total_naira - totalRefundedNaira))}</span></div>
    ` : '';

    // September 2026 change spec section 9: "when a booked item is
    // swapped for another, the system must record and display the
    // full chain -- original item + price, the swap, and the new
    // item + price/topup/balance." Same evolving-receipt principle as
    // the refund history above -- the swap trail lives on the same
    // receipt, not a separate document, and shows every swap that's
    // ever happened, not just the current item.
    const swapHistory = sale.item_swap_history || [];
    const swapHistoryHtml = swapHistory.length ? `
      <div class="receipt-payment-history">
        <div class="receipt-payment-history-title">Item change history</div>
        ${swapHistory.map((s) => {
          const changedAt = s.changed_at ? new Date(s.changed_at) : null;
          const when = changedAt
            ? `${changedAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${changedAt.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`
            : '';
          return `<div class="receipt-payment-history-entry">
            <div class="receipt-payment-history-row">
              <span>${when}</span>
              <span>${escapeHtml(s.from_product_name || 'item')} (×${s.from_quantity}, ${formatNaira(s.from_unit_price_naira)}) → ${escapeHtml(s.to_product_name || 'item')} (×${s.to_quantity}, ${formatNaira(s.to_unit_price_naira)})</span>
              <span></span>
            </div>
            <div class="receipt-payment-history-staff">
              Balance: ${formatNaira(s.previous_balance_naira)} → ${formatNaira(s.new_balance_naira)}
              ${s.refund_naira ? ` — ${formatNaira(s.refund_naira)} refunded (${s.refund_method === 'cash' ? 'cash' : 'bank transfer'})` : ''}
              ${s.changed_by_name ? ` — by ${escapeHtml(s.changed_by_name)}` : ''}
            </div>
          </div>`;
        }).join('')}
      </div>
    ` : '';

    // Change due -- the customer tendered more than the total (e.g.
    // paid ₦500 cash for a ₦250 item). Derived straight from what was
    // actually collected vs. the total, same on a fresh receipt or a
    // later reprint, no separate field to keep in sync.
    const changeDueNaira = !isPartPayment ? Math.max(paidNaira - sale.total_naira, 0) : 0;

    // Change owed that couldn't be handed over on the spot (platform
    // update spec section 2.5) -- SaleOut.pending_changes is the list
    // of status rows (see PendingChangeSummary) embedded specifically
    // so the receipt can show whether each is still open or already
    // resolved, without a second round-trip. Re-rendering this receipt
    // later (reprint from Sales & Receipts) reflects whatever the live
    // status is at that point, since it always comes straight off the
    // sale. A sale can carry more than one row -- change owed can be
    // split across settlement methods (part cash, part airtime, etc.)
    // -- so every row gets its own line, each independently settled.
    const pendingChanges = sale.pending_changes || [];
    const changeMethodLabels = { cash: 'cash', transfer: 'bank transfer', airtime: 'airtime' };
    const changeStatusHtmlOld = pendingChanges.map((pendingChange) => `
      <div class="receipt-balance-note" style="${pendingChange.status === 'settled' ? '' : 'font-weight:600;'}">
        ${pendingChange.status === 'settled'
          ? `Change of ${formatNaira(pendingChange.amount_naira)} settled${pendingChange.settlement_method ? ` via ${changeMethodLabels[pendingChange.settlement_method] || pendingChange.settlement_method}` : ''}.`
          : `Change pending — ${formatNaira(pendingChange.amount_naira)} not yet given${pendingChange.settlement_method ? ` (${changeMethodLabels[pendingChange.settlement_method] || pendingChange.settlement_method}${pendingChange.status === 'pending' ? ', awaiting approval' : ''})` : ''}${pendingChange.reference_code ? ` — ref ${escapeHtml(pendingChange.reference_code)}` : ''}.`}
      </div>`).join('');

    // PATCH3B3-RECEIPT-CHANGE-WORDS (replaces PATCH3B2; words follow the live status)
    const changeStatusHtml = pendingChanges.map((pc) => {
      const amount = formatNaira(pc.amount_naira);
      const method = pc.settlement_method;
      const isOpen = pc.status !== 'settled';
      let words;
      if (method === 'airtime' || method === 'transfer') {
        const name = method === 'airtime' ? 'Airtime' : 'Bank transfer';
        if (pc.status === 'pending') words = name + ' - being processed, awaiting approval';
        else if (pc.status === 'settled') words = name + ' - approved';
        else words = 'Balance we owe you';
      } else if (pc.status === 'settled') {
        if (method === 'cash') words = 'Balance - collected, completed';
        else if (method === 'coupon') words = 'Balance - turned into a discount code, used, completed';
        else words = 'Balance - completed';
      } else {
        words = 'Balance we owe you';
      }
      const ref = pc.reference_code ? escapeHtml(pc.reference_code) : '';
      const refLine = ref
        ? '<div class="receipt-balance-note">Ref ' + ref + (isOpen ? ' - show this code when you come back.' : '') + '</div>'
        : '';
      return '<div class="receipt-totals-row"' + (isOpen ? ' style="font-weight:600;"' : '') + '><span>' + words + '</span><span>' + amount + '</span></div>' + refLine;
    }).join('');

    const totalsHtml = `
      ${sale.discount_naira ? `<div class="receipt-totals-row"><span>Discount</span><span>-${formatNaira(sale.discount_naira)}</span></div>` : ''}
      <div class="receipt-totals-row receipt-grand-total"><span>${isPartPayment ? 'Item total' : 'Total'}</span><span>${formatNaira(sale.total_naira)}</span></div>
      <div class="receipt-totals-row"><span>${isPartPayment ? 'Deposit paid' : (paymentMethod || 'Payment')}</span><span>${formatNaira(paidNaira)}</span></div>
      ${(changeDueNaira > 0.5 && !(sale.pending_changes || []).length) ? `<div class="receipt-totals-row" style="font-weight:600;"><span>Change due</span><span>${formatNaira(changeDueNaira)}</span></div>` : ''}
      ${changeStatusHtml}
      ${isPartPayment ? `
        <div class="receipt-totals-row"><span>${paymentMethod || 'Payment method'}</span><span></span></div>
        ${sale.storage_fee_naira > 0 ? `<div class="receipt-totals-row"><span>Storage/security fee (365+ days, ₦100/day)</span><span>${formatNaira(sale.storage_fee_naira)}</span></div>` : ''}
        <div class="receipt-totals-row" style="font-weight:600;"><span>Balance due</span><span>${formatNaira(balanceNaira + (sale.storage_fee_naira || 0))}</span></div>
        <div class="receipt-balance-note">Part payment — booking, not a completed sale. Balance due on pickup. Bookings held beyond 365 days accrue a flat ₦100 per day storage/security fee on the item.</div>
        ${storageFeeNote}` : ''}
      ${paymentHistoryHtml}
      ${swapHistoryHtml}
      ${refundHistoryHtml}`;

    // The QR code always points at the store's own online shop (a
    // fixed setting) -- never at a per-sale invoice link. It only
    // appears at all if a super admin has actually set a website URL.
    const websiteUrl = store.websiteUrl || null;

    container.innerHTML = `
      <div class="receipt-logo">${logoBlock}</div>
      <div class="receipt-ticket-meta">
        <div style="font-weight:700;font-size:1.15em;">${escapeHtml(store.name || 'Nick Cosmetics')}</div>
        ${branchHtml(sale.branch_id)}
        <div>Ticket ${escapeHtml(shortId)}</div>
        <div>${created.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${created.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}</div>
        <div>Served by: ${escapeHtml((sale.cashier_name || 'Unknown').toUpperCase())}</div>
        ${sale.customer_name ? `<div>Customer: ${escapeHtml(sale.customer_name)}${sale.customer_phone ? ` (${escapeHtml(sale.customer_phone)})` : ''}</div>` : ''}
      </div>
      ${sale.channel === 'transfer' ? `<div class="receipt-ticket-meta" style="margin-top:6px;"><div style="font-weight:700;">BRANCH TRANSFER</div>${sale.notes ? `<div>${escapeHtml(sale.notes)}</div>` : ''}</div>` : ''}
      <div class="receipt-items">${itemsHtml}</div>
      <div class="receipt-totals">${totalsHtml}</div>
      ${websiteUrl ? `
        <div class="receipt-qr-block">
          <div class="receipt-qr" id="receipt-qr-${escapeHtml(sale.id)}"></div>
          <div class="receipt-qr-caption">
            ${store.websiteQrCaption ? `<div>${escapeHtml(store.websiteQrCaption)}</div>` : ''}
          </div>
        </div>` : ''}
      <div class="receipt-store-footer">
        ${branchSupportHtml(sale.branch_id)}<div class="receipt-thank-you">Thank you for shopping with us. We look forward to serving you again.</div><div class="receipt-returns-note" style="font-size:0.85em;opacity:0.8;margin-top:4px;">Please keep this receipt for returns or exchanges.</div>
      </div>
      <div class="receipt-powered-by">Powered by Nick Cosmetics</div>`;

    // QR is a "nice to have" -- only draws if the qrcodejs CDN script
    // loaded successfully (see the <script> tag in the host page). If
    // it's unavailable (offline, blocked CDN), the caption text above
    // still shows, just without the scannable code.
    if (websiteUrl && window.QRCode) {
      const qrEl = document.getElementById(`receipt-qr-${sale.id}`);
      if (qrEl) {
        try {
          new window.QRCode(qrEl, { text: websiteUrl, width: 76, height: 76, correctLevel: window.QRCode.CorrectLevel.M });
        } catch (err) {
          qrEl.remove();
        }
      }
    }
  }

  // PATCH-RECEIPT-BRANCH: branch name and address on every receipt type.
  let branchCache = null;
  let branchLoading = null;
  function loadBranchCache() {
    if (branchCache) return Promise.resolve(branchCache);
    if (branchLoading) return branchLoading;
    if (typeof Api === "undefined" || typeof Auth === "undefined" || !Auth.getToken()) return Promise.resolve(null);
    branchLoading = Api.listBranches().then((rows) => {
      const map = {};
      (rows || []).forEach((b) => { map[b.id] = b; });
      branchCache = map;
      return map;
    }).catch(() => null).finally(() => { branchLoading = null; });
    return branchLoading;
  }
  function branchHtml(branchId, fallbackName) {
    let name = fallbackName || "";
    let address = "";
    let support = "";
    const b = (branchCache && branchId) ? branchCache[branchId] : null;
    if (b) {
      name = b.name || name;
      address = b.address || "";
      support = b.support_line || "";
    } else if (!name && branchId && typeof BranchContext !== "undefined" && BranchContext.get() === branchId) {
      name = BranchContext.getName();
    }
    if (!name && !address && !support) return "";
    return (name ? `<div style="font-weight:600;">Branch: ${escapeHtml(name)}</div>` : "") + (address ? `<div>${escapeHtml(address)}</div>` : "");
  }
  function branchSupportHtml(branchId) {
    const b = (branchCache && branchId) ? branchCache[branchId] : null;
    const s = b ? (b.support_line || "") : "";
    return s ? `<div>Support line: ${escapeHtml(s)}</div>` : "";
  }
  function withBranch(fn) {
    return function (container, ...args) {
      fn(container, ...args);
      if (branchCache || !container) return;
      const tok = String(Date.now()) + "-" + Math.random();
      container.dataset.rcptTok = tok;
      loadBranchCache().then((map) => {
        if (map && container.dataset.rcptTok === tok) fn(container, ...args);
      });
    };
  }
  try { loadBranchCache(); } catch (err) { /* non-fatal */ }

  function renderMovement(container, mv, store = {}) {
    // fix55: stock movement receipt, same look as the sale receipt
    if (!container || !mv) return;
    const created = new Date(mv.created_at);
    const logoBlock = store.logoUrl
      ? `<img src="${escapeHtml(store.logoUrl)}" alt="${escapeHtml(store.name || 'Store logo')}" class="receipt-logo-img" />`
      : `<div class="receipt-logo-placeholder">\uD83D\uDCF7 Your logo</div>`;
    const qty = Number(mv.quantity_pieces || 0);
    const dateText = created.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })
      + ', ' + created.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' });
    container.innerHTML = `
      <div class="receipt-logo">${logoBlock}</div>
      <div class="receipt-ticket-meta">
        <div style="font-weight:700;font-size:1.15em;">${escapeHtml(store.name || 'Nick Cosmetics')}</div>
        ${branchHtml(mv.branch_id)}
        <div style="font-weight:700;">STOCK MOVEMENT RECEIPT</div>
        <div>Ref ${escapeHtml(mv.receipt_no)}</div>
        <div>${escapeHtml(dateText)}</div>
        <div>Done by: ${escapeHtml(String(mv.created_by_name || 'Unknown').toUpperCase())}</div>
      </div>
      <div class="receipt-items">
        <div class="receipt-line-item">
          <div class="receipt-line-item-row">
            <span class="receipt-line-qty">${qty}</span>
            <span class="receipt-line-name">${escapeHtml(mv.product_name || 'Unknown item')}</span>
            <span class="receipt-line-amount"></span>
          </div>
          <div class="receipt-line-unit">${escapeHtml(mv.route_text || '')}</div>
        </div>
      </div>
      <div class="receipt-totals">
        <div class="receipt-totals-row receipt-grand-total"><span>Quantity moved</span><span>${qty} pcs</span></div>
        <div class="receipt-totals-row"><span>Type</span><span>${escapeHtml(mv.type_label || '')}</span></div>
        ${mv.notes ? `<div class="receipt-totals-row"><span>Note</span><span></span></div><div class="receipt-line-unit" style="white-space:normal;">${escapeHtml(mv.notes)}</div>` : ''}
        <div class="receipt-balance-note">Signature: ____________________</div>
      </div>
      <div class="receipt-store-footer">
        ${branchSupportHtml(mv.branch_id)}<div class="receipt-thank-you">Stock movement record. Keep for stock control.</div>
      </div>
      <div class="receipt-powered-by">Powered by Nick Cosmetics</div>`;
  }

  function renderReturn(container, rt, store = {}) {
    // RET6: branch return receipt, same look as the stock movement receipt
    if (!container || !rt) return;
    const created = new Date(rt.created_at);
    const logoBlock = store.logoUrl
      ? `<img src="${escapeHtml(store.logoUrl)}" alt="${escapeHtml(store.name || 'Store logo')}" class="receipt-logo-img" />`
      : `<div class="receipt-logo-placeholder">\uD83D\uDCF7 Your logo</div>`;
    const qty = Number(rt.quantity_pieces || 0);
    const dateText = created.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })
      + ', ' + created.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' });
    const heading = {
      requested: 'BRANCH RETURN REQUEST',
      accepted: 'BRANCH RETURN REQUEST',
      completed: 'BRANCH RETURN RECEIPT',
      declined: 'BRANCH RETURN - DECLINED',
      cancelled: 'BRANCH RETURN - CANCELLED',
    }[rt.status] || 'BRANCH RETURN';
    const isCompleted = rt.status === 'completed';
    const paidBy = rt.payment_method === 'cash' ? 'Cash' : (rt.payment_method ? 'Bank transfer' : '-');
    const acct = [rt.refund_bank_name, rt.refund_account_number, rt.refund_account_name]
      .filter(Boolean).map((x) => escapeHtml(String(x))).join(', ');
    const paidToRow = rt.payment_method === 'transfer'
      ? `<div class="receipt-totals-row"><span>Paid to</span><span>${acct || 'No account details were given.'}</span></div>`
      : '';
    container.innerHTML = `
      <div class="receipt-logo">${logoBlock}</div>
      <div class="receipt-ticket-meta">
        <div style="font-weight:700;font-size:1.15em;">${escapeHtml(store.name || 'Nick Cosmetics')}</div>
        ${branchHtml(rt.branch_id)}
        <div style="font-weight:700;">${heading}</div>
        <div>Ref ${escapeHtml(rt.ref_no || '')}</div>
        <div>${escapeHtml(dateText)}</div>
        <div>Done by: ${escapeHtml(String(rt.done_by || 'Unknown').toUpperCase())}</div>
      </div>
      <div class="receipt-items">
        <div class="receipt-line-item">
          <div class="receipt-line-item-row">
            <span class="receipt-line-qty">${qty}</span>
            <span class="receipt-line-name">${escapeHtml(rt.product_name || 'Unknown item')}</span>
            <span class="receipt-line-amount"></span>
          </div>
        </div>
      </div>
      <div class="receipt-totals">
        <div class="receipt-totals-row"><span>Route</span><span>${escapeHtml(rt.route_text || '')}</span></div>
        <div class="receipt-totals-row"><span>Price per piece</span><span>${formatNaira(rt.unit_price_naira)}</span></div>
        <div class="receipt-totals-row receipt-grand-total"><span>${isCompleted ? 'Money paid back' : 'Money to pay back'}</span><span>${formatNaira(rt.amount_naira)}</span></div>
        <div class="receipt-totals-row"><span>Status</span><span>${escapeHtml(rt.status_text || '')}</span></div>
        <div class="receipt-totals-row"><span>Paid by</span><span>${paidBy}</span></div>
        ${paidToRow}
        <div class="receipt-balance-note">Signature: ____________________</div>
      </div>
      <div class="receipt-store-footer">
        ${branchSupportHtml(rt.branch_id)}<div class="receipt-thank-you">Branch return record. No new sale and no new invoice.</div>
      </div>
      <div class="receipt-powered-by">Powered by Nick Cosmetics</div>`;
  }

  function print() {
    window.print();
  }

  // Printable receipt for a Credit List entry -- opening a new credit,
  // a part-payment against one, or the final "fully paid" receipt.
  // Reuses the same visual language as a normal sale receipt (logo,
  // ticket meta, store footer) but a credit entry isn't a Sale row,
  // so it gets its own lightweight layout rather than being coerced
  // into `render()`'s sale-shaped input.
  function renderCredit(container, creditEntry, store = {}, context = 'open') {
    if (!container || !creditEntry) return;
    const c = creditEntry;
    const opened = new Date(c.opened_at);
    const shortId = (c.id || '').toString().slice(0, 8).toUpperCase();

    const logoBlock = store.logoUrl
      ? `<img src="${escapeHtml(store.logoUrl)}" alt="${escapeHtml(store.name || 'Store logo')}" class="receipt-logo-img" />`
      : `<div class="receipt-logo-placeholder">📷 Your logo</div>`;

    const heading = {
      open: 'OPEN CREDIT RECEIPT',
      payment: 'CREDIT PAYMENT RECEIPT',
      completed: 'CREDIT — FULLY PAID',
    }[context] || 'CREDIT RECEIPT';

    const itemLine = c.product_name
      ? `<div class="receipt-line-item"><div class="receipt-line-item-row">
          <span class="receipt-line-qty">${c.quantity || 1}</span>
          <span class="receipt-line-name">${escapeHtml(c.product_name)}</span>
          <span class="receipt-line-amount">${formatNaira(c.amount_naira)}</span>
        </div></div>`
      : (c.description ? `<div class="receipt-line-item">${escapeHtml(c.description)}</div>` : '');

    const paymentHistoryHtml = (c.payments || []).length ? `
      <div class="receipt-payment-history">
        <div class="receipt-payment-history-title">Payment history</div>
        ${c.payments.map((p) => {
          const paidAt = p.paid_at ? new Date(p.paid_at) : null;
          const when = paidAt
            ? `${paidAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${paidAt.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`
            : '';
          return `<div class="receipt-payment-history-entry">
            <div class="receipt-payment-history-row">
              <span>${when}</span>
              <span>${paymentLabels[p.method] || escapeHtml(p.method || '')}</span>
              <span>${formatNaira(p.amount_naira)}</span>
            </div>
            ${p.received_by_name ? `<div class="receipt-payment-history-staff">Received by: ${escapeHtml(p.received_by_name)}</div>` : ''}
          </div>`;
        }).join('')}
      </div>` : '';

    const balance = Math.max((c.amount_naira || 0) - (c.amount_paid_naira || 0), 0);

    container.innerHTML = `
      <div class="receipt-logo">${logoBlock}</div>
      <div class="receipt-ticket-meta">
        <div style="font-weight:700;font-size:1.15em;">${escapeHtml(store.name || 'Nick Cosmetics')}</div>
        <div style="font-weight:700;">${heading}</div>
        ${branchHtml(c.branch_id, c.branch_name)}
        <div>Ticket ${escapeHtml(shortId)}</div>
        <div>${opened.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${opened.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}</div>
        <div>Customer: ${escapeHtml(c.customer_full_name)} (${escapeHtml(c.customer_phone)})</div>
        <div>Opened by: ${escapeHtml((c.opened_by_name || 'Unknown').toUpperCase())}</div>
      </div>
      <div class="receipt-items">${itemLine}</div>
      <div class="receipt-totals">
        <div class="receipt-totals-row receipt-grand-total"><span>Amount owed</span><span>${formatNaira(c.amount_naira)}</span></div>
        <div class="receipt-totals-row"><span>Paid so far</span><span>${formatNaira(c.amount_paid_naira || 0)}</span></div>
        <div class="receipt-totals-row" style="font-weight:600;"><span>Balance</span><span>${formatNaira(balance)}</span></div>
        ${paymentHistoryHtml}
      </div>
      <div class="receipt-store-footer">
        ${branchSupportHtml(c.branch_id)}<div class="receipt-thank-you">Thank you for shopping with us. We look forward to serving you again.</div>
      </div>
      <div class="receipt-powered-by">Powered by Nick Cosmetics</div>`;
  }

  // Item 6: POS-opened Open Credit against a multi-item cart creates
  // one Credit record per line (see js/pos.js openPosCredit -- the
  // backend Credit entity is still single-product per record), but
  // the customer should get one physical ticket for the whole
  // transaction, not one per item. Takes the array of created Credit
  // records (all sharing the same customer/branch/opened_by) plus the
  // combined amount actually paid now, and renders it as a single
  // multi-line receipt using the same visual language as renderCredit
  // above.
  function renderCreditBatch(container, credits, amountPaidNaira, store = {}) {
    if (!container || !credits || !credits.length) return;
    const first = credits[0];
    const opened = new Date(first.opened_at);
    const shortId = (first.id || '').toString().slice(0, 8).toUpperCase();
    const totalOwed = credits.reduce((sum, c) => sum + (c.amount_naira || 0), 0);
    const balance = Math.max(totalOwed - (amountPaidNaira || 0), 0);

    const logoBlock = store.logoUrl
      ? `<img src="${escapeHtml(store.logoUrl)}" alt="${escapeHtml(store.name || 'Store logo')}" class="receipt-logo-img" />`
      : `<div class="receipt-logo-placeholder">📷 Your logo</div>`;

    const itemsHtml = credits.map((c) => `
      <div class="receipt-line-item"><div class="receipt-line-item-row">
        <span class="receipt-line-qty">${c.quantity || 1}</span>
        <span class="receipt-line-name">${escapeHtml(c.product_name || '')}</span>
        <span class="receipt-line-amount">${formatNaira(c.amount_naira)}</span>
      </div></div>`).join('');

    container.innerHTML = `
      <div class="receipt-logo">${logoBlock}</div>
      <div class="receipt-ticket-meta">
        <div style="font-weight:700;font-size:1.15em;">${escapeHtml(store.name || 'Nick Cosmetics')}</div>
        <div style="font-weight:700;">OPEN CREDIT RECEIPT</div>
        ${branchHtml(first.branch_id, first.branch_name)}
        <div>Ticket ${escapeHtml(shortId)}</div>
        <div>${opened.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${opened.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}</div>
        <div>Customer: ${escapeHtml(first.customer_full_name)} (${escapeHtml(first.customer_phone)})</div>
        <div>Opened by: ${escapeHtml((first.opened_by_name || 'Unknown').toUpperCase())}</div>
      </div>
      <div class="receipt-items">${itemsHtml}</div>
      <div class="receipt-totals">
        <div class="receipt-totals-row receipt-grand-total"><span>Amount owed</span><span>${formatNaira(totalOwed)}</span></div>
        <div class="receipt-totals-row"><span>Paid now</span><span>${formatNaira(amountPaidNaira || 0)}</span></div>
        <div class="receipt-totals-row" style="font-weight:600;"><span>Balance</span><span>${formatNaira(balance)}</span></div>
      </div>
      <div class="receipt-store-footer">
        ${branchSupportHtml(first.branch_id)}<div class="receipt-thank-you">Thank you for shopping with us. We look forward to serving you again.</div>
      </div>
      <div class="receipt-powered-by">Powered by Nick Cosmetics</div>`;
  }

  // Printable refund receipt (Sales & Receipt → Refund → Details).
  // Pulls entirely from the actual refund/sale record returned by
  // GET /sales/refunds/{id} -- itemized lines, customer, cashier,
  // original sale reference -- same visual language as a normal
  // sale receipt rather than a disconnected plain-text summary.
  function renderRefund(container, refund, store = {}) {
    if (!container || !refund) return;
    const processedAt = new Date(refund.created_at);
    const originalSaleAt = new Date(refund.original_sale_at);
    const shortRefundId = (refund.id || '').toString().slice(0, 8).toUpperCase();
    const shortSaleId = (refund.receipt_number || refund.sale_id || '').toString().slice(0, 8).toUpperCase();
    const items = refund.items || [];

    const logoBlock = store.logoUrl
      ? `<img src="${escapeHtml(store.logoUrl)}" alt="${escapeHtml(store.name || 'Store logo')}" class="receipt-logo-img" />`
      : `<div class="receipt-logo-placeholder">📷 Your logo</div>`;

    const itemsHtml = items.length ? items.map((it) => `
      <div class="receipt-line-item">
        <div class="receipt-line-item-row">
          <span class="receipt-line-qty">${it.quantity}</span>
          <span class="receipt-line-name">${escapeHtml(it.product_name || 'Item')}</span>
          <span class="receipt-line-amount">${formatNaira(it.line_total_naira)}</span>
        </div>
        <div class="receipt-line-unit">${formatNaira(it.unit_price_naira)} / ${escapeHtml(it.unit_label || 'unit')}${it.spoiled ? ' · spoiled, written off' : (it.restocked ? ' · restocked' : ' · not restocked')}</div>
      </div>`).join('')
      // Older refunds processed before itemized lines were recorded
      // won't have any -- fall back to the aggregate amount rather
      // than showing nothing.
      : `<div class="receipt-line-item">${escapeHtml(refund.reason || 'Refunded item(s)')}</div>`;

    const actionLabels = { cash: 'Cash refund', transfer: 'Bank transfer refund', exchange: 'Swap Item(s)' };
    const actionLabel = actionLabels[refund.action] || 'Cash refund';
    const settlementLabels = {
      store_paid_customer: 'Store paid customer the difference',
      customer_topped_up: 'Customer topped up the difference',
      even: 'Even swap, no balance due',
    };
    const bankDetailsHtml = (refund.bank_name || refund.account_number) ? `
        <div class="receipt-totals-row"><span>Bank</span><span>${escapeHtml(refund.bank_name || '')}</span></div>
        <div class="receipt-totals-row"><span>Account number</span><span>${escapeHtml(refund.account_number || '')}</span></div>
        <div class="receipt-totals-row"><span>Account name</span><span>${escapeHtml(refund.account_name || '')}</span></div>` : '';
    const statusHtml = refund.status ? `
        <div class="receipt-totals-row"><span>Status</span><span>${refund.status === 'pending' ? 'Pending — processed within 24 hours' : 'Completed'}</span></div>` : '';

    container.innerHTML = `
      <div class="receipt-logo">${logoBlock}</div>
      <div class="receipt-ticket-meta">
        <div style="font-weight:700;font-size:1.15em;">${escapeHtml(store.name || 'Nick Cosmetics')}</div>
        <div style="font-weight:700;">REFUND RECEIPT</div>
        ${branchHtml(refund.branch_id, refund.branch_name)}
        <div>Refund ticket ${escapeHtml(shortRefundId)}</div>
        <div>Original sale ${escapeHtml(shortSaleId)}, sold ${originalSaleAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })} by ${escapeHtml((refund.original_cashier_name || 'unknown').toUpperCase())}</div>
        <div>${processedAt.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${processedAt.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}</div>
        <div>Processed by: ${escapeHtml((refund.processed_by_name || 'Unknown').toUpperCase())}</div>
        ${refund.customer_name ? `<div>Customer: ${escapeHtml(refund.customer_name)}${refund.customer_phone ? ` (${escapeHtml(refund.customer_phone)})` : ''}</div>` : ''}
      </div>
      <div class="receipt-items">${itemsHtml}</div>
      <div class="receipt-totals">
        <div class="receipt-totals-row receipt-grand-total"><span>Refunded</span><span>${formatNaira(refund.amount_naira)}</span></div>
        <div class="receipt-totals-row"><span>Type</span><span>${escapeHtml(actionLabel)}</span></div>
        ${refund.original_payment_method ? `<div class="receipt-totals-row"><span>Original payment method</span><span>${escapeHtml(paymentLabels[refund.original_payment_method] || refund.original_payment_method)}</span></div>` : ''}
        ${refund.settlement ? `<div class="receipt-totals-row"><span>${escapeHtml(settlementLabels[refund.settlement] || refund.settlement)}</span><span>${refund.settlement_amount_naira != null ? formatNaira(refund.settlement_amount_naira) : ''}</span></div>` : ''}
        ${bankDetailsHtml}
        ${statusHtml}
        <div class="receipt-totals-row"><span>Reason</span><span></span></div>
        <div class="receipt-line-unit" style="white-space:normal;">${escapeHtml(refund.reason || '—')}</div>
      </div>
      ${refund.image_url ? `<div class="receipt-payment-history"><div class="receipt-payment-history-title">Photo of returned item</div><img src="${escapeHtml(refund.image_url)}" alt="Returned item" style="max-width:100%;border-radius:8px;margin-top:6px;" /></div>` : ''}
      <div class="receipt-store-footer">
        ${branchSupportHtml(refund.branch_id)}<div class="receipt-thank-you">Thank you for shopping with us. We look forward to serving you again.</div>
      </div>
      <div class="receipt-powered-by">Powered by Nick Cosmetics</div>`;
  }

  return { renderMovement: withBranch(renderMovement), renderReturn: withBranch(renderReturn), render: withBranch(render), renderCredit: withBranch(renderCredit), renderCreditBatch: withBranch(renderCreditBatch), renderRefund: withBranch(renderRefund), print, formatNaira, escapeHtml };
})();

// ------------------------------------------------------------ Store branding
// What appears on the printed receipt (logo, store name, contact, and
// the online-store URL + caption used to build the receipt QR code --
// this always points at the store's own website, never at a per-sale
// invoice link).
// Backed by the /settings/store API (Store Settings tab under My
// Profile is where a super admin edits it, since platform update spec
// section 6 folded the old standalone settings.html page in there) --
// localStorage is kept only as a same-device cache so `current()` can
// stay synchronous and receipts still print fine offline/before the
// first refresh() completes.
const StoreBranding = (() => {
  const KEY = 'nickcosmetics_receipt_branding';

  function current() {
    try {
      const stored = JSON.parse(localStorage.getItem(KEY) || '{}');
      return {
        name: stored.name || 'Nick Cosmetics',
        email: stored.email || '',
        logoUrl: stored.logoUrl || '',
        websiteUrl: stored.websiteUrl || '',
        websiteQrCaption: stored.websiteQrCaption || '',
      };
    } catch (err) {
      return { name: 'Nick Cosmetics', email: '', logoUrl: '', websiteUrl: '', websiteQrCaption: '' };
    }
  }

  function save(fields) {
    localStorage.setItem(KEY, JSON.stringify({ ...current(), ...fields }));
  }

  // Pulls the latest branding from the backend and refreshes the
  // local cache. Safe to call on every admin page load -- if it's
  // offline or the call fails, the existing cached/default values in
  // current() are left untouched.
  async function refresh() {
    if (typeof Api === 'undefined') return current();
    try {
      const settings = await Api.getStoreSettings();
      save({
        name: settings.store_name,
        email: settings.store_email || '',
        logoUrl: settings.logo_url || '',
        websiteUrl: settings.website_url || '',
        websiteQrCaption: settings.website_qr_caption || '',
      });
    } catch (err) {
      // Keep whatever was already cached.
    }
    return current();
  }

  return { current, save, refresh };
})();
// PATCH3E-SWAP-LABELS
