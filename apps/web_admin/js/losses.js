// Losses tab, folded into Transactions (platform update spec section
// 6 -- "Pending Change and Losses should be folded into the
// Transactions menu, not standalone nav items"). Combines
// StockAdjustment and SaleLoss server-side (see GET
// /inventory/losses/history) so this tab doesn't need to know which
// table a given row came from.
//
// currentUser, roleLabels, toastTimer, showToast, escapeHtml,
// formatNaira are already declared by js/sales.js, which loads first
// on this shared page -- reused here rather than redeclared (let/
// const can't be redeclared in the same scope, unlike a plain
// function). Auth.requireAuth()/Perms.applyNav() are harmless to
// repeat (sales.js already ran them); Perms.guardPage('view_all_reports')
// is deliberately NOT called here the way the old standalone page did
// -- that would redirect every staff member away from the whole
// Transactions page. Only the Losses tab button itself is hidden for
// them (see the Perms.can check right after the tab bar in
// sales.html).
Auth.requireAuth();
Perms.applyNav();

function formatDateTime(iso) {
  const d = new Date(iso);
  return `${d.toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}, ${d.toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`;
}

// ----------------------------------------------------------- Lightbox

const imageLightboxBackdrop = document.getElementById('image-lightbox-backdrop');
const imageLightboxImg = document.getElementById('image-lightbox-img');
function openImageLightbox(url) {
  imageLightboxImg.src = url;
  imageLightboxBackdrop.classList.add('visible');
}
function closeImageLightbox() {
  imageLightboxBackdrop.classList.remove('visible');
  imageLightboxImg.src = '';
}
document.getElementById('image-lightbox-close').addEventListener('click', closeImageLightbox);
imageLightboxBackdrop.addEventListener('click', (e) => { if (e.target === imageLightboxBackdrop) closeImageLightbox(); });
document.getElementById('losses-tbody').addEventListener('click', (e) => {
  const img = e.target.closest('[data-enlarge-image]');
  if (img) openImageLightbox(img.dataset.enlargeImage);
});

// -------------------------------------------------------------- Filters

const categoryLabels = {
  damaged: 'Damaged', expired: 'Expired', theft: 'Stolen', missing: 'Missing',
  defective: 'Defective', supplier_return: 'Supplier return', recount: 'Recount',
  other: 'Other',
};

let activeLossTab = 'all'; // 'all' | 'expired'
let lossRowsCache = [];

function currentFilters() {
  const monthValue = document.getElementById('losses-month').value; // "YYYY-MM"
  const [year, month] = monthValue ? monthValue.split('-').map(Number) : [undefined, undefined];
  const category = activeLossTab === 'expired' ? 'expired' : (document.getElementById('losses-category').value || undefined);
  return { branchId: BranchContext.get(), category, year, month };
}

async function loadLossesHistory() {
  const tbody = document.getElementById('losses-tbody');
  const table = document.getElementById('losses-table');
  const emptyState = document.getElementById('losses-empty');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="10">Loading…</td></tr>';
  try {
    lossRowsCache = await Api.lossesHistory(currentFilters());
    if (!lossRowsCache.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
    } else {
      table.style.display = '';
      emptyState.style.display = 'none';
      tbody.innerHTML = lossRowsCache.map(renderLossRow).join('');
    }
    // The two KPI cards above (overall/this-month) are loaded
    // separately, straight from the summary endpoint -- they always
    // show the true totals regardless of what's filtered here. This
    // line is the only thing that reflects the current filter state.
    const filteredTotal = lossRowsCache.reduce((sum, r) => sum + (r.value_naira || 0), 0);
    const filterLine = document.getElementById('losses-filtered-total-line');
    if (filterLine) {
      const filtersActive = document.getElementById('losses-category').value ||
        document.getElementById('losses-month').value || activeLossTab === 'expired';
      filterLine.textContent = filtersActive
        ? `Total for current filter: ${formatNaira(filteredTotal)} (${lossRowsCache.length} item${lossRowsCache.length === 1 ? '' : 's'})`
        : '';
    }
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
  loadLossesSummaryCards();
  loadLossesReviewSection();
}

// Same underlying data/actions as the Dashboard's loss-review banner
// (see js/dashboard.js loadLossReviewBanner()) -- pending theft
// reports awaiting Super Admin confirmation, plus damaged/expired
// reports nobody has acknowledged yet. Kept as its own section here
// (rather than mixed into the permanent history table below) since
// these need action taken on them, not just browsing.
const lossReasonLabels = { damaged: 'Damaged', expired: 'Expired', theft: 'Stolen' };
// A spoiled-refund loss carries its sale id inside the note text
// ("... refund on sale <id>"). Pull it out so the reviewer can open
// that sale's receipt (in a new tab) before approving or rejecting.
// Shows where a combined loss came from (shelf vs warehouse).
function reviewSplitNote(a) {
  const w = a.warehouse_quantity || 0;
  const s = a.shelf_quantity || 0;
  let text = '';
  if (w > 0) text = s > 0 ? `Shelf ${s} + Warehouse ${w}` : 'From warehouse';
  else if ((a.parts || 1) > 1) text = `Taken from ${a.parts} shelf batches`;
  return text ? `<div style="font-size:12px;color:var(--ink-muted);margin-bottom:4px;">${text}</div>` : '';
}
function reviewSaleLink(a) {
  const m = /sale ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i.exec(a.notes || '');
  if (!m) return '';
  return `<div style="margin-bottom:4px;"><button type="button" class="btn btn-ghost btn-sm" data-review-refund="${a.id}">View refund details</button></div>`;
}
async function loadLossesReviewSection() {
  const section = document.getElementById('losses-review-section');
  const tbody = document.getElementById('losses-review-tbody');
  if (!section || !tbody) return;
  let items;
  try {
    items = await Api.pendingReviewAdjustments();
  } catch (err) {
    section.style.display = 'none'; // e.g. a role without manage_inventory
    return;
  }
  if (!items.length) {
    section.style.display = 'none';
    tbody.innerHTML = '';
    return;
  }
  section.style.display = '';
  window.lossReviewCache = items;
  tbody.innerHTML = items.map((a) => {
    const thumb = a.image_url && lossIsVideo(a.image_url) ? lossVideoTile(a.id, 36) : a.image_url
      ? `<img src="${escapeHtml(a.image_url)}" alt="" data-enlarge-image="${escapeHtml(a.image_url)}" style="width:36px;height:36px;object-fit:cover;border-radius:8px;cursor:zoom-in;border:1px solid var(--border);" />`
      : `<div style="width:36px;height:36px;border-radius:8px;background:var(--bg-soft,#f2f2f2);border:1px solid var(--border);"></div>`;
    const details = [
      a.reason === 'theft' && a.theft_caused_by
        ? (a.theft_caused_by === 'staff' ? `Alleged against ${escapeHtml(a.theft_by_staff_name || 'a staff member')}` : 'Reported by/against a customer')
        : '',
      a.reason === 'damaged' && a.damage_caused_by
        ? (a.damage_caused_by === 'staff' ? `Caused by ${escapeHtml(a.damage_staff_name || 'a staff member')}` : 'Caused by a customer')
        : '',
      a.notes ? `"${escapeHtml(a.notes)}"` : '',
    ].filter(Boolean).join(' — ');
    const actionBtn = a.reason === 'theft'
      ? `<button type="button" class="btn btn-primary btn-sm" data-review-confirm-theft="${a.id}">Confirm — deduct stock</button>`
      : `<div style="display:flex;gap:6px;flex-wrap:wrap;"><button type="button" class="btn btn-primary btn-sm" data-review-approve="${a.id}">Approve as loss</button><button type="button" class="btn btn-ghost btn-sm" data-review-reject="${a.id}">Reject</button></div>`;
    return `
      <tr>
        <td>${thumb}</td>
        <td><strong>${escapeHtml(a.product_name || 'Unknown product')}</strong></td>
        <td><span class="category-badge">${escapeHtml(lossReasonLabels[a.reason] || a.reason)}</span></td>
        <td>${Math.abs(a.quantity_delta)}</td>
        <td>${a.reason === 'theft'
          ? (a.value_naira ? formatNaira(a.value_naira) : '-')
          : `<input type="number" min="0" step="0.01" data-review-value="${a.id}" value="${a.value_naira ? a.value_naira : ''}" placeholder="Enter value" style="width:110px;" />`}</td>
        <td>${escapeHtml(a.branch_name || '—')}</td>
        <td style="max-width:220px;">${reviewViewButton(a)}${reviewSplitNote(a)}${reviewSaleLink(a)}${details || '—'}</td>
        <td>${actionBtn}</td>
      </tr>`;
  }).join('');

  tbody.querySelectorAll('[data-review-confirm-theft]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!(await showConfirmModal('This will deduct the stock from inventory now.', { title: 'Confirm this theft report?', confirmLabel: 'Confirm' }))) return;
      btn.disabled = true;
      try {
        await Api.confirmTheftAdjustment(btn.dataset.reviewConfirmTheft);
        loadLossesHistory(); // re-fetches history + summary cards + this section
      } catch (err) {
        showToast(err.message, true);
        btn.disabled = false;
      }
    });
  });
  tbody.querySelectorAll('[data-review-acknowledge]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        await Api.markAdjustmentReviewed(btn.dataset.reviewAcknowledge);
        loadLossesHistory();
      } catch (err) {
        showToast(err.message, true);
        btn.disabled = false;
      }
    });
  });
}

