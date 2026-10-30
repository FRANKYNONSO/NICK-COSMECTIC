Auth.requireAuth();
Perms.applyNav();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link')?.addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

// Staff can view the full catalog and check stock, but can't add, edit,
// or delete products -- server enforces this too (manage_products),
// this just keeps the button from being visibly clickable-then-403.
// Section 16: a Super Admin viewing "All Branches" also can't add/edit
// products (no specific branch to attach the starting-stock batch to)
// -- unlike the permission check above, this one has to react live to
// the branch picker changing, not just at page load.
function updateAddProductVisibility() {
  const btn = document.getElementById('new-product-btn');
  if (!btn) return;
  const allowed = Perms.can('manage_products') && !!BranchContext.get();
  btn.style.display = allowed ? '' : 'none';
}
updateAddProductVisibility();
window.addEventListener('branch-context-changed', updateAddProductVisibility);
// Deletion history is an audit trail -- same permission gate as the
// sales audit-log/reports pages (spec section 9: "administrator
// should be able to review these records").
if (!Perms.can('view_all_reports')) {
  document.getElementById('deletion-history-btn')?.remove();
}

// A cashier has neither manage_products nor manage_inventory, so they
// never see any button in the Actions column -- previously that just
// left an empty cell (spec section 23/34: "do not leave an empty
// Actions column... use the available space properly"). Remove the
// whole column instead, so their table reads Product | Barcode |
// Category | Price | Stock, Stock at the end. Super admin/branch
// manager keep the column exactly as before.
const showActionsColumn = Perms.can('manage_products') || Perms.can('manage_inventory');
if (!showActionsColumn) {
  document.querySelector('#products-table thead th:last-child')?.remove();
}
// "Select several items from the master catalog -> Add to Restock
// List" -- the person's explicit request for a checkbox-driven flow
// on top of the plain per-row buttons. Hidden entirely for anyone
// without manage_restock_list, same pattern as showActionsColumn above.
const canBulkAddRestock = Perms.can('manage_restock_list');
const restockSelected = new Set();
let selectMode = false;

// Generic in-app confirm modal -- replaces window.confirm() so this
// looks and behaves like the rest of the app's UI (and keeps working
// the same way once this is packaged/deployed live, unlike a native
// browser confirm() bar). Usage: if (!(await showConfirmModal('Delete this?'))) return;
function showConfirmModal(message, { title = 'Are you sure?', confirmLabel = 'Confirm' } = {}) {
  const backdrop = document.getElementById('confirm-modal-backdrop');
  document.getElementById('confirm-modal-title').textContent = title;
  document.getElementById('confirm-modal-message').textContent = message;
  const confirmBtn = document.getElementById('confirm-modal-confirm');
  const cancelBtn = document.getElementById('confirm-modal-cancel');
  confirmBtn.textContent = confirmLabel;
  backdrop.classList.add('visible');
  return new Promise((resolve) => {
    const cleanup = (result) => {
      backdrop.classList.remove('visible');
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(result);
    };
    const onConfirm = () => cleanup(true);
    const onCancel = () => cleanup(false);
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
  });
}

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

function formatDateTime(iso) {
  return new Date(iso).toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short', hour12: true, timeZone: 'Africa/Lagos' });
}

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 2 })}`;
}

let debounceTimer = null;
function debounce(fn, ms) {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fn, ms);
}

// ------------------------------------------------------------ Branches / categories

let branchCache = [];
let categoryCache = [];

async function loadFilters() {
  try {
    branchCache = await Api.listBranches();
    BranchContext.renderBadge('branch-badge');
  } catch (err) { /* non-fatal */ }

  try {
    categoryCache = await Api.listCategories();
    const categoryFilter = document.getElementById('category-filter');
    categoryFilter.innerHTML = '<option value="">All categories</option>' +
      categoryCache.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  } catch (err) { /* non-fatal */ }
}


// -------------------------------------------------------------- Products

function stockPill(qty, reorder) {
  if (qty === null || qty === undefined) return `<span class="stock-pill not-carried">Not carried at this branch</span>`;
  if (qty <= 0) return `<span class="stock-pill out">Out of stock</span>`;
  if (qty <= reorder) return `<span class="stock-pill low">${qty} left · Low</span>`;
  return `<span class="stock-pill ok">${qty} in stock</span>`;
}

// Category / brand / size live in a small muted line under the product
// name (InventoryPro style) instead of taking up their own columns.
function formatSizeText(sizeStr) {
  if (!sizeStr) return '';
  const parsed = parseSizeString(sizeStr);
  const measure = parsed.amount ? `${parsed.amount}${parsed.unit || ''}` : '';
  return [parsed.label, measure].filter(Boolean).join(' ');
}

function productMetaLine(p) {
  const parts = [p.category_name, p.brand_name, formatSizeText(p.size)].filter(Boolean);
  if (!parts.length) return '';
  return `<div class="cell-sub">${parts.map((x) => `<span>${escapeHtml(x)}</span>`).join('')}</div>`;
}

async function loadProducts() {
  const branchId = BranchContext.get();
  const categoryId = document.getElementById('category-filter').value || undefined;
  const search = document.getElementById('search-input').value.trim() || undefined;
  const lowStockOnly = document.getElementById('low-stock-only').checked;
  const outOfStockOnly = document.getElementById('out-of-stock-only').checked;
  const needsInvoiceOnly = document.getElementById('needs-invoice-only')?.checked || false;

  const tbody = document.getElementById('products-tbody');
  const table = document.getElementById('products-table');
  const emptyState = document.getElementById('empty-state');
  emptyState.style.display = 'none';
  table.style.display = '';
  const colCount = 5 + (showActionsColumn ? 1 : 0);
  if (!tbody.querySelector('tr:not(.loading-row)')) tbody.innerHTML = `<tr class="loading-row"><td colspan="${colCount}">Loading products…</td></tr>`;

  try {
    const products = await Api.listProducts({ branchId, categoryId, search, lowStockOnly, outOfStockOnly, needsInvoiceOnly });
    // PATCH4B-STOCK-SPLIT-LINE
    // Shelf / warehouse / total per product for people who manage inventory.
    // It is extra information only: if it cannot be loaded the table still shows.
    const stockSplit = new Map();
    if (branchId && Perms.can('manage_inventory')) {
      try {
        const split = await Api.goodsStockSummary({ branchId });
        (split || []).forEach((s) => stockSplit.set(s.product_id, s));
      } catch (err) { /* no grey line when the summary is not available */ }
    }
    if (!products.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
      return;
    }
    tbody.innerHTML = products.map((p) => `
      <tr>
        <td>
          <div class="img-wrap">
          ${canBulkAddRestock && branchId ? `<input type="checkbox" class="restock-select-check" aria-label="Select ${escapeHtml(p.name)}" data-product-id="${p.id}" data-product-name="${escapeHtml(p.name)}" ${restockSelected.has(p.id) ? 'checked' : ''} />` : ''}
          ${p.image_url
            ? `<img class="product-thumb-lg" src="${escapeHtml(p.image_url)}" alt="" data-enlarge-image="${escapeHtml(p.image_url)}" style="cursor:zoom-in;" />`
            : `<div class="product-thumb-lg placeholder">—</div>`}
          </div>
        </td>
        <td class="name-cell"><span class="cell-strong">${escapeHtml(p.name)}</span>${productMetaLine(p)}</td>
        <td class="mono cell-medium">${p.barcode ? escapeHtml(p.barcode) : '—'}</td>
        <td><span class="cell-strong">${formatNaira(p.price_naira)}</span></td>
        <td>${stockPill(p.quantity_available, p.reorder_level ?? 0)}${stockSplitLine(stockSplit.get(p.id))}</td>
        ${showActionsColumn ? `
        <td class="c-actions">
          <div class="row-actions-links">
            <div class="row-actions-line">
            ${Perms.can('manage_products') && branchId ? `
            <button class="action-link action-link-edit" data-edit="${p.id}">Edit</button>
            <button class="action-link action-link-delete" data-delete="${p.id}">Delete</button>
            ` : ''}
            </div>
            <div class="row-actions-line">
            ${Perms.can('manage_inventory') && branchId ? `<button class="action-link action-link-restock" data-restock="${p.id}" data-restock-name="${escapeHtml(p.name)}">Restock</button>` : ''}
            ${Perms.can('manage_inventory') && branchId ? `<button class="action-link action-link-history" data-shelf-limit="${p.id}" data-shelf-limit-name="${escapeHtml(p.name)}">Shelf default</button>` : ''}
            ${Perms.can('manage_inventory') && branchId ? `<button class="action-link action-link-history" data-shelf-reduce="${p.id}" data-shelf-reduce-name="${escapeHtml(p.name)}">Reduce stock</button>` : ''}
            ${Perms.can('manage_inventory') ? `<button class="action-link action-link-history" data-history="${p.id}" data-history-name="${escapeHtml(p.name)}">History</button>` : ''}
            ${p.needs_invoice && Perms.can('manage_inventory') ? `<button class="action-link action-link-invoice" data-invoice="${p.id}">Add to invoice</button>` : ''}
            </div>
          </div>
        </td>` : ''}
      </tr>`).join('');

    products.forEach((p) => {
      document.querySelector(`[data-edit="${p.id}"]`)?.addEventListener('click', () => openEditModal(p));
      document.querySelector(`[data-delete="${p.id}"]`)?.addEventListener('click', () => deleteProduct(p));
      document.querySelector(`[data-restock="${p.id}"]`)?.addEventListener('click', () => openRestockModal(p.id, p.name));
      document.querySelector(`[data-shelf-limit="${p.id}"]`)?.addEventListener('click', () => openShelfLimitModal(p.id, p.name));
      document.querySelector(`[data-history="${p.id}"]`)?.addEventListener('click', () => openHistoryModal(p.id, p.name));
      document.querySelector(`[data-shelf-reduce="${p.id}"]`)?.addEventListener('click', () => openShelfReduceModal(p.id, p.name, stockSplit.get(p.id)));
      document.querySelector(`[data-invoice="${p.id}"]`)?.addEventListener('click', () => openInvoicePrompt(p));
    });
    wireRestockCheckboxes();
  } catch (err) {
    tbody.innerHTML = '';
    table.style.display = 'none';
    emptyState.style.display = 'block';
    emptyState.innerHTML = `<div class="display">Couldn't load products</div><p>${escapeHtml(err.message)}</p>`;
    showToast(err.message, true);
  }
}

// -------------------------------------------- Restock List bulk-add
// The person's requested flow: tick several items in this master
// catalog, then push all of them onto the Restock List in one go,
// rather than one-at-a-time. Ticks persist in restockSelected across
// re-renders (filtering, searching) so switching a filter doesn't
// silently lose a selection -- same fix as the Restock List page's
// own checkbox-reset bug, applied here from the start.
function updateRestockBulkBar() {
  const bar = document.getElementById('restock-bulk-bar');
  if (!bar) return;
  const count = restockSelected.size;
  document.getElementById('restock-bulk-count').textContent = `${count} item${count === 1 ? '' : 's'} selected`;
  bar.classList.toggle('show', count > 0);
}

function wireRestockCheckboxes() {
  document.querySelectorAll('.restock-select-check').forEach((el) => {
    el.addEventListener('change', () => {
      if (el.checked) restockSelected.add(el.dataset.productId);
      else restockSelected.delete(el.dataset.productId);
      updateRestockBulkBar();
    });
  });
  updateRestockBulkBar();
}

