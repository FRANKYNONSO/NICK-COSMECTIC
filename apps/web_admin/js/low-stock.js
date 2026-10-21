Auth.requireAuth();
Perms.applyNav();
const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

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

// Shared thumbnail markup for both the Low Stock / Out of Stock tables
// (renderStockRow) and the Expiry table (renderExpiryRow). A product
// with no image_url gets a muted photo-outline icon instead of a blank
// box, so it reads as "no photo uploaded" rather than looking like a
// broken/missing image.
function productThumbHtml(imageUrl, name) {
  if (imageUrl) {
    return `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(name)}" data-enlarge-image="${escapeHtml(imageUrl)}" style="width:56px;height:56px;object-fit:cover;border-radius:10px;cursor:zoom-in;border:1px solid var(--border);" />`;
  }
  return `<div title="No photo uploaded" style="width:56px;height:56px;border-radius:10px;background:var(--bg-soft,#f2f2f2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;">
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="var(--ink-faint,#9aa3b2)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="2"/>
      <circle cx="8.5" cy="9.5" r="1.5"/>
      <path d="M21 15l-5-5-9 9"/>
    </svg>
  </div>`;
}

function stockPill(qty, reorder) {
  if (qty <= 0) return `<span class="stock-pill out">Out of stock</span>`;
  if (qty <= reorder) return `<span class="stock-pill low">${qty} left · Low</span>`;
  return `<span class="stock-pill ok">${qty} in stock</span>`;
}

let branchCache = [];
let lowStockRowsCache = [];
let expiryRowsCache = [];

// Page-wide branch filter now comes from the Dashboard's global branch
// picker (BranchContext, api.js) instead of a local select here. The
// "restock-branch" field further down is a different, legitimate use
// of a branch select (which branch a restock lands in), so it stays.
async function loadBranches() {
  try {
    branchCache = await Api.listBranches();
  } catch (err) {
    showToast(err.message, true);
    return;
  }
  if (!branchCache.length) {
    document.querySelector('#low-stock-table').style.display = 'none';
    document.getElementById('empty-state').style.display = 'none';
    document.getElementById('no-branch-state').style.display = 'block';
    return;
  }
  BranchContext.renderBadge('branch-badge');
  // Restock modal's own branch select (spec section 5) -- pre-filled
  // from the active global branch (or the caller's own branch), but
  // always editable, since the page can be viewing "All branches"
  // while a single restock still has to land somewhere.
  document.getElementById('restock-branch').innerHTML =
    branchCache.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
  await loadLowStock();
  await loadExpiry();
}

window.addEventListener('branch-context-changed', () => { loadLowStock(); loadExpiry(); });

async function loadLowStock() {
  const branchId = BranchContext.get() || currentUser?.branch_id || undefined;

  const table = document.getElementById('low-stock-table');
  const tbody = document.getElementById('low-stock-tbody');
  const emptyState = document.getElementById('empty-state');
  const outTable = document.getElementById('out-of-stock-table');
  const outTbody = document.getElementById('out-of-stock-tbody');
  const outEmptyState = document.getElementById('out-of-stock-empty-state');
  emptyState.style.display = 'none';
  outEmptyState.style.display = 'none';
  table.style.display = '';
  outTable.style.display = '';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="7">Checking stock levels-</td></tr>';
  outTbody.innerHTML = '';

  try {
    const rows = await Api.lowStock(branchId);
    lowStockRowsCache = rows;
    // The server returns everything at-or-below reorder level, which
    // includes fully out-of-stock items (0 is always <= a reorder
    // level) -- split that single list into two sections here so
    // "still has a few left" and "completely finished" read as
    // distinct alerts instead of one blended table.
    const lowRows = rows.filter((r) => r.quantity_available > 0);
    const outRows = rows.filter((r) => r.quantity_available <= 0);
    document.getElementById('low-stock-tab-count').textContent = lowRows.length;
    document.getElementById('out-of-stock-tab-count').textContent = outRows.length;

    if (!lowRows.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
    } else {
      tbody.innerHTML = lowRows.map(renderStockRow).join('');
    }

    if (!outRows.length) {
      outTable.style.display = 'none';
      outEmptyState.style.display = 'block';
    } else {
      outTbody.innerHTML = outRows.map(renderStockRow).join('');
    }
  } catch (err) {
    tbody.innerHTML = '';
    outTbody.innerHTML = '';
    table.style.display = 'none';
    outTable.style.display = 'none';
    showToast(err.message, true);
  }
}