// "View refund details": shows what was filled in on the refund that
// produced this spoiled-item loss, so it can be checked before approving.
function showRefundDetailsModal(d) {
  const actionLabels = { cash: 'Cash', transfer: 'Bank transfer', exchange: 'Exchange' };
  const row = (label, value) => value
    ? `<div style="display:flex;gap:12px;padding:7px 0;border-bottom:1px solid var(--border);"><div style="flex:0 0 150px;color:var(--ink-muted);font-size:13px;">${label}</div><div style="flex:1;font-size:14px;word-break:break-word;">${value}</div></div>`
    : '';
  const itemState = d.spoiled ? 'Marked spoiled (sent to warehouse, not restocked)' : (d.restocked ? 'Put back on the shelf' : 'Not put back');
  const bank = [d.bank_name, d.account_name, d.account_number].filter(Boolean).map(escapeHtml).join(' - ');
  const photo = d.photo_url
    ? `<img src="${escapeHtml(d.photo_url)}" alt="" data-enlarge-image="${escapeHtml(d.photo_url)}" style="max-width:100%;max-height:220px;border-radius:10px;cursor:zoom-in;border:1px solid var(--border);" />`
    : '<span style="color:var(--ink-muted);">No photo was uploaded</span>';
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop visible';
  backdrop.innerHTML = `
    <div class="modal" style="max-width:500px;max-height:90vh;overflow-y:auto;">
      <h3>Refund details</h3>
      ${row('Product', escapeHtml(d.product_name || 'Unknown product') + (d.unit_label ? ' (' + escapeHtml(d.unit_label) + ')' : ''))}
      ${row('Quantity refunded', escapeHtml(String(d.quantity)))}
      ${row('Unit price', formatNaira(d.unit_price_naira))}
      ${row('Line total', formatNaira(d.line_total_naira))}
      ${row('What was done with the item', escapeHtml(itemState))}
      ${row('Reason given', escapeHtml(d.reason || 'No reason was written'))}
      ${row('Photo of returned item', photo)}
      ${row('Refund method', escapeHtml(actionLabels[d.action] || d.action || '-'))}
      ${row('Bank details', bank)}
      ${row('Total refund amount', formatNaira(d.refund_amount_naira))}
      ${row('Refund status', escapeHtml(d.refund_status || '-'))}
      ${row('Processed by', escapeHtml(d.processed_by_name || '-'))}
      ${row('Branch', escapeHtml(d.branch_name || '-'))}
      ${row('Date and time', escapeHtml(formatDateTime(d.refunded_at)))}
      <div class="modal-actions" style="display:flex;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="btn btn-primary" data-refund-close>Close</button>
      </div>
    </div>`;
  const close = () => backdrop.remove();
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop || (e.target.closest && e.target.closest('[data-refund-close]'))) close(); });
  document.body.appendChild(backdrop);
}
document.addEventListener('click', async (e) => {
  const btn = e.target.closest ? e.target.closest('[data-review-refund]') : null;
  if (!btn) return;
  btn.disabled = true;
  try {
    const d = await Api.lossRefundDetails(btn.dataset.reviewRefund);
    showRefundDetailsModal(d);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});
// Approve / Reject for damaged and expired losses waiting for review.
document.addEventListener('click', async (e) => {
  const approveBtn = e.target.closest ? e.target.closest('[data-review-approve]') : null;
  const rejectBtn = e.target.closest ? e.target.closest('[data-review-reject]') : null;
  if (!approveBtn && !rejectBtn) return;
  if (approveBtn) {
    const id = approveBtn.dataset.reviewApprove;
    const input = document.querySelector(`[data-review-value="${id}"]`);
    const raw = input ? input.value.trim() : '';
    if (!(Number(raw) > 0)) {
      showToast('Enter the value of this loss (in naira) before approving it.', true);
      if (input) input.focus();
      return;
    }
    if (!(await showConfirmModal('This will count the loss in your totals and history at the value entered.', { title: 'Approve this as a loss?', confirmLabel: 'Approve' }))) return;
    approveBtn.disabled = true;
    try {
      await Api.approveAdjustment(id, raw);
      showToast('Loss approved.');
      loadLossesHistory();
    } catch (err) {
      showToast(err.message, true);
      approveBtn.disabled = false;
    }
    return;
  }
  const rid = rejectBtn.dataset.reviewReject;
  const reason = window.prompt('Why is this not a loss? (for example: item is fine and goes back on the shelf)');
  if (reason === null) return;
  if (!reason.trim()) { showToast('A reason is required to reject.', true); return; }
  rejectBtn.disabled = true;
  try {
    await Api.rejectAdjustment(rid, reason.trim());
    showToast('Loss rejected and stock put back.');
    loadLossesHistory();
  } catch (err) {
    showToast(err.message, true);
    rejectBtn.disabled = false;
  }
});
// Today, This Week, this-month, and all-time totals, fed by GET
// /inventory/losses/summary -- the same endpoint the dashboard's
// Losses card uses, called with different date bounds for each.
// Deliberately NOT derived from lossRowsCache/currentFilters() above:
// those totals need to stay fixed reference points no matter what the
// table is currently filtered to, otherwise "this month" (or now
// "this week"/"today") silently means something different depending
// on the Category/Month dropdowns.
async function loadLossesSummaryCards() {
  const branchId = BranchContext.get();
  const alltimeEl = document.getElementById('losses-alltime-total');
  const monthEl = document.getElementById('losses-month-total');
  const weekEl = document.getElementById('losses-week-total');
  const todayEl = document.getElementById('losses-today-total');
  if (!alltimeEl || !monthEl) return;
  try {
    const allTime = await Api.lossesSummary(branchId);
    alltimeEl.textContent = formatNaira(allTime.total_value_naira || 0);
  } catch (err) {
    alltimeEl.textContent = '—';
  }
  try {
    const now = new Date();
    const startDate = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
    const endDate = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().slice(0, 10);
    const thisMonth = await Api.lossesSummary(branchId, startDate, endDate);
    monthEl.textContent = formatNaira(thisMonth.total_value_naira || 0);
  } catch (err) {
    monthEl.textContent = '—';
  }
  // This week: calendar week, Monday through Sunday, same "full
  // bounds" convention as This Month above (not just "so far").
  if (weekEl) {
    try {
      const now = new Date();
      const day = now.getDay(); // 0 (Sun) .. 6 (Sat)
      const diffToMonday = day === 0 ? -6 : 1 - day;
      const monday = new Date(now);
      monday.setDate(now.getDate() + diffToMonday);
      const sunday = new Date(monday);
      sunday.setDate(monday.getDate() + 6);
      const thisWeek = await Api.lossesSummary(branchId, monday.toISOString().slice(0, 10), sunday.toISOString().slice(0, 10));
      weekEl.textContent = formatNaira(thisWeek.total_value_naira || 0);
    } catch (err) {
      weekEl.textContent = '—';
    }
  }
  if (todayEl) {
    try {
      const todayStr = new Date().toISOString().slice(0, 10);
      const today = await Api.lossesSummary(branchId, todayStr, todayStr);
      todayEl.textContent = formatNaira(today.total_value_naira || 0);
    } catch (err) {
      todayEl.textContent = '—';
    }
  }
}

function renderLossRow(r) {
  const thumb = r.image_url
    ? `<img src="${escapeHtml(r.image_url)}" alt="${escapeHtml(r.product_name || '')}" data-enlarge-image="${escapeHtml(r.image_url)}" style="width:40px;height:40px;object-fit:cover;border-radius:8px;cursor:zoom-in;border:1px solid var(--border);" />`
    : `<div style="width:40px;height:40px;border-radius:8px;background:var(--bg-soft,#f2f2f2);border:1px solid var(--border);"></div>`;
  // A recorded loss with value_naira === 0 almost always means the
  // product had no cost price on file at the moment this specific
  // stock batch was created (see _restock_cost_basis_kobo /
  // create_stock_adjustment in the backend) -- it is NOT the same
  // thing as "this item cost nothing". Flagging it here instead of
  // silently printing ₦0.00 is the only way anyone notices the gap,
  // since fixing the product's price afterward does not retroactively
  // fix batches that already exist.
  const valueCell = r.value_naira > 0
    ? formatNaira(r.value_naira)
    : `<span title="No cost price was on file for this product when this stock batch was created -- this is not a genuine ₦0 loss." style="color:var(--danger);font-weight:600;cursor:help;">⚠️ No cost on file</span>`;
  return `
    <tr>
      <td>${thumb}</td>
      <td><strong>${escapeHtml(r.product_name || 'Unknown product')}</strong>${r.sku ? `<div class="mono" style="color:var(--ink-faint);font-size:12px;">${escapeHtml(r.sku)}</div>` : ''}</td>
      <td><span class="category-badge">${escapeHtml(categoryLabels[r.category] || r.category)}</span></td>
      <td>${r.quantity}</td>
      <td>${valueCell}</td>
      <td>${escapeHtml(r.branch_name || '—')}</td>
      <td>${escapeHtml(r.recorded_by_name || '—')}</td>
      <td>${formatDateTime(r.created_at)}</td>
      <td>${escapeHtml(r.notes || '—')}${r.sale_id ? ` <a href="sales.html?sale_id=${encodeURIComponent(r.sale_id)}" style="font-size:12px;">View sale →</a>` : ''}</td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-loss-view="${r.id}">View</button></td>
    </tr>`;
}

// ---- View a loss: full details plus the photo or video proof (fix02) ----
function lossIsVideo(url) {
  return /\.(mp4|mov|webm)(\?.*)?$/i.test(String(url || ''));
}
function lossVideoTile(id, size) {
  return '<button type="button" class="btn btn-ghost btn-sm" data-loss-view-pending="' + id + '" title="Play the video" style="width:' + size + 'px;height:' + size + 'px;padding:0;border-radius:8px;">&#9654;</button>';
}
function reviewViewButton(a) {
  return '<div style="margin-bottom:4px;"><button type="button" class="btn btn-ghost btn-sm" data-loss-view-pending="' + a.id + '">View details and proof</button></div>';
}
function lossStatusLabel(s) {
  const m = { completed: 'Approved as a loss', pending_review: 'Waiting for review', pending_confirmation: 'Waiting for confirmation', rejected: 'Rejected' };
  return m[s] || s || '-';
}
function lossProofHtml(url) {
  if (!url) return '<span style="color:var(--ink-muted);">No photo or video was uploaded</span>';
  const u = escapeHtml(url);
  const link = '<div style="margin-top:6px;"><a href="' + u + '" target="_blank" rel="noopener" style="font-size:12px;">Open the file in a new tab</a></div>';
  if (lossIsVideo(url)) {
    return '<video src="' + u + '" controls playsinline preload="metadata" style="max-width:100%;max-height:300px;border-radius:10px;border:1px solid var(--border);background:#000;"></video>' + link;
  }
  return '<img src="' + u + '" alt="Proof" style="max-width:100%;max-height:300px;border-radius:10px;border:1px solid var(--border);" />' + link;
}
function lossFromHistory(r) {
  return {
    product: r.product_name, sku: r.sku, kind: r.category, qty: r.quantity, value: r.value_naira,
    id: r.id, branchId: r.branch_id, branch: r.branch_name, by: r.recorded_by_name, when: r.created_at, notes: r.notes, who: '',
    proof: r.evidence_url, status: r.status, reviewer: r.reviewed_by_name, reviewedAt: r.reviewed_at, saleId: r.sale_id,
  };
}
function lossFromPending(a) {
  let who = '';
  if (a.reason === 'theft' && a.theft_caused_by) who = a.theft_caused_by === 'staff' ? 'Staff: ' + (a.theft_by_staff_name || 'a staff member') : 'A customer';
  if (a.reason === 'damaged' && a.damage_caused_by) who = a.damage_caused_by === 'staff' ? 'Staff: ' + (a.damage_staff_name || 'a staff member') : 'A customer';
  return {
    product: a.product_name, sku: '', kind: a.reason, qty: Math.abs(a.quantity_delta), value: a.value_naira,
    id: a.id, branchId: a.branch_id, branch: a.branch_name, by: a.adjusted_by_name, when: a.created_at, notes: a.notes, who: who,
    proof: a.image_url, status: a.status, reviewer: a.reviewed_by_name, reviewedAt: a.reviewed_at, saleId: null,
  };
}
function showLossDetailModalOld(d) {
  const row = (label, value) => value
    ? '<div style="display:flex;gap:12px;padding:7px 0;border-bottom:1px solid var(--border);"><div style="flex:0 0 140px;color:var(--ink-muted);font-size:13px;">' + label + '</div><div style="flex:1;font-size:14px;word-break:break-word;">' + value + '</div></div>'
    : '';
  const kindLabel = categoryLabels[d.kind] || lossReasonLabels[d.kind] || d.kind || '-';
  const valueText = (d.value === null || d.value === undefined) ? '' : formatNaira(d.value);
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop visible';
  backdrop.innerHTML = '<div class="modal" style="max-width:560px;max-height:90vh;overflow-y:auto;"><h3>Loss details</h3>'
    + row('Product', escapeHtml(d.product || 'Unknown product') + (d.sku ? ' (' + escapeHtml(d.sku) + ')' : ''))
    + row('Recorded as', escapeHtml(kindLabel))
    + row('Status', escapeHtml(lossStatusLabel(d.status)))
    + row('Quantity', escapeHtml(String(d.qty)))
    + row('Value', valueText)
    + row('Branch', escapeHtml(d.branch || '-'))
    + row('Recorded by', escapeHtml(d.by || '-'))
    + row('Date recorded', d.when ? escapeHtml(formatDateTime(d.when)) : '')
    + row('Who caused it', d.who ? escapeHtml(d.who) : '')
    + row('Notes', d.notes ? escapeHtml(d.notes) : '')
    + row('Reviewed by', d.reviewer ? escapeHtml(d.reviewer) : '')
    + row('Date reviewed', d.reviewedAt ? escapeHtml(formatDateTime(d.reviewedAt)) : '')
    + row('Proof', lossProofHtml(d.proof))
    + (d.saleId ? row('Sale', '<a href="sales.html?sale_id=' + encodeURIComponent(d.saleId) + '">View sale</a>') : '')
    + '<div style="display:flex;justify-content:flex-end;margin-top:16px;"><button type="button" class="btn btn-primary" data-loss-modal-close>Close</button></div></div>';
  const close = () => backdrop.remove();
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop || (e.target.closest && e.target.closest('[data-loss-modal-close]'))) close();
  });
  document.body.appendChild(backdrop);
}
// ---- fix03: loss receipt window, click-to-zoom photo, video full screen ----
function lossShortId(id) {
  return String(id || '').replace(/-/g, '').slice(0, 8).toUpperCase();
}
function lossFullscreen(el) {
  if (!el) return;
  const fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
  if (fn) {
    try { fn.call(el); } catch (err) { /* the browser refused; the native button still works */ }
  }
}
function lossOpenZoom(url) {
  const old = document.getElementById('loss-zoom-overlay');
  if (old) old.remove();
  const ov = document.createElement('div');
  ov.id = 'loss-zoom-overlay';
  ov.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:100000;background:rgba(2,6,14,.92);display:flex;flex-direction:column;align-items:center;justify-content:center;padding:16px;cursor:zoom-out;';
  ov.innerHTML = '<img src="' + escapeHtml(url) + '" alt="Proof" style="max-width:96vw;max-height:86vh;object-fit:contain;border-radius:8px;" />'
    + '<button type="button" class="btn btn-primary" style="margin-top:14px;">Close</button>';
  ov.addEventListener('click', () => ov.remove());
  document.body.appendChild(ov);
}
if (!window.__lossKeyBound) {
  window.__lossKeyBound = true;
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const z = document.getElementById('loss-zoom-overlay');
    if (z) { z.remove(); return; }
    const m = document.getElementById('loss-detail-backdrop');
    if (m) m.remove();
  });
}
function lossFindBranch(d) {
  if (typeof Api === 'undefined' || !Api.listBranches) return Promise.resolve(null);
  return Api.listBranches().then((rows) => {
    rows = rows || [];
    let b = null;
    if (d.branchId) b = rows.find((x) => String(x.id) === String(d.branchId));
    if (!b && d.branch) b = rows.find((x) => String(x.name || '').toLowerCase() === String(d.branch).toLowerCase());
    return b || null;
  }).catch(() => null);
}
function lossReceiptHtml(d, branch) {
  let brand = { name: 'Nick Cosmetics', logoUrl: '' };
  try {
    if (typeof StoreBranding !== 'undefined') brand = StoreBranding.current();
  } catch (err) { /* keep the default */ }
  const br = branch || {};
  const branchName = br.name || d.branch || '';
  const kindLabel = categoryLabels[d.kind] || lossReasonLabels[d.kind] || d.kind || '-';
  const valueText = (d.value === null || d.value === undefined) ? '-' : formatNaira(d.value);
  const logo = brand.logoUrl
    ? '<img src="' + escapeHtml(brand.logoUrl) + '" alt="' + escapeHtml(brand.name || 'Store logo') + '" class="receipt-logo-img" />'
    : '';
  const line = (a, b) => '<div class="receipt-totals-row"><span>' + a + '</span><span>' + b + '</span></div>';
  let h = '';
  h += '<div class="receipt-logo">' + logo + '</div>';
  h += '<div class="receipt-ticket-meta">';
  h += '<div style="font-weight:700;font-size:1.15em;">' + escapeHtml(brand.name || 'Nick Cosmetics') + '</div>';
  h += '<div style="font-weight:700;">LOSS RECEIPT</div>';
  if (branchName) h += '<div style="font-weight:600;">Branch: ' + escapeHtml(branchName) + '</div>';
  if (br.address) h += '<div>' + escapeHtml(br.address) + '</div>';
  if (d.id) h += '<div>Ticket ' + escapeHtml(lossShortId(d.id)) + '</div>';
  if (d.when) h += '<div>' + escapeHtml(formatDateTime(d.when)) + '</div>';
  h += '<div>Recorded by: ' + escapeHtml(String(d.by || 'Unknown').toUpperCase()) + '</div>';
  h += '</div>';
  h += '<div class="receipt-items"><div class="receipt-line-item"><div class="receipt-line-item-row">'
    + '<span class="receipt-line-qty">' + escapeHtml(String(d.qty)) + '</span>'
    + '<span class="receipt-line-name">' + escapeHtml(d.product || 'Unknown product') + '</span>'
    + '<span class="receipt-line-amount">' + valueText + '</span></div>'
    + '<div class="receipt-line-unit">' + (d.sku ? escapeHtml(d.sku) + ' / ' : '') + escapeHtml(kindLabel) + '</div></div></div>';
  h += '<div class="receipt-totals">';
  h += '<div class="receipt-totals-row receipt-grand-total"><span>Loss value</span><span>' + valueText + '</span></div>';
  h += line('Type', escapeHtml(kindLabel));
  h += line('Status', escapeHtml(lossStatusLabel(d.status)));
  if (d.who) h += line('Caused by', escapeHtml(d.who));
  if (d.reviewer) h += line('Reviewed by', escapeHtml(String(d.reviewer).toUpperCase()));
  if (d.reviewedAt) h += line('Date reviewed', escapeHtml(formatDateTime(d.reviewedAt)));
  if (d.notes) {
    h += line('Notes', '');
    h += '<div class="receipt-line-unit" style="white-space:normal;margin-left:0;">' + escapeHtml(d.notes) + '</div>';
  }
  h += '</div>';
  h += '<div class="receipt-store-footer">';
  if (br.support_line) h += '<div>Support line: ' + escapeHtml(br.support_line) + '</div>';
  h += '<div class="receipt-thank-you">Internal stock loss record. Not a customer receipt.</div>';
  h += '</div>';
  h += '<div class="receipt-powered-by">Powered by Nick Cosmetics</div>';
  return h;
}
function lossProofBoxHtml(url) {
  if (!url) return '<div style="color:var(--ink-muted);font-size:13px;">No photo or video was uploaded for this loss.</div>';
  const u = escapeHtml(url);
  if (lossIsVideo(url)) {
    let h = '<div style="font-weight:700;font-size:13px;margin-bottom:6px;">Video proof</div>';
    h += '<video id="loss-proof-video" src="' + u + '" controls playsinline preload="metadata" style="width:100%;max-height:320px;border-radius:10px;border:1px solid var(--border);background:#000;"></video>';
    h += '<div id="loss-proof-video-error" style="display:none;color:#f87171;font-size:12.5px;margin-top:6px;">This video could not be played in this browser. An .mp4 or .webm file works best.</div>';
    h += '<div style="margin-top:8px;"><button type="button" class="btn btn-ghost btn-sm" data-loss-fs>Full screen</button></div>';
    return h;
  }
  let h = '<div style="font-weight:700;font-size:13px;margin-bottom:6px;">Photo proof</div>';
  h += '<img src="' + u + '" alt="Proof" data-loss-zoom="' + u + '" title="Click to zoom in" style="max-width:100%;max-height:260px;border-radius:10px;border:1px solid var(--border);cursor:zoom-in;" />';
  h += '<div style="color:var(--ink-muted);font-size:12px;margin-top:4px;">Click the photo to zoom in.</div>';
  return h;
}
function showLossDetailModal(d) {
  const existing = document.getElementById('loss-detail-backdrop');
  if (existing) existing.remove();
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop visible';
  backdrop.id = 'loss-detail-backdrop';
  backdrop.innerHTML = '<div class="modal receipt-modal" style="max-width:520px;max-height:92vh;overflow-y:auto;">'
    + '<div style="display:flex;gap:10px;justify-content:flex-end;margin-bottom:10px;">'
    + '<button type="button" class="btn btn-ghost" data-loss-modal-close>Close</button>'
    + '<button type="button" class="btn btn-primary" data-loss-print>Print receipt</button></div>'
    + '<div class="receipt-sheet" id="loss-receipt-sheet"></div>'
    + '<div id="loss-proof-box" style="margin-top:16px;"></div></div>';
  const sheet = backdrop.querySelector('#loss-receipt-sheet');
  const paint = (branch) => { sheet.innerHTML = lossReceiptHtml(d, branch); };
  paint(null);
  lossFindBranch(d).then((b) => { if (b) paint(b); });
  backdrop.querySelector('#loss-proof-box').innerHTML = lossProofBoxHtml(d.proof);
  const vid = backdrop.querySelector('#loss-proof-video');
  if (vid) {
    vid.addEventListener('error', () => {
      const msg = backdrop.querySelector('#loss-proof-video-error');
      if (msg) msg.style.display = 'block';
    });
  }
  backdrop.addEventListener('click', (e) => {
    const t = e.target;
    if (t === backdrop || (t.closest && t.closest('[data-loss-modal-close]'))) {
      backdrop.remove();
      return;
    }
    if (!t.closest) return;
    if (t.closest('[data-loss-print]')) {
      if (typeof Receipt !== 'undefined' && Receipt.print) Receipt.print(); else window.print();
      return;
    }
    const z = t.closest('[data-loss-zoom]');
    if (z) { lossOpenZoom(z.getAttribute('data-loss-zoom')); return; }
    if (t.closest('[data-loss-fs]')) lossFullscreen(backdrop.querySelector('#loss-proof-video'));
  });
  document.body.appendChild(backdrop);
}
document.getElementById('losses-review-tbody')?.addEventListener('click', (e) => {
  const img = e.target.closest('[data-enlarge-image]');
  if (img) openImageLightbox(img.dataset.enlargeImage);
});
document.addEventListener('click', (e) => {
  if (!e.target.closest) return;
  const h = e.target.closest('[data-loss-view]');
  if (h) {
    const r = (lossRowsCache || []).find((x) => String(x.id) === h.dataset.lossView);
    if (r) showLossDetailModal(lossFromHistory(r));
    return;
  }
  const p = e.target.closest('[data-loss-view-pending]');
  if (p) {
    const a = (window.lossReviewCache || []).find((x) => String(x.id) === p.dataset.lossViewPending);
    if (a) showLossDetailModal(lossFromPending(a));
  }
});
function clearLossEvidencePreview() {
  const box = document.getElementById('direct-loss-evidence-preview');
  if (box) box.innerHTML = '';
}
function showLossEvidencePreview(file, url) {
  let box = document.getElementById('direct-loss-evidence-preview');
  if (!box) {
    const st = document.getElementById('direct-loss-evidence-status');
    if (!st) return;
    box = document.createElement('div');
    box.id = 'direct-loss-evidence-preview';
    box.style.marginTop = '8px';
    st.parentNode.insertBefore(box, st.nextSibling);
  }
  const u = escapeHtml(url);
  box.innerHTML = (file && file.type && file.type.indexOf('video/') === 0)
    ? '<video src="' + u + '" controls playsinline preload="metadata" style="max-width:100%;max-height:200px;border-radius:10px;border:1px solid var(--border);background:#000;"></video>'
    : '<img src="' + u + '" alt="Preview" style="max-width:100%;max-height:200px;border-radius:10px;border:1px solid var(--border);" />';
}

