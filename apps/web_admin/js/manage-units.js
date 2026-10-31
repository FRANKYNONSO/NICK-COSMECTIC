// PATCH10B -- Manage Units page: every active unit gets a column in the Products table.
Auth.requireAuth();
Perms.applyNav();
Perms.guardPage('manage_products');

const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
document.getElementById('current-user-name').textContent = (currentUser && currentUser.full_name) || 'Signed in';
document.getElementById('current-user-role').textContent =
  roleLabels[currentUser && currentUser.role] || (currentUser && currentUser.role) || '';
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
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function errText(err) {
  return (err && err.message) ? err.message : 'Something went wrong. Try again.';
}

// Carton, bag and roll sizes live on each product (existing product columns).
// Every other unit uses the new per-product size table, with the unit number as default.
const PER_PRODUCT_CODES = ['carton', 'bag', 'roll'];

let allUnits = [];
let allProducts = [];
let unitSizes = {};          // { productId: { unitId: pieces } }
let productsLoaded = false;

function activeColumnUnits() {
  return allUnits
    .filter((u) => u.is_active && u.code !== 'piece')
    .slice()
    .sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0));
}

// ------------------------------------------------------------------ Units
async function loadUnits() {
  try {
    allUnits = await Api.listPackagingUnits({ includeInactive: true });
  } catch (err) {
    allUnits = [];
    showToast(errText(err), true);
  }
  renderUnits();
  renderProducts();
}

function renderUnits() {
  const tbody = document.getElementById('units-tbody');
  tbody.innerHTML = allUnits.map((u) => {
    if (u.code === 'piece') {
      return '<tr><td>' + escapeHtml(u.label) + '</td><td>' + escapeHtml(u.plural_label) +
        '</td><td>1</td><td colspan="3" class="mu-note">Base unit. Always on.</td></tr>';
    }
    const perProduct = PER_PRODUCT_CODES.includes(u.code);
    const pieces = perProduct
      ? '<span class="mu-note">Set per product below</span>'
      : '<input type="number" class="mu-num" min="2" step="1" data-f="pieces" placeholder="none" value="' +
        (u.fixed_pieces == null ? '' : u.fixed_pieces) + '" />';
    return '<tr data-unit-id="' + escapeHtml(u.id) + '">' +
      '<td><input type="text" data-f="label" maxlength="40" value="' + escapeHtml(u.label) + '" /></td>' +
      '<td><input type="text" data-f="plural" maxlength="40" value="' + escapeHtml(u.plural_label) + '" /></td>' +
      '<td>' + pieces + '</td>' +
      '<td><input type="checkbox" data-f="breakdown"' + (u.include_in_breakdown ? ' checked' : '') + ' /></td>' +
      '<td><input type="checkbox" data-f="active"' + (u.is_active ? ' checked' : '') + ' /></td>' +
      '<td><button type="button" class="btn btn-ghost" data-act="save-unit" style="width:auto;">Save</button></td>' +
      '</tr>';
  }).join('');
}

async function saveUnit(tr) {
  const id = tr.dataset.unitId;
  const unit = allUnits.find((u) => u.id === id);
  const body = {
    label: tr.querySelector('[data-f="label"]').value.trim(),
    plural_label: tr.querySelector('[data-f="plural"]').value.trim(),
    include_in_breakdown: tr.querySelector('[data-f="breakdown"]').checked,
    is_active: tr.querySelector('[data-f="active"]').checked,
  };
  if (!body.label) { showToast('Give the unit a name.', true); return; }
  const pcs = tr.querySelector('[data-f="pieces"]');
  if (pcs) {
    const raw = pcs.value.trim();
    if (raw === '') {
      body.fixed_pieces = null;
      if (unit && unit.fixed_pieces != null) {
        const okClear = window.confirm('Remove the default size of ' + body.label + '? ' +
          'Products that have no size of their own will stop using this unit. Piece totals do not change.');
        if (!okClear) return;
      }
    } else {
      const n = Number(raw);
      if (!Number.isInteger(n) || n < 2) { showToast('Pieces in one unit must be 2 or more.', true); return; }
      body.fixed_pieces = n;
      if (unit && unit.fixed_pieces !== n) {
        const ok = window.confirm('Change ' + body.label + ' to ' + n + ' pieces? ' +
          'This changes how stock and receipts are shown. Piece totals do not change.');
        if (!ok) return;
      }
    }
  }
  try {
    await Api.updatePackagingUnit(id, body);
    showToast('Unit saved.');
    await loadUnits();
  } catch (err) {
    showToast(errText(err), true);
  }
}