function renderStockRow(r) {
  const muteToggle = r.quantity_available <= 0
    ? `<button type="button" class="action-link action-link-mute" data-mute-toggle="${r.product_id}" data-muted="${r.out_of_stock_alert_muted}">${r.out_of_stock_alert_muted ? 'Unmute alert' : "Don't remind me"}</button>`
    : '';
  const thumb = productThumbHtml(r.image_url, r.product_name);
  return `
      <tr>
        <td class="thumb-cell">${thumb}</td>
        <td><span class="cell-strong">${escapeHtml(r.product_name)}</span></td>
        <td class="mono cell-medium">${r.barcode ? escapeHtml(r.barcode) : '-'}</td>
        <td>${r.size ? `<span class="category-badge">${escapeHtml(r.size)}</span>` : '<span style="color:var(--ink-faint)">-</span>'}</td>
        <td>${stockPill(r.quantity_available, r.reorder_level)}</td>
        <td>${r.reorder_level}</td>
        <td>${r.category_name ? `<span class="category-badge">${escapeHtml(r.category_name)}</span>` : '<span style="color:var(--ink-faint)">-</span>'}</td>
        <td>
          <div class="row-actions-links">
            ${Perms.can('manage_inventory') ? `<button type="button" class="action-link action-link-restock" data-restock="${r.product_id}">Restock</button>` : ''}
            ${muteToggle}
          </div>
        </td>
      </tr>`;
}

// Click-to-enlarge: any thumbnail rendered above (in either table) opens
// the shared lightbox at full size.
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
for (const id of ['low-stock-tbody', 'out-of-stock-tbody']) {
  document.getElementById(id).addEventListener('click', (e) => {
    const img = e.target.closest('[data-enlarge-image]');
    if (img) openImageLightbox(img.dataset.enlargeImage);
  });
}

document.getElementById('out-of-stock-tbody').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-mute-toggle]');
  if (!btn) return;
  const productId = btn.dataset.muteToggle;
  const currentlyMuted = btn.dataset.muted === 'true';
  try {
    if (currentlyMuted) {
      await Api.unmuteOutOfStockAlert(productId);
    } else {
      await Api.muteOutOfStockAlert(productId);
    }
    await loadLowStock();
  } catch (err) {
    showToast(err.message, true);
  }
});

// ---------------------------------------------------------------- Inline restock
//
// variant-category-system-spec.md section 5: restock right from this
// page instead of bouncing to Warehouse. Pulls straight from this
// branch's warehouse (same rule as Warehouse's Pull to store) -- "if
// it is not on the warehouse, it will not be able to restock" -- so
// the quantity is capped at what's actually sitting there, and cost/
// price fields update the product's own on-file prices going forward
// rather than a one-off batch override.

const restockBackdrop = document.getElementById('restock-modal-backdrop');
const restockError = document.getElementById('restock-error');
let activeRestockRow = null;

function openRestockModal(productId) {
  const row = lowStockRowsCache.find((r) => r.product_id === productId);
  if (!row) return;
  activeRestockRow = row;
  document.getElementById('restock-readout').textContent =
    `${row.product_name} · SKU ${row.sku}`;
  document.getElementById('restock-quantity').value = 1;
  document.getElementById('restock-cost').value = '';
  document.getElementById('restock-cost').placeholder = `Keep current (${formatNaira(row.cost_price_naira)})`;
  document.getElementById('restock-price').value = '';
  document.getElementById('restock-price').placeholder = `Keep current (${formatNaira(row.selling_price_naira)})`;
  const branchSelect = document.getElementById('restock-branch');
  const filterBranchId = BranchContext.get();
  if (filterBranchId) branchSelect.value = filterBranchId;
  else if (currentUser?.branch_id) branchSelect.value = currentUser.branch_id;
  restockError.classList.remove('visible');
  restockBackdrop.classList.add('visible');
  refreshRestockWarehouseAvailability();
}