document.querySelectorAll('[data-loss-tab]').forEach((btn) => {
  btn.addEventListener('click', () => {
    activeLossTab = btn.dataset.lossTab;
    document.querySelectorAll('[data-loss-tab]').forEach((b) => b.classList.toggle('active', b === btn));
    // The Expired tab (parallel to Low Stock's Expiry Alert tab, per
    // spec section 18.4) is a fixed lens onto category=expired -- the
    // category dropdown is disabled while it's active so the two
    // filters can't silently contradict each other.
    const categorySelect = document.getElementById('losses-category');
    categorySelect.disabled = activeLossTab === 'expired';
    if (activeLossTab === 'expired') categorySelect.value = '';
    loadLossesHistory();
  });
});

document.getElementById('losses-category').addEventListener('change', loadLossesHistory);
document.getElementById('losses-month').addEventListener('change', loadLossesHistory);
document.getElementById('losses-clear-filters').addEventListener('click', () => {
  document.getElementById('losses-category').value = '';
  document.getElementById('losses-month').value = '';
  activeLossTab = 'all';
  document.querySelectorAll('[data-loss-tab]').forEach((b) => b.classList.toggle('active', b.dataset.lossTab === 'all'));
  document.getElementById('losses-category').disabled = false;
  loadLossesHistory();
});