document.getElementById('units-tbody').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act="save-unit"]');
  if (!btn) return;
  const tr = btn.closest('tr');
  if (tr) saveUnit(tr);
});

document.getElementById('nu-add-btn').addEventListener('click', async () => {
  const label = document.getElementById('nu-label').value.trim();
  const plural = document.getElementById('nu-plural').value.trim();
  const raw = document.getElementById('nu-pieces').value.trim();
  if (!label) { showToast('Give the unit a name.', true); return; }
  const body = {
    label: label,
    include_in_breakdown: document.getElementById('nu-breakdown').checked,
  };
  if (raw !== '') {
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 2) { showToast('Pieces in one unit must be 2 or more, or leave it blank.', true); return; }
    body.fixed_pieces = n;
  }
  if (plural) body.plural_label = plural;
  try {
    await Api.createPackagingUnit(body);
    document.getElementById('nu-label').value = '';
    document.getElementById('nu-plural').value = '';
    document.getElementById('nu-pieces').value = '';
    showToast('Unit added.');
    await loadUnits();
  } catch (err) {
    showToast(errText(err), true);
  }
});

// --------------------------------------------------------------- Products
async function loadSizes() {
  unitSizes = {};
  try {
    const r = await Api.listProductUnitSizes();
    const rows = Array.isArray(r) ? r : ((r && (r.items || r.sizes)) || []);
    rows.forEach((row) => {
      const pid = String(row.product_id);
      if (!unitSizes[pid]) unitSizes[pid] = {};
      unitSizes[pid][String(row.unit_id)] = row.pieces;
    });
  } catch (err) {
    showToast(errText(err), true);
  }
}

async function loadProducts() {
  try {
    const r = await Api.listProducts({});
    allProducts = Array.isArray(r) ? r : ((r && (r.items || r.products)) || []);
  } catch (err) {
    allProducts = [];
    showToast(errText(err), true);
  }
  allProducts.sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')));
  await loadSizes();
  productsLoaded = true;
  renderProducts();
}

function packValue(p, key) {
  const v = p['default_pieces_per_' + key];
  return v == null ? '' : String(v);
}

function applySearch() {
  const q = document.getElementById('mu-search').value.trim().toLowerCase();
  document.getElementById('mu-tbody').querySelectorAll('tr').forEach((tr) => {
    tr.hidden = q !== '' && !(tr.dataset.name || '').includes(q);
  });
}

function renderProducts() {
  if (!productsLoaded) return;
  const cols = activeColumnUnits();
  document.getElementById('mu-thead').innerHTML = '<tr><th>Product</th><th>Category</th>' +
    cols.map((u) => '<th>Pieces per ' + escapeHtml(String(u.label || '').toLowerCase()) + '</th>').join('') +
    '<th></th></tr>';
  const tbody = document.getElementById('mu-tbody');
  tbody.innerHTML = allProducts.map((p) => {
    const pid = String(p.id);
    const boxes = cols.map((u) => {
      if (PER_PRODUCT_CODES.includes(u.code)) {
        const v = packValue(p, u.code);
        return '<td><input type="number" class="mu-num" min="1" step="1" data-k="' + escapeHtml(u.code) +
          '" data-orig="' + escapeHtml(v) + '" value="' + escapeHtml(v) + '" /></td>';
      }
      const own = unitSizes[pid] ? unitSizes[pid][String(u.id)] : null;
      const v = own == null ? '' : String(own);
      const ph = u.fixed_pieces == null ? '' : String(u.fixed_pieces);
      return '<td><input type="number" class="mu-num" min="2" step="1" data-u="' + escapeHtml(u.id) +
        '" data-orig="' + escapeHtml(v) + '" value="' + escapeHtml(v) + '" placeholder="' + escapeHtml(ph) + '" /></td>';
    }).join('');
    return '<tr data-product-id="' + escapeHtml(p.id) + '" data-name="' + escapeHtml(String(p.name || '').toLowerCase()) + '">' +
      '<td>' + escapeHtml(p.name) + '</td><td>' + escapeHtml(p.category || '') + '</td>' + boxes +
      '<td><button type="button" class="btn btn-ghost" data-act="save-product" style="width:auto;" disabled>Save</button></td>' +
      '</tr>';
  }).join('');
  document.getElementById('mu-empty').style.display = allProducts.length ? 'none' : '';
  applySearch();
}

