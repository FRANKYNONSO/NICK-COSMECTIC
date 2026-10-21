Auth.requireAuth();
Perms.applyNav();
Perms.guardPage('view_restock_list');

const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

const canManage = Perms.can('manage_restock_list');

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
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function productThumbHtml(imageUrl, name) {
  if (imageUrl) {
    return `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(name)}" data-enlarge-image="${escapeHtml(imageUrl)}" style="width:48px;height:48px;object-fit:cover;border-radius:10px;cursor:zoom-in;border:1px solid var(--border);" />`;
  }
  return `<div title="No photo uploaded" style="width:48px;height:48px;border-radius:10px;background:var(--bg-soft,#f2f2f2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;">
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="var(--ink-faint,#9aa3b2)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M21 15l-5-5-9 9"/>
    </svg>
  </div>`;
}

const UNIT_LABELS = [
  { v: 'pieces', label: 'Pieces' },
  { v: 'dozens', label: 'Dozens' },
  { v: 'cartons', label: 'Cartons' },
  { v: 'bags', label: 'Bags' },
  { v: 'half_dozen', label: 'Half dozen' },
  { v: 'half_carton', label: 'Half carton' },
];

function reasonTag(reason) {
  if (reason === 'out_of_stock') return '<span class="stock-pill out">Out of stock</span>';
  if (reason === 'low_stock') return '<span class="stock-pill low">Low stock</span>';
  if (reason === 'custom') return '<span class="restock-reason-tag">Manually typed</span>';
  return '<span class="restock-reason-tag">Manually added</span>';
}

// Every row needs one stable, unique key to attach DOM handlers by.
// product_id alone isn't enough any more: freeform items (typed
// straight onto the list, no catalog product) all have product_id
// null, so several of them would collide on the same key. entry_id is
// unique and present on every row except a catalog row nobody has
// edited yet (which always has a real, unique product_id instead).
function rowKey(r) {
  return r.entry_id || r.product_id;
}

let rows = [];
// Tracks which entries are ticked, independent of the DOM -- renderRows()
// rebuilds every row's HTML on each edit (simplest way to keep quantity/
// unit in sync), which was wiping out checkbox state on every single
// quantity or unit edit. Restoring `checked` from this set in
// renderRows() is what actually fixes that.
let selectedEntryIds = new Set();

async function loadRestockList() {
  const branchId = BranchContext.get();
  const noBranch = document.getElementById('restock-no-branch-state');
  const table = document.getElementById('restock-table');
  const empty = document.getElementById('restock-empty-state');
  const addCustom = document.getElementById('restock-add-custom');

  if (!branchId) {
    // A super_admin with "All branches" selected has no single branch
    // to fetch stock for -- the server requires branch_id here since,
    // unlike the Low Stock report, a shopping list needs one concrete
    // branch's stock to react to. Ask them to pick one.
    table.style.display = 'none';
    empty.style.display = 'none';
    if (addCustom) addCustom.style.display = 'none';
    noBranch.style.display = 'block';
    return;
  }
  noBranch.style.display = 'none';
  table.style.display = '';
  if (addCustom) addCustom.style.display = '';

  try {
    rows = await Api.restockList.list(branchId);
    await loadCustomerRequestMap(branchId);
  } catch (err) {
    showToast(err.message || 'Could not load the restock list.', true);
    rows = [];
  }
  renderRows();
}