// "If it is not on the warehouse, it will not be able to restock. It
// shouldn't be able to restock." -- restocking pulls straight from
// this branch's warehouse (same as Warehouse's Pull to store), so the
// modal shows what's actually available there and caps the quantity
// field at it, refreshed whenever the branch picker changes.
let restockAvailablePieces = 0;
async function refreshRestockWarehouseAvailability() {
  const readout = document.getElementById('restock-warehouse-readout');
  const qtyInput = document.getElementById('restock-quantity');
  const branchId = document.getElementById('restock-branch').value;
  if (!activeRestockRow || !branchId) {
    readout.textContent = '';
    return;
  }
  readout.textContent = 'Checking warehouse-';
  try {
    const stock = await Api.listWarehouseStock(branchId);
    const match = stock.find((s) => s.product_id === activeRestockRow.product_id);
    restockAvailablePieces = match?.quantity_pieces || 0;
    if (restockAvailablePieces > 0) {
      readout.style.color = '';
      readout.textContent = `${restockAvailablePieces} piece(s) available in this branch's warehouse.`;
      qtyInput.max = restockAvailablePieces;
      qtyInput.disabled = false;
      if (Number(qtyInput.value) > restockAvailablePieces) qtyInput.value = restockAvailablePieces;
    } else {
      readout.style.color = 'var(--danger)';
      readout.textContent = 'Nothing in this branch\u2019s warehouse for this product - push it to warehouse first.';
      qtyInput.disabled = true;
    }
  } catch (err) {
    readout.textContent = '';
  }
}
document.getElementById('restock-branch').addEventListener('change', refreshRestockWarehouseAvailability);

function closeRestockModal() {
  restockBackdrop.classList.remove('visible');
  activeRestockRow = null;
}