window.addEventListener('branch-context-changed', () => { if (activeTab === 'losses') loadLossesHistory(); });
if (window.location.hash === '#losses') activateTab('losses');

// ------------------------------------------------ Direct "Record a loss" entry
// Previously the only way to record a loss was Inventory > a product's
// Restock history > a specific batch's "Record loss" button -- correct
// once you're already looking at a batch, but there was no way to get
// there starting from "I have a loss to report." This reuses the same
// POST /inventory/stock-adjustments submission and the same
// accountability/evidence fields; it just asks for the product (and
// then, if there's more than one batch with stock left, the batch)
// first, defaulting to the batch expiring soonest -- FEFO, the same
// ordering the rest of the app already uses for stock deduction.
//
// Bug fix (Sept 2026 dictated request): the product field previously
// pointed at a native <datalist> in sales.html that was never
// populated, so nothing ever showed up while typing. sales.html now
// gives this field the same custom-dropdown markup already used
// elsewhere in the app (.item-autocomplete-wrap / .item-autocomplete-
// dropdown) with the exact ids this file expects
// (#direct-loss-product-field / #direct-loss-product-suggestions), so
// the dropdown below actually has somewhere to render into. This file
// also now keeps the FULL matched product (not just its id) so the
// category and price can be shown once one is picked.