function renderRows() {
  const tbody = document.getElementById('restock-tbody');
  const empty = document.getElementById('restock-empty-state');
  // Drop selections for entries that no longer exist (removed,
  // downloaded, or restocked off the list) so the count in the bottom
  // bar stays honest.
  const liveKeys = new Set(rows.map(rowKey));
  selectedEntryIds.forEach((id) => { if (!liveKeys.has(id)) selectedEntryIds.delete(id); });

  if (!rows.length) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    updateBottomBar();
    document.getElementById('restock-select-all').checked = false;
    return;
  }
  empty.style.display = 'none';

  tbody.innerHTML = rows.map((r) => {
    const key = rowKey(r);
    const unitOptions = UNIT_LABELS.map((u) =>
      `<option value="${u.v}" ${u.v === r.unit_label ? 'selected' : ''}>${u.label}</option>`
    ).join('');
    const showQtyBox = !(r.unit_label === 'half_dozen' || r.unit_label === 'half_carton');
    const disabled = canManage ? '' : 'disabled';
    const isChecked = r.entry_id && selectedEntryIds.has(key) ? 'checked' : '';
    return `<tr data-row-key="${key}">
      <td class="thumb-cell">${canManage ? `<input type="checkbox" class="restock-row-check" data-entry-id="${r.entry_id || ''}" data-row-key="${key}" ${isChecked}  />` : ''}</td>
      <td class="thumb-cell">${productThumbHtml(r.image_url, r.product_name)}</td>
      <td>
        <div style="font-weight:600;">${escapeHtml(r.product_name)}</div>
        <div class="restock-reason-tag">${escapeHtml(r.category_name || '')}</div>
      </td>
      <td>${reasonTag(r.reason)}${customerBadgeHtml(r)}</td>
      <td>
        <div style="display:flex; gap:6px; align-items:center;">
          ${showQtyBox ? `<input type="number" min="1" value="${r.quantity}" style="width:64px;" class="restock-qty-input" data-row-key="${key}" ${disabled} />` : ''}
          <select class="restock-unit-select" data-row-key="${key}" ${disabled}>${unitOptions}</select>
        </div>
      </td>
      <td>${canManage ? `<button type="button" class="btn btn-ghost btn-sm restock-remove-btn" data-entry-id="${r.entry_id || ''}" data-row-key="${key}" style="width:auto;">Remove</button>` : ''}</td>
    </tr>`;
  }).join('');

  wireRowEvents();
  updateBottomBar();
  const allBoxes = document.querySelectorAll('.restock-row-check:not(:disabled)');
  document.getElementById('restock-select-all').checked =
    allBoxes.length > 0 && [...allBoxes].every((el) => el.checked);
}

async function persistQuantityUnit(key, quantity, unitLabel) {
  const row = rows.find((r) => rowKey(r) === key);
  if (!row) return;
  const branchId = BranchContext.get();
  try {
    let result;
    if (row.entry_id) {
      result = await Api.restockList.updateItem(row.entry_id, { quantity, unitLabel });
    } else {
      result = await Api.restockList.addItem({ productId: row.product_id, branchId, quantity, unitLabel, isManual: row.reason === 'manual' });
    }
    Object.assign(row, result);
    renderRows();
  } catch (err) {
    showToast(err.message || 'Could not save that change.', true);
  }
}

async function persistThenSelect(key, wantSelected) {
  const row = rows.find((r) => rowKey(r) === key);
  if (!row) return;
  if (!wantSelected) { renderRows(); return; }
  const qtyInput = document.querySelector(`.restock-qty-input[data-row-key="${key}"]`);
  const unitSelect = document.querySelector(`.restock-unit-select[data-row-key="${key}"]`);
  await persistQuantityUnit(
    key,
    qtyInput ? Number(qtyInput.value) || 1 : (row.quantity || 1),
    unitSelect ? unitSelect.value : (row.unit_label || 'pieces')
  );
  if (row.entry_id) { selectedEntryIds.add(rowKey(row)); renderRows(); }
}

function wireRowEvents() {
  document.querySelectorAll('.restock-qty-input').forEach((el) => {
    el.addEventListener('change', () => {
      const key = el.dataset.rowKey;
      const row = rows.find((r) => rowKey(r) === key);
      const unitSelect = document.querySelector(`.restock-unit-select[data-row-key="${key}"]`);
      persistQuantityUnit(key, Number(el.value) || 1, unitSelect ? unitSelect.value : (row ? row.unit_label : 'pieces'));
    });
  });
  document.querySelectorAll('.restock-unit-select').forEach((el) => {
    el.addEventListener('change', () => {
      const key = el.dataset.rowKey;
      const row = rows.find((r) => rowKey(r) === key);
      const qtyInput = document.querySelector(`.restock-qty-input[data-row-key="${key}"]`);
      persistQuantityUnit(key, qtyInput ? Number(qtyInput.value) || 1 : (row ? row.quantity : 1), el.value);
    });
  });
  document.querySelectorAll('.restock-remove-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const entryId = btn.dataset.entryId;
      const key = btn.dataset.rowKey;
      if (!entryId) {
        // Never touched/persisted -- nothing on the server to delete,
        // just drop it from the current view.
        rows = rows.filter((r) => rowKey(r) !== key);
        renderRows();
        return;
      }
      if (!confirm(removeConfirmText(entryId))) return;
      try {
        await Api.restockList.removeItem(entryId);
        rows = rows.filter((r) => r.entry_id !== entryId);
        selectedEntryIds.delete(key);
        renderRows();
        showToast('Removed from restock list.');
      } catch (err) {
        showToast(err.message || 'Could not remove that item.', true);
      }
    });
  });
  document.querySelectorAll('.restock-row-check').forEach((el) => {
    el.addEventListener('change', () => {
      const entryId = el.dataset.entryId;
      const key = el.dataset.rowKey;
      if (!entryId) { persistThenSelect(key, el.checked); return; }
      if (el.checked) selectedEntryIds.add(key); else selectedEntryIds.delete(key);
      updateBottomBar();
    });
  });
}

