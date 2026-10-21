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
  tbody.innerHTML = '<tr class="loading-row"><td colspan="9">Loading…</td></tr>';
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
  tbody.innerHTML = items.map((a) => {
    const thumb = a.image_url
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
      : `<button type="button" class="btn btn-ghost btn-sm" data-review-acknowledge="${a.id}">Acknowledge</button>`;
    return `
      <tr>
        <td>${thumb}</td>
        <td><strong>${escapeHtml(a.product_name || 'Unknown product')}</strong></td>
        <td><span class="category-badge">${escapeHtml(lossReasonLabels[a.reason] || a.reason)}</span></td>
        <td>${Math.abs(a.quantity_delta)}</td>
        <td>${a.value_naira ? formatNaira(a.value_naira) : '<span style="color:var(--danger);">⚠️ No cost on file</span>'}</td>
        <td>${escapeHtml(a.branch_name || '—')}</td>
        <td style="max-width:220px;">${details || '—'}</td>
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
    </tr>`;
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
let directLossEvidenceUrl = null;

const directLossBackdrop = document.getElementById('direct-loss-modal-backdrop');
const directLossProductInput = document.getElementById('direct-loss-product');
const directLossBatchField = document.getElementById('direct-loss-batch-field');
const directLossBatchSelect = document.getElementById('direct-loss-batch');
const directLossSubmitBtn = document.getElementById('direct-loss-modal-submit');

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
  directLossBatchField.style.display = 'none';
  directLossBatches = [];
  directLossEvidenceUrl = null;
  document.getElementById('direct-loss-evidence-status').textContent = '';
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
  directLossBatchField.style.display = 'none';
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
  const errorEl = document.getElementById('direct-loss-modal-error');
  errorEl.classList.remove('visible');
  // Show product category + price as soon as it's picked, before the
  // batch/quantity/reason fields below (Sept 2026 dictated request).
  document.getElementById('direct-loss-product-info').textContent =
    `${product.category_name || 'Uncategorized'} · ${formatNaira(product.selling_price_naira)}`;
  try {
    const batches = (await Api.listStockBatches(productId, BranchContext.get()))
      .filter((b) => b.quantity_remaining > 0)
      // FEFO: soonest expiry first; batches with no expiry date sort last.
      .sort((a, b) => {
        if (!a.expiry_date && !b.expiry_date) return new Date(a.received_at) - new Date(b.received_at);
        if (!a.expiry_date) return 1;
        if (!b.expiry_date) return -1;
        return new Date(a.expiry_date) - new Date(b.expiry_date);
      });
    directLossBatches = batches;
    if (!batches.length) {
      errorEl.textContent = 'No batches with remaining stock found for this product at this branch.';
      errorEl.classList.add('visible');
      directLossBatchField.style.display = 'none';
      directLossSubmitBtn.disabled = true;
      return;
    }
    directLossBatchSelect.innerHTML = batches.map((b, i) => {
      const expiry = b.expiry_date ? new Date(b.expiry_date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' }) : 'no expiry set';
      const received = new Date(b.received_at).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' });
      return `<option value="${i}">${b.quantity_remaining} left · received ${received} · expires ${expiry}</option>`;
    }).join('');
    directLossBatchSelect.value = '0'; // default to the soonest-expiring batch (FEFO)
    document.getElementById('direct-loss-batch-hint').textContent =
      batches.length > 1 ? 'Defaulted to the batch expiring soonest -- change it if this loss is from a different batch.' : '';
    directLossBatchField.style.display = '';
    document.getElementById('direct-loss-quantity').max = batches[0].quantity_remaining;
    directLossSubmitBtn.disabled = false;
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  }
});
directLossBatchSelect.addEventListener('change', () => {
  const batch = directLossBatches[Number(directLossBatchSelect.value)];
  if (batch) document.getElementById('direct-loss-quantity').max = batch.quantity_remaining;
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
  if (!file) { directLossEvidenceUrl = null; statusEl.textContent = ''; return; }
  statusEl.textContent = 'Uploading…';
  try {
    const { url } = await Api.uploadLossEvidence(file);
    directLossEvidenceUrl = url;
    statusEl.textContent = file.type.startsWith('video/') ? 'Video attached ✓' : 'Photo attached ✓';
  } catch (err) {
    directLossEvidenceUrl = null;
    statusEl.textContent = `Upload failed: ${err.message}`;
  }
});

document.getElementById('direct-loss-modal-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('direct-loss-modal-error');
  const batch = directLossBatches[Number(directLossBatchSelect.value)];
  if (!batch) {
    errorEl.textContent = 'Pick a product with remaining stock first.';
    errorEl.classList.add('visible');
    return;
  }
  const qty = Number(document.getElementById('direct-loss-quantity').value);
  const reason = document.getElementById('direct-loss-reason').value;

  const payload = {
    stock_batch_id: batch.id,
    quantity_delta: -Math.abs(qty),
    reason,
    notes: document.getElementById('direct-loss-notes').value.trim() || null,
  };
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
