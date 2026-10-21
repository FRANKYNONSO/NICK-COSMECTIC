Auth.requireAuth();
Perms.guardPageAny(['view_stock', 'manage_inventory']);
Perms.applyNav();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

// Staff get a simplified view: no Receive Stock / Push to Store forms
// (warehouse-edit-only) and no ability to log a new delivery receipt
// (warehouse-admin-only) -- both blocked server-side too
// (manage_inventory), this is UX only. What staff DO keep, alongside
// the warehouse stock-on-hand history: the full "Branch-to-branch
// transfer history" (view-only, no class restricting it) so every
// staff member can see who pushed what and when, and the "Waybill
// complaint history" table further down (already-logged receipts and
// their damaged/missing counts are viewable records, just not
// creatable by staff).
if (!Perms.can('manage_inventory')) {
  document.querySelectorAll('.warehouse-edit-only, .warehouse-admin-only').forEach((el) => { el.style.display = 'none'; });
  const subtitle = document.querySelector('.page-head p');
  if (subtitle) subtitle.textContent = 'View what\u2019s currently stocked in the warehouse.';
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

function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { maximumFractionDigits: 2 })}`;
}

// -------------------------------------------------------- Section tabs
//
// September 2026 restructure: the four real warehouse workflows (Push
// to Warehouse, Warehouse Activity History, Branch-to-Branch Transfer,
// Log a Waybill Complaint) are now tabbed panels instead of a long
// vertical stack, matching the .tabs pattern already used on
// Performance Records (records-metric-tabs / records-period-tabs).
document.getElementById('warehouse-section-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-section]');
  if (!btn) return;
  const section = btn.dataset.section;
  document.querySelectorAll('#warehouse-section-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.warehouse-panel').forEach((p) => {
    p.style.display = p.dataset.panel === section ? '' : 'none';
  });
});

// ------------------------------------------------------------ Branch context
// The "viewing" branch (intake receipts / transfers list) now comes
// from the Dashboard's global branch picker, not a local one here --
// see BranchContext in api.js. The "From"/"To" pickers on the Push to
// store / transfer form are a different, legitimate use of a branch
// selector (choosing the two branches involved in a transfer), so
// those are untouched below.

let currentBranchId = BranchContext.get() || null;
let branchCache = [];

function currentBranchName() {
  return branchCache.find((b) => b.id === currentBranchId)?.name || 'Pick a branch on the Dashboard';
}

// From branch for the branch-to-branch transfer form is always the
// current dashboard/session branch -- never manually selectable
// (spec section 13). Shown as a read-only badge, same pattern as the
// invoice page's branch badge.
function renderTrFromBadge() {
  const badge = document.getElementById('tr-from-badge');
  if (badge) badge.textContent = currentBranchName();
}

window.addEventListener('branch-context-changed', (e) => {
  currentBranchId = e.detail.branchId || null;
  renderTrFromBadge();
  loadIntakeReceipts();
  loadTransfers();
  loadWarehouseStock();
  loadWarehouseHistory();
  loadPushableInvoiceItems();
  refreshTransferAvailability();
});

async function loadBranches() {
  try {
    branchCache = await Api.listBranches();
    BranchContext.renderBadge('branch-badge');
    if (!currentBranchId && branchCache.length) {
      showToast('Pick a branch on the Dashboard to view its warehouse.', true);
    }

    // Populate the "To" branch picker with real branches -- there's
    // no separate "Warehouse" entity in this system, just branches.
    // "From" is never manually selectable (spec section 13): it's
    // always the branch tied to the current dashboard/session,
    // rendered as a read-only badge below instead of a dropdown.
    const toSelect = document.getElementById('tr-to');
    const opts = branchCache.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
    toSelect.innerHTML = opts;
    renderTrFromBadge();
  } catch (err) {
    showToast(err.message, true);
  }
}

// ------------------------------------------------------ Category/product/variant
//
// Cascading category -> product -> variant pickers for the Push to store
// form (element-id prefix "tr-"). Parameterized by prefix so it can still
// serve more than one form if one gets added later.

async function loadCategoriesFilter(prefix) {
  try {
    const categories = await Api.listCategories();
    const select = document.getElementById(`${prefix}-category`);
    select.innerHTML = '<option value="">All categories</option>' +
      categories.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  } catch (err) {
    showToast(err.message, true);
  }
}

// Item 13: the "Quick-add category/product/branch/unit label" buttons
// that used to live on this Branch-to-Branch Transfer form were
// removed -- those things should already exist (created via Add
// Product, Branches, etc.) before a transfer happens, so offering to
// create them mid-transfer just invited inconsistent/duplicate data.
// The tr-category/tr-product/tr-to/tr-unit selects themselves are
// untouched; they still populate from loadCategoriesFilter/
// loadProductsFilter/loadBranches/the fixed unit list below.

// Unit label is a fixed dropdown (carton/cartons/dozens/pieces) plus
// whatever labels have been quick-added in this browser -- stored
// locally since there's no dedicated "unit labels" table on the
// backend; the transfer itself just saves whichever string is selected
// (see migration m9n0o1p2q3r4 dropping the old piece/carton/dozen-only
// check constraint).
const UNIT_LABEL_STORAGE_KEY = 'nickcosmetics_custom_unit_labels';
function loadCustomUnitLabels() {
  try {
    return JSON.parse(localStorage.getItem(UNIT_LABEL_STORAGE_KEY) || '[]');
  } catch (err) {
    return [];
  }
}
function applyCustomUnitLabels() {
  const select = document.getElementById('tr-unit');
  const existing = new Set([...select.options].map((o) => o.value));
  loadCustomUnitLabels().forEach((label) => {
    if (!existing.has(label)) {
      const opt = document.createElement('option');
      opt.value = label;
      opt.textContent = label;
      select.appendChild(opt);
    }
  });
}
applyCustomUnitLabels();

// Unit-label-specific field labels/visibility -- "Cartons"/"Dozens" both
// take a count plus how many pieces are in one of them; "Pieces" is
// just a piece count on its own (pieces_per_unit is sent as 1, hidden,
// so quantity_pieces = unit_count * 1 still works server-side without
// a schema change). A custom quick-added label (e.g. "bag") falls back
// to the same two-field shape as Cartons/Dozens, generically worded.
const UNIT_FIELD_CONFIG = {
  carton: { countLabel: 'Carton count', perUnitLabel: 'Pieces per carton', showPerUnit: true },
  dozen: { countLabel: 'Dozen count', perUnitLabel: 'Pieces per dozen', showPerUnit: true },
  bag: { countLabel: 'Bag count', perUnitLabel: 'Pieces per bag', showPerUnit: true },
  piece: { countLabel: 'Pieces count', perUnitLabel: null, showPerUnit: false },
};

function applyUnitFieldConfig() {
  const unit = document.getElementById('tr-unit').value;
  const config = UNIT_FIELD_CONFIG[unit] || {
    countLabel: 'Count',
    perUnitLabel: `Pieces per ${unit || 'unit'}`,
    showPerUnit: true,
  };
  document.getElementById('tr-count-label').textContent = config.countLabel;
  const perUnitGroup = document.getElementById('tr-per-unit-group');
  const perUnitInput = document.getElementById('tr-per-unit');
  if (config.showPerUnit) {
    perUnitGroup.style.display = '';
    document.getElementById('tr-per-unit-label').textContent = config.perUnitLabel;
    perUnitInput.required = true;
  } else {
    perUnitGroup.style.display = 'none';
    perUnitInput.required = false;
    perUnitInput.value = 1;
  }
}
document.getElementById('tr-unit').addEventListener('change', applyUnitFieldConfig);
applyUnitFieldConfig();

async function loadProductsFilter(prefix) {
  try {
    const categoryId = document.getElementById(`${prefix}-category`).value || undefined;
    const products = await Api.listProducts({ categoryId });
    const select = document.getElementById(`${prefix}-product`);
    select.innerHTML = products.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}${p.size ? ` (${escapeHtml(p.size)})` : ''}</option>`).join('');
    await loadVariantsFilter(prefix);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadVariantsFilter(prefix) {
  // Variants were retired (update spec 3, Section 6) -- a Product is
  // now the sellable unit on its own. This dropdown is kept (rather
  // than removed, which would mean rewiring every submit handler and
  // HTML file that reads `${prefix}-variant`.value) but now just
  // mirrors the single selected product, so it still resolves to the
  // right id everywhere downstream.
  const productId = document.getElementById(`${prefix}-product`).value;
  const select = document.getElementById(`${prefix}-variant`);
  if (!productId) {
    select.innerHTML = '';
    return;
  }
  try {
    const product = await Api.getProduct(productId);
    select.innerHTML = `<option value="${product.id}">${escapeHtml(product.name)} — ${escapeHtml(product.sku)} (${formatNaira(product.selling_price_naira)})</option>`;
  } catch (err) {
    showToast(err.message, true);
  }
}