let directLossProductMap = {}; // product name -> full product object, refreshed per keystroke
let directLossBatches = [];
let directLossWarehouseQty = 0; // pieces of this product in the branch warehouse
// The browser also fires a native "change" on the product box when focus
// leaves it (e.g. clicking the Take-it-from picker next); without this
// guard that re-ran the loader and reset the picker back to Shelf.
let directLossLoadedProductId = null;
let directLossEvidenceUrl = null;

const directLossBackdrop = document.getElementById('direct-loss-modal-backdrop');
const directLossProductInput = document.getElementById('direct-loss-product');
const directLossSubmitBtn = document.getElementById('direct-loss-modal-submit');
const directLossQtyInput = document.getElementById('direct-loss-quantity');
const directLossUseAllBtn = document.getElementById('direct-loss-use-all');
const directLossLocationField = document.getElementById('direct-loss-location-field');
const directLossLocationSelect = document.getElementById('direct-loss-location');

// Where the loss comes out of: 'shelf', 'warehouse', or 'any' (shelf and
// warehouse together -- the shelf first, soonest expiry first, then the
// warehouse for whatever is left). No batch is picked by hand: the server
// works the shelf batches out itself.
function directLossLocation() {
  return directLossLocationSelect.value || 'any';
}
function directLossShelfTotal() {
  return directLossBatches.reduce((sum, b) => sum + b.quantity_remaining, 0);
}
// The most that can be lost right now, for whichever place is chosen:
// shelf and warehouse together = the figure Inventory shows as Total.
function directLossMaxQty() {
  const location = directLossLocation();
  if (location === 'warehouse') return directLossWarehouseQty;
  if (location === 'any') return directLossShelfTotal() + directLossWarehouseQty;
  return directLossShelfTotal();
}
function refreshDirectLossQtyLimits() {
  const max = directLossMaxQty();
  directLossQtyInput.max = max;
  directLossUseAllBtn.textContent = `Use all (${max})`;
  directLossUseAllBtn.style.display = max > 0 ? '' : 'none';
  let hint = '';
  if (max > 0) {
    const location = directLossLocation();
    if (location === 'warehouse') hint = `${max} available in the warehouse.`;
    else if (location === 'any') hint = `${max} in total: ${directLossShelfTotal()} on the shelf + ${directLossWarehouseQty} in the warehouse. Comes off the shelf first, then the warehouse.`;
    else hint = `${max} available on the shelf.`;
  }
  document.getElementById('direct-loss-quantity-hint').textContent = hint;
}
directLossLocationSelect.addEventListener('change', refreshDirectLossQtyLimits);