// "Select items" button: checkboxes stay hidden until the person turns
// selection on, and turning it off clears whatever was ticked.
function setSelectMode(on) {
  selectMode = on;
  document.getElementById('products-table')?.classList.toggle('selecting', on);
  const btn = document.getElementById('select-mode-btn');
  if (btn) {
    btn.textContent = on ? 'Cancel selection' : 'Select items';
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
  if (!on) {
    restockSelected.clear();
    document.querySelectorAll('.restock-select-check').forEach((el) => { el.checked = false; });
    updateRestockBulkBar();
  }
}

const selectModeBtn = document.getElementById('select-mode-btn');
if (selectModeBtn) {
  if (!canBulkAddRestock) {
    selectModeBtn.remove();
  } else {
    selectModeBtn.style.display = '';
    selectModeBtn.addEventListener('click', () => {
      if (!selectMode && !BranchContext.get()) {
        showToast('Choose a branch first, then select items.', true);
        return;
      }
      setSelectMode(!selectMode);
    });
  }
}

document.getElementById('restock-bulk-add-btn')?.addEventListener('click', async () => {
  const branchId = BranchContext.get();
  if (!branchId || !restockSelected.size) return;
  const btn = document.getElementById('restock-bulk-add-btn');
  const productIds = [...restockSelected];
  btn.disabled = true;
  btn.textContent = 'Adding…';
  try {
    // is_manual: true -- these are explicit picks from the catalog,
    // not driven by a low-stock alert, so they stay on the Restock
    // List regardless of stock level (see restock_list.py docstring).
    await Promise.all(productIds.map((productId) =>
      Api.restockList.addItem({ productId, branchId, quantity: 1, unitLabel: 'pieces', isManual: true })
    ));
    showToast(`Added ${productIds.length} item${productIds.length === 1 ? '' : 's'} to the Restock List.`);
    setSelectMode(false);
  } catch (err) {
    showToast(err.message || 'Could not add those items.', true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Add to Restock List';
  }
});

// Sept 2026 change: a plain confirm() is enough now -- no reason
// picker, no notes. The delete is still recorded in Deletion history
// on the backend (with a generic reason) so it stays auditable and
// undoable via Restore, but the person deleting the item isn't asked
// anything beyond "are you sure?".
async function deleteProduct(p) {
  const ok = await showConfirmModal(
    `Delete "${p.name}" from inventory? You can restore it later from Deletion history.`,
    { title: 'Delete product', confirmLabel: 'Delete' }
  );
  if (!ok) return;
  try {
    await Api.deleteProduct(p.id, { reason: 'other', reason_note: null });
    showToast('Product deleted.');
    loadProducts();
  } catch (err) {
    showToast(err.message, true);
  }
}

// Deletion history (spec section 9) -- traceable, auditable record of
// every deleted product.
const deletionHistoryBackdrop = document.getElementById('deletion-history-backdrop');
document.getElementById('deletion-history-btn')?.addEventListener('click', async () => {
  const tbody = document.getElementById('deletion-history-tbody');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="7">Loading…</td></tr>';
  deletionHistoryBackdrop.classList.add('visible');
  try {
    const records = await Api.listInventoryDeletions();
    const reasonLabels = {
      damaged: 'Damaged', expired: 'Expired', lost: 'Lost', spoiled: 'Spoiled',
      incorrect_entry: 'Incorrect entry', duplicate: 'Duplicate product', other: 'Other',
    };
    tbody.innerHTML = records.length ? records.map((r) => `
      <tr>
        <td>${formatDateTime(r.created_at)}</td>
        <td>
          <div class="product-cell">
            ${r.image_url
              ? `<img class="product-thumb" src="${escapeHtml(r.image_url)}" alt="" data-enlarge-image="${escapeHtml(r.image_url)}" style="cursor:zoom-in;" />`
              : `<div class="product-thumb placeholder">—</div>`}
            <span>${escapeHtml(r.product_name)}${r.sku ? ` <span style="color:var(--ink-faint);">(${escapeHtml(r.sku)})</span>` : ''}</span>
          </div>
        </td>
        <td>${r.quantity_affected} ${escapeHtml(r.unit_of_measure || '')}</td>
        <td>${escapeHtml(reasonLabels[r.reason] || r.reason)}${r.reason_note ? `<div style="color:var(--ink-faint);font-size:11.5px;">${escapeHtml(r.reason_note)}</div>` : ''}</td>
        <td>${escapeHtml(r.deleted_by_name || 'Unknown')}</td>
        <td>${escapeHtml(r.branch_name || '—')}</td>
        <td>${r.is_loss ? 'Yes' : 'No'}</td>
        <td>${r.product_hard_deleted ? '<span style="color:var(--ink-faint);font-size:12px;">Permanently deleted</span>' : (r.product_id ? `${r.product_is_deleted ? `<button type="button" class="btn-ghost btn-sm" data-restore-product="${r.product_id}">Restore</button> ` : ''}<button type="button" class="btn-ghost btn-sm" data-hard-delete-product="${r.product_id}" style="color:var(--danger);">Delete permanently</button>` : '')}</td>
      </tr>`).join('') : '<tr><td colspan="8" style="text-align:center;color:var(--ink-faint);">No deletions recorded yet.</td></tr>';
    tbody.querySelectorAll('[data-restore-product]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        // Item 8: restore brings back identity fields only (image,
        // name, barcode, category, size) -- quantity-in-stock and
        // price are deliberately reset by the server, not carried
        // over, so the item comes back dormant until manually
        // restocked and re-priced.
        const okRestore = await showConfirmModal(
          'This will come back with its name/category/size, but at ₦0 price and 0 in stock -- you\'ll need to restock and re-price it separately.',
          { title: 'Restore this product?', confirmLabel: 'Restore' }
        );
        if (!okRestore) return;
        btn.disabled = true;
        btn.textContent = 'Restoring…';
        try {
          await Api.restoreProduct(btn.dataset.restoreProduct);
          showToast('Product restored -- restock and re-price it before it can be sold.');
          document.getElementById('deletion-history-btn').click();
          if (typeof loadProducts === 'function') loadProducts();
        } catch (err) {
          showToast(err.message, true);
          btn.disabled = false;
          btn.textContent = 'Restore';
        }
      });
    });
    // Permanent delete (Sept 2026 change) -- removes the item from the
    // inventory list for good. Never touches stock batches, sales,
    // invoices, or "total invested" figures -- those all reference the
    // product row by id independently and keep working exactly as
    // before; this only stops the row from ever being offered as
    // Restore/re-edit again. Adding the item back later is a fresh
    // Add Product (a brand-new row).
    tbody.querySelectorAll('[data-hard-delete-product]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const okHardDelete = await showConfirmModal(
          'This cannot be undone. If the item is currently active, it will be removed from inventory too. Past invoices, sales, and total-invested figures are not affected -- you can always add the item back later through Add Product.',
          { title: 'Permanently delete this item?', confirmLabel: 'Delete permanently' }
        );
        if (!okHardDelete) return;
        btn.disabled = true;
        btn.textContent = 'Deleting…';
        try {
          await Api.hardDeleteProduct(btn.dataset.hardDeleteProduct);
          showToast('Product permanently removed from the inventory list.');
          document.getElementById('deletion-history-btn').click();
        } catch (err) {
          showToast(err.message, true);
          btn.disabled = false;
          btn.textContent = 'Delete permanently';
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--danger);">${escapeHtml(err.message)}</td></tr>`;
  }
});
document.getElementById('deletion-history-close').addEventListener('click', () => deletionHistoryBackdrop.classList.remove('visible'));

window.addEventListener('branch-context-changed', loadProducts);
document.getElementById('category-filter')?.addEventListener('change', loadProducts);
document.getElementById('low-stock-only')?.addEventListener('change', () => {
  if (document.getElementById('low-stock-only').checked) document.getElementById('out-of-stock-only').checked = false;
  loadProducts();
});
document.getElementById('out-of-stock-only')?.addEventListener('change', () => {
  if (document.getElementById('out-of-stock-only').checked) document.getElementById('low-stock-only').checked = false;
  loadProducts();
});
document.getElementById('needs-invoice-only')?.addEventListener('change', loadProducts);
document.getElementById('search-input')?.addEventListener('input', () => debounce(loadProducts, 300));

// ----------------------------------------------------- Create / Edit modal

const modalBackdrop = document.getElementById('product-modal-backdrop');
const productForm = document.getElementById('product-form');
const modalError = document.getElementById('modal-error');
const nameSuggestions = document.getElementById('p-name-suggestions');
const imagePreview = document.getElementById('p-image-preview');
const imageFileInput = document.getElementById('p-image-file');
const initialQtyInput = document.getElementById('p-initial-qty');

let brandCache = [];

function resetModalExtras() {
  nameSuggestions.style.display = 'none';
  nameSuggestions.innerHTML = '';
  imagePreview.style.display = 'none';
  imagePreview.src = '';
  imageFileInput.value = '';
  stopBarcodeScan();
}

function slugifySku(text) {
  return (text || '')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '-')
    .replace(/(^-+|-+$)/g, '')
    .slice(0, 40);
}

// Category-based size suggestions -- once a category is picked, offer
// any sizes already used by other products in it as one-click options
// (Big/Medium/Small quick-picks and free-text entry both still work
// unchanged alongside this).
async function loadCategorySizeSuggestions() {
  const categoryId = categoryHidden.value;
  const container = document.getElementById('p-size-category-suggestions');
  if (!categoryId) {
    container.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  try {
    const sizes = await Api.listCategorySizes(categoryId);
    if (!sizes.length) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }
    container.innerHTML = '<p style="font-size:11px;color:var(--ink-faint);margin:0 0 6px;">Used before in this category:</p>' +
      sizes.map((s) => `<button type="button" class="btn-ghost btn-sm" data-size-suggestion="${escapeHtml(s)}" style="margin:0 6px 6px 0;">${escapeHtml(s)}</button>`).join('');
    container.style.display = 'block';
    container.querySelectorAll('[data-size-suggestion]').forEach((btn) => {
      btn.addEventListener('click', () => applySizeValue(btn.dataset.sizeSuggestion));
    });
  } catch (err) { /* non-fatal -- suggestions are a convenience, not required */ }
}
document.getElementById('p-category')?.addEventListener('change', loadCategorySizeSuggestions);

// Category field is a searchable dropdown, same UX as the Product
// name field above (spec section 14) -- type to filter the existing
// category list, click to select. #p-category stays a hidden input
// holding the id, so every other existing reader of "#p-category".value
// keeps working unchanged; selecting here just sets it and dispatches
// 'change'.
const categoryInput = document.getElementById('p-category-input');
const categoryHidden = document.getElementById('p-category');
const categoryDropdown = document.getElementById('p-category-dropdown');

function selectCategory(category) {
  categoryInput.value = category ? category.name : '';
  categoryHidden.value = category ? category.id : '';
  categoryHidden.dispatchEvent(new Event('change'));
  categoryDropdown.style.display = 'none';
  categoryDropdown.innerHTML = '';
}

function renderCategoryDropdown(query) {
  const matches = categoryCache.filter((c) => c.name.toLowerCase().includes(query.toLowerCase()));
  const rowsHtml = matches.slice(0, 8).map((c) => `<div class="item-autocomplete-row" data-category-id="${c.id}">${escapeHtml(c.name)}</div>`).join('');
  categoryDropdown.innerHTML = rowsHtml || `<div class="item-autocomplete-empty">No matching categories -- try Quick Add Category below.</div>`;
  categoryDropdown.style.display = '';
  categoryDropdown.querySelectorAll('[data-category-id]').forEach((el) => {
    const category = matches.find((c) => c.id === el.dataset.categoryId);
    el.addEventListener('mousedown', (e) => { e.preventDefault(); if (category) selectCategory(category); });
  });
}
categoryInput?.addEventListener('input', () => {
  categoryHidden.value = '';
  const query = categoryInput.value.trim();
  if (!query) { categoryDropdown.style.display = 'none'; return; }
  renderCategoryDropdown(query);
});
categoryInput?.addEventListener('focus', () => {
  if (categoryInput.value.trim()) renderCategoryDropdown(categoryInput.value.trim());
});
categoryInput?.addEventListener('blur', () => setTimeout(() => { categoryDropdown.style.display = 'none'; }, 150));

// "Also list under" (extra categories) was removed from the Add
// Product form (September 2026 change spec section 5) -- a product
// keeps whatever extra_category_ids it already has from before this
// change, it just can't be set/edited from here anymore.