function formatNaira(amount) {
  return `â‚¦${Number(amount || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// The Restock button exists in BOTH the Low Stock and the Out of Stock tables.
// Only the first used to have a click handler, so Restock on the Out of Stock
// tab did nothing.
for (const restockTbodyId of ['low-stock-tbody', 'out-of-stock-tbody']) {
  document.getElementById(restockTbodyId).addEventListener('click', (e) => {
    const btn = e.target.closest('[data-restock]');
    if (btn) openRestockModal(btn.dataset.restock);
  });
}

document.getElementById('restock-modal-cancel').addEventListener('click', closeRestockModal);
restockBackdrop.addEventListener('click', (e) => { if (e.target === restockBackdrop) closeRestockModal(); });

document.getElementById('restock-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!activeRestockRow) return;
  const branchId = document.getElementById('restock-branch').value;
  const quantity = Number(document.getElementById('restock-quantity').value || 0);
  const costInputValue = document.getElementById('restock-cost').value;
  const priceInputValue = document.getElementById('restock-price').value;

  if (quantity > restockAvailablePieces) {
    restockError.textContent = `Only ${restockAvailablePieces} piece(s) available in the warehouse.`;
    restockError.classList.add('visible');
    return;
  }

  const submitBtn = document.getElementById('restock-submit');
  submitBtn.disabled = true;
  restockError.classList.remove('visible');
  try {
    if (costInputValue !== '' || priceInputValue !== '') {
      const priceUpdate = {};
      if (costInputValue !== '') priceUpdate.cost_price_naira = Number(costInputValue);
      if (priceInputValue !== '') priceUpdate.selling_price_naira = Number(priceInputValue);
      await Api.updateProduct(activeRestockRow.product_id, priceUpdate);
    }
    // Restocking pulls straight from this branch's warehouse -- same
    // move as Warehouse's "Pull to store", tracked in pieces.
    const result = await Api.pullToStore(branchId, {
      product_id: activeRestockRow.product_id,
      unit_label: 'piece',
      unit_count: quantity,
      pieces_per_unit: 1,
      notes: 'Restocked from Low Stock alert',
    });
    showToast(`Added ${result.quantity_pieces} piece(s) to the shop floor.`);
    closeRestockModal();
    await loadLowStock();
  } catch (err) {
    restockError.textContent = err.message;
    restockError.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});

// ------------------------------------------------------------- Expiry Alert

async function loadExpiry() {
  const branchId = BranchContext.get() || currentUser?.branch_id || undefined;
  const table = document.getElementById('expiry-table');
  const tbody = document.getElementById('expiry-tbody');
  const emptyState = document.getElementById('expiry-empty-state');
  try {
    expiryRowsCache = await Api.expiryAlerts(branchId);
    document.getElementById('expiry-tab-count').textContent = expiryRowsCache.length;
    if (!expiryRowsCache.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
    } else {
      table.style.display = '';
      emptyState.style.display = 'none';
      tbody.innerHTML = expiryRowsCache.map(renderExpiryRow).join('');
    }
  } catch (err) {
    tbody.innerHTML = '';
    table.style.display = 'none';
    showToast(err.message, true);
  }
  // Deep-link support (dashboard.html links to low-stock.html#expiry) is
  // now handled by switchAlertTab() at page load, which switches to the
  // Expiry tab directly instead of scrolling to it.
}

function renderExpiryRow(r) {
  const thumb = productThumbHtml(r.image_url, r.product_name);
  const urgent = r.days_until_expiry <= 30;
  let supplierCell = '<span style="color:var(--ink-faint)">-</span>';
  if (r.supplier_name) {
    const contactParts = [];
    if (r.supplier_phone) contactParts.push(`<a href="tel:${escapeHtml(r.supplier_phone)}">${escapeHtml(r.supplier_phone)}</a>`);
    if (r.supplier_email) contactParts.push(`<a href="mailto:${escapeHtml(r.supplier_email)}?subject=${encodeURIComponent('Return request: ' + r.product_name)}">${escapeHtml(r.supplier_email)}</a>`);
    supplierCell = `
      <div>${escapeHtml(r.supplier_name)}</div>
      ${contactParts.length ? `<div style="font-size:12px;color:var(--ink-faint);">${contactParts.join(' · ')}</div>` : ''}
      ${contactParts.length ? `<button type="button" class="action-link action-link-edit" data-request-return="${r.stock_batch_id}" style="margin-top:4px;">Request return</button>` : ''}`;
  }
  return `
      <tr>
        <td class="thumb-cell">${thumb}</td>
        <td><span class="cell-strong">${escapeHtml(r.product_name)}</span></td>
        <td class="mono cell-medium">${r.barcode ? escapeHtml(r.barcode) : '-'}</td>
        <td>${r.size ? `<span class="category-badge">${escapeHtml(r.size)}</span>` : '<span style="color:var(--ink-faint)">-</span>'}</td>
        <td><span class="cell-strong">${formatNaira(r.selling_price_naira)}</span></td>
        <td>${r.quantity_remaining}</td>
        <td><span class="stock-pill ${urgent ? 'out' : 'low'}">${new Date(r.expiry_date).toLocaleDateString('en-NG', { dateStyle: 'medium' })} · ${r.days_until_expiry}d</span></td>
        <td>${supplierCell}</td>
      </tr>`;
}

document.getElementById('expiry-tbody').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-request-return]');
  if (!btn) return;
  const row = expiryRowsCache.find((r) => r.stock_batch_id === btn.dataset.requestReturn);
  if (!row) return;
  const lines = [`Return request for: ${row.product_name} (SKU ${row.sku})`, `Quantity: ${row.quantity_remaining}`, `Expiry date: ${row.expiry_date}`];
  if (row.supplier_email) {
    window.location.href = `mailto:${row.supplier_email}?subject=${encodeURIComponent('Return request: ' + row.product_name)}&body=${encodeURIComponent(lines.join('\n'))}`;
  } else if (row.supplier_phone) {
    showToast(`Call ${row.supplier_name} at ${row.supplier_phone} to arrange the return.`);
  }
});

document.getElementById('expiry-tbody').addEventListener('click', (e) => {
  const img = e.target.closest('[data-enlarge-image]');
  if (img) openImageLightbox(img.dataset.enlargeImage);
});

// ------------------------------------------------------------ Alert tabs
//
// Three segmented tabs (Low Stock / Out of Stock / Expiry) so each
// alert type gets its own focused table instead of one long page that
// has to be scrolled through to find the section you want.
const alertTabsEl = document.getElementById('alert-tabs');
function switchAlertTab(tab) {
  document.querySelectorAll('#alert-tabs [data-alert-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.alertTab === tab);
  });
  document.getElementById('low-stock-section').style.display = tab === 'low-stock' ? '' : 'none';
  document.getElementById('out-of-stock-section').style.display = tab === 'out-of-stock' ? '' : 'none';
  document.getElementById('expiry-section').style.display = tab === 'expiry' ? '' : 'none';
}
alertTabsEl.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-alert-tab]');
  if (btn) switchAlertTab(btn.dataset.alertTab);
});
// Deep link support: dashboard.html links to low-stock.html#expiry.
const _hashTab = window.location.hash.replace('#', '');
switchAlertTab(['low-stock', 'out-of-stock', 'expiry'].includes(_hashTab) ? _hashTab : 'low-stock');

loadBranches();

// Auto-refresh so a restock or a POS/storefront sale that pushes an item
// into (or out of) low-stock shows up without a manual reload.
const LOW_STOCK_POLL_MS = 30000;
setInterval(() => {
  if (document.hidden) return;
  if (restockBackdrop.classList.contains('visible')) return;
  loadLowStock();
}, LOW_STOCK_POLL_MS);