document.getElementById('restock-select-all').addEventListener('change', (e) => {
  document.querySelectorAll('.restock-row-check:not(:disabled)').forEach((el) => {
    if (!el.dataset.entryId) { if (e.target.checked) persistThenSelect(el.dataset.rowKey, true); return; }
    el.checked = e.target.checked;
    if (e.target.checked) selectedEntryIds.add(el.dataset.rowKey); else selectedEntryIds.delete(el.dataset.rowKey);
  });
  updateBottomBar();
});

function updateBottomBar() {
  const count = selectedEntryIds.size;
  const bar = document.getElementById('restock-bottombar');
  document.getElementById('restock-sel-count').textContent = `${count} item${count === 1 ? '' : 's'} selected`;
  bar.classList.toggle('show', count > 0);
}

document.getElementById('restock-review-btn').addEventListener('click', () => {
  const checkedKeys = [...selectedEntryIds];
  if (!checkedKeys.length) return;
  // Supplier is optional -- typing one just labels the PDF and, if
  // it's a new name, adds it to the shared supplier registry the
  // Invoice page also uses.
  const supplierName = document.getElementById('restock-supplier-name').value.trim();

  const rowsSel = checkedKeys.map((key) => rows.find((r) => rowKey(r) === key)).filter(Boolean);
  document.getElementById('restock-confirm-sub').textContent = supplierName
    ? `Sending to ${supplierName} — these items will leave the list once downloaded.`
    : 'No supplier specified — these items will still leave the list once downloaded.';
  document.getElementById('restock-confirm-rows').innerHTML = rowsSel.map((r) => {
    const unitLabel = (UNIT_LABELS.find((u) => u.v === r.unit_label) || {}).label || r.unit_label;
    const qtyText = (r.unit_label === 'half_dozen' || r.unit_label === 'half_carton') ? unitLabel : `${r.quantity} × ${unitLabel}`;
    return `<div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border);">
      <span>${escapeHtml(r.product_name)}</span><strong>${escapeHtml(qtyText)}</strong>
    </div>`;
  }).join('');
  window._restockPending = { entryIds: rowsSel.map((r) => r.entry_id), supplierName };
  document.getElementById('restock-confirm-backdrop').classList.add('visible');
});

document.getElementById('restock-confirm-back').addEventListener('click', () => {
  document.getElementById('restock-confirm-backdrop').classList.remove('visible');
});

document.getElementById('restock-confirm-go').addEventListener('click', async () => {
  const pending = window._restockPending;
  if (!pending) return;
  try {
    await Api.restockList.download(pending.entryIds, pending.supplierName || null);
    showToast('Downloaded — those items have been removed from the list.');
    document.getElementById('restock-confirm-backdrop').classList.remove('visible');
    document.getElementById('restock-supplier-name').value = '';
    selectedEntryIds.clear();
    await loadRestockList();
    await loadSupplierOptions(); // pick up a newly-typed supplier immediately
  } catch (err) {
    showToast(err.message || 'Could not generate the download.', true);
  }
});

// Existing suppliers (same registry the Invoice page's supplier picker
// uses) feed a <datalist> so typing shows matching names, while still
// allowing a brand-new name to be typed freely -- the backend creates
// it on download if it doesn't already exist.
async function loadSupplierOptions() {
  try {
    const suppliers = await Api.listSuppliers();
    const datalist = document.getElementById('restock-supplier-datalist');
    datalist.innerHTML = suppliers.map((s) => `<option value="${escapeHtml(s.name)}"></option>`).join('');
  } catch (err) {
    // Non-critical -- the field still works as free text without it.
  }
}
loadSupplierOptions();