// Quick Add Category (spec section 14/15) -- create a new category
// without leaving the product form, and it becomes selectable
// immediately (categoryCache is refreshed, then auto-selected here).
document.getElementById('p-quick-add-category')?.addEventListener('click', async () => {
  const name = await showPromptModal('', { title: 'New category name', confirmLabel: 'Add' });
  if (!name || !name.trim()) return;
  // Don't create a duplicate if one already exists with this name.
  const existing = categoryCache.find((c) => c.name.toLowerCase() === name.trim().toLowerCase());
  if (existing) {
    selectCategory(existing);
    showToast(`"${existing.name}" already exists -- selected it.`);
    return;
  }
  try {
    const category = await Api.createCategory({ name: name.trim() });
    await loadFilters();
    selectCategory(category);
    showToast(`Category "${category.name}" added.`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// --------------------------------------------------------------- Brand
// Item 5: Brand is now a plain optional field -- it no longer feeds
// into building the product name (the Byproduct field it used to
// combine with was removed entirely; the bug where typing a byproduct
// silently overwrote a manually-typed name is gone along with it).
// Still a searchable-dropdown-with-quick-add UX, same as Category.

const brandInput = document.getElementById('p-brand-input');
const brandHidden = document.getElementById('p-brand');
const brandDropdown = document.getElementById('p-brand-dropdown');
const nameInput = document.getElementById('p-name');

async function loadBrands() {
  try {
    brandCache = await Api.listBrands();
  } catch (err) { /* non-fatal */ }
}

function selectBrand(brand) {
  brandInput.value = brand ? brand.name : '';
  brandHidden.value = brand ? brand.id : '';
  brandDropdown.style.display = 'none';
  brandDropdown.innerHTML = '';
}

function renderBrandDropdown(query) {
  const matches = brandCache.filter((b) => b.name.toLowerCase().includes(query.toLowerCase()));
  const rowsHtml = matches.slice(0, 8).map((b) => `<div class="item-autocomplete-row" data-brand-id="${b.id}">${escapeHtml(b.name)}</div>`).join('');
  brandDropdown.innerHTML = rowsHtml || `<div class="item-autocomplete-empty">No matching brands -- try Quick Add Brand below.</div>`;
  brandDropdown.style.display = '';
  brandDropdown.querySelectorAll('[data-brand-id]').forEach((el) => {
    const brand = matches.find((b) => b.id === el.dataset.brandId);
    el.addEventListener('mousedown', (e) => { e.preventDefault(); if (brand) selectBrand(brand); });
  });
}
brandInput?.addEventListener('input', () => {
  brandHidden.value = '';
  const query = brandInput.value.trim();
  if (!query) { brandDropdown.style.display = 'none'; return; }
  renderBrandDropdown(query);
});
brandInput?.addEventListener('focus', () => {
  if (brandInput.value.trim()) renderBrandDropdown(brandInput.value.trim());
});
brandInput?.addEventListener('blur', () => setTimeout(() => { brandDropdown.style.display = 'none'; }, 150));
nameInput?.addEventListener('input', () => { nameInput.dataset.userEdited = 'true'; });
const skuElement = document.getElementById('p-sku');
skuElement?.addEventListener('input', () => { skuElement.dataset.userEdited = 'true'; });

document.getElementById('p-quick-add-brand')?.addEventListener('click', async () => {
  const name = await showPromptModal('', { title: 'New brand name', confirmLabel: 'Add' });
  if (!name || !name.trim()) return;
  const existing = brandCache.find((b) => b.name.toLowerCase() === name.trim().toLowerCase());
  if (existing) {
    selectBrand(existing);
    showToast(`"${existing.name}" already exists -- selected it.`);
    return;
  }
  try {
    const brand = await Api.createBrand({ name: name.trim() });
    await loadBrands();
    selectBrand(brand);
    showToast(`Brand "${brand.name}" added.`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// ------------------------------------------------------- Starting stock
//
// Section 2: always visible and required, never gated behind a
// checkbox, one quantity for the one product (no more per-variant
// list). Still calls the same Warehouse "receive stock" API you'd
// otherwise use later.

function refreshInitialStockAvailability() {
  // Starting stock always belongs to whichever branch is currently
  // active app-wide (spec section 5) -- never a branch picked inside
  // this form. If the header is on "All branches" there's no single
  // branch for opening stock to belong to, so the field is simply
  // unavailable until a specific branch is chosen from the Dashboard.
  const branchId = BranchContext.get();
  const hint = document.getElementById('p-initial-branch-hint');
  hint.textContent = branchId ? BranchContext.getName() : 'All branches (pick a branch on the Dashboard first)';
  initialQtyInput.disabled = !branchId;
}
window.addEventListener('branch-context-changed', refreshInitialStockAvailability);

// Item 4/5: toggles which fields show for Add vs Edit -- see the
// data-add-only / data-edit-only markers on inventory.html's product
// modal. Add gets the full form; Edit is deliberately minimal (price,
// reorder level, expiration, photo, plus a category *correction*
// option) since brand/size/SKU/barcode/extra-categories were all set
// once at creation and aren't meant to be casually changed here.
function applyProductModalMode(mode) {
  document.querySelectorAll('#product-form [data-add-only]').forEach((el) => {
    el.style.display = mode === 'add' ? '' : 'none';
  });
  document.querySelectorAll('#product-form [data-edit-only]').forEach((el) => {
    el.style.display = mode === 'edit' ? '' : 'none';
  });

}

// "Add to catalog only" draft-mode feature removed (dictated request).

function openCreateModal() {
  productForm.reset();
  document.getElementById('product-id').value = '';
  document.getElementById('product-modal-title').textContent = 'Add product';
  document.getElementById('product-modal-sub').textContent =
    'Type the full product name directly (e.g. "Nivea Cocoa Butter Lotion") -- brand is optional and only helps with search/filtering.';
  applyProductModalMode('add');
  nameInput.dataset.userEdited = '';
  skuElement.dataset.userEdited = '';
  selectBrand(null);
  resetSizeUI();
  document.getElementById('p-expiration').value = '';
  selectCategory(null);
  initialQtyInput.value = 0;
  document.getElementById('p-initial-stock-wrap').style.display = '';

  document.getElementById('p-wholesale-price').value = '';
  document.getElementById('p-wholesale-min-qty').value = '';
  // PATCH5A-PACK-SIZES
  document.getElementById('p-pack-carton').value = '';
  document.getElementById('p-pack-bag').value = '';
  document.getElementById('p-pack-roll').value = '';

  refreshInitialStockAvailability();
  resetModalExtras();
  document.getElementById('p-hidden-from-staff-wrap').style.display = currentUser?.role === 'super_admin' ? 'block' : 'none';
  document.getElementById('p-hidden-from-staff').checked = false;
  modalError.classList.remove('visible');
  modalBackdrop.classList.add('visible');
}

async function openEditModal(p) {
  productForm.reset();
  document.getElementById('product-id').value = p.id;
  document.getElementById('product-modal-title').textContent = 'Edit product';
  document.getElementById('product-modal-sub').textContent =
    'Quick edits only -- price, reorder level, expiration date, and photo. Name, brand, size, and SKU were set when the product was created.';
  applyProductModalMode('edit');
  nameInput.value = p.name;
  nameInput.dataset.userEdited = 'true'; // don't clobber an existing name on edit
  document.getElementById('p-name-static').textContent = p.name;
  selectBrand(p.brand_id ? brandCache.find((b) => b.id === p.brand_id) : null);
  document.getElementById('p-sku').value = p.sku || '';
  document.getElementById('p-barcode').value = p.barcode || '';
  document.getElementById('p-category').value = p.category_id || '';
  document.getElementById('p-category-input').value = p.category || '';
  document.getElementById('p-category-static').textContent = p.category || 'Uncategorized';
  // Starts collapsed to the read-only readout above -- "Change
  // category" reveals the searchable picker for a genuine correction
  // (see the p-category-correct-btn handler below). Reset both back
  // to their collapsed state each time this modal opens, in case a
  // previous edit session left the picker revealed.
  document.getElementById('p-category-input').closest('.field').style.display = 'none';
  document.getElementById('p-category-correct-btn').style.display = '';
  // Item 5: dropdown options are now uppercase (BIG/MEDIUM/SMALL) but
  // a product created before this change may still have the old
  // mixed-case value stored -- uppercase it here for display only, so
  // the edit form's selection still matches instead of silently
  // falling back to "No size". The stored value itself isn't touched
  // unless the form is actually re-saved.
  applySizeValue(p.size || '');
  document.getElementById('p-price').value = p.selling_price_naira ?? '';
  document.getElementById('p-wholesale-price').value = p.wholesale_price_naira ?? '';
  document.getElementById('p-wholesale-min-qty').value = p.wholesale_min_quantity ?? '';
  document.getElementById('p-reorder').value = p.reorder_level ?? 5;
  // PATCH5A-PACK-SIZES
  document.getElementById('p-pack-carton').value = p.default_pieces_per_carton ?? '';
  document.getElementById('p-pack-bag').value = p.default_pieces_per_bag ?? '';
  document.getElementById('p-pack-roll').value = p.default_pieces_per_roll ?? '';
  document.getElementById('p-expiration').value = p.expiration_date || '';
  resetModalExtras();
  // hidden_from_staff (feature spec 2) -- only a super_admin can see or
  // change this, both here and enforced server-side in
  // PATCH /catalog/products/{id}.
  const hiddenWrap = document.getElementById('p-hidden-from-staff-wrap');
  hiddenWrap.style.display = currentUser?.role === 'super_admin' ? 'block' : 'none';
  document.getElementById('p-hidden-from-staff').checked = !!p.hidden_from_staff;
  if (p.image_url) {
    imagePreview.src = p.image_url;
    imagePreview.style.display = '';
  }
  modalError.classList.remove('visible');
  modalBackdrop.classList.add('visible');

  // Starting stock only applies at creation time -- once a product
  // exists, adding more stock is Warehouse -> Receive stock, so this
  // field is hidden rather than repurposed for topping up.
  document.getElementById('p-initial-stock-wrap').style.display = 'none';
}

// The category picker (search input + dropdown + quick-add) is an
// data-add-only block, so it's hidden by default in Edit mode even
// though "Change category" needs to reveal exactly that block --
// this un-hides just that one element without touching the rest of
// the add-only fields around it.
document.getElementById('p-category-correct-btn')?.addEventListener('click', (e) => {
  const pickerField = document.getElementById('p-category-input').closest('.field');
  if (pickerField) pickerField.style.display = '';
  e.target.style.display = 'none';
});

document.getElementById('new-product-btn')?.addEventListener('click', openCreateModal);
document.getElementById('product-modal-cancel')?.addEventListener('click', () => {
  stopBarcodeScan();
  modalBackdrop.classList.remove('visible');
});

// Item 5: "quick add size" -- add a free-typed value as a new <option>
// (kept, so it's remembered for the rest of this session) and select
// it. The backend no longer restricts size to a fixed set (see
// alembic aa11bb22cc33), so any text entered here is valid to save.
// ------------------------------------------- Size (label + measurement)
//
// Sept 2026 change: a size now combines a customer-facing LABEL
// (Big/Medium/Small, or a custom label) with an OPTIONAL precise
// measurement (e.g. 500g, 750ml) -- both can be set at once, since a
// "Big" item may come in more than one exact weight/volume. Still
// stored as a single free-text string on the product (#p-size,
// unchanged on the backend) in the form "<LABEL> <AMOUNT><UNIT>" (or
// just the label, or just the measurement) -- parseSizeString() below
// pulls it back apart wherever it needs to be redisplayed or edited.
const sizeHiddenInput = document.getElementById('p-size');

let unitRows = [];   // every measuring unit saved on the server
const UNIT_GROUP_ORDER = ['Weight', 'Volume', 'Length', 'Custom'];
// Only used if the server can't be reached, so the form still works.
const FALLBACK_UNITS = [
  ['mg', 'Weight'], ['g', 'Weight'], ['kg', 'Weight'],
  ['ml', 'Volume'], ['cl', 'Volume'], ['L', 'Volume'],
  ['mm', 'Length'], ['cm', 'Length'], ['m', 'Length'],
];

function sizeUnitNames() { return unitRows.map((u) => u.name); }

function defaultSizeUnit() {
  const names = sizeUnitNames();
  return names.includes('g') ? 'g' : (names[0] || '');
}

function normalizeSizeUnit(raw) {
  const lower = (raw || '').toLowerCase();
  return sizeUnitNames().find((u) => u.toLowerCase() === lower) || defaultSizeUnit();
}

function sizeUnitRegex() {
  const all = sizeUnitNames()
    .map((u) => u.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .sort((a, b) => b.length - a.length);
  if (!all.length) return null;
  return new RegExp('^(.*?)\\s*(\\d+(?:\\.\\d+)?)\\s*(' + all.join('|') + ')$', 'i');
}

function parseSizeString(value) {
  const trimmed = (value || '').trim();
  if (!trimmed) return { label: '', amount: '', unit: defaultSizeUnit() };
  const re = sizeUnitRegex();
  const match = re ? trimmed.match(re) : null;
  if (match) {
    return { label: match[1].trim(), amount: match[2], unit: normalizeSizeUnit(match[3]) };
  }
  return { label: trimmed, amount: '', unit: defaultSizeUnit() };
}

// ---- Measuring units (all saved on the server, any can be removed) ----
function renderUnitSelect() {
  const sel = document.getElementById('p-size-measurement-unit');
  if (!sel) return;
  const keep = sel.value;
  sel.innerHTML = '';
  if (!unitRows.length) {
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'No units';
    sel.appendChild(none);
    sel.value = '';
    return;
  }
  const rank = (g) => { const i = UNIT_GROUP_ORDER.indexOf(g); return i === -1 ? 99 : i; };
  const groups = {};
  unitRows.forEach((u) => {
    const g = u.group_name || 'Custom';
    (groups[g] = groups[g] || []).push(u);
  });
  Object.keys(groups).sort((a, b) => rank(a) - rank(b)).forEach((g) => {
    const og = document.createElement('optgroup');
    og.label = g;
    groups[g].forEach((u) => {
      const opt = document.createElement('option');
      opt.value = u.name;
      opt.textContent = u.name;
      og.appendChild(opt);
    });
    sel.appendChild(og);
  });
  sel.value = sizeUnitNames().includes(keep) ? keep : defaultSizeUnit();
}

function renderUnitManagePanel() {
  const panel = document.getElementById('p-unit-manage-panel');
  if (!panel) return;
  if (!unitRows.length) {
    panel.innerHTML = '<p style="font-size:12px;color:var(--ink-faint);margin:0;">No units yet. Use + Add unit.</p>';
    return;
  }
  panel.innerHTML = unitRows.map((u) =>
    `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;">` +
    `<span>${escapeHtml(u.name)} <span style="font-size:11px;color:var(--ink-faint);">${escapeHtml(u.group_name || '')}</span></span>` +
    (u.id ? `<button type="button" class="btn-ghost btn-sm" data-unit-remove="${escapeHtml(String(u.id))}">Remove</button>` : '') +
    `</div>`
  ).join('');
}

async function loadUnits() {
  try {
    unitRows = await Api.listUnits();
  } catch (err) {
    unitRows = FALLBACK_UNITS.map(([name, group_name]) => ({ id: null, name, group_name }));
  }
  renderUnitSelect();
  renderUnitManagePanel();
  syncCombinedSize();
}

document.getElementById('p-unit-add-btn')?.addEventListener('click', async () => {
  const raw = await showPromptModal('', { title: 'New measuring unit (letters only, e.g. sachet)', confirmLabel: 'Add' });
  if (!raw || !raw.trim()) return;
  const name = raw.trim();
  if (!/^[A-Za-z]{1,12}$/.test(name)) {
    showToast('Use letters only, up to 12 characters (e.g. sachet).', true);
    return;
  }
  try {
    const unit = await Api.createUnit({ name });
    await loadUnits();
    document.getElementById('p-size-measurement-unit').value = unit.name;
    syncCombinedSize();
    showToast(`Unit "${unit.name}" added.`);
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('p-unit-manage-btn')?.addEventListener('click', () => {
  const panel = document.getElementById('p-unit-manage-panel');
  panel.style.display = panel.style.display === 'none' ? '' : 'none';
});

document.getElementById('p-unit-manage-panel')?.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-unit-remove]');
  if (!btn) return;
  const row = unitRows.find((u) => String(u.id) === btn.dataset.unitRemove);
  if (!row) return;
  if (!(await showConfirmModal(`Remove the unit "${row.name}"?`, { title: 'Remove unit', confirmLabel: 'Remove' }))) return;
  try {
    try {
      await Api.deleteUnit(row.id);
    } catch (err) {
      if (!/still use/i.test(err.message || '')) throw err;
      const force = await showConfirmModal(
        `${err.message} Remove "${row.name}" anyway? Their size text stays (e.g. 500${row.name}) but will show as a plain label.`,
        { title: 'Unit is in use', confirmLabel: 'Remove anyway' }
      );
      if (!force) return;
      await Api.deleteUnit(row.id, true);
    }
    await loadUnits();
    showToast(`Unit "${row.name}" removed.`);
  } catch (err) {
    showToast(err.message, true);
  }
});

function formatSizeCell(sizeStr) {
  if (!sizeStr) return '<span style="color:var(--ink-faint)">—</span>';
  const parsed = parseSizeString(sizeStr);
  const wrapOpen = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:6px;">';
  const wrapClose = '</div>';
  if (parsed.label && parsed.amount) {
    return `${wrapOpen}<span class="category-badge">${escapeHtml(parsed.label)}</span><span style="font-size:13px;font-weight:700;color:var(--success);">${escapeHtml(parsed.amount)}${escapeHtml(parsed.unit)}</span>${wrapClose}`;
  }
  if (parsed.label) {
    return `${wrapOpen}<span class="category-badge">${escapeHtml(parsed.label)}</span>${wrapClose}`;
  }
  return `${wrapOpen}<span style="font-size:13px;font-weight:700;color:var(--success);">${escapeHtml(parsed.amount)}${escapeHtml(parsed.unit)}</span>${wrapClose}`;
}

function syncCombinedSize() {
  const select = document.getElementById('p-size-label-select');
  let label = select.value;
  if (label === '__custom__') {
    label = document.getElementById('p-size-label-custom').value.trim();
  }
  const amount = document.getElementById('p-size-measurement-amount').value.trim();
  const unit = document.getElementById('p-size-measurement-unit').value;
  const measurement = amount && unit ? `${amount}${unit}` : '';
  sizeHiddenInput.value = [label, measurement].filter(Boolean).join(' ').trim();
}

// Applies a resolved size string (e.g. from Edit, or a category
// suggestion) back into the label + measurement fields.
function applySizeValue(value) {
  const parsed = parseSizeString(value);
  const select = document.getElementById('p-size-label-select');
  const customInput = document.getElementById('p-size-label-custom');
  const presetLabels = ['BIG', 'MEDIUM', 'SMALL'];
  const upperLabel = parsed.label.toUpperCase();
  if (!parsed.label) {
    select.value = '';
    customInput.style.display = 'none';
    customInput.value = '';
  } else if (presetLabels.includes(upperLabel)) {
    select.value = upperLabel;
    customInput.style.display = 'none';
    customInput.value = '';
  } else {
    select.value = '__custom__';
    customInput.style.display = '';
    customInput.value = parsed.label;
  }
  document.getElementById('p-size-measurement-amount').value = parsed.amount;
  document.getElementById('p-size-measurement-unit').value = parsed.unit;
  syncCombinedSize();
}

function resetSizeUI() {
  document.getElementById('p-size-label-select').value = '';
  document.getElementById('p-size-label-custom').style.display = 'none';
  document.getElementById('p-size-label-custom').value = '';
  document.getElementById('p-size-measurement-amount').value = '';
  document.getElementById('p-size-measurement-unit').value = defaultSizeUnit();
  sizeHiddenInput.value = '';
}

document.getElementById('p-size-label-select')?.addEventListener('change', () => {
  const isCustom = document.getElementById('p-size-label-select').value === '__custom__';
  document.getElementById('p-size-label-custom').style.display = isCustom ? '' : 'none';
  syncCombinedSize();
});
document.getElementById('p-size-label-custom')?.addEventListener('input', syncCombinedSize);
document.getElementById('p-size-measurement-amount')?.addEventListener('input', syncCombinedSize);
document.getElementById('p-size-measurement-unit')?.addEventListener('change', syncCombinedSize);

// --------------------------------------------------- Product name typeahead
//
// Helps avoid accidentally creating a duplicate product when what's
// actually wanted is to restock something already in the catalog.
// Picking a suggestion switches the form into editing that existing
// product (same as clicking "Edit" on its row) rather than just filling
// in the name field, since filling only the name would still create a
// duplicate on save.

// Sept 2026 dictated request: product names should default to Title
// Case (first letter of each word capitalized, rest lowercase) --
// e.g. typing "ditto liquid" becomes "Ditto Liquid". Applied when the
// field loses focus (not on every keystroke, so the cursor doesn't
// jump around mid-word), and re-applied as a safety net right before
// save in case the field is bypassed (e.g. paste-and-immediately-submit).
function toProductNameCase(str) {
  return str.replace(/\S+/g, (word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase());
}
nameInput.addEventListener('blur', () => {
  nameInput.value = toProductNameCase(nameInput.value);
});

document.getElementById('p-name')?.addEventListener('input', (e) => {
  const query = e.target.value.trim();
  // Auto-fill a suggested SKU from the name for brand-new products only
  // -- never overwrite a SKU that's already saved on an existing one,
  // and never overwrite one the user has manually started editing.
  if (!document.getElementById('product-id').value && skuElement.dataset.userEdited !== 'true') {
    document.getElementById('p-sku').value = slugifySku(query);
  }
  if (query.length < 2 || document.getElementById('product-id').value) {
    nameSuggestions.style.display = 'none';
    return;
  }
  debounce(async () => {
    try {
      const [matches, templates] = await Promise.all([
        Api.listProducts({ search: query }),
        Api.suggestProductTemplates(query).catch(() => []),
      ]);
      // Any live product with this near-exact name already exists --
      // don't also suggest its template, since opening the live
      // product for editing is the more useful of the two actions.
      const matchedNames = new Set(matches.map((m) => m.name.toLowerCase()));
      const templateMatches = templates.filter((t) => !matchedNames.has(t.name.toLowerCase()));
      if (!matches.length && !templateMatches.length) {
        nameSuggestions.style.display = 'none';
        return;
      }
      const productRows = matches.slice(0, 6).map((m) => `
        <div class="cart-row" style="padding:8px 10px;display:flex;align-items:center;justify-content:space-between;gap:8px;">
          <div class="info" style="cursor:pointer;flex:1;" data-suggest="${m.id}"><div class="name">${escapeHtml(m.name)}${m.size ? ` <span class="category-badge" style="font-size:11px;">${escapeHtml(m.size)}</span>` : ''}</div><div class="unit">${m.barcode ? escapeHtml(m.barcode) : 'No barcode'} · ${formatNaira(m.price_naira)}</div></div>
          <button type="button" class="btn-ghost btn-sm" data-suggest-edit="${m.id}" style="flex:none;">Edit this one</button>
        </div>`).join('');
      // Item 8: a template match offers to pre-fill category + size
      // options -- it's a starting point, not an existing product, so
      // clicking it does NOT open an edit modal (nothing to edit yet).
      const templateRows = templateMatches.slice(0, 4).map((t) => `
        <div class="cart-row" style="cursor:pointer;padding:8px 10px;" data-suggest-template="${t.id}">
          <div class="info"><div class="name">${escapeHtml(t.name)} <span class="category-badge" style="font-size:11px;">Template</span></div><div class="unit">${t.primary_category_name ? escapeHtml(t.primary_category_name) : 'No category set'}${t.sizes.length ? ` · ${escapeHtml(t.sizes.join(', '))}` : ''}</div></div>
        </div>`).join('');
      nameSuggestions.innerHTML = productRows + templateRows;
      nameSuggestions.style.display = 'block';
      matches.slice(0, 6).forEach((m) => {
        nameSuggestions.querySelector(`[data-suggest="${m.id}"]`)?.addEventListener('click', () => {
          nameSuggestions.style.display = 'none';
          nameInput.value = m.name;
          nameInput.dataset.userEdited = 'true';
          selectBrand(m.brand_id ? brandCache.find((b) => b.id === m.brand_id) : null);
          selectCategory(m.category_id ? categoryCache.find((c) => c.id === m.category_id) : null);
          if (m.size) {
            const sizeSelect = document.getElementById('p-size');
            let opt = [...sizeSelect.options].find((o) => o.value === m.size);
            if (!opt) {
              opt = document.createElement('option');
              opt.value = m.size;
              opt.textContent = m.size;
              sizeSelect.appendChild(opt);
            }
            sizeSelect.value = m.size;
          }
          if (m.price_naira) document.getElementById('p-price').value = m.price_naira;
          document.getElementById('p-barcode').value = '';
          document.getElementById('p-sku').value = '';
          skuElement.dataset.userEdited = '';
          showToast(`Filled in from "${m.name}" -- adjust size, barcode, or price, then save as a new entry.`);
        });
        nameSuggestions.querySelector(`[data-suggest-edit="${m.id}"]`)?.addEventListener('click', (e) => {
          e.stopPropagation();
          nameSuggestions.style.display = 'none';
          openEditModal(m);
          showToast(`Editing existing product "${m.name}".`);
        });
      });
      templateMatches.slice(0, 4).forEach((t) => {
        nameSuggestions.querySelector(`[data-suggest-template="${t.id}"]`)?.addEventListener('click', () => {
          nameSuggestions.style.display = 'none';
          nameInput.value = t.name;
          nameInput.dataset.userEdited = 'true';
          if (t.primary_category_id) {
            document.getElementById('p-category').value = t.primary_category_id;
            document.getElementById('p-category-input').value = t.primary_category_name || '';
          }
          if (t.sizes.length) {
            const sizeSelect = document.getElementById('p-size');
            let opt = [...sizeSelect.options].find((o) => o.value === t.sizes[0]);
            if (!opt) {
              opt = document.createElement('option');
              opt.value = t.sizes[0];
              opt.textContent = t.sizes[0];
              sizeSelect.appendChild(opt);
            }
            sizeSelect.value = t.sizes[0];
          }
          document.getElementById('p-sku').value = slugifySku(t.name);
          showToast(`Filled in from the "${t.name}" template.`);
        });
      });
    } catch (err) { /* non-fatal -- typeahead is a convenience, not required */ }
  }, 300);
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('#p-name') && !e.target.closest('#p-name-suggestions')) {
    nameSuggestions.style.display = 'none';
  }
});

// -------------------------------------------------------- Inline category add
// (Superseded by the Quick Add Category button next to the searchable
// category field above -- see spec section 14/15.)

// -------------------------------------------------------------- Barcode scan
//
// Uses the browser's native BarcodeDetector API (Chrome/Edge, including
// Android) -- no external library/CDN dependency. Where it's not
// supported (Safari/iOS, Firefox as of this build), the button explains
// that plainly instead of silently doing nothing; typing the barcode by
// hand still always works everywhere.

let barcodeScanStream = null;
let barcodeScanRAF = null;

function stopBarcodeScan() {
  if (barcodeScanRAF) cancelAnimationFrame(barcodeScanRAF);
  barcodeScanRAF = null;
  if (barcodeScanStream) {
    barcodeScanStream.getTracks().forEach((t) => t.stop());
    barcodeScanStream = null;
  }
  document.getElementById('p-barcode-scan-video').style.display = 'none';
  document.getElementById('p-barcode-scan-hint').style.display = 'none';
  document.getElementById('p-barcode-scan-btn').textContent = '📷 Scan';
}

document.getElementById('p-barcode-scan-btn')?.addEventListener('click', async () => {
  if (barcodeScanStream) {
    stopBarcodeScan();
    return;
  }
  const hint = document.getElementById('p-barcode-scan-hint');
  if (!('BarcodeDetector' in window)) {
    hint.textContent = "Camera barcode scanning isn't supported on this browser -- type the barcode in manually instead.";
    hint.style.display = 'block';
    return;
  }
  try {
    barcodeScanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const video = document.getElementById('p-barcode-scan-video');
    video.srcObject = barcodeScanStream;
    video.style.display = 'block';
    await video.play();
    document.getElementById('p-barcode-scan-btn').textContent = 'Stop scanning';
    hint.textContent = 'Point the camera at the barcode…';
    hint.style.display = 'block';

    const detector = new BarcodeDetector();
    const scanFrame = async () => {
      if (!barcodeScanStream) return;
      try {
        const codes = await detector.detect(video);
        if (codes.length) {
          document.getElementById('p-barcode').value = codes[0].rawValue;
          showToast('Barcode captured.');
          stopBarcodeScan();
          return;
        }
      } catch (err) { /* keep trying on transient decode errors */ }
      barcodeScanRAF = requestAnimationFrame(scanFrame);
    };
    barcodeScanRAF = requestAnimationFrame(scanFrame);
  } catch (err) {
    hint.textContent = `Couldn't access the camera: ${err.message}`;
    hint.style.display = 'block';
  }
});

productForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = document.getElementById('product-id').value;
  const saveBtn = document.getElementById('product-modal-save');
  saveBtn.disabled = true;
  modalError.classList.remove('visible');

  const name = toProductNameCase(document.getElementById('p-name').value.trim());
  const brandId = brandHidden.value || null;
  const skuFieldValue = document.getElementById('p-sku').value.trim();
  const skuManuallySet = skuElement.dataset.userEdited === 'true';
  const sku = skuManuallySet ? (skuFieldValue || undefined) : undefined;
  const barcode = document.getElementById('p-barcode').value.trim() || null;
  const categoryValue = document.getElementById('p-category').value;
  const categoryId = categoryValue && categoryValue !== '__new__' ? categoryValue : null;
  const size = document.getElementById('p-size').value || null;
  const sellingPrice = document.getElementById('p-price').value;
  const reorderLevel = Number(document.getElementById('p-reorder').value || 0);
  const expirationDate = document.getElementById('p-expiration').value || null;
  // "Add to catalog only" draft-mode feature removed (dictated request) --
  // Add Product now always requires price and starting stock.
  const isDraft = false;

  // September 2026 change spec section 22 -- both blank is fine (no
  // wholesale tier); if either is filled in, both are required
  // together, since "a price with no threshold" or "a threshold with
  // no price" is meaningless on its own.
  const wholesalePriceRaw = document.getElementById('p-wholesale-price').value;
  const wholesaleMinQtyRaw = document.getElementById('p-wholesale-min-qty').value;
  if (!isDraft && (wholesalePriceRaw || wholesaleMinQtyRaw) && !(wholesalePriceRaw && wholesaleMinQtyRaw)) {
    modalError.textContent = 'Fill in both the wholesale price and the minimum quantity, or leave both blank.';
    modalError.classList.add('visible');
    saveBtn.disabled = false;
    return;
  }

  // PATCH5A-PACK-SIZES: blank = no default (sent as null, which clears it).
  const packRaw = {
    default_pieces_per_carton: document.getElementById('p-pack-carton').value.trim(),
    default_pieces_per_bag: document.getElementById('p-pack-bag').value.trim(),
    default_pieces_per_roll: document.getElementById('p-pack-roll').value.trim(),
  };
  const packSizes = {};
  for (const packKey of Object.keys(packRaw)) {
    const packText = packRaw[packKey];
    if (packText === '') { packSizes[packKey] = null; continue; }
    const packNumber = Number(packText);
    if (!Number.isInteger(packNumber) || packNumber < 1) {
      modalError.textContent = 'Pieces in a pack must be a whole number, 1 or more.';
      modalError.classList.add('visible');
      saveBtn.disabled = false;
      return;
    }
    packSizes[packKey] = packNumber;
  }

  if (!isDraft && (sellingPrice === '' || Number(sellingPrice) < 0)) {
    modalError.textContent = 'Enter a selling price.';
    modalError.classList.add('visible');
    saveBtn.disabled = false;
    return;
  }
  // Starting stock is required (spec section 2), not optional -- only
  // enforced on create, since Edit hides this field entirely, and a
  // draft product (section 15) skips it altogether.
  const initialBranchId = BranchContext.get();
  const initialQty = Number(initialQtyInput.value || 0);
  if (!id && !isDraft) {
    if (!initialBranchId) {
      modalError.textContent = 'Pick a branch on the Dashboard first -- starting stock has to belong to one specific branch.';
      modalError.classList.add('visible');
      saveBtn.disabled = false;
      return;
    }
    if (initialQtyInput.value === '' || initialQty < 0) {
      modalError.textContent = 'Enter the starting stock quantity (0 if none yet).';
      modalError.classList.add('visible');
      saveBtn.disabled = false;
      return;
    }
  }

  try {
    let savedProduct;
    const hiddenFromStaff = currentUser?.role === 'super_admin'
      ? document.getElementById('p-hidden-from-staff').checked
      : undefined;
    const commonFields = {
      name, brand_id: brandId, barcode, category_id: categoryId,
      size, expiration_date: expirationDate,
      selling_price_naira: isDraft ? 0 : Number(sellingPrice), reorder_level: reorderLevel,
      wholesale_price_naira: (!isDraft && wholesalePriceRaw) ? Number(wholesalePriceRaw) : null,
      wholesale_min_quantity: (!isDraft && wholesaleMinQtyRaw) ? Number(wholesaleMinQtyRaw) : null,
      ...packSizes, // PATCH5A-PACK-SIZES
    };
    if (id) {
      // "Also list under" was removed from this form (spec section 5)
      // -- extra_category_ids is deliberately left out of an edit
      // save entirely (not sent as an empty list) so a product that
      // already has extra categories from before this change keeps
      // them; this form just can't touch that field anymore.
      savedProduct = await Api.updateProduct(id, {
        ...commonFields,
        ...(hiddenFromStaff !== undefined ? { hidden_from_staff: hiddenFromStaff } : {}),
      });
      showToast('Product updated.');
    } else {
      // A brand-new product has no extra categories to lose, so an
      // empty list here is correct (not a data-loss risk like above).
      // Section 15: status='draft' when "add to catalog only" is
      // checked -- it stays dormant/non-live until someone later
      // opens it from Inventory and fills in price/stock.
      savedProduct = await Api.createProduct({
        ...commonFields, extra_category_ids: [], sku, status: isDraft ? 'draft' : 'active',
      });
      if (hiddenFromStaff) {
        await Api.updateProduct(savedProduct.id, { hidden_from_staff: true });
      }
      showToast(isDraft ? 'Added to catalog — fill in price and stock later to make it sellable.' : 'Product added.');

      // Starting stock (spec section 2) -- always recorded on create,
      // at the currently active branch (spec section 5 -- never a
      // branch picked inside this form), UNLESS this is a draft
      // (section 15), which has no stock yet by definition. Same call
      // the Warehouse page makes, just offered here too so a
      // brand-new product doesn't need a separate trip to Warehouse
      // just to become sellable. Cost isn't collected in this form
      // (spec section 6) -- this opening batch starts at ₦0 cost and
      // gets a real cost the first time it's properly restocked from
      // Warehouse.
      if (!isDraft && initialQty > 0) {
        await Api.receiveStock({
          product_id: savedProduct.id,
          branch_id: initialBranchId,
          quantity_received: initialQty,
          unit_cost_naira: 0,
          batch_number: 'Starting stock (added with product)',
        });
        // fix44-new-product-shelf-default: more than 24 in starting stock -> shelf 24, the rest goes to the warehouse
        // fix45: the shelf limit can be typed in the Add Product form (blank = 24)
        const shelfFieldRaw = (document.getElementById('p-shelf-default') || { value: '' }).value.trim();
        const shelfDefaultMax = /^\d+$/.test(shelfFieldRaw) ? Number(shelfFieldRaw) : 24;
        const shelfDefaultAlert = Math.min(5, shelfDefaultMax);
        if (initialQty > shelfDefaultMax) {
          try {
            const shelfResult = await Api.saveShelfSetting({
              branch_id: initialBranchId,
              product_id: savedProduct.id,
              max_shelf_quantity: shelfDefaultMax,
              replenish_alert_level: shelfDefaultAlert,
              move_excess_to_warehouse: true, // fix53: new product only, splits its starting stock
            });
            const movedNew = (shelfResult && Number(shelfResult.moved_to_warehouse)) || 0;
            if (movedNew > 0) {
              showToast('Product added. Shelf ' + shelfDefaultMax + ', ' + movedNew + ' piece(s) moved to the warehouse.');
            }
          } catch (shelfErr) {
            showToast('Product added, but the shelf limit could not be set: ' + shelfErr.message, true);
          }
        }
      }
    }

    // Image upload needs a product id, so it happens as a follow-up call
    // after create/update succeeds -- see /catalog/products/{id}/images.
    const file = imageFileInput.files[0];
    if (file && savedProduct?.id) {
      try {
        await Api.uploadProductImage(savedProduct.id, file);
      } catch (err) {
        showToast(`Product saved, but the photo upload failed: ${err.message}`, true);
      }
    }
    stopBarcodeScan();
    modalBackdrop.classList.remove('visible');
    loadProducts();
    // Add Product -> Invoice Workflow (Sept 2026 spec): only offered
    // right after creating a brand-new, non-draft product -- editing
    // an existing product, or adding a catalog-only draft (no stock/
    // cost yet, nothing to invoice), never triggers this.
    if (!id && !isDraft) {
      openInvoicePrompt(savedProduct);
    }
  } catch (err) {
    modalError.textContent = err.message;
    modalError.classList.add('visible');
  } finally {
    saveBtn.disabled = false;
  }
});

// ------------------------------------------------------------------ Init

loadBrands();
loadUnits().then(() => loadFilters()).then(loadProducts);

// Auto-refresh stock/prices so this list doesn't go stale while a POS sale
// or storefront order changes numbers elsewhere. Skipped while the add/edit
// modal is open or the search box has focus, so we don't yank the table out
// from under an in-progress edit or interrupt typing.
const INVENTORY_POLL_MS = 30000;
setInterval(() => {
  if (document.hidden) return;
  if (modalBackdrop.classList.contains('visible')) return;
  if (document.activeElement === document.getElementById('search-input')) return;
  loadProducts();
}, INVENTORY_POLL_MS);

// -------------------------------------------------------- Restock (from warehouse)
//
// September 2026 change spec section 7: "Stock cannot be manually
// conjured into 'in stock' -- it must be pulled from a real warehouse
// record." This calls the same already-gated /warehouse/pull-to-store
// endpoint the Warehouse page uses (Api.pullToStore) -- never the
// freehand Api.receiveStock -- and shows/caps the form against the
// branch's actual WarehouseStock.quantity_pieces for this product.

let currentRestockProductId = null;
async function openRestockModal(productId, productName) {
  currentRestockProductId = productId;
  const backdrop = document.getElementById('restock-modal-backdrop');
  const form = document.getElementById('restock-modal-form');
  const errorEl = document.getElementById('restock-modal-error');
  const availableEl = document.getElementById('restock-modal-available');
  const emptyEl = document.getElementById('restock-modal-empty');
  errorEl.classList.remove('visible');
  document.getElementById('restock-modal-product-name').textContent = productName;
  document.getElementById('restock-unit-label').value = 'piece';
  document.getElementById('restock-pieces-per-unit').value = 1;
  document.getElementById('restock-unit-count').value = 1;

  const branchId = BranchContext.get() || currentUser?.branch_id;
  backdrop.classList.add('visible');
  availableEl.textContent = 'Checking warehouse stock…';
  form.style.display = 'none';
  emptyEl.style.display = 'none';
  try {
    const warehouseRows = await Api.listWarehouseStock(branchId);
    const row = warehouseRows.find((w) => w.product_id === productId);
    const availablePieces = row?.quantity_pieces ?? 0;
    if (availablePieces > 0) {
      availableEl.textContent = `${availablePieces} piece(s) available in this branch's warehouse.`;
      form.style.display = '';
      form.dataset.availablePieces = availablePieces;
    } else {
      availableEl.textContent = '';
      emptyEl.style.display = '';
      form.dataset.availablePieces = '0';
    }
  } catch (err) {
    availableEl.textContent = '';
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  }
}

document.getElementById('restock-modal-cancel').addEventListener('click', () => {
  document.getElementById('restock-modal-backdrop').classList.remove('visible');
});

document.getElementById('restock-modal-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const errorEl = document.getElementById('restock-modal-error');
  errorEl.classList.remove('visible');
  const unitLabel = document.getElementById('restock-unit-label').value.trim();
  const piecesPerUnit = Number(document.getElementById('restock-pieces-per-unit').value);
  const unitCount = Number(document.getElementById('restock-unit-count').value);
  const availablePieces = Number(form.dataset.availablePieces || 0);
  const requestedPieces = piecesPerUnit * unitCount;
  if (requestedPieces > availablePieces) {
    errorEl.textContent = `Only ${availablePieces} piece(s) available in the warehouse -- can't restock ${requestedPieces}.`;
    errorEl.classList.add('visible');
    return;
  }
  const branchId = BranchContext.get() || currentUser?.branch_id;
  const submitBtn = document.getElementById('restock-modal-submit');
  submitBtn.disabled = true;
  try {
    await Api.pullToStore(branchId, {
      product_id: currentRestockProductId,
      unit_label: unitLabel,
      unit_count: unitCount,
      pieces_per_unit: piecesPerUnit,
    });
    document.getElementById('restock-modal-backdrop').classList.remove('visible');
    showToast('Restocked from warehouse.');
    loadProducts();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});

// ------------------------------------------------------------ Restock history
//
// Every "Receive stock" submission on the Warehouse page creates a
// StockBatch row (quantity_received, unit_cost_kobo, received_at) --
// this already *is* a full restock history per product, it just
// wasn't exposed anywhere in the UI.

let currentHistoryProductId = null;
let currentHistoryProductName = null;
async function openHistoryModal(productId, productName) {
  currentHistoryProductId = productId;
  currentHistoryProductName = productName;
  loadProductReturnsAndCredit(productId);
  loadShelfMovements(productId); // fix52
  const backdrop = document.getElementById('history-modal-backdrop');
  const tbody = document.getElementById('history-tbody');
  document.getElementById('history-modal-title').textContent = `Stock history — ${productName}`;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="6">Loading…</td></tr>';
  backdrop.classList.add('visible');

  try {
    const rows = (await Api.listStockBatches(productId))
      .sort((a, b) => new Date(b.received_at) - new Date(a.received_at));

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="color:var(--ink-faint);">No restocks recorded yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((r) => {
      const branch = branchCache.find((b) => b.id === r.branch_id);
      return `<tr>
        <td>${new Date(r.received_at).toLocaleDateString('en-NG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
        <td>${branch ? escapeHtml(branch.name) : '—'}</td>
        <td>${r.quantity_received}</td>
        <td>${formatNaira(r.unit_cost_kobo / 100)}</td>
        <td>${r.expiry_date ? new Date(r.expiry_date).toLocaleDateString('en-NG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' }) : '—'}</td>
        <td><button type="button" class="btn btn-ghost btn-sm record-loss-btn" data-batch-id="${r.id}" data-remaining="${r.quantity_remaining}" ${r.quantity_remaining <= 0 ? 'disabled' : ''}>Record loss</button></td>
      </tr>`;
    }).join('');
    tbody.querySelectorAll('.record-loss-btn').forEach((btn) => {
      btn.addEventListener('click', () => openLossModal(btn.dataset.batchId, Number(btn.dataset.remaining)));
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:var(--danger);">${escapeHtml(err.message)}</td></tr>`;
  }
}

// PATCH8A-PRODUCT-RETURNS-CREDIT: second table in the History window --
// refunds and goods taken on credit for this product.
function productReturnCreditRowHtml(r) {
  const when = new Date(r.happened_at).toLocaleDateString('en-NG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' });
  return `<tr>
    <td>${when}</td>
    <td>${r.branch_name ? escapeHtml(r.branch_name) : '&mdash;'}</td>
    <td>${escapeHtml(r.what)}</td>
    <td>${r.quantity}</td>
    <td>${r.detail ? escapeHtml(r.detail) : '&mdash;'}</td>
  </tr>`;
}

async function loadProductReturnsAndCredit(productId) {
  const tbody = document.getElementById('history-returns-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading...</td></tr>';
  try {
    const rows = await Api.productReturnsAndCredit(productId);
    if (productId !== currentHistoryProductId) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--ink-faint);">No refunds or credit for this product yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(productReturnCreditRowHtml).join('');
  } catch (err) {
    if (productId !== currentHistoryProductId) return;
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--danger);">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('history-modal-close')?.addEventListener('click', () => {
  document.getElementById('history-modal-backdrop').classList.remove('visible');
});

// ---------------------------------------------------------------- Record a loss (spec section 1)
// Filed against one specific batch from the Restock history modal
// above -- see POST /inventory/stock-adjustments.

let activeLossBatchId = null;
let uploadedLossEvidenceUrl = null;

// Staff member's name is a dropdown of the current branch's staff
// (rather than a free-text field) -- see js/losses.js's identical copy
// for the reasoning. Loaded fresh each time the modal opens.
let lossBranchStaff = [];
async function loadLossBranchStaff() {
  const damageSelect = document.getElementById('loss-damage-staff-name');
  const theftSelect = document.getElementById('loss-theft-by-staff-name');
  try {
    lossBranchStaff = await Api.listUsers({ branchId: BranchContext.get() });
    const options = '<option value="">Select…</option>' +
      lossBranchStaff.map((u) => `<option value="${escapeHtml(u.full_name)}">${escapeHtml(u.full_name)}</option>`).join('');
    damageSelect.innerHTML = options;
    theftSelect.innerHTML = options;
  } catch (err) {
    // Non-fatal -- the dropdowns just stay empty.
  }
}

function openLossModal(batchId, remaining) {
  activeLossBatchId = batchId;
  uploadedLossEvidenceUrl = null;
  document.getElementById('loss-modal-form').reset();
  document.getElementById('loss-evidence-status').textContent = '';
  document.getElementById('loss-quantity').max = remaining;
  document.getElementById('loss-modal-batch-readout').textContent = `${remaining} unit${remaining === 1 ? '' : 's'} remaining in this batch.`;
  document.getElementById('loss-modal-error').classList.remove('visible');
  loadLossBranchStaff();
  applyLossReasonFieldVisibility();
  document.getElementById('loss-modal-backdrop').classList.add('visible');
}
document.getElementById('loss-modal-cancel')?.addEventListener('click', () => {
  document.getElementById('loss-modal-backdrop').classList.remove('visible');
});

// Item 11 / September 2026 change spec section 4: Damaged/Stolen each
// need their own accountability fields, shown only when that reason
// is selected; all three (damaged/stolen/expired) need evidence and
// (for damaged) a note explaining what happened.
function applyLossReasonFieldVisibility() {
  const reason = document.getElementById('loss-reason').value;
  document.getElementById('loss-damage-by-field').style.display = reason === 'damaged' ? '' : 'none';
  const isDamageByStaff = reason === 'damaged' && document.getElementById('loss-damage-by').value === 'staff';
  document.getElementById('loss-damage-staff-field').style.display = isDamageByStaff ? '' : 'none';
  // Section 4: "auto-fill the staff's name" for a staff-caused damage
  // report -- defaults to whoever's reporting it (the common case is
  // self-reporting), editable if it was actually someone else.
  const damageStaffSelect = document.getElementById('loss-damage-staff-name');
  if (isDamageByStaff && !damageStaffSelect.value && currentUser?.full_name) {
    const match = Array.from(damageStaffSelect.options).find((o) => o.value === currentUser.full_name);
    if (match) damageStaffSelect.value = currentUser.full_name;
  }
  document.getElementById('loss-theft-caused-by-field').style.display = reason === 'theft' ? '' : 'none';
  const isTheftByStaff = reason === 'theft' && document.getElementById('loss-theft-caused-by').value === 'staff';
  document.getElementById('loss-theft-by-staff-field').style.display = isTheftByStaff ? '' : 'none';
  document.getElementById('loss-theft-time-field').style.display = reason === 'theft' ? '' : 'none';

  const needsEvidence = ['damaged', 'theft', 'expired'].includes(reason);
  document.getElementById('loss-evidence-field').style.display = needsEvidence ? '' : 'none';
  document.getElementById('loss-evidence').required = needsEvidence;

  const notesLabel = document.getElementById('loss-notes-label');
  notesLabel.textContent = reason === 'damaged' ? 'What happened? *' : 'Notes (optional)';
  document.getElementById('loss-notes').required = reason === 'damaged';
}
document.getElementById('loss-reason')?.addEventListener('change', applyLossReasonFieldVisibility);
document.getElementById('loss-damage-by')?.addEventListener('change', applyLossReasonFieldVisibility);
document.getElementById('loss-theft-caused-by')?.addEventListener('change', applyLossReasonFieldVisibility);

document.getElementById('loss-evidence')?.addEventListener('change', async (e) => {
  const file = e.target.files[0];
  const statusEl = document.getElementById('loss-evidence-status');
  if (!file) { uploadedLossEvidenceUrl = null; statusEl.textContent = ''; return; }
  statusEl.textContent = 'Uploading…';
  try {
    const { url } = await Api.uploadLossEvidence(file);
    uploadedLossEvidenceUrl = url;
    statusEl.textContent = file.type.startsWith('video/') ? 'Video attached ✓' : 'Photo attached ✓';
  } catch (err) {
    uploadedLossEvidenceUrl = null;
    statusEl.textContent = `Upload failed: ${err.message}`;
  }
});

document.getElementById('loss-modal-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const submitBtn = document.getElementById('loss-modal-submit');
  const errorEl = document.getElementById('loss-modal-error');
  const qty = Number(document.getElementById('loss-quantity').value);
  const reason = document.getElementById('loss-reason').value;

  const payload = {
    stock_batch_id: activeLossBatchId,
    // A loss always reduces remaining stock -- quantity_delta is
    // negative for every reason offered here (see StockAdjustmentCreate;
    // positive deltas are reserved for a recount finding more stock
    // than expected, which this form doesn't offer).
    quantity_delta: -Math.abs(qty),
    reason,
    notes: document.getElementById('loss-notes').value.trim() || null,
  };
  if (reason === 'damaged') {
    const damagedBy = document.getElementById('loss-damage-by').value;
    if (!damagedBy) {
      errorEl.textContent = 'Record whether the item was damaged by a customer or a staff member.';
      errorEl.classList.add('visible');
      return;
    }
    payload.damage_caused_by = damagedBy;
    if (damagedBy === 'staff') {
      const staffName = document.getElementById('loss-damage-staff-name').value;
      if (!staffName) {
        errorEl.textContent = "Select the staff member responsible for the damage.";
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
    const theftBy = document.getElementById('loss-theft-caused-by').value;
    if (!theftBy) {
      errorEl.textContent = 'Record whether the item was stolen by a customer or a staff member.';
      errorEl.classList.add('visible');
      return;
    }
    payload.theft_caused_by = theftBy;
    if (theftBy === 'staff') {
      const staffName = document.getElementById('loss-theft-by-staff-name').value;
      if (!staffName) {
        errorEl.textContent = "Select the staff member responsible for the theft.";
        errorEl.classList.add('visible');
        return;
      }
      payload.theft_by_staff_name = staffName;
    }
    const noticedAt = document.getElementById('loss-theft-noticed-at').value;
    if (noticedAt) {
      const today = new Date().toISOString().slice(0, 10);
      payload.theft_noticed_at = new Date(`${today}T${noticedAt}:00`).toISOString();
    }
  }
  if (['damaged', 'theft', 'expired'].includes(reason)) {
    if (!uploadedLossEvidenceUrl) {
      errorEl.textContent = 'Upload a photo or video of the item as evidence.';
      errorEl.classList.add('visible');
      return;
    }
    payload.image_url = uploadedLossEvidenceUrl;
  }

  submitBtn.disabled = true;
  errorEl.classList.remove('visible');
  try {
    const result = await Api.createStockAdjustment(payload);
    // Section 4: theft isn't deducted yet -- it's reported, notifies
    // the super admin, and only comes off the shelf once confirmed.
    showToast(
      result.status === 'pending_confirmation'
        ? 'Theft reported -- the super admin has been notified and will confirm before it comes off the shelf.'
        : 'Loss recorded.'
    );
    document.getElementById('loss-modal-backdrop').classList.remove('visible');
    if (currentHistoryProductId) await openHistoryModal(currentHistoryProductId, currentHistoryProductName);
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});

// Click-to-enlarge: same shared lightbox pattern as the low-stock page,
// so tapping a product thumbnail here shows the full-size image too.
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
document.getElementById('image-lightbox-close')?.addEventListener('click', closeImageLightbox);
imageLightboxBackdrop?.addEventListener('click', (e) => { if (e.target === imageLightboxBackdrop) closeImageLightbox(); });
document.getElementById('products-tbody')?.addEventListener('click', (e) => {
  const img = e.target.closest('[data-enlarge-image]');
  if (img) openImageLightbox(img.dataset.enlargeImage);
});
// Same zoomable-thumbnail behavior on the Deletion history table.
document.getElementById('deletion-history-tbody')?.addEventListener('click', (e) => {
  const img = e.target.closest('[data-enlarge-image]');
  if (img) openImageLightbox(img.dataset.enlargeImage);
});




// ------------------------------------------------------------------
// Add Product -> Invoice Workflow (Sept 2026 spec): after a brand-new,
// non-draft product is saved, offer to attach it to a purchase
// invoice -- either starting a fresh draft or adding it to an
// existing unfinished one. Entirely optional; skipping just leaves
// Inventory exactly as it already behaved before this feature.
const invoicePromptBackdrop = document.getElementById('invoice-prompt-backdrop');
const invoicePromptProductName = document.getElementById('invoice-prompt-product-name');
const invoicePromptError = document.getElementById('invoice-prompt-error');
const invoicePromptExistingWrap = document.getElementById('invoice-prompt-existing-wrap');
const invoicePromptExistingSelect = document.getElementById('invoice-prompt-existing-select');
const invoicePromptNewDraftBtn = document.getElementById('invoice-prompt-new-draft');
const invoicePromptNewWrap = document.getElementById('invoice-prompt-new-wrap');
const invoicePromptSupplierInput = document.getElementById('invoice-prompt-supplier-input');
const invoicePromptSupplierDropdown = document.getElementById('invoice-prompt-supplier-dropdown');
const invoicePromptSupplierHint = document.getElementById('invoice-prompt-supplier-hint');
const invoicePromptNewConfirmBtn = document.getElementById('invoice-prompt-new-confirm');
const invoicePromptExistingToggleBtn = document.getElementById('invoice-prompt-existing-toggle');
const invoicePromptExistingConfirmBtn = document.getElementById('invoice-prompt-existing-confirm');
const invoicePromptBackBtn = document.getElementById('invoice-prompt-back');
let invoicePromptProduct = null;

function _invPromptEscape(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

let invoicePromptSupplierMatch = null;
let invoicePromptSupplierResults = [];

function openInvoicePrompt(product) {
  invoicePromptProduct = product;
  invoicePromptProductName.textContent = product.name;
  invoicePromptError.textContent = '';
  invoicePromptError.classList.remove('visible');
  invoicePromptExistingWrap.style.display = 'none';
  invoicePromptExistingConfirmBtn.style.display = 'none';
  invoicePromptNewWrap.style.display = 'none';
  invoicePromptNewConfirmBtn.style.display = 'none';
  invoicePromptNewDraftBtn.style.display = ''; // fix15: one-path-at-a-time invoice prompt
  invoicePromptExistingToggleBtn.style.display = '';
  invoicePromptBackBtn.style.display = 'none'; // fix39: hidden until a sub-flow is chosen
  invoicePromptSupplierInput.value = '';
  invoicePromptSupplierHint.textContent = '';
  invoicePromptSupplierMatch = null;
  invoicePromptBackdrop.classList.add('visible');
}

function closeInvoicePrompt() {
  invoicePromptBackdrop.classList.remove('visible');
  invoicePromptProduct = null;
  // Sept 2026: refresh so a cleared needs_invoice flag (and this row's
  // "Add to invoice" action / the Awaiting-invoice filter) update
  // immediately instead of requiring a manual reload.
  loadProducts();
}

function hideInvoicePromptSupplierDropdown() {
  invoicePromptSupplierDropdown.style.display = 'none';
  invoicePromptSupplierDropdown.innerHTML = '';
}

async function searchInvoicePromptSuppliers(query) {
  if (!query || !query.trim()) { hideInvoicePromptSupplierDropdown(); return; }
  try {
    invoicePromptSupplierResults = await Api.listSuppliers(query.trim());
  } catch (err) { return; }
  const rowsHtml = invoicePromptSupplierResults.slice(0, 8).map((s) => `
    <div class="item-autocomplete-row" data-supplier-id="${s.id}">
      <span>${_invPromptEscape(s.name)}</span>
      <span class="price">${_invPromptEscape(s.contact_phone || '')}</span>
    </div>`).join('');
  invoicePromptSupplierDropdown.innerHTML = rowsHtml || `<div class="item-autocomplete-empty">No matching suppliers — will be saved as new.</div>`;
  invoicePromptSupplierDropdown.style.display = '';
  invoicePromptSupplierDropdown.querySelectorAll('[data-supplier-id]').forEach((el) => {
    const supplier = invoicePromptSupplierResults.find((s) => s.id === el.dataset.supplierId);
    el.addEventListener('mousedown', (e) => {
      e.preventDefault();
      if (!supplier) return;
      invoicePromptSupplierInput.value = supplier.name;
      invoicePromptSupplierMatch = supplier;
      invoicePromptSupplierHint.textContent = `Using ${supplier.name}.`;
      hideInvoicePromptSupplierDropdown();
    });
  });
}

let invoicePromptSupplierDebounce;
invoicePromptSupplierInput.addEventListener('input', () => {
  invoicePromptSupplierMatch = null;
  invoicePromptSupplierHint.textContent = '';
  clearTimeout(invoicePromptSupplierDebounce);
  invoicePromptSupplierDebounce = setTimeout(
    () => searchInvoicePromptSuppliers(invoicePromptSupplierInput.value),
    250
  );
});
invoicePromptSupplierInput.addEventListener('blur', () => {
  setTimeout(hideInvoicePromptSupplierDropdown, 150);
});

function invoiceLineItemForProduct(product) {
  // Quantity mirrors whatever starting stock was just recorded for
  // this product (falls back to 1) -- rate/total are deliberately
  // left at 0 here (spec: "should NOT automatically contain... Unit
  // rate, Total price"); a super admin/branch manager fills those in
  // later from the Invoice page.
  return {
    item_name: product.name,
    product_id: product.id,
    quantity: product.quantity_available > 0 ? product.quantity_available : 1,
    unit_label: 'pieces',
    rate_naira: 0,
  };
}

// First click just reveals the supplier field -- the actual invoice
// isn't created until "Create draft" (below) is pressed, so supplier
// name feedback (Sept 2026: drafts were piling up indistinguishable
// from each other with no supplier shown) is asked for right here,
// still entirely optional.
invoicePromptNewDraftBtn.addEventListener('click', () => {
  invoicePromptNewWrap.style.display = 'block';
  invoicePromptNewConfirmBtn.style.display = 'block';
  invoicePromptNewDraftBtn.style.display = 'none';
  invoicePromptExistingToggleBtn.style.display = 'none';
  invoicePromptBackBtn.style.display = 'block'; // fix39
  invoicePromptSupplierInput.focus();
});

invoicePromptNewConfirmBtn.addEventListener('click', async () => {
  if (!invoicePromptProduct) return;
  invoicePromptNewConfirmBtn.disabled = true;
  try {
    const typedName = invoicePromptSupplierInput.value.trim();
    await Api.createPurchaseInvoice({
      status: 'draft',
      branch_id: BranchContext.get(),
      supplier_id: invoicePromptSupplierMatch ? invoicePromptSupplierMatch.id : undefined,
      supplier_name: typedName || undefined,
      items: [invoiceLineItemForProduct(invoicePromptProduct)],
    });
    showToast('Invoice draft created — open it from the Invoice page to fill in the rest.');
    closeInvoicePrompt();
  } catch (err) {
    invoicePromptError.textContent = err.message;
    invoicePromptError.classList.add('visible');
  } finally {
    invoicePromptNewConfirmBtn.disabled = false;
  }
});

invoicePromptExistingToggleBtn.addEventListener('click', async () => {
  invoicePromptNewDraftBtn.style.display = 'none';
  invoicePromptExistingToggleBtn.style.display = 'none'; // fix39: was left visible next to the confirm button
  invoicePromptBackBtn.style.display = 'block'; // fix39
  invoicePromptExistingWrap.style.display = 'block';
  invoicePromptExistingConfirmBtn.style.display = 'block';
  invoicePromptExistingSelect.innerHTML = '<option value="">Loading…</option>';
  try {
    const drafts = await Api.listPurchaseInvoices({ status: 'draft' });
    if (!drafts.length) {
      invoicePromptExistingSelect.innerHTML = '<option value="">No draft invoices yet</option>';
      return;
    }
    invoicePromptExistingSelect.innerHTML = drafts.map((inv) => {
      const dateStr = new Date(inv.invoice_date).toLocaleDateString('en-NG');
      const label = inv.supplier_name ? `${inv.supplier_name} \u2014 ${dateStr}` : `Draft from ${dateStr}`;
      const itemNames = (inv.items || []).map((it) => it.item_name).join(', ') || 'no items yet';
      return `<option value="${inv.id}">${_invPromptEscape(label)} (${_invPromptEscape(itemNames)})</option>`;
    }).join('');
  } catch (err) {
    invoicePromptError.textContent = err.message;
    invoicePromptError.classList.add('visible');
  }
});

invoicePromptExistingConfirmBtn.addEventListener('click', async () => {
  if (!invoicePromptProduct) return;
  const invoiceId = invoicePromptExistingSelect.value;
  if (!invoiceId) {
    invoicePromptError.textContent = 'Pick a draft invoice first.';
    invoicePromptError.classList.add('visible');
    return;
  }
  invoicePromptExistingConfirmBtn.disabled = true;
  try {
    await Api.updatePurchaseInvoice(invoiceId, {
      add_items: [invoiceLineItemForProduct(invoicePromptProduct)],
    });
    showToast('Added to the draft invoice.');
    closeInvoicePrompt();
  } catch (err) {
    invoicePromptError.textContent = err.message;
    invoicePromptError.classList.add('visible');
  } finally {
    invoicePromptExistingConfirmBtn.disabled = false;
  }
});

// fix42: "Save for later" closes the invoice prompt without creating or
// changing any invoice. The product is already saved and stays flagged
// "Awaiting invoice" (use "Add to invoice" on its row later).
document.getElementById('invoice-prompt-save-later').addEventListener('click', () => {
  showToast('Saved as draft for later. It stays in "Awaiting invoice" - use "Add to invoice" on its row when you are ready.');
  closeInvoicePrompt();
});
// fix39: shared Back button for both sub-flows -- returns to the
// initial "Create new" / "Add to existing" choice screen.
invoicePromptBackBtn.addEventListener('click', () => {
  invoicePromptExistingWrap.style.display = 'none';
  invoicePromptExistingConfirmBtn.style.display = 'none';
  invoicePromptNewWrap.style.display = 'none';
  invoicePromptNewConfirmBtn.style.display = 'none';
  invoicePromptNewDraftBtn.style.display = '';
  invoicePromptExistingToggleBtn.style.display = '';
  invoicePromptBackBtn.style.display = 'none';
  invoicePromptError.textContent = '';
  invoicePromptError.classList.remove('visible');
  hideInvoicePromptSupplierDropdown();
});


// fix52: Reduce stock window. Sends only the pieces ABOVE the shelf default back to the warehouse.
let currentShelfReduceProductId = null;
let currentShelfReduceMax = 0;

function closeShelfReduceModal() {
  document.getElementById('shelf-reduce-backdrop').classList.remove('visible');
}

function showShelfReduceError(message) {
  const errorEl = document.getElementById('shelf-reduce-error');
  errorEl.textContent = message;
  errorEl.classList.add('visible');
}

function openShelfReduceModal(productId, productName, split) {
  currentShelfReduceProductId = productId;
  const inputEl = document.getElementById('shelf-reduce-pieces');
  const submitBtn = document.getElementById('shelf-reduce-submit');
  const errorEl = document.getElementById('shelf-reduce-error');
  errorEl.textContent = '';
  errorEl.classList.remove('visible');
  const shelf = Number((split && split.shelf_sellable_stock) || 0);
  const shelfDefault = (split && split.limits_configured && split.max_shelf_quantity != null) ? Number(split.max_shelf_quantity) : 24;
  const excess = shelf - shelfDefault;
  currentShelfReduceMax = excess > 0 ? excess : 0;
  document.getElementById('shelf-reduce-title').textContent = 'Reduce stock: ' + productName;
  document.getElementById('shelf-reduce-info').textContent = 'On the shelf now: ' + shelf + '. Shelf default: ' + shelfDefault + '.';
  if (excess > 0) {
    inputEl.value = excess;
    inputEl.disabled = false;
    submitBtn.disabled = false;
  } else {
    inputEl.value = '';
    inputEl.disabled = true;
    submitBtn.disabled = true;
    showShelfReduceError('The shelf holds ' + shelf + ' and the default is ' + shelfDefault + ', so there is nothing above the default to send back.');
  }
  document.getElementById('shelf-reduce-backdrop').classList.add('visible');
}

document.getElementById('shelf-reduce-cancel').addEventListener('click', closeShelfReduceModal);

document.getElementById('shelf-reduce-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  document.getElementById('shelf-reduce-error').classList.remove('visible');
  const raw = document.getElementById('shelf-reduce-pieces').value.trim();
  if (!/^\d+$/.test(raw) || Number(raw) < 1) {
    showShelfReduceError('Type a whole number, 1 or more.');
    return;
  }
  const pieces = Number(raw);
  if (pieces > currentShelfReduceMax) {
    showShelfReduceError('You can send back at most ' + currentShelfReduceMax + ' piece(s): only the pieces above the shelf default.');
    return;
  }
  const branchId = BranchContext.get() || currentUser?.branch_id;
  const submitBtn = document.getElementById('shelf-reduce-submit');
  submitBtn.disabled = true;
  try {
    const result = await Api.reduceShelfStock({
      branch_id: branchId,
      product_id: currentShelfReduceProductId,
      pieces: pieces,
    });
    closeShelfReduceModal();
    const movedPieces = (result && Number(result.moved_to_warehouse)) || pieces;
    showToast(movedPieces + ' piece(s) sent back to the warehouse.');
    loadProducts();
  } catch (err) {
    showShelfReduceError(err.message);
  } finally {
    submitBtn.disabled = false;
  }
});

// fix52: "Shelf movements" table in the Stock history window (warehouse to shelf, shelf to warehouse, branch sends)
const SHELF_MOVE_LABELS = {
  warehouse_to_store: 'Warehouse to shelf',
  shelf_to_warehouse: 'Shelf to warehouse',
  warehouse_to_branch: 'Sent to another branch',
};

async function loadShelfMovements(productId) {
  const tbody = document.getElementById('history-shelf-moves-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading...</td></tr>';
  const branchId = BranchContext.get() || currentUser?.branch_id;
  try {
    const all = await Api.listStockTransfers({ branchId: branchId, productId: productId });
    if (productId !== currentHistoryProductId) return;
    const rows = (all || []).filter((t) => SHELF_MOVE_LABELS[t.transfer_type]);
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--ink-faint);">No shelf movements recorded yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((t) => {
      let what = SHELF_MOVE_LABELS[t.transfer_type];
      if (t.transfer_type === 'warehouse_to_branch' && branchId && t.from_branch_id !== branchId) {
        what = 'Received from another branch warehouse';
      }
      const when = new Date(t.created_at).toLocaleString('en-NG', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Lagos' });
      return '<tr>'
        + '<td>' + escapeHtml(when) + '</td>'
        + '<td>' + escapeHtml(what) + '</td>'
        + '<td>' + Number(t.quantity_pieces) + '</td>'
        + '<td>' + (t.created_by_name ? escapeHtml(t.created_by_name) : '&mdash;') + '</td>'
        + '<td>' + (t.notes ? escapeHtml(t.notes) : '&mdash;') + '</td>'
        + '</tr>';
    }).join('');
  } catch (err) {
    if (productId !== currentHistoryProductId) return;
    tbody.innerHTML = '<tr><td colspan="5" style="color:var(--danger);">' + escapeHtml(err.message) + '</td></tr>';
  }
}

// PATCH4A-SHELF-LIMIT-WINDOW
// "Shelf limit" window: one product at one branch. It reads the saved limit
// with Api.listShelfSettings and saves with Api.saveShelfSetting
// (PUT /goods-receiving/shelf-settings, needs manage_inventory).
let currentShelfLimitProductId = null;

function closeShelfLimitModal() {
  document.getElementById('shelf-limit-backdrop').classList.remove('visible');
}

function showShelfLimitError(message) {
  const errorEl = document.getElementById('shelf-limit-error');
  errorEl.textContent = message;
  errorEl.classList.add('visible');
}

async function openShelfLimitModal(productId, productName) {
  currentShelfLimitProductId = productId;
  const backdrop = document.getElementById('shelf-limit-backdrop');
  const errorEl = document.getElementById('shelf-limit-error');
  const statusEl = document.getElementById('shelf-limit-status');
  const maxEl = document.getElementById('shelf-limit-max');
  const alertEl = document.getElementById('shelf-limit-alert');
  const saveBtn = document.getElementById('shelf-limit-submit');
  errorEl.textContent = '';
  errorEl.classList.remove('visible');
  document.getElementById('shelf-limit-title').textContent = 'Shelf default: ' + productName; // fix49
  document.getElementById('shelf-limit-branch').textContent = 'Branch: ' + (BranchContext.getName() || '');
  maxEl.value = 24;
  alertEl.value = 5;
  statusEl.textContent = 'Checking the saved limit...';
  saveBtn.disabled = true;
  backdrop.classList.add('visible');

  const branchId = BranchContext.get() || currentUser?.branch_id;
  try {
    const rows = await Api.listShelfSettings({ branchId, productId });
    const row = (rows || []).find((r) => r.product_id === productId);
    if (row) {
      maxEl.value = row.max_shelf_quantity;
      alertEl.value = row.replenish_alert_level;
      statusEl.textContent = 'Saved limit for this branch.';
    } else {
      statusEl.textContent = 'No limit set yet. The suggested numbers are filled in.';
    }
    saveBtn.disabled = false;
  } catch (err) {
    statusEl.textContent = '';
    showShelfLimitError(err.message);
  }
}

document.getElementById('shelf-limit-cancel').addEventListener('click', closeShelfLimitModal);

document.getElementById('shelf-limit-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  document.getElementById('shelf-limit-error').classList.remove('visible');
  const maxRaw = document.getElementById('shelf-limit-max').value.trim();
  const alertRaw = document.getElementById('shelf-limit-alert').value.trim();
  if (!/^\d+$/.test(maxRaw) || !/^\d+$/.test(alertRaw)) {
    showShelfLimitError('Type whole numbers, 0 or more.');
    return;
  }
  const maxPieces = Number(maxRaw);
  const alertPieces = Number(alertRaw);
  if (alertPieces > maxPieces) {
    showShelfLimitError('The alert level cannot be higher than the most pieces on the shelf.');
    return;
  }
  const branchId = BranchContext.get() || currentUser?.branch_id;
  const saveBtn = document.getElementById('shelf-limit-submit');
  saveBtn.disabled = true;
  try {
    const shelfSaveResult = await Api.saveShelfSetting({
      branch_id: branchId,
      product_id: currentShelfLimitProductId,
      max_shelf_quantity: maxPieces,
      replenish_alert_level: alertPieces,
    });
    closeShelfLimitModal();
    // fix43-shelf-excess
    const movedPieces = (shelfSaveResult && Number(shelfSaveResult.moved_to_warehouse)) || 0;
    const filledPieces = (shelfSaveResult && Number(shelfSaveResult.moved_to_shelf)) || 0;
    showToast(movedPieces > 0 ? 'Shelf limit saved: ' + maxPieces + '. ' + movedPieces + ' piece(s) moved to the warehouse.' : (filledPieces > 0 ? 'Shelf default saved: ' + maxPieces + '. ' + filledPieces + ' piece(s) moved from the warehouse to the shelf.' : 'Shelf default saved: ' + maxPieces + ' pieces. Nothing was moved.')); // fix46
    loadProducts();
  } catch (err) {
    showShelfLimitError(err.message);
  } finally {
    saveBtn.disabled = false;
  }
});

// PATCH4B-STOCK-SPLIT-LINE
// Grey line under the stock pill. "Shelf" is the sellable shelf stock (the same
// number as the pill), "Total" is sellable shelf plus warehouse. Pieces.
function stockSplitLine(s) {
  if (!s) return '';
  const shelf = Number(s.shelf_sellable_stock ?? 0);
  const warehouse = Number(s.warehouse_stock ?? 0);
  const total = Number(s.total_sellable_stock ?? (shelf + warehouse));
  // fix47: show the saved shelf limit next to the shelf number
  const limitText = (s.limits_configured && s.max_shelf_quantity != null) ? ' (default ' + Number(s.max_shelf_quantity) + ')' : ''; // fix48
  return `<div class="cell-sub" data-stock-split="1"><span>Shelf ${shelf}${limitText}</span><span>Warehouse ${warehouse}</span><span>Total ${total}</span></div>`;
}