async function fetchDirectLossWarehouseQty(productId) {
  try {
    const branchId = BranchContext.get();
    const res = await apiRequest(`/inventory/warehouse-qty?product_id=${encodeURIComponent(productId)}${branchId ? `&branch_id=${encodeURIComponent(branchId)}` : ''}`);
    return Number(res?.quantity_pieces) || 0;
  } catch (err) {
    return null; // couldn't read it -- the form says so, rather than silently hiding the warehouse
  }
}
directLossUseAllBtn.addEventListener('click', () => {
  directLossQtyInput.value = directLossMaxQty();
  directLossQtyInput.dispatchEvent(new Event('input', { bubbles: true }));
});

// Staff member's name is now a dropdown of the current branch's staff
// (rather than a free-text field) -- the person doesn't have to type
// (or misspell) a name, and the value we store matches an actual
// account. Loaded fresh each time the modal opens, since the active
// branch can change between visits.
let directLossBranchStaff = [];
async function loadDirectLossBranchStaff() {
  const damageSelect = document.getElementById('direct-loss-damage-staff-name');
  const theftSelect = document.getElementById('direct-loss-theft-by-staff-name');
  try {
    directLossBranchStaff = await Api.listUsers({ branchId: BranchContext.get() });
    const options = '<option value="">Select…</option>' +
      directLossBranchStaff.map((u) => `<option value="${escapeHtml(u.full_name)}">${escapeHtml(u.full_name)}</option>`).join('');
    damageSelect.innerHTML = options;
    theftSelect.innerHTML = options;
  } catch (err) {
    // Non-fatal -- the dropdowns just stay empty; the reason-specific
    // required-field check on submit still catches an unmade choice.
  }
}