function refreshDirty(tr) {
  let dirty = false;
  tr.querySelectorAll('input[data-orig]').forEach((i) => {
    if (i.value.trim() !== i.dataset.orig) dirty = true;
  });
  tr.querySelector('[data-act="save-product"]').disabled = !dirty;
  tr.classList.toggle('mu-dirty', dirty);
}

function parsePack(raw, minValue) {
  const s = String(raw).trim();
  if (s === '') return { ok: true, value: null };
  const n = Number(s);
  if (!Number.isInteger(n) || n < minValue) return { ok: false, value: null };
  return { ok: true, value: n };
}

async function saveProduct(tr) {
  const id = tr.dataset.productId;
  const pid = String(id);
  const packInputs = Array.from(tr.querySelectorAll('input[data-k]'));
  const sizeInputs = Array.from(tr.querySelectorAll('input[data-u]'))
    .filter((i) => i.value.trim() !== i.dataset.orig);
  const packDirty = packInputs.some((i) => i.value.trim() !== i.dataset.orig);

  const packPayload = {};
  if (packDirty) {
    for (const input of packInputs) {
      const r = parsePack(input.value, 1);
      if (!r.ok) { showToast('Pieces per ' + input.dataset.k + ' must be a whole number, 1 or more.', true); return; }
      packPayload['default_pieces_per_' + input.dataset.k] = r.value;
    }
  }
  const sizes = {};
  for (const input of sizeInputs) {
    const r = parsePack(input.value, 2);
    if (!r.ok) { showToast('Pieces in one unit must be a whole number, 2 or more.', true); return; }
    sizes[input.dataset.u] = r.value;
  }
  if (!packDirty && !sizeInputs.length) { refreshDirty(tr); return; }

  const btn = tr.querySelector('[data-act="save-product"]');
  btn.disabled = true;
  let failed = false;

  if (packDirty) {
    try {
      await Api.updateProduct(id, packPayload);
      const product = allProducts.find((p) => String(p.id) === pid);
      packInputs.forEach((input) => {
        input.dataset.orig = input.value.trim();
        if (product) product['default_pieces_per_' + input.dataset.k] = packPayload['default_pieces_per_' + input.dataset.k];
      });
    } catch (err) {
      failed = true;
      showToast(errText(err), true);
    }
  }

  if (!failed && sizeInputs.length) {
    try {
      await Api.setProductUnitSizes(id, sizes);
      if (!unitSizes[pid]) unitSizes[pid] = {};
      sizeInputs.forEach((input) => {
        input.dataset.orig = input.value.trim();
        const uid = input.dataset.u;
        if (sizes[uid] == null) delete unitSizes[pid][uid];
        else unitSizes[pid][uid] = sizes[uid];
      });
    } catch (err) {
      failed = true;
      showToast(errText(err), true);
    }
  }

  refreshDirty(tr);
  if (!failed) showToast('Saved.');
}

const muBody = document.getElementById('mu-tbody');
muBody.addEventListener('input', (e) => {
  const tr = e.target.closest('tr');
  if (tr) refreshDirty(tr);
});
muBody.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act="save-product"]');
  if (!btn) return;
  const tr = btn.closest('tr');
  if (tr) saveProduct(tr);
});

document.getElementById('mu-search').addEventListener('input', applySearch);

loadUnits();
loadProducts();