document.getElementById('tr-category').addEventListener('change', () => loadProductsFilter('tr'));
document.getElementById('tr-product').addEventListener('change', () => {
  loadVariantsFilter('tr');
  refreshTransferAvailability();
});

// September 2026 change spec section 19: "cap the transfer at what
// the warehouse actually shows as available" -- shown live as the
// product selection changes, so the cashier knows the ceiling before
// even trying to submit (the server still enforces the real cap at
// confirm time -- this is a helpful preview, not the source of truth).
let transferAvailablePieces = null;
async function refreshTransferAvailability() {
  const hint = document.getElementById('tr-availability-hint');
  const productId = document.getElementById('tr-variant').value;
  if (!currentBranchId || !productId) {
    hint.textContent = '';
    transferAvailablePieces = null;
    return;
  }
  hint.textContent = 'Checking availability…';
  try {
    const levels = await Api.stockLevels(currentBranchId);
    const level = levels.find((l) => l.product_id === productId);
    transferAvailablePieces = level ? level.quantity_available : 0;
    hint.textContent = `${transferAvailablePieces} piece(s) available at this branch.`;
  } catch (err) {
    hint.textContent = '';
    transferAvailablePieces = null;
  }
}

// ---------------------------------------------------------------- Push to store

document.getElementById('transfer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('transfer-error');
  errorEl.classList.remove('visible');

  const productId = document.getElementById('tr-variant').value;
  const toBranchId = document.getElementById('tr-to').value;
  const paymentMethod = document.getElementById('tr-payment-method').value;

  if (!currentBranchId) {
    errorEl.textContent = 'Pick a branch on the Dashboard first.';
    errorEl.classList.add('visible');
    return;
  }
  if (!productId) {
    errorEl.textContent = 'Pick a product.';
    errorEl.classList.add('visible');
    return;
  }
  if (currentBranchId === toBranchId) {
    errorEl.textContent = 'From and To must be different branches.';
    errorEl.classList.add('visible');
    return;
  }
  if (!paymentMethod) {
    errorEl.textContent = 'Pick how the receiving branch will settle this transfer.';
    errorEl.classList.add('visible');
    return;
  }

  const unitLabel = document.getElementById('tr-unit').value;
  const unitCount = Number(document.getElementById('tr-count').value || 0);
  const piecesPerUnit = unitLabel === 'piece' ? 1 : Number(document.getElementById('tr-per-unit').value || 1);
  const quantityPieces = unitCount * piecesPerUnit;

  if (transferAvailablePieces != null && quantityPieces > transferAvailablePieces) {
    errorEl.textContent = `Only ${transferAvailablePieces} piece(s) available -- can't transfer ${quantityPieces}.`;
    errorEl.classList.add('visible');
    return;
  }

  const payload = {
    product_id: productId,
    from_branch_id: currentBranchId,
    to_branch_id: toBranchId,
    unit_label: unitLabel,
    unit_count: unitCount,
    pieces_per_unit: piecesPerUnit,
    payment_method: paymentMethod,
    notes: document.getElementById('tr-notes').value || null,
  };

  await openTransferConfirmModal(payload, productId, toBranchId, quantityPieces);
});

// September 2026 change spec section 19: "On confirming, show a
// receipt-style summary (items, cost, who processed it) before final
// confirmation." Cost is priced at supplier/invoice cost -- computed
// via a dry-run FIFO preview here (nothing is deducted until "Confirm
// & transfer" is actually clicked).
let pendingTransferPayload = null;
async function openTransferConfirmModal(payload, productId, toBranchId, quantityPieces) {
  const backdrop = document.getElementById('transfer-confirm-backdrop');
  const sheet = document.getElementById('transfer-confirm-sheet');
  const errorEl = document.getElementById('transfer-confirm-error');
  errorEl.classList.remove('visible');
  pendingTransferPayload = payload;
  sheet.innerHTML = 'Working out the cost…';
  backdrop.classList.add('visible');

  let product = {};
  try {
    product = await Api.getProduct(productId);
  } catch (err) {
    // Non-fatal -- the summary below just shows "Product" as a fallback.
  }
  try {
    const preview = await Api.previewTransferCost({
      productId, fromBranchId: currentBranchId, quantityPieces,
    });
    sheet.innerHTML = `
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span>Item</span><strong>${escapeHtml(product.name || 'Product')}</strong></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span>Quantity</span><strong>${payload.unit_count} ${escapeHtml(payload.unit_label)}(s) = ${quantityPieces} pcs</strong></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span>From → To</span><strong>${escapeHtml(branchName(currentBranchId))} → ${escapeHtml(branchName(toBranchId))}</strong></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span>Cost (supplier price)</span><strong>${formatNaira(preview.total_cost_naira)}</strong></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span>Settlement</span><strong>${payload.payment_method === 'cash' ? 'Cash' : 'Bank transfer'}</strong></div>
      <div style="display:flex;justify-content:space-between;"><span>Processed by</span><strong>${escapeHtml(currentUser?.full_name || 'You')}</strong></div>
    `;
  } catch (err) {
    sheet.innerHTML = '';
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  }
}
document.getElementById('transfer-confirm-back')?.addEventListener('click', () => {
  document.getElementById('transfer-confirm-backdrop').classList.remove('visible');
});
document.getElementById('transfer-confirm-submit')?.addEventListener('click', async () => {
  const btn = document.getElementById('transfer-confirm-submit');
  const errorEl = document.getElementById('transfer-confirm-error');
  errorEl.classList.remove('visible');
  btn.disabled = true;
  try {
    const result = await Api.createStockTransfer(pendingTransferPayload);
    document.getElementById('transfer-confirm-backdrop').classList.remove('visible');
    showToast(`Transferred ${result.quantity_pieces} piece(s) — ${formatNaira(result.total_cost_naira || 0)}.`);
    document.getElementById('tr-count').value = 1;
    document.getElementById('tr-per-unit').value = 1;
    document.getElementById('tr-payment-method').value = '';
    document.getElementById('tr-notes').value = '';
    refreshTransferAvailability();
    loadTransfers();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
  }
});

function branchName(id) {
  return branchCache.find((b) => b.id === id)?.name || 'Unknown branch';
}

// Defensively avoids "2 cartonss" if an older transfer has a
// unit_label already saved in plural form (from before unit values
// were standardized to singular) -- only appends "s" when the label
// doesn't already end in one.
function pluralizeUnit(label, count) {
  if (count === 1) return label;
  return label.endsWith('s') ? label : `${label}s`;
}

// Quantity text for a transfer row. New-style pushes (unit_label 'mixed')
// show their cartons/dozens/pieces breakdown; everything else keeps the
// original "2 cartons (48 pcs)" wording.
function whTransferQtyText(t) {
  if (t.unit_label === 'mixed') return whPushBreakdown(t);
  return `${t.unit_count} ${escapeHtml(pluralizeUnit(t.unit_label, t.unit_count))} (${t.quantity_pieces} pcs)`;
}