// "Add a new item not in your inventory" -- name + optional photo,
// straight onto the list, no catalog Product behind it.
document.getElementById('custom-item-add-btn').addEventListener('click', async () => {
  const branchId = BranchContext.get();
  if (!branchId) return;
  const nameInput = document.getElementById('custom-item-name');
  const photoInput = document.getElementById('custom-item-photo');
  const qtyInput = document.getElementById('custom-item-qty');
  const unitSelect = document.getElementById('custom-item-unit');
  const name = nameInput.value.trim();
  if (!name) { showToast('Type an item name first.', true); return; }
  const customerPayload = typeof CustomerOrder !== 'undefined' ? CustomerOrder.collect() : null;
  if (customerPayload === false) return;

  const btn = document.getElementById('custom-item-add-btn');
  btn.disabled = true;
  btn.textContent = 'Adding…';
  try {
    let imageUrl = null;
    if (photoInput.files && photoInput.files[0]) {
      const uploadResult = await Api.restockList.uploadImage(photoInput.files[0]);
      imageUrl = uploadResult.url;
    }
    if (customerPayload) {
      await Api.customerRequests.create(Object.assign({
        branch_id: branchId,
        item_name: name,
        item_image_url: imageUrl,
        quantity: Number(qtyInput.value) || 1,
        unit_label: unitSelect.value,
      }, customerPayload));
      CustomerOrder.reset();
    } else {
      await Api.restockList.addItem({
      branchId,
      customName: name,
      customImageUrl: imageUrl,
      quantity: Number(qtyInput.value) || 1,
      unitLabel: unitSelect.value,
    });
    }
    nameInput.value = '';
    photoInput.value = '';
    qtyInput.value = '1';
    unitSelect.value = 'pieces';
    showToast(customerPayload ? `Added "${name}" and recorded the customer's deposit.` : `Added "${name}" to the restock list.`);
    await loadRestockList();
  } catch (err) {
    showToast(err.message || 'Could not add that item.', true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Add to list';
  }
});

// Click-to-enlarge lightbox -- same pattern as low-stock.js/inventory.js.
document.addEventListener('click', (e) => {
  const trigger = e.target.closest('[data-enlarge-image]');
  if (!trigger) return;
  document.getElementById('image-lightbox-img').src = trigger.dataset.enlargeImage;
  document.getElementById('image-lightbox-backdrop').classList.add('visible');
});
document.getElementById('image-lightbox-close').addEventListener('click', () => {
  document.getElementById('image-lightbox-backdrop').classList.remove('visible');
});

BranchContext.renderBadge('branch-badge');
window.addEventListener('branch-context-changed', loadRestockList);
loadRestockList();


// ======================================================================
// Customer orders (deposit pre-orders) -- added by fix-customer-requests.
// "Add a new item not in your inventory" can now also record the
// customer who asked for it and the deposit they paid. The order itself
// is tracked on customer-requests.html; here we only collect the details
// and badge the matching restock rows.
// ======================================================================
var customerRequestsByEntry = new Map();

function crEsc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function crNaira(amount) {
  return '\u20A6' + Number(amount || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function loadCustomerRequestMap(branchId) {
  customerRequestsByEntry = new Map();
  try {
    if (!(typeof Api !== 'undefined' && Api.customerRequests)) return;
    const open = await Api.customerRequests.list(branchId, 'open');
    open.forEach((req) => {
      if (!req.restock_list_entry_id) return;
      const list = customerRequestsByEntry.get(req.restock_list_entry_id) || [];
      list.push(req);
      customerRequestsByEntry.set(req.restock_list_entry_id, list);
    });
  } catch (err) {
    // Non-critical -- the list works without the badges.
  }
}

function customerBadgeHtml(r) {
  const list = r && r.entry_id ? customerRequestsByEntry.get(r.entry_id) : null;
  if (!list || !list.length) return '';
  return list.map((req) =>
    '<div class="restock-reason-tag" style="color:var(--accent,#6c4bd6);font-weight:600;">' +
    '<a href="customer-requests.html#pending" style="color:inherit;text-decoration:none;">Customer order: ' +
    crEsc(req.customer_name) + ' &middot; deposit ' + crEsc(crNaira(req.deposit_naira)) + '</a></div>'
  ).join('');
}

function removeConfirmText(entryId) {
  const list = customerRequestsByEntry.get(entryId);
  if (list && list.length) {
    return 'This item is attached to ' + list.length + ' customer order(s) (' +
      list.map((q) => q.customer_name).join(', ') +
      '). Removing it from the restock list does NOT cancel the order or the deposit - it stays under Customer Orders. Remove it from the list anyway?';
  }
  return 'Remove this item from the restock list?';
}

var CustomerOrder = (function () {
  const host = document.getElementById('restock-add-custom');
  if (!host) return { collect: function () { return null; }, reset: function () {} };

  const panel = document.createElement('div');
  panel.id = 'customer-order-panel';
  panel.style.cssText = 'margin-top:12px;padding-top:12px;border-top:1px dashed var(--border);';
  panel.innerHTML =
    '<label style="display:flex;gap:8px;align-items:center;font-weight:600;cursor:pointer;">' +
    '<input type="checkbox" id="co-toggle" /> This is for a customer who paid a deposit</label>' +
    '<div id="co-fields" style="display:none;margin-top:12px;">' +
    '<div style="display:flex;gap:10px;flex-wrap:wrap;">' +
    '<input type="text" id="co-name" placeholder="Customer name" maxlength="200" style="flex:1;min-width:180px;" />' +
    '<input type="tel" id="co-phone" placeholder="Phone number" maxlength="40" style="flex:1;min-width:160px;" />' +
    '<input type="number" id="co-deposit" placeholder="Deposit paid (\u20A6)" min="0" step="0.01" style="width:170px;" />' +
    '</div>' +
    '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">' +
    '<select id="co-method" style="display:none;width:210px;"><option value="">How was the deposit paid?</option><option value="cash">Cash</option><option value="transfer">Transfer</option><option value="card">Card</option><option value="online">Online</option></select>' +
    '<div style="display:flex;flex-direction:column;gap:3px;"><label for="co-total-pieces" style="font-size:12px;font-weight:600;">Total pieces in this order</label>' +
    '<input type="number" id="co-total-pieces" placeholder="Total pieces in this order" min="1" step="1" style="width:220px;" /></div>' +
    '<div style="display:flex;flex-direction:column;gap:3px;"><label for="co-need-pieces" style="font-size:12px;font-weight:600;">Customer needs (pieces)</label>' +
    '<input type="number" id="co-need-pieces" placeholder="Customer needs (pieces)" min="1" step="1" style="width:220px;" /></div>' +
    '</div>' +
    '<div class="restock-reason-tag" style="margin-top:6px;">"Customer needs" starts equal to the total. Lower it if the customer wants fewer pieces.</div>' +
    '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">' +
    '<input type="text" id="co-address" placeholder="Address or other way to reach the customer" maxlength="500" style="flex:1;min-width:240px;" />' +
    '<input type="text" id="co-notes" placeholder="Notes (colour, size, how they want it...)" maxlength="2000" style="flex:1;min-width:240px;" />' +
    '</div>' +
    '<div class="restock-reason-tag" style="margin-top:8px;">The item goes on the restock list as usual. When it reaches the shelf, a booking is made for the "Customer needs" pieces automatically, and the bell reminds you to contact the customer.</div>' +
    '</div>';
  host.appendChild(panel);

  const toggle = panel.querySelector('#co-toggle');
  const fields = panel.querySelector('#co-fields');
  toggle.addEventListener('change', function () { fields.style.display = toggle.checked ? '' : 'none'; });

  function val(id) { return panel.querySelector('#' + id).value.trim(); }

  // PATCH1-CUSTOMER-ORDER-FIELDS
  const qtyEl = document.getElementById('custom-item-qty');
  const unitEl = document.getElementById('custom-item-unit');
  const methodEl = panel.querySelector('#co-method');
  const depositEl = panel.querySelector('#co-deposit');
  const totalEl = panel.querySelector('#co-total-pieces');
  function syncTotalPieces(force) {
    if (!qtyEl || !unitEl) return;
    const unit = unitEl.value;
    const qty = Number(qtyEl.value) || 0;
    if (unit === 'pieces') {
      totalEl.value = qty > 0 ? String(qty) : '';
      totalEl.readOnly = true;
      totalEl.placeholder = 'Total pieces in this order';
      return;
    }
    totalEl.readOnly = false;
    if (force) totalEl.dataset.edited = '';
    if (totalEl.dataset.edited) return;
    if (unit === 'dozens') {
      totalEl.value = qty > 0 ? String(qty * 12) : '';
    } else {
      totalEl.value = '';
      totalEl.placeholder = 'Type the total pieces';
    }
  }
  totalEl.addEventListener('input', function () { totalEl.dataset.edited = totalEl.value ? '1' : ''; });
  if (qtyEl) qtyEl.addEventListener('input', function () { syncTotalPieces(false); });
  if (unitEl) unitEl.addEventListener('change', function () { syncTotalPieces(true); });
  depositEl.addEventListener('input', function () {
    const has = Number(depositEl.value) > 0;
    methodEl.style.display = has ? '' : 'none';
    if (!has) methodEl.value = '';
  });
  toggle.addEventListener('change', function () { if (toggle.checked) syncTotalPieces(false); });

  // PATCH2C-CUSTOMER-NEEDS: "Customer needs" starts equal to the total and can be lowered.
  const needEl = panel.querySelector('#co-need-pieces');
  function syncNeed() {
    const total = Number(totalEl.value) || 0;
    if (!(total > 0)) {
      if (!needEl.dataset.edited) needEl.value = '';
      return;
    }
    const cur = Number(needEl.value) || 0;
    if (!needEl.dataset.edited || cur > total || cur <= 0) {
      needEl.value = String(total);
      needEl.dataset.edited = '';
    }
  }
  needEl.addEventListener('input', function () { needEl.dataset.edited = needEl.value ? '1' : ''; });
  totalEl.addEventListener('input', syncNeed);
  if (qtyEl) qtyEl.addEventListener('input', syncNeed);
  if (unitEl) unitEl.addEventListener('change', syncNeed);
  toggle.addEventListener('change', function () { if (toggle.checked) syncNeed(); });

  return {
    // null -> not a customer order; false -> invalid (already toasted);
    // object -> the customer fields for the API.
    collect: function () {
      if (!toggle.checked) return null;
      if (!(typeof Api !== 'undefined' && Api.customerRequests)) {
        showToast('Customer orders are not connected to the API yet - tell Claude.', true);
        return false;
      }
      const name = val('co-name');
      const phone = val('co-phone');
      const deposit = Number(val('co-deposit'));
      if (!name) { showToast("Enter the customer's name.", true); return false; }
      if (phone.replace(/\D/g, '').length < 7) { showToast("Enter the customer's phone number.", true); return false; }
      // PATCH1-CUSTOMER-ORDER-FIELDS (a deposit of 0 is allowed now)
      if (!(deposit >= 0)) { showToast('The deposit must be 0 or more.', true); return false; }
      const method = val('co-method');
      if (deposit > 0 && !method) { showToast('Choose how the deposit was paid.', true); return false; }
      const unitNow = unitEl ? unitEl.value : 'pieces';
      const totalPieces = Number(val('co-total-pieces')) || 0;
      const needPieces = Number(val('co-need-pieces')) || 0;
      if ((totalPieces && !Number.isInteger(totalPieces)) || (needPieces && !Number.isInteger(needPieces))) { showToast('Pieces must be whole numbers.', true); return false; }
      if (!(totalPieces > 0)) { showToast('Type the total pieces in this order.', true); return false; }
      if (!(needPieces > 0)) { showToast('Type how many pieces the customer needs.', true); return false; }
      if (needPieces > totalPieces) { showToast('The customer cannot need more pieces than the whole order.', true); return false; }
      return {
        customer_name: name,
        customer_phone: phone,
        customer_address: val('co-address') || null,
        notes: val('co-notes') || null,
        deposit_naira: deposit,
        deposit_method: deposit > 0 ? method : null,
        order_pieces: totalPieces > 0 ? totalPieces : null,
        customer_pieces: needPieces > 0 ? needPieces : null
      };
    },
    reset: function () {
      ['co-name', 'co-phone', 'co-deposit', 'co-address', 'co-notes'].forEach(function (id) { panel.querySelector('#' + id).value = ''; });
      ['co-total-pieces', 'co-need-pieces', 'co-method'].forEach(function (id) { panel.querySelector('#' + id).value = ''; });
      totalEl.dataset.edited = '';
      needEl.dataset.edited = '';
      methodEl.style.display = 'none';
      toggle.checked = false;
      fields.style.display = 'none';
    }
  };
})();