document.getElementById('direct-loss-open-btn')?.addEventListener('click', () => {
  document.getElementById('direct-loss-modal-form').reset();
  document.getElementById('direct-loss-modal-error').classList.remove('visible');
  document.getElementById('direct-loss-product-info').textContent = '';
  directLossLocationField.style.display = 'none';
  directLossBatches = [];
  directLossWarehouseQty = 0;
  directLossLoadedProductId = null;
  directLossEvidenceUrl = null;
  directLossUseAllBtn.style.display = 'none';
  document.getElementById('direct-loss-quantity-hint').textContent = '';
  document.getElementById('direct-loss-evidence-status').textContent = '';
  clearLossEvidencePreview();
  directLossSubmitBtn.disabled = true;
  loadDirectLossBranchStaff();
  applyDirectLossReasonFieldVisibility();
  directLossBackdrop.classList.add('visible');
  directLossProductInput.focus();
});
document.getElementById('direct-loss-modal-cancel')?.addEventListener('click', () => {
  directLossBackdrop.classList.remove('visible');
});

let directLossSearchDebounce = null;
directLossProductInput.addEventListener('input', () => {
  directLossLoadedProductId = null;
  directLossLocationField.style.display = 'none';
  directLossUseAllBtn.style.display = 'none';
  document.getElementById('direct-loss-quantity-hint').textContent = '';
  directLossSubmitBtn.disabled = true;
  document.getElementById('direct-loss-product-info').textContent = '';
  clearTimeout(directLossSearchDebounce);
  const term = directLossProductInput.value.trim();
  const dropdown = document.getElementById('direct-loss-product-suggestions');
  if (term.length < 2) { dropdown.style.display = 'none'; return; }
  directLossSearchDebounce = setTimeout(async () => {
    try {
      const products = await Api.listProducts({ branchId: BranchContext.get(), search: term });
      directLossProductMap = {};
      products.forEach((p) => { directLossProductMap[p.name] = p; });
      renderDirectLossSuggestions(products);
    } catch (err) {
      // A failed search just leaves the dropdown as-is -- not worth a toast mid-typing.
    }
  }, 250);
});

function renderDirectLossSuggestions(products) {
  const dropdown = document.getElementById('direct-loss-product-suggestions');
  if (!products.length) {
    dropdown.innerHTML = '<div class="item-autocomplete-empty">No matching products</div>';
    dropdown.style.display = 'block';
    return;
  }
  dropdown.innerHTML = products.map((p) => `
    <div class="item-autocomplete-row" data-name="${escapeHtml(p.name)}">
      <span>${escapeHtml(p.name)}</span>
      <span class="price">${formatNaira(p.selling_price_naira)}</span>
    </div>`).join('');
  dropdown.style.display = 'block';
  dropdown.querySelectorAll('[data-name]').forEach((row) => {
    // mousedown (not click) fires before the input's blur, so the
    // selection registers even though clicking the row would
    // otherwise blur the input first.
    row.addEventListener('mousedown', (e) => {
      e.preventDefault();
      directLossProductInput.value = row.dataset.name;
      dropdown.style.display = 'none';
      directLossProductInput.dispatchEvent(new Event('change'));
    });
  });
}

document.addEventListener('click', (e) => {
  const field = document.getElementById('direct-loss-product-field');
  if (field && !field.contains(e.target)) {
    document.getElementById('direct-loss-product-suggestions').style.display = 'none';
  }
});

directLossProductInput.addEventListener('change', async () => {
  const product = directLossProductMap[directLossProductInput.value];
  const productId = product?.id;
  if (!productId) return; // typed text doesn't match a selected product yet
  if (productId === directLossLoadedProductId) return; // already loaded -- don't reset the user's choices
  directLossLoadedProductId = productId;
  const errorEl = document.getElementById('direct-loss-modal-error');
  errorEl.classList.remove('visible');
  // Show product category + price as soon as it's picked, before the
  // batch/quantity/reason fields below (Sept 2026 dictated request).
  document.getElementById('direct-loss-product-info').textContent =
    `${product.category_name || 'Uncategorized'} · ${formatNaira(product.selling_price_naira)}`;
  try {
    const [allBatches, warehouseResult] = await Promise.all([
      Api.listStockBatches(productId, BranchContext.get()),
      fetchDirectLossWarehouseQty(productId),
    ]);
    const warehouseUnreadable = warehouseResult === null;
    const warehouseQty = warehouseUnreadable ? 0 : warehouseResult;
    const batches = allBatches
      .filter((b) => b.quantity_remaining > 0)
      // FEFO: soonest expiry first; batches with no expiry date sort last.
      .sort((a, b) => {
        if (!a.expiry_date && !b.expiry_date) return new Date(a.received_at) - new Date(b.received_at);
        if (!a.expiry_date) return 1;
        if (!b.expiry_date) return -1;
        return new Date(a.expiry_date) - new Date(b.expiry_date);
      });
    directLossBatches = batches;
    directLossWarehouseQty = warehouseQty;
    const shelfTotal = batches.reduce((sum, b) => sum + b.quantity_remaining, 0);
    if (!shelfTotal && !warehouseQty) {
      directLossLoadedProductId = null;
      errorEl.textContent = 'No stock found for this product at this branch, on the shelf or in the warehouse.';
      errorEl.classList.add('visible');
      directLossLocationField.style.display = 'none';
      directLossSubmitBtn.disabled = true;
      return;
    }
    // Always offer the choice: shelf, warehouse, or both together. A place
    // with nothing in it is shown but can't be picked.
    const total = shelfTotal + warehouseQty;
    directLossLocationSelect.innerHTML =
      `<option value="any"${total ? '' : ' disabled'}>Shelf and warehouse (${total})</option>` +
      `<option value="shelf"${shelfTotal ? '' : ' disabled'}>Shelf (${shelfTotal})</option>` +
      `<option value="warehouse"${warehouseQty ? '' : ' disabled'}>Warehouse (${warehouseUnreadable ? 'unavailable' : warehouseQty})</option>`;
    directLossLocationSelect.value = 'any';
    document.getElementById('direct-loss-location-hint').textContent = warehouseUnreadable
      ? "Couldn't read the warehouse stock, so only the shelf is offered. Make sure the API is updated and restarted."
      : 'Shelf and warehouse takes it off the shelf first, then the warehouse.';
    directLossLocationField.style.display = '';
    refreshDirectLossQtyLimits();
    directLossSubmitBtn.disabled = false;
  } catch (err) {
    directLossLoadedProductId = null;
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  }
});