async function loadTransfers() {
  const tbody = document.getElementById('transfers-tbody');
  const emptyState = document.getElementById('transfers-empty');
  if (!currentBranchId) return;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading…</td></tr>';
  try {
    // Branch-to-branch history only shows the plain shop-to-shop moves
    // -- the three warehouse-specific move types get their own
    // "Warehouse activity history" table (loadWarehouseHistory).
    const transfers = await Api.listStockTransfers({ branchId: currentBranchId, transferType: 'branch_to_branch' });
    if (!transfers.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = transfers.map((t) => `
      <tr>
        <td>${new Date(t.created_at).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
        <td>${escapeHtml(branchName(t.from_branch_id))} → ${escapeHtml(branchName(t.to_branch_id))}</td>
        <td>${t.unit_count} ${escapeHtml(pluralizeUnit(t.unit_label, t.unit_count))} (${t.quantity_pieces} pcs)</td>
        <td>${t.created_by_name ? escapeHtml(t.created_by_name) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td>${t.notes ? escapeHtml(t.notes) : '<span style="color:var(--ink-faint)">—</span>'}</td>
      </tr>`).join('');
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

// ------------------------------------------------------- Warehouse ledger
// Backstock sitting in this branch's own warehouse -- separate from
// the sellable "Stock on hand" list (now on Performance Records). See
// WarehouseStock.

function formatWarehouseUnitBreakdown(breakdown) {
  if (!breakdown || !breakdown.length) return '—';
  return breakdown.map((b) => `${b.unit_count} ${pluralizeUnit(b.unit_label, b.unit_count)}`).join(', ');
}

async function loadWarehouseStock() {
  const tbody = document.getElementById('warehouse-stock-tbody');
  const emptyState = document.getElementById('warehouse-stock-empty');
  if (!currentBranchId) return;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading…</td></tr>';
  try {
    const rows = await Api.listWarehouseStock(currentBranchId);
    if (!rows.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = rows.map((r) => `
      <tr>
        <td>${escapeHtml(r.product_name || '—')}</td>
        <td>${escapeHtml(r.category_name || '—')}</td>
        <td>${r.quantity_pieces}</td>
        <td>${escapeHtml(r.breakdown_text || formatWarehouseUnitBreakdown(r.unit_breakdown))}</td>
        <td><button type="button" class="btn-ghost btn-sm" data-push-history="${r.product_id}" data-push-history-name="${escapeHtml(r.product_name || '')}" data-push-history-on-hand="${r.quantity_pieces}">History</button></td>
      </tr>`).join('');
    tbody.querySelectorAll('[data-push-history]').forEach((btn) => {
      btn.addEventListener('click', () => openPushHistoryModal(
        btn.dataset.pushHistory, btn.dataset.pushHistoryName, Number(btn.dataset.pushHistoryOnHand)
      ));
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

// September 2026 change spec section 18 item 7: "After pushing, the
// warehouse should show, per item: name, total pieces, total cartons,
// and a push history." No backend change needed -- /warehouse/transfers
// already supported filtering by product_id + transfer_type.
async function openPushHistoryModal(productId, productName, onHandPieces) {
  const backdrop = document.getElementById('push-history-backdrop');
  const tbody = document.getElementById('push-history-tbody');
  document.getElementById('push-history-title').textContent = `Push history — ${productName}`;
  document.getElementById('push-history-on-hand').textContent = `${onHandPieces} piece(s)`;
  document.getElementById('push-history-total-pushed').textContent = '—';
  document.getElementById('push-history-total-cartons').textContent = '—';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="4">Loading…</td></tr>';
  backdrop.classList.add('visible');
  try {
    const pushes = await Api.listStockTransfers({
      branchId: currentBranchId, productId, transferType: 'store_to_warehouse',
    });
    const totalPushedPieces = pushes.reduce((sum, p) => sum + (p.quantity_pieces || 0), 0);
    const totalCartons = pushes.reduce((sum, p) => (
      sum + (p.unit_label === 'carton' ? (p.unit_count || 0) : (p.unit_label === 'mixed' ? (p.cartons_count || 0) : 0))
    ), 0);
    document.getElementById('push-history-total-pushed').textContent = `${totalPushedPieces} piece(s)`;
    document.getElementById('push-history-total-cartons').textContent =
      pushes.some(whPushUsedOtherUnits) && totalCartons > 0
        ? `${totalCartons} (some pushes used other units)`
        : `${totalCartons}`;
    if (!pushes.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--ink-faint);">No pushes recorded yet.</td></tr>';
      return;
    }
    tbody.innerHTML = pushes.map((p) => `
      <tr>
        <td>${new Date(p.created_at).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
        <td>${whPushBreakdown(p)}</td>
        <td>${escapeHtml(p.notes || '—')}</td>
        <td>${escapeHtml(p.created_by_name || 'Unknown')}</td>
      </tr>`).join('');
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}
document.getElementById('push-history-close')?.addEventListener('click', () => {
  document.getElementById('push-history-backdrop').classList.remove('visible');
});

const WAREHOUSE_MOVE_LABELS = {
  store_to_warehouse: 'Pushed to warehouse',
  warehouse_to_store: 'Pulled to store',
  warehouse_to_branch: 'Sent to another branch',
};

async function loadWarehouseHistory() {
  const tbody = document.getElementById('wh-history-tbody');
  const emptyState = document.getElementById('wh-history-empty');
  if (!currentBranchId) return;
  const filterType = document.getElementById('wh-history-filter').value || undefined;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="6">Loading…</td></tr>';
  try {
    const all = await Promise.all(
      ['store_to_warehouse', 'warehouse_to_store', 'warehouse_to_branch']
        .filter((t) => !filterType || t === filterType)
        .map((t) => Api.listStockTransfers({ branchId: currentBranchId, transferType: t }))
    );
    const transfers = all.flat().sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    if (!transfers.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = transfers.map((t) => {
      const route = t.transfer_type === 'warehouse_to_branch'
        ? `${escapeHtml(branchName(t.from_branch_id))}'s warehouse → ${escapeHtml(branchName(t.to_branch_id))}'s shop`
        : t.transfer_type === 'store_to_warehouse'
          ? `${escapeHtml(branchName(t.from_branch_id))}'s shop → its own warehouse`
          : `${escapeHtml(branchName(t.from_branch_id))}'s warehouse → its own shop`;
      return `
        <tr>
          <td>${new Date(t.created_at).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
          <td>${escapeHtml(WAREHOUSE_MOVE_LABELS[t.transfer_type] || t.transfer_type)}</td>
          <td>${route}</td>
          <td>${whTransferQtyText(t)}</td>
          <td>${t.created_by_name ? escapeHtml(t.created_by_name) : '<span style="color:var(--ink-faint)">—</span>'}</td>
          <td>${t.notes ? escapeHtml(t.notes) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        </tr>`;
    }).join('');
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}
document.getElementById('wh-history-filter')?.addEventListener('change', loadWarehouseHistory);

// --------------------------------------------------- Move stock (warehouse)
// September 2026 change spec section 18: "Pull to Store" was removed
// entirely as a separate warehouse action here -- restocking now
// happens from Inventory Alerts -> Restock (section 7), which pulls
// from this same warehouse ledger via the same
// /warehouse/pull-to-store endpoint underneath, rather than
// duplicating a manual-entry path on this page. This form now only
// ever pushes -- see loadPushableInvoiceItems below.

let pushableInvoiceItemsCache = [];
let whSupplierCache = [];

async function loadWhSuppliers() {
  const select = document.getElementById('wh-supplier');
  try {
    whSupplierCache = await Api.listSuppliers();
    const current = select.value;
    select.innerHTML = '<option value="">All suppliers</option>' +
      whSupplierCache.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
    select.value = current;
  } catch (err) { /* non-fatal -- item picker still works unfiltered */ }
}
function populateWhInvoiceSelect() {
  const supplierSelect = document.getElementById('wh-supplier');
  const invoiceSelect = document.getElementById('wh-invoice');
  const supplierId = supplierSelect.value;
  const supplierName = (supplierSelect.selectedOptions[0]?.textContent || '').trim().toLowerCase();

  if (!supplierId) {
    invoiceSelect.innerHTML = '<option value="">Select a supplier first…</option>';
    invoiceSelect.disabled = true;
    return;
  }

  const matches = pushableInvoiceItemsCache.filter((it) =>
    it.supplier_id === supplierId ||
    (supplierName && (it.supplier_name || '').trim().toLowerCase() === supplierName)
  );

  const seen = new Map();
  matches.forEach((it) => { if (!seen.has(it.invoice_id)) seen.set(it.invoice_id, it.invoice_date); });
  const invoices = Array.from(seen.entries()).sort((a, b) => b[1].localeCompare(a[1]));

  invoiceSelect.disabled = false;
  invoiceSelect.innerHTML = '<option value="">All invoices from this supplier</option>' +
    invoices.map(([id, date]) => `<option value="${id}">${new Date(date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</option>`).join('');
}

document.getElementById('wh-supplier').addEventListener('change', () => {
  document.getElementById('wh-invoice-item-input').value = '';
  document.getElementById('wh-invoice-item').value = '';
  document.getElementById('wh-invoice-item-hint').textContent = '';
  populateWhInvoiceSelect();
  renderWhInvoiceItemDropdown('');
});
document.getElementById('wh-invoice').addEventListener('change', () => {
  document.getElementById('wh-invoice-item-input').value = '';
  document.getElementById('wh-invoice-item').value = '';
  document.getElementById('wh-invoice-item-hint').textContent = '';
  renderWhInvoiceItemDropdown('');
});

async function loadPushableInvoiceItems() {
  const input = document.getElementById('wh-invoice-item-input');
  const hint = document.getElementById('wh-invoice-item-hint');
  if (!currentBranchId) {
    pushableInvoiceItemsCache = [];
    hint.textContent = 'Pick a branch on the Dashboard first.';
    return;
  }
  input.placeholder = 'Loading…';
  try {
    pushableInvoiceItemsCache = await Api.listPushableInvoiceItems(currentBranchId);
    input.placeholder = 'Type an item name…';
    if (!pushableInvoiceItemsCache.length) {
      hint.textContent = 'Add a purchase invoice with this item on it first — only items on an invoice can be pushed to warehouse.';
    } else {
      hint.textContent = '';
    }
    loadWhSuppliers();
    populateWhInvoiceSelect();
  } catch (err) {
    input.placeholder = 'Failed to load';
    showToast(err.message, true);
  }
}

function selectPushableInvoiceItem(item) {
  document.getElementById('wh-invoice-item-dropdown').style.display = 'none';
  const input = document.getElementById('wh-invoice-item-input');
  input.value = '';
  document.getElementById('wh-invoice-item').value = '';
  if (!item) return;

  // Item selection now only ADDS a row to the staging table below --
  // no quantity fields to fill here. Quantity/unit config happens per
  // row, after selection (user spec).
  const key = `inv:${item.invoice_line_item_id}`;
  if (whStagedItems.some((r) => r.key === key)) {
    showToast('That item is already in the list below.');
    return;
  }
  whStagedItems.push({
    key,
    source: 'invoice',
    item_name: item.item_name,
    product_id: item.product_id,
    invoice_line_item_id: item.invoice_line_item_id,
    invoice_date: item.invoice_date,
    supplier_name: item.supplier_name,
    ...whComponentsForInvoiceItem(item),
  });
  renderWhStagingTable();
}

function renderWhInvoiceItemDropdown(query) {
  const dropdown = document.getElementById('wh-invoice-item-dropdown');
  const supplierSelect = document.getElementById('wh-supplier');
  const supplierId = supplierSelect.value;
  let matches = pushableInvoiceItemsCache;
  // Sept 2026 dictated request: once an item has been added to the
  // staging table below, it should no longer show up as selectable in
  // this dropdown (previously it stayed selectable, and re-picking it
  // just showed an "already in the list below" toast instead of being
  // filtered out up front).
  matches = matches.filter((it) => !whStagedItems.some((r) => r.key === `inv:${it.invoice_line_item_id}`));
  if (supplierId) {
    // Invoice.supplier_id is only ever set when the person typing the
    // invoice happened to pick a saved-supplier suggestion (or typed
    // an exact name match) -- plenty of real invoices only have the
    // free-text supplier_name filled in, with no id link. Filtering
    // on id alone silently hid every one of those ("select a supplier,
    // nothing shows up even though they clearly delivered before").
    // Falling back to a case-insensitive name match against the
    // chosen supplier catches those too.
    const supplierName = (supplierSelect.selectedOptions[0]?.textContent || '').trim().toLowerCase();
    matches = matches.filter((it) =>
      it.supplier_id === supplierId ||
      (supplierName && (it.supplier_name || '').trim().toLowerCase() === supplierName)
    );
  }
  const invoiceId = document.getElementById('wh-invoice').value;
  if (invoiceId) matches = matches.filter((it) => it.invoice_id === invoiceId);
  if (query) matches = matches.filter((it) => it.item_name.toLowerCase().includes(query.toLowerCase()));
  if (!matches.length) {
    dropdown.innerHTML = `<div class="item-autocomplete-empty">${pushableInvoiceItemsCache.length ? 'No matching un-pushed invoice items.' : 'No un-pushed invoice items for this branch.'}</div>`;
  } else {
    dropdown.innerHTML = matches.slice(0, 20).map((it, i) => `
      <div class="item-autocomplete-row${it.is_unmatched ? ' item-autocomplete-unmatched' : ''}" data-wh-item-index="${pushableInvoiceItemsCache.indexOf(it)}">
        ${escapeHtml(it.item_name)} — ${it.remaining} of ${it.quantity} ${escapeHtml(it.unit_label)}(s) left
        <span style="color:var(--ink-faint);"> (Invoice ${it.invoice_date}, ${escapeHtml(it.supplier_name)})</span>
        ${it.is_unmatched ? '<span style="color:#b45309;font-weight:600;"> - No matching product, click to link</span>' : ''}
      </div>`).join('');
    dropdown.querySelectorAll('[data-wh-item-index]').forEach((el) => {
      el.addEventListener('click', () => {
        const it = pushableInvoiceItemsCache[Number(el.dataset.whItemIndex)];
        if (it.is_unmatched) {
          openWhLinkProductBox(it);
        } else {
          selectPushableInvoiceItem(it);
        }
      });
    });
  }
  dropdown.style.display = '';
}

let whLinkProductSearchDebounce;
function openWhLinkProductBox(item) {
  const dropdown = document.getElementById('wh-invoice-item-dropdown');
  dropdown.style.display = 'none';
  document.getElementById('wh-invoice-item-input').value = item.item_name;

  let box = document.getElementById('wh-link-product-box');
  if (!box) {
    box = document.createElement('div');
    box.id = 'wh-link-product-box';
    box.className = 'item-autocomplete-dropdown';
    document.getElementById('wh-invoice-item-input').insertAdjacentElement('afterend', box);
  }
  box.innerHTML = `
    <div style="padding:8px;">
      <div style="margin-bottom:6px;">Link "<strong>${escapeHtml(item.item_name)}</strong>" to a product:</div>
      <input type="text" id="wh-link-product-search" placeholder="Search products..." style="width:100%;margin-bottom:6px;">
      <div id="wh-link-product-results"></div>
      <button type="button" id="wh-link-product-cancel" style="margin-top:6px;">Cancel</button>
    </div>`;
  box.style.display = '';

  const searchInput = document.getElementById('wh-link-product-search');
  const resultsDiv = document.getElementById('wh-link-product-results');
  searchInput.focus();

  searchInput.addEventListener('input', () => {
    const query = searchInput.value.trim();
    clearTimeout(whLinkProductSearchDebounce);
    if (query.length < 2) { resultsDiv.innerHTML = ''; return; }
    whLinkProductSearchDebounce = setTimeout(async () => {
      let results = [];
      try {
        results = await Api.listProducts({ search: query });
      } catch (err) { /* non-fatal -- search just stays empty */ }
      resultsDiv.innerHTML = results.slice(0, 8).map((p) => `
        <div class="item-autocomplete-row" data-link-product-id="${p.id}">
          <span>${escapeHtml(p.name)}${p.size ? ` <span style="color:var(--ink-faint);">(${escapeHtml(p.size)})</span>` : ''}</span>
          <span class="price">${p.price_naira != null ? formatNaira(p.price_naira) : ''}</span>
        </div>`).join('');
      resultsDiv.querySelectorAll('[data-link-product-id]').forEach((el) => {
        el.addEventListener('click', async () => {
          const productId = el.dataset.linkProductId;
          const product = results.find((p) => String(p.id) === String(productId));
          try {
            await Api.linkInvoiceLineItemProduct(item.invoice_line_item_id, productId);
            item.product_id = productId;
            item.is_unmatched = false;
            // Use the catalog product's own name from here on, not the
            // wording the distributor happened to write on the invoice --
            // the warehouse should stay consistent with the name the
            // store already knows this item by.
            if (product) item.item_name = product.name;
            showToast('Item linked to product.');
            closeWhLinkProductBox();
            selectPushableInvoiceItem(item);
          } catch (err) {
            showToast(err.message, true);
          }
        });
      });
    }, 250);
  });

  document.getElementById('wh-link-product-cancel').addEventListener('click', closeWhLinkProductBox);
}

function closeWhLinkProductBox() {
  const box = document.getElementById('wh-link-product-box');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
}

document.getElementById('wh-invoice-item-input').addEventListener('input', (e) => {
  document.getElementById('wh-invoice-item').value = '';
  renderWhInvoiceItemDropdown(e.target.value.trim());
});
document.getElementById('wh-invoice-item-input').addEventListener('focus', (e) => renderWhInvoiceItemDropdown(e.target.value.trim()));
document.addEventListener('click', (e) => {
  if (!e.target.closest('#wh-invoice-item-group')) {
    document.getElementById('wh-invoice-item-dropdown').style.display = 'none';
  }
});

// --------------------------------------------------- Manual push (photo-only invoice)
//
// "If for eventually there was no item that was manually typed from
// the invoice, it is just image that we snapped and uploaded... you
// have to type it out... corresponding to what we have on the
// inventory." Falls back to the same category/product/variant pickers
// used elsewhere on this page, and skips invoice_line_item_id entirely
// (no quantity cap to check against -- see push_to_warehouse backend).
let whManualMode = false;
let whStagedItems = [];

function applyWhManualMode() {
  document.getElementById('wh-invoice-item-group').style.display = whManualMode ? 'none' : '';
  document.getElementById('wh-supplier').closest('.field').style.display = whManualMode ? 'none' : '';
  document.getElementById('wh-invoice').closest('.field').style.display = whManualMode ? 'none' : '';
  document.getElementById('wh-manual-category-group').style.display = whManualMode ? '' : 'none';
  document.getElementById('wh-manual-product-group').style.display = whManualMode ? '' : 'none';
  document.getElementById('wh-manual-variant-group').style.display = whManualMode ? '' : 'none';
  document.getElementById('wh-manual-add-group').style.display = whManualMode ? '' : 'none';
  document.getElementById('wh-manual-toggle').textContent = whManualMode
    ? 'Actually, pick from the invoice list instead'
    : "Item isn't listed here (invoice was just a photo)? Enter it manually";
  if (whManualMode) {
    loadCategoriesFilter('wh-manual');
    loadProductsFilter('wh-manual');
  }
}
document.getElementById('wh-manual-toggle').addEventListener('click', () => {
  whManualMode = !whManualMode;
  applyWhManualMode();
});
document.getElementById('wh-manual-category').addEventListener('change', () => loadProductsFilter('wh-manual'));
document.getElementById('wh-manual-product').addEventListener('change', () => loadVariantsFilter('wh-manual'));

// PATCH5D-MANUAL-PACK-PREFILL
// A manual row has no invoice line, so its only source for pack numbers is
// the product itself. Filled once, when the row is added; staff can change
// any box and nothing here runs again. A dozen is 12. A roll default goes in
// the carton box when the product has no carton default (rolls show as a
// carton row in the warehouse). Counts stay 0. No default means an empty box.
function whApplyManualPackDefaults(row, product) {
  if (!product) return row;
  const defCarton = product.default_pieces_per_carton || null;
  const defBag = product.default_pieces_per_bag || null;
  const defRoll = product.default_pieces_per_roll || null;
  row.cartons_pieces_per_unit = defCarton || defRoll;
  row.bags_pieces_per_unit = defBag;
  if (row.show_dozens) row.dozens_pieces_per_unit = 12;
  return row;
}

// Manual mode also only ADDS to the staging table -- same flow as
// picking from the invoice list, just a different source of items.
document.getElementById('wh-manual-add-btn').addEventListener('click', async () => {
  const variantSelect = document.getElementById('wh-manual-variant');
  const productSelect = document.getElementById('wh-manual-product');
  const productId = variantSelect.value || productSelect.value;
  if (!productId) {
    showToast('Pick a product first.', true);
    return;
  }
  const itemName = (productSelect.selectedOptions[0] && productSelect.selectedOptions[0].textContent) || 'Item';
  const key = `manual:${productId}:${Date.now()}`;
  const manualRow = {
    key,
    source: 'manual',
    item_name: itemName,
    product_id: productId,
    ...whBlankComponents(true, true, true),
  };
  try {
    const product = await Api.getProduct(productId);
    whApplyManualPackDefaults(manualRow, product);
  } catch (err) {
    // The row is still added, empty, as before. Adding is never blocked.
  }
  whStagedItems.push(manualRow);
  renderWhStagingTable();
});

// September 2026 change spec section 18: this form only ever pushes
// to warehouse now -- "Pull to Store" was removed as a separate
// action here entirely (see the comment above loadPushableInvoiceItems).
const WH_MOVE_CONFIG = { store_to_warehouse: { submitLabel: 'Push to warehouse', successVerb: 'Pushed' } };
function applyWhMoveTypeConfig() {
  document.getElementById('wh-move-submit').textContent = WH_MOVE_CONFIG.store_to_warehouse.submitLabel;
  applyWhManualMode();
  loadPushableInvoiceItems();
}
applyWhMoveTypeConfig();

const WH_UNIT_FIELD_CONFIG = {
  carton: { countLabel: 'Carton count', perUnitLabel: 'Pieces per carton', showPerUnit: true },
  dozen: { countLabel: 'Dozen count', perUnitLabel: 'Pieces per dozen', showPerUnit: true },
  bag: { countLabel: 'Bag count', perUnitLabel: 'Pieces per bag', showPerUnit: true },
  piece: { countLabel: 'Pieces count', perUnitLabel: null, showPerUnit: false },
};

// Invoice unit_label is freehand text ("dozens", "Carton", "pcs", ...),
// but the push form's dropdown only has these three exact options. Map
// common variants onto the option they mean so the dropdown actually
// defaults to what the invoice says, instead of silently falling back
// to the first option whenever the wording doesn't match exactly.
function normalizeWhUnitLabel(label) {
  const key = (label || '').trim().toLowerCase();
  if (['carton', 'cartons', 'ctn', 'ctns', 'box', 'boxes'].includes(key)) return 'carton';
  if (['dozen', 'dozens', 'doz', 'dz'].includes(key)) return 'dozen';
  if (['bag', 'bags'].includes(key)) return 'bag';
  if (['piece', 'pieces', 'pcs', 'pc'].includes(key)) return 'piece';
  return 'carton';
}

// ---------------------------------------------------- Staging table
//
// Restructure (user spec): picking an item no longer fills a single
// set of quantity fields -- it adds a row to this staging table, and
// EVERY row gets its own unit/count/pieces-per-unit inputs with a
// live-calculated total. One submit walks the whole table and pushes
// each row with Api.pushToWarehouse, one call per row.

// September 2026 change (second revision) -- a push is counted as three
// independent components, not "primary unit + one remainder":
//   cartons_count x cartons_pieces_per_unit  (+ half a carton if ticked)
//   dozens_count  x dozens_pieces_per_unit   (+ half a dozen if ticked)
//   pieces_count  (added as-is, never multiplied)
// Nothing is pre-filled with what remains on the invoice: the admin
// enters what is going to the warehouse now. The invoice line is shown
// only as a reference (what is on it, what is left).

function whBlankComponents(showCartons, showDozens, showBags) {
  return {
    show_cartons: showCartons,
    show_dozens: showDozens,
    show_bags: showBags,
    cartons_count: 0,
    cartons_pieces_per_unit: null,
    cartons_half: false,
    dozens_count: 0,
    dozens_pieces_per_unit: null,
    dozens_half: false,
    bags_count: 0,
    bags_pieces_per_unit: null,
    bags_half: false,
    pieces_count: 0,
    line: null,
  };
}

function whComponentsForInvoiceItem(item) {
  const ch = item.cartons_total_halves;
  const dh = item.dozens_total_halves;
  const bh = item.bags_total_halves;
  const unit = normalizeWhUnitLabel(item.unit_label);
  const base = whBlankComponents(
    ch === undefined ? unit === 'carton' : ch > 0,
    dh === undefined ? unit === 'dozen' : dh > 0,
    bh === undefined ? unit === 'bag' : bh > 0,
  );
  // PATCH5C-WAREHOUSE-PACK-PREFILL
  // Pack numbers are filled in once, when the row is created. The line's
  // own number comes first, then the product default. Staff can still
  // change any box; nothing here runs again after that. A dozen is 12.
  const rawUnit = (item.unit_label || '').trim().toLowerCase();
  const isRollLine = rawUnit === 'roll' || rawUnit === 'rolls';
  const linePack = item.pieces_per_carton || null;
  const defCarton = item.default_pieces_per_carton || null;
  const defBag = item.default_pieces_per_bag || null;
  const defRoll = item.default_pieces_per_roll || null;
  if (unit === 'bag') {
    base.cartons_pieces_per_unit = defCarton;
    base.bags_pieces_per_unit = linePack || defBag;
  } else {
    base.cartons_pieces_per_unit = linePack || (isRollLine ? defRoll : defCarton);
    base.bags_pieces_per_unit = base.show_bags ? defBag : null;
  }
  if (base.show_dozens) base.dozens_pieces_per_unit = 12;
  base.line = ch === undefined ? null : {
    cartons_total_halves: item.cartons_total_halves || 0,
    cartons_remaining_halves: item.cartons_remaining_halves || 0,
    dozens_total_halves: item.dozens_total_halves || 0,
    dozens_remaining_halves: item.dozens_remaining_halves || 0,
    bags_total_halves: item.bags_total_halves || 0,
    bags_remaining_halves: item.bags_remaining_halves || 0,
    pieces_total: item.pieces_total || 0,
    pieces_remaining: item.pieces_remaining || 0,
    received_pieces: item.received_pieces || 0,
    outstanding_pieces: item.outstanding_pieces == null ? null : item.outstanding_pieces,
  };
  return base;
}

// Halves -> display text: 5 -> "2\u00bd", 1 -> "\u00bd", 4 -> "2".
function whFmtHalves(h) {
  const whole = Math.floor(h / 2);
  if (h % 2) return whole ? `${whole}\u00bd` : '\u00bd';
  return `${whole}`;
}

function whStageRowTotal(row) {
  const c = Number(row.cartons_pieces_per_unit || 0);
  const d = Number(row.dozens_pieces_per_unit || 0);
  const b = Number(row.bags_pieces_per_unit || 0);
  return Number(row.cartons_count || 0) * c + (row.cartons_half ? Math.floor(c / 2) : 0)
    + Number(row.dozens_count || 0) * d + (row.dozens_half ? Math.floor(d / 2) : 0)
    + Number(row.bags_count || 0) * b + (row.bags_half ? Math.floor(b / 2) : 0)
    + Number(row.pieces_count || 0);
}

// Returns '' when the row is fine, otherwise a sentence saying what to fix.
function whStageRowProblem(row, allowEmpty) {
  const cc = Number(row.cartons_count || 0);
  const dc = Number(row.dozens_count || 0);
  const bc = Number(row.bags_count || 0);
  const pc = Number(row.pieces_count || 0);
  const bPpu = Number(row.bags_pieces_per_unit || 0);
  const cPpu = Number(row.cartons_pieces_per_unit || 0);
  const dPpu = Number(row.dozens_pieces_per_unit || 0);
  if ((cc > 0 || row.cartons_half) && cPpu <= 0) return 'Enter pieces per carton.';
  if ((dc > 0 || row.dozens_half) && dPpu <= 0) return 'Enter pieces per dozen.';
  if ((bc > 0 || row.bags_half) && bPpu <= 0) return 'Enter pieces per bag.';
  const line = row.line;
  if (line) {
    const wantC = cc * 2 + (row.cartons_half ? 1 : 0);
    const wantD = dc * 2 + (row.dozens_half ? 1 : 0);
    const wantB = bc * 2 + (row.bags_half ? 1 : 0);
    if (wantC > line.cartons_remaining_halves) {
      return `Only ${whFmtHalves(line.cartons_remaining_halves)} carton(s) left on the invoice line.`;
    }
    if (wantD > line.dozens_remaining_halves) {
      return `Only ${whFmtHalves(line.dozens_remaining_halves)} dozen(s) left on the invoice line.`;
    }
    if (wantB > (line.bags_remaining_halves || 0)) {
      return `Only ${whFmtHalves(line.bags_remaining_halves || 0)} bag(s) left on the invoice line.`;
    }
    if (line.pieces_total > 0 && pc > line.pieces_remaining) {
      return `Only ${line.pieces_remaining} piece(s) left on the invoice line.`;
    }
  }
  if (!allowEmpty && whStageRowTotal(row) <= 0) return 'Enter a quantity greater than 0.';
  return '';
}

function whFormatQtySummary(row) {
  const parts = [];
  const cc = Number(row.cartons_count || 0);
  const dc = Number(row.dozens_count || 0);
  const pc = Number(row.pieces_count || 0);
  const bc = Number(row.bags_count || 0);
  if (cc > 0) parts.push(`${cc} carton${cc === 1 ? '' : 's'}`);
  if (row.cartons_half) parts.push('\u00bd carton');
  if (dc > 0) parts.push(`${dc} dozen${dc === 1 ? '' : 's'}`);
  if (row.dozens_half) parts.push('\u00bd dozen');
  if (bc > 0) parts.push(`${bc} bag${bc === 1 ? '' : 's'}`);
  if (row.bags_half) parts.push('\u00bd bag');
  if (pc > 0) parts.push(`${pc} piece${pc === 1 ? '' : 's'}`);
  return parts.length ? parts.join(' + ') : 'Set quantity';
}

function whMultiplierSummary(row) {
  const parts = [];
  if ((Number(row.cartons_count || 0) > 0 || row.cartons_half) && row.cartons_pieces_per_unit) {
    parts.push(`${row.cartons_pieces_per_unit} / carton`);
  }
  if ((Number(row.dozens_count || 0) > 0 || row.dozens_half) && row.dozens_pieces_per_unit) {
    parts.push(`${row.dozens_pieces_per_unit} / dozen`);
  }
  if ((Number(row.bags_count || 0) > 0 || row.bags_half) && row.bags_pieces_per_unit) {
    parts.push(`${row.bags_pieces_per_unit} / bag`);
  }
  return parts.length ? parts.join('<br/>') : '&mdash;';
}

function whLineReference(row) {
  if (!row.line) return 'Manual push \u2014 nothing on an invoice to cap against.';
  const L = row.line;
  const bits = [];
  if (L.cartons_total_halves > 0) bits.push(`Cartons: ${whFmtHalves(L.cartons_total_halves)} on invoice, ${whFmtHalves(L.cartons_remaining_halves)} left`);
  if (L.dozens_total_halves > 0) bits.push(`Dozens: ${whFmtHalves(L.dozens_total_halves)} on invoice, ${whFmtHalves(L.dozens_remaining_halves)} left`);
  if (L.bags_total_halves > 0) bits.push(`Bags: ${whFmtHalves(L.bags_total_halves)} on invoice, ${whFmtHalves(L.bags_remaining_halves)} left`);
  if (L.pieces_total > 0) bits.push(`Pieces: ${L.pieces_total} on invoice, ${L.pieces_remaining} left`);
  const refBase = bits.length ? 'On the invoice line \u2014 ' + bits.join(' \u00b7 ') : '';
  const refNote = whReceivedNote(row);
  return refNote ? (refBase ? refBase + '. ' : '') + refNote : refBase;
}

// PATCH6C-LEFT-FIGURE: one grey sentence when part of the invoice line was already
// received in Receive Goods. Empty when nothing was received or the total is unknown.
function whReceivedNote(row) {
  const L = row.line;
  if (!L || !(L.received_pieces > 0) || L.outstanding_pieces == null) return '';
  return `${L.received_pieces} piece(s) of this line were already received in Receive Goods, so only ${L.outstanding_pieces} piece(s) can still be pushed.`;
}

function whLeftHint(row) {
  const L = row.line;
  const bits = [];
  if (L.cartons_remaining_halves > 0) bits.push(`${whFmtHalves(L.cartons_remaining_halves)} carton(s)`);
  if (L.dozens_remaining_halves > 0) bits.push(`${whFmtHalves(L.dozens_remaining_halves)} dozen(s)`);
  if (L.bags_remaining_halves > 0) bits.push(`${whFmtHalves(L.bags_remaining_halves)} bag(s)`);
  if (L.pieces_total > 0 && L.pieces_remaining > 0) bits.push(`${L.pieces_remaining} piece(s)`);
  return `left on invoice: ${bits.join(', ') || 'nothing'}`;
}

// Push history: what was actually pushed, with the total pieces as the figure.
function whPushBreakdown(p) {
  const pl = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  let text;
  if (p.unit_label === 'mixed') {
    const c = p.cartons_pieces_per_unit || 0;
    const d = p.dozens_pieces_per_unit || 0;
    const b = p.bags_pieces_per_unit || 0;
    const parts = [];
    if (p.cartons_count) parts.push(`${pl(p.cartons_count, 'carton', 'cartons')} (\u00d7${c})`);
    if (p.cartons_half) parts.push(`\u00bd carton (\u00d7${c})`);
    if (p.dozens_count) parts.push(`${pl(p.dozens_count, 'dozen', 'dozens')} (\u00d7${d})`);
    if (p.dozens_half) parts.push(`\u00bd dozen (\u00d7${d})`);
    if (p.bags_count) parts.push(`${pl(p.bags_count, 'bag', 'bags')} (\u00d7${b})`);
    if (p.bags_half) parts.push(`\u00bd bag (\u00d7${b})`);
    if (p.pieces_count) parts.push(pl(p.pieces_count, 'piece', 'pieces'));
    text = parts.join(' + ') || '\u2014';
  } else {
    text = `${p.unit_count} ${escapeHtml(p.unit_label)}(s) \u00d7 ${p.pieces_per_unit}`;
    const rl = p.remainder_unit_label;
    if (rl === 'half_carton') text += ' + \u00bd carton';
    else if (rl === 'half_dozen') text += ' + \u00bd dozen';
    else if (rl === 'dozens' && p.remainder_quantity) text += ` + ${p.remainder_quantity} dozens`;
    else if (rl === 'pieces' && p.remainder_quantity) text += ` + ${p.remainder_quantity} pieces`;
    else if (p.is_half_partial) text += ' + \u00bd';
  }
  return `${text} = <strong>${p.quantity_pieces} pcs</strong>`;
}

function whPushUsedOtherUnits(p) {
  if (p.unit_label === 'mixed') {
    return Number(p.dozens_count || 0) > 0 || !!p.dozens_half || Number(p.bags_count || 0) > 0 || !!p.bags_half || Number(p.pieces_count || 0) > 0;
  }
  return p.unit_label !== 'carton';
}

function whStagingRowHtml(row) {
  const sourceTag = row.source === 'invoice'
    ? `<span style="color:var(--ink-faint);font-size:11.5px;">Invoice ${row.invoice_date}, ${escapeHtml(row.supplier_name)}</span>`
    : `<span class="category-badge">Manual</span>`;
  // PATCH6C-LEFT-FIGURE: second grey line when part of the invoice line was received
  const recvNote = whReceivedNote(row);
  const recvHint = recvNote
    ? `<div style="font-size:11.5px;color:var(--ink-faint);">${escapeHtml(recvNote)}</div>`
    : '';
  const capHint = row.line
    ? `<div style="font-size:11.5px;color:var(--ink-faint);">${whLeftHint(row)}</div>${recvHint}`
    : '';
  return `
    <tr data-key="${row.key}">
      <td style="padding:6px 8px;"><strong>${escapeHtml(row.item_name)}</strong><br/>${sourceTag}${capHint}</td>
      <td style="padding:6px 8px;">
        <button type="button" class="btn btn-ghost btn-sm wh-stage-qty-summary-btn" data-key="${row.key}" style="width:100%;text-align:left;white-space:normal;">${whFormatQtySummary(row)}</button>
      </td>
      <td style="padding:6px 8px;">${whMultiplierSummary(row)}</td>
      <td style="padding:6px 8px;"><strong>${whStageRowTotal(row)}</strong></td>
      <td style="padding:6px 8px;"><button type="button" class="btn-ghost btn-sm wh-stage-remove" data-key="${row.key}" title="Remove">&times;</button></td>
    </tr>`;
}

function renderWhStagingTable() {
  const tbody = document.getElementById('wh-staging-tbody');
  const empty = document.getElementById('wh-staging-empty');
  if (!whStagedItems.length) {
    tbody.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = whStagedItems.map(whStagingRowHtml).join('');
}

document.getElementById('wh-staging-tbody').addEventListener('click', (e) => {
  const summaryBtn = e.target.closest('.wh-stage-qty-summary-btn');
  if (summaryBtn) {
    openWhQtyDetailPopup(summaryBtn.dataset.key);
    return;
  }
  const btn = e.target.closest('.wh-stage-remove');
  if (!btn) return;
  whStagedItems = whStagedItems.filter((r) => r.key !== btn.dataset.key);
  renderWhStagingTable();
});

// ------------------------------------------------------- Quantity popup
const whQtyDetailBackdrop = document.getElementById('wh-qty-detail-backdrop');
let whQtyDetailEditingKey = null;
function whQdEl(id) { return document.getElementById(id); }

function whQdRead() {
  const num = (id) => Math.max(0, Math.floor(Number(whQdEl(id).value || 0)));
  return {
    cartons_count: num('wh-qd-cartons-count'),
    cartons_pieces_per_unit: num('wh-qd-cartons-ppu') || null,
    cartons_half: whQdEl('wh-qd-cartons-half').checked,
    dozens_count: num('wh-qd-dozens-count'),
    dozens_pieces_per_unit: num('wh-qd-dozens-ppu') || null,
    dozens_half: whQdEl('wh-qd-dozens-half').checked,
    bags_count: num('wh-qd-bags-count'),
    bags_pieces_per_unit: num('wh-qd-bags-ppu') || null,
    bags_half: whQdEl('wh-qd-bags-half').checked,
    pieces_count: num('wh-qd-pieces-count'),
  };
}

function whQdRefresh() {
  const row = whStagedItems.find((r) => r.key === whQtyDetailEditingKey);
  if (!row) return;
  const cand = Object.assign({}, row, whQdRead());
  whQdEl('wh-qd-total').textContent = `Total: ${whStageRowTotal(cand)} pieces`;
  whQdEl('wh-qd-error').textContent = whStageRowProblem(cand, true);
}

function whQdClose() {
  whQtyDetailBackdrop.classList.remove('visible');
  whQtyDetailEditingKey = null;
}

function openWhQtyDetailPopup(key) {
  const row = whStagedItems.find((r) => r.key === key);
  if (!row) return;
  whQtyDetailEditingKey = key;
  whQdEl('wh-qd-cartons-block').style.display = row.show_cartons ? '' : 'none';
  whQdEl('wh-qd-dozens-block').style.display = row.show_dozens ? '' : 'none';
  whQdEl('wh-qd-bags-block').style.display = row.show_bags ? '' : 'none';
  whQdEl('wh-qd-cartons-count').value = row.cartons_count || 0;
  whQdEl('wh-qd-cartons-ppu').value = row.cartons_pieces_per_unit || '';
  whQdEl('wh-qd-cartons-half').checked = !!row.cartons_half;
  whQdEl('wh-qd-dozens-count').value = row.dozens_count || 0;
  whQdEl('wh-qd-dozens-ppu').value = row.dozens_pieces_per_unit || '';
  whQdEl('wh-qd-dozens-half').checked = !!row.dozens_half;
  whQdEl('wh-qd-bags-count').value = row.bags_count || 0;
  whQdEl('wh-qd-bags-ppu').value = row.bags_pieces_per_unit || '';
  whQdEl('wh-qd-bags-half').checked = !!row.bags_half;
  whQdEl('wh-qd-pieces-count').value = row.pieces_count || 0;
  whQdEl('wh-qd-ref').textContent = whLineReference(row);
  whQtyDetailBackdrop.classList.add('visible');
  whQdRefresh();
}

['wh-qd-cartons-count', 'wh-qd-cartons-ppu', 'wh-qd-cartons-half',
 'wh-qd-dozens-count', 'wh-qd-dozens-ppu', 'wh-qd-dozens-half',
 'wh-qd-bags-count', 'wh-qd-bags-ppu', 'wh-qd-bags-half',
 'wh-qd-pieces-count'].forEach((id) => {
  whQdEl(id).addEventListener('input', whQdRefresh);
  whQdEl(id).addEventListener('change', whQdRefresh);
});
document.getElementById('wh-qty-detail-cancel').addEventListener('click', whQdClose);
whQtyDetailBackdrop.addEventListener('click', (e) => {
  if (e.target === whQtyDetailBackdrop) whQdClose();
});
document.getElementById('wh-qty-detail-save').addEventListener('click', () => {
  const row = whStagedItems.find((r) => r.key === whQtyDetailEditingKey);
  if (!row) return;
  const values = whQdRead();
  const problem = whStageRowProblem(Object.assign({}, row, values), true);
  if (problem) {
    whQdEl('wh-qd-error').textContent = problem;
    return;
  }
  Object.assign(row, values);
  whQdClose();
  renderWhStagingTable();
});

document.getElementById('wh-move-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('wh-move-error');
  errorEl.classList.remove('visible');

  if (!currentBranchId) {
    errorEl.textContent = 'Pick a branch on the Dashboard first.';
    errorEl.classList.add('visible');
    return;
  }
  if (!whStagedItems.length) {
    errorEl.textContent = 'Add at least one item to the list before pushing.';
    errorEl.classList.add('visible');
    return;
  }

  const notes = document.getElementById('wh-notes').value || null;
  const btn = document.getElementById('wh-move-submit');
  btn.disabled = true;

  let pushedPieces = 0;
  const succeededKeys = [];
  const failures = [];

  for (const row of whStagedItems) {
    const problem = whStageRowProblem(row, false);
    if (problem) {
      failures.push(`${row.item_name}: ${problem}`);
      continue;
    }
    const payload = {
      product_id: row.product_id,
      cartons_count: Number(row.cartons_count || 0),
      cartons_pieces_per_unit: Number(row.cartons_pieces_per_unit || 0) || null,
      cartons_half: !!row.cartons_half,
      dozens_count: Number(row.dozens_count || 0),
      dozens_pieces_per_unit: Number(row.dozens_pieces_per_unit || 0) || null,
      dozens_half: !!row.dozens_half,
      bags_count: Number(row.bags_count || 0),
      bags_pieces_per_unit: Number(row.bags_pieces_per_unit || 0) || null,
      bags_half: !!row.bags_half,
      pieces_count: Number(row.pieces_count || 0),
      notes,
    };
    if (row.source === 'invoice') payload.invoice_line_item_id = row.invoice_line_item_id;
    try {
      const result = await Api.pushToWarehouse(currentBranchId, payload);
      pushedPieces += result.quantity_pieces;
      succeededKeys.push(row.key);
    } catch (err) {
      failures.push(`${row.item_name}: ${err.message}`);
    }
  }

  btn.disabled = false;
  whStagedItems = whStagedItems.filter((row) => !succeededKeys.includes(row.key));
  renderWhStagingTable();

  if (succeededKeys.length) {
    showToast(`${WH_MOVE_CONFIG.store_to_warehouse.successVerb} ${pushedPieces} piece(s) across ${succeededKeys.length} item(s).`);
  }
  if (failures.length) {
    errorEl.textContent = failures.join(' | ');
    errorEl.classList.add('visible');
  }
  if (succeededKeys.length && !failures.length) {
    document.getElementById('wh-notes').value = '';
  }
  loadWarehouseStock();
  loadWarehouseHistory();
  whManualMode = false;
  applyWhManualMode();
  loadPushableInvoiceItems();
});

// -------------------------------------------------------- Waybill complaints

async function loadIntakeReceipts() {
  const tbody = document.getElementById('intake-tbody');
  const emptyState = document.getElementById('intake-empty');
  if (!currentBranchId) return;
  tbody.innerHTML = '<tr class="loading-row"><td colspan="10">Loading…</td></tr>';
  try {
    const receipts = await Api.listIntakeReceipts(currentBranchId);
    if (!receipts.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = receipts.map((r) => `
      <tr>
        <td>${new Date(r.created_at).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
        <td>${r.invoice_date ? new Date(r.invoice_date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' }) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td>${r.supplier_name ? escapeHtml(r.supplier_name) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td>${r.line_item_name ? escapeHtml(r.line_item_name) : '<span style="color:var(--ink-faint)">Whole delivery</span>'}</td>
        <td>${r.item_count ?? '—'}</td>
        <td>${r.damaged_count ? `<span style="color:var(--danger);">${r.damaged_count}</span>` : '—'}</td>
        <td>${r.missing_count ? `<span style="color:var(--danger);">${r.missing_count}</span>` : '—'}</td>
        <td>${r.created_by_name ? escapeHtml(r.created_by_name) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td>${r.notes ? escapeHtml(r.notes) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td><a href="${r.image_url}" target="_blank" rel="noopener" class="action-link action-link-edit" style="font-weight:700;">View evidence</a></td>
      </tr>`).join('');
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

// Spec: "you should be able to select the supplier... just select
// that particular supplier[, then] history of the item they have
// brought in... arranged based on recent." Supplier first, then their
// invoice history (recent first) -- no "when did this item come to
// store" question.
const intakeSupplierSelect = document.getElementById('intake-supplier-select');
const intakeInvoiceSelect = document.getElementById('intake-invoice');
let intakeInvoiceCache = [];
let intakeSupplierCache = [];

async function loadIntakeSuppliers() {
  intakeSupplierSelect.innerHTML = '<option value="">Loading…</option>';
  try {
    intakeSupplierCache = await Api.listSuppliers();
    intakeSupplierSelect.innerHTML = '<option value="">Select a supplier…</option>' +
      intakeSupplierCache.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
  } catch (err) {
    intakeSupplierSelect.innerHTML = '<option value="">Could not load suppliers</option>';
    showToast(err.message, true);
  }
}
loadIntakeSuppliers();

intakeSupplierSelect.addEventListener('change', async () => {
  intakeInvoiceSelect.innerHTML = '<option value="">Loading…</option>';
  populateIntakeLineItemSelect(null);
  if (!intakeSupplierSelect.value || !currentBranchId) {
    intakeInvoiceSelect.innerHTML = '<option value="">Select a supplier first…</option>';
    return;
  }
  try {
    intakeInvoiceCache = await Api.listPurchaseInvoices({ branchId: currentBranchId, supplierId: intakeSupplierSelect.value });
    intakeInvoiceSelect.innerHTML = intakeInvoiceCache.length
      ? '<option value="">Select a delivery…</option>' + intakeInvoiceCache.map((inv) => `
          <option value="${inv.id}">
            ${new Date(inv.invoice_date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}
          </option>`).join('')
      : '<option value="">No deliveries on file from this supplier</option>';
  } catch (err) {
    intakeInvoiceSelect.innerHTML = '<option value="">Could not load deliveries</option>';
    showToast(err.message, true);
  }
});

intakeInvoiceSelect.addEventListener('change', () => {
  const invoice = intakeInvoiceCache.find((inv) => inv.id === intakeInvoiceSelect.value);
  populateIntakeLineItemSelect(invoice);
});

// The invoice's own typed-in line items (see js/invoice.js) -- picking
// one is how a reporter selects the exact product being complained
// about ("this is the product that you gave me 30 cartons"), and
// auto-fills the item count from what the invoice said was delivered.
// If the invoice was only ever saved as a photo (no typed lines), or
// the reporter picks "Type the product name" below, the manual name
// field takes over instead of blocking the complaint entirely.
const intakeLineItemSelect = document.getElementById('intake-line-item');
const intakeManualNameGroup = document.getElementById('intake-manual-name-group');
function populateIntakeLineItemSelect(invoice) {
  intakeManualNameGroup.style.display = 'none';
  if (!invoice) {
    intakeLineItemSelect.innerHTML = '<option value="">Select an invoice first…</option>';
    return;
  }
  const options = ['<option value="">Whole delivery (not one specific item)</option>'];
  if (invoice.items && invoice.items.length) {
    options.push(...invoice.items.map((it) => `<option value="${it.id}" data-qty="${it.quantity}">${escapeHtml(it.item_name)} × ${it.quantity}</option>`));
  }
  options.push('<option value="__manual__">Type the product name…</option>');
  intakeLineItemSelect.innerHTML = options.join('');
}
intakeLineItemSelect.addEventListener('change', () => {
  intakeManualNameGroup.style.display = intakeLineItemSelect.value === '__manual__' ? '' : 'none';
  const selected = intakeLineItemSelect.selectedOptions[0];
  const qty = selected?.dataset.qty;
  if (qty) document.getElementById('intake-count').value = qty;
});

document.getElementById('intake-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('intake-error');
  errorEl.classList.remove('visible');

  const fileInput = document.getElementById('intake-file');
  if (!fileInput.files.length) {
    errorEl.textContent = 'Attach a photo or video.';
    errorEl.classList.add('visible');
    return;
  }
  if (!currentBranchId) {
    errorEl.textContent = 'Pick a branch first.';
    errorEl.classList.add('visible');
    return;
  }
  if (!intakeInvoiceSelect.value) {
    errorEl.textContent = 'Select the delivery this was on.';
    errorEl.classList.add('visible');
    return;
  }
  const isManualItem = intakeLineItemSelect.value === '__manual__';
  if (isManualItem && !document.getElementById('intake-manual-name').value.trim()) {
    errorEl.textContent = 'Type the product name.';
    errorEl.classList.add('visible');
    return;
  }

  const formData = new FormData();
  formData.append('branch_id', currentBranchId);
  formData.append('invoice_id', intakeInvoiceSelect.value);
  if (intakeLineItemSelect.value && !isManualItem) formData.append('invoice_line_item_id', intakeLineItemSelect.value);
  if (isManualItem) formData.append('item_name', document.getElementById('intake-manual-name').value.trim());
  formData.append('file', fileInput.files[0]);
  if (document.getElementById('intake-count').value) formData.append('item_count', document.getElementById('intake-count').value);
  if (document.getElementById('intake-damaged').value) formData.append('damaged_count', document.getElementById('intake-damaged').value);
  if (document.getElementById('intake-missing').value) formData.append('missing_count', document.getElementById('intake-missing').value);
  if (document.getElementById('intake-notes').value) formData.append('notes', document.getElementById('intake-notes').value);

  const btn = document.getElementById('intake-submit');
  btn.disabled = true;
  try {
    await Api.uploadIntakeReceipt(formData);
    showToast('Waybill complaint logged.');
    // Spec: "if there are still other complaints for other products,
    // you should be able to still add a product again and lodge its
    // own complaint too" -- keep the same supplier/invoice selected so
    // logging another item on this same delivery doesn't require
    // re-picking the supplier and invoice again. Only the item-specific
    // fields reset.
    intakeLineItemSelect.value = '';
    intakeManualNameGroup.style.display = 'none';
    document.getElementById('intake-manual-name').value = '';
    document.getElementById('intake-count').value = '';
    document.getElementById('intake-damaged').value = '';
    document.getElementById('intake-missing').value = '';
    document.getElementById('intake-notes').value = '';
    document.getElementById('intake-file').value = '';
    loadIntakeReceipts();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------------ Init

loadCategoriesFilter('tr');
loadProductsFilter('tr');
loadBranches().then(() => {
  loadIntakeReceipts();
  loadTransfers();
  loadWarehouseStock();
  loadWarehouseHistory();
});