// Mirrors inventory.js's applyLossReasonFieldVisibility -- kept as a
// separate copy (rather than a shared function) since this page and
// inventory.html never load the same script bundle.
function applyDirectLossReasonFieldVisibility() {
  const reason = document.getElementById('direct-loss-reason').value;
  document.getElementById('direct-loss-damage-by-field').style.display = reason === 'damaged' ? '' : 'none';
  const isDamageByStaff = reason === 'damaged' && document.getElementById('direct-loss-damage-by').value === 'staff';
  document.getElementById('direct-loss-damage-staff-field').style.display = isDamageByStaff ? '' : 'none';
  // Defaults to whoever's reporting it (the common case is
  // self-reporting) -- editable if it was actually someone else.
  const damageStaffSelect = document.getElementById('direct-loss-damage-staff-name');
  if (isDamageByStaff && !damageStaffSelect.value && currentUser?.full_name) {
    const match = Array.from(damageStaffSelect.options).find((o) => o.value === currentUser.full_name);
    if (match) damageStaffSelect.value = currentUser.full_name;
  }
  document.getElementById('direct-loss-theft-caused-by-field').style.display = reason === 'theft' ? '' : 'none';
  const isTheftByStaff = reason === 'theft' && document.getElementById('direct-loss-theft-caused-by').value === 'staff';
  document.getElementById('direct-loss-theft-by-staff-field').style.display = isTheftByStaff ? '' : 'none';
  document.getElementById('direct-loss-theft-time-field').style.display = reason === 'theft' ? '' : 'none';

  const needsEvidence = ['damaged', 'theft', 'expired'].includes(reason);
  document.getElementById('direct-loss-evidence-field').style.display = needsEvidence ? '' : 'none';
  document.getElementById('direct-loss-evidence').required = needsEvidence;

  const notesLabel = document.getElementById('direct-loss-notes-label');
  notesLabel.textContent = reason === 'damaged' ? 'What happened? *' : 'Notes (optional)';
  document.getElementById('direct-loss-notes').required = reason === 'damaged';
}
document.getElementById('direct-loss-reason')?.addEventListener('change', applyDirectLossReasonFieldVisibility);
document.getElementById('direct-loss-damage-by')?.addEventListener('change', applyDirectLossReasonFieldVisibility);
document.getElementById('direct-loss-theft-caused-by')?.addEventListener('change', applyDirectLossReasonFieldVisibility);

document.getElementById('direct-loss-evidence')?.addEventListener('change', async (e) => {
  const file = e.target.files[0];
  const statusEl = document.getElementById('direct-loss-evidence-status');
  clearLossEvidencePreview();
  if (!file) { directLossEvidenceUrl = null; statusEl.textContent = ''; return; }
  statusEl.textContent = 'Uploading…';
  try {
    const { url } = await Api.uploadLossEvidence(file);
    directLossEvidenceUrl = url;
    showLossEvidencePreview(file, url);
    statusEl.textContent = file.type.startsWith('video/') ? 'Video attached ✓' : 'Photo attached ✓';
  } catch (err) {
    directLossEvidenceUrl = null;
    statusEl.textContent = `Upload failed: ${err.message}`;
  }
});

document.getElementById('direct-loss-modal-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('direct-loss-modal-error');
  const product = directLossProductMap[directLossProductInput.value];
  if (!product || (!directLossBatches.length && directLossWarehouseQty <= 0)) {
    errorEl.textContent = 'Pick a product with remaining stock first.';
    errorEl.classList.add('visible');
    return;
  }
  const qty = Number(document.getElementById('direct-loss-quantity').value);
  const maxQty = directLossMaxQty();
  if (!(qty >= 1) || qty > maxQty) {
    errorEl.textContent = `Enter a quantity between 1 and ${maxQty}.`;
    errorEl.classList.add('visible');
    return;
  }
  const reason = document.getElementById('direct-loss-reason').value;

  const payload = {
    quantity_delta: -Math.abs(qty),
    reason,
    notes: document.getElementById('direct-loss-notes').value.trim() || null,
  };
  // The server works the batches out from the product: 'any' = shelf and
  // warehouse together (shelf first, soonest expiry first, then the
  // warehouse), 'shelf' = shelf only, 'warehouse' = warehouse only.
  payload.location = directLossLocation();
  payload.product_id = product.id;
  const branchId = BranchContext.get();
  if (branchId) payload.branch_id = branchId;
  if (reason === 'damaged') {
    const damagedBy = document.getElementById('direct-loss-damage-by').value;
    if (!damagedBy) {
      errorEl.textContent = 'Record whether the item was damaged by a customer or a staff member.';
      errorEl.classList.add('visible');
      return;
    }
    payload.damage_caused_by = damagedBy;
    if (damagedBy === 'staff') {
      const staffName = document.getElementById('direct-loss-damage-staff-name').value;
      if (!staffName) {
        errorEl.textContent = 'Select the staff member responsible for the damage.';
        errorEl.classList.add('visible');
        return;
      }
      payload.damage_staff_name = staffName;
    }
    if (!payload.notes) {
      errorEl.textContent = 'Write a note explaining what happened to the item.';
      errorEl.classList.add('visible');
      return;
    }
  }
  if (reason === 'theft') {
    const theftBy = document.getElementById('direct-loss-theft-caused-by').value;
    if (!theftBy) {
      errorEl.textContent = 'Record whether the item was stolen by a customer or a staff member.';
      errorEl.classList.add('visible');
      return;
    }
    payload.theft_caused_by = theftBy;
    if (theftBy === 'staff') {
      const staffName = document.getElementById('direct-loss-theft-by-staff-name').value;
      if (!staffName) {
        errorEl.textContent = 'Select the staff member responsible for the theft.';
        errorEl.classList.add('visible');
        return;
      }
      payload.theft_by_staff_name = staffName;
    }
    const noticedAt = document.getElementById('direct-loss-theft-noticed-at').value;
    if (noticedAt) {
      const today = new Date().toISOString().slice(0, 10);
      payload.theft_noticed_at = new Date(`${today}T${noticedAt}:00`).toISOString();
    }
  }
  if (['damaged', 'theft', 'expired'].includes(reason)) {
    if (!directLossEvidenceUrl) {
      errorEl.textContent = 'Upload a photo or video of the item as evidence.';
      errorEl.classList.add('visible');
      return;
    }
    payload.image_url = directLossEvidenceUrl;
  }

  directLossSubmitBtn.disabled = true;
  errorEl.classList.remove('visible');
  try {
    const result = await Api.createStockAdjustment(payload);
    showToast(
      result.status === 'pending_confirmation'
        ? 'Theft reported -- the super admin has been notified and will confirm before it comes off the shelf.'
        : 'Loss recorded.'
    );
    directLossBackdrop.classList.remove('visible');
    loadLossesHistory();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    directLossSubmitBtn.disabled = false;
  }
});
