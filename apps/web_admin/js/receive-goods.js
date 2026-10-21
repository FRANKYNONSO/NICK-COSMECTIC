// Receive Goods (fix41, step 3): enter quantities, save a draft, reopen and edit saved drafts.
// Saving or editing a draft never changes stock. Confirming (posting) is not on this page yet.
Auth.requireAuth();
Perms.applyNav();
const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
document.getElementById('current-user-name').textContent = (currentUser && currentUser.full_name) || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser && currentUser.role] || (currentUser && currentUser.role) || '';
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
function escapeAttr(str) {
  return escapeHtml(String(str)).replace(/"/g, '&quot;');
}

function el(id) { return document.getElementById(id); }
function show(id, on) { el(id).style.display = on ? '' : 'none'; }
function asList(x) {
  if (Array.isArray(x)) return x;
  if (x && Array.isArray(x.items)) return x.items;
  return [];
}

function errText(err) {
  if (!err) return 'Something went wrong';
  const d = err.detail;
  if (typeof d === 'string' && d) return d;
  if (Array.isArray(d)) {
    return d.map((x) => {
      const where = Array.isArray(x.loc) ? x.loc.slice(1).join('.') : '';
      return (where ? where + ': ' : '') + (x.msg || '');
    }).join('; ');
  }
  if (d && typeof d === 'object' && d.message) return d.message;
  return err.message || 'Something went wrong';
}

function shortId(id) { return String(id || '').slice(0, 8); }
function koboToNgn(k) {
  if (k === null || k === undefined) return '';
  const n = Number(k);
  if (!isFinite(n)) return '';
  const whole = Math.floor(n / 100);
  const frac = n % 100;
  return frac === 0 ? String(whole) : whole + '.' + String(frac).padStart(2, '0');
}
function toLocalInput(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
}
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '' : d.toLocaleString();
}

const MAX_ROWS = 300;
const MAX_LINES = 500;
const MAX_QTY = 1000000;
// Shown only as a suggestion for products that have no limits row yet.
const DEFAULT_MAX_SHELF = 24;

let branches = [];
let products = [];
let summaryById = {};
let entries = {};
let drafts = [];
let loadSeq = 0;
let draftSeq = 0;
let saving = false;
let receipt = null;
let dirty = false;
let activeBranch = '';
function newKey() { return 'rg-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); }
let idemKey = newKey();

function canReceive() {
  try { return Perms.can('manage_inventory'); } catch (e) { return false; }
}

// ---------- validation ----------
function parseWhole(v) { return /^\d+$/.test(v) ? parseInt(v, 10) : null; }
function parseKobo(v) {
  if (!/^\d+(\.\d{1,2})?$/.test(v)) return null;
  const parts = v.split('.');
  const frac = ((parts[1] || '') + '00').slice(0, 2);
  return parseInt(parts[0], 10) * 100 + parseInt(frac, 10);
}
function entryOf(id) { return entries[id] || { received: '', shelf: '', cost: '' }; }
function isBlankEntry(e) { return e.received === '' && e.shelf === '' && e.cost === ''; }
function hasEntries() { return Object.keys(entries).some((id) => !isBlankEntry(entries[id])); }
function limitsConfigured(id) {
  const s = summaryById[id];
  return !!(s && s.limits_configured);
}
function capacityOf(id) {
  const s = summaryById[id];
  return s ? s.available_shelf_capacity : DEFAULT_MAX_SHELF;
}

// PATCH6A-SHELF-FILL: when staff type Received, fill To shelf with what fits under the
// saved shelf limit. A number staff typed in To shelf is never changed.
let shelfAuto = {};
function suggestedShelf(id, receivedText) {
  if (!limitsConfigured(id)) return null;
  const rec = parseWhole(receivedText);
  if (rec === null || rec < 1 || rec > MAX_QTY) return null;
  return Math.min(rec, capacityOf(id));
}
function applyShelfSuggestion(id, cur, field) {
  if (field === 'shelf') { shelfAuto[id] = false; return; }
  if (field !== 'received') return;
  if (cur.shelf !== '' && !shelfAuto[id]) return;
  const s = suggestedShelf(id, cur.received);
  if (s === null) {
    if (shelfAuto[id]) { cur.shelf = ''; shelfAuto[id] = false; }
    return;
  }
  cur.shelf = String(s);
  shelfAuto[id] = true;
}
function syncShelfBox(id, value) {
  const tr = el('rg-tbody').querySelector('tr[data-row="' + id + '"]');
  const box = tr ? tr.querySelector('input[data-field="shelf"]') : null;
  if (box && box.value !== value) box.value = value;
}

function checkEntry(id) {
  const e = entryOf(id);
  const r = { blank: false, error: null, warn: null, info: null, note: null, received: 0, shelf: 0, warehouse: 0, kobo: null };
  if (isBlankEntry(e)) { r.blank = true; return r; }
  if (e.received === '') { r.error = 'Enter the quantity received.'; return r; }
  const rec = parseWhole(e.received);
  if (rec === null) { r.error = 'Received must be a whole number.'; return r; }
  if (rec < 1) { r.error = 'Received must be at least 1.'; return r; }
  if (rec > MAX_QTY) { r.error = 'Received is too large.'; return r; }
  let shelf = 0;
  if (e.shelf !== '') {
    shelf = parseWhole(e.shelf);
    if (shelf === null) { r.error = 'To shelf must be a whole number.'; return r; }
    if (shelf > rec) { r.error = 'To shelf cannot exceed received (' + rec + ').'; return r; }
  }
  if (e.cost !== '') {
    const kobo = parseKobo(e.cost);
    if (kobo === null) { r.error = 'Cost must look like 1500 or 1500.50.'; return r; }
    r.kobo = kobo;
  } else {
    r.note = 'Cost unknown: will be marked unverified.';
  }
  r.received = rec;
  r.shelf = shelf;
  r.warehouse = rec - shelf;
  const cap = capacityOf(id);
  if (shelf > cap) {
    if (limitsConfigured(id)) {
      r.warn = 'Only ' + cap + ' unit(s) fit on the shelf. Confirming is blocked unless a manager overrides.';
    } else {
      r.info = 'No shelf limit is set for this product, so the suggested maximum of ' + cap + ' is not enforced.';
    }
  }
  if (shelfAuto[id] && limitsConfigured(id)) {
    if (shelf === 0) r.autoNote = 'The shelf is full, so all of them go to the warehouse.';
    else if (shelf < rec) r.autoNote = 'Shelf filled in: ' + shelf + ' fit under the limit, the other ' + (rec - shelf) + ' go to the warehouse. You can change it.';
  }
  return r;
}

function buildLines() {
  const out = { lines: [], errors: 0, warns: 0, received: 0, shelf: 0 };
  products.forEach((p) => {
    if (!entries[p.id]) return;
    const c = checkEntry(p.id);
    if (c.blank) return;
    if (c.error) { out.errors++; return; }
    if (c.warn) out.warns++;
    out.received += c.received;
    out.shelf += c.shelf;
    const line = { product_id: p.id, quantity_received: c.received, quantity_to_shelf: c.shelf };
    if (c.kobo !== null) line.unit_cost_kobo = c.kobo;
    out.lines.push(line);
  });
  return out;
}

// ---------- rendering ----------
function pillClass(state) {
  if (state === 'out_of_stock') return 'out';
  if (state === 'shelf_empty' || state === 'low_shelf') return 'low';
  return 'ok';
}

function statusHtml(s) {
  if (!s) return '<span class="stock-pill out">Out of stock</span>';
  let html = '<span class="stock-pill ' + pillClass(s.stock_state) + '">' + escapeHtml(s.alert_label || s.stock_state || '') + '</span>';
  if (s.over_limit) html += ' <span class="stock-pill low">Over shelf limit</span>';
  if (s.has_expired_shelf_stock && s.expired_label) {
    html += '<div style="font-size:11.5px;color:var(--ink-muted);margin-top:4px;">' + escapeHtml(s.expired_label) + '</div>';
  }
  return html;
}

function inputHtml(id, field, value, width, mode) {
  return '<input type="text" inputmode="' + mode + '" autocomplete="off" data-pid="' + escapeAttr(id) + '" data-field="' + field + '" value="' + escapeAttr(value || '') + '"' +
    ' style="width:' + width + 'px;padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit;text-align:right;" />';
}

function whText(c) { return (c.blank || c.error) ? '-' : String(c.warehouse); }

function checkHtml(c) {
  if (c.blank) return '';
  if (c.error) return '<div style="max-width:240px;white-space:normal;color:var(--danger,#e5484d);">' + escapeHtml(c.error) + '</div>';
  let h = '<div style="max-width:240px;white-space:normal;">';
  h += c.warn ? '<span class="stock-pill low">Over shelf limit</span>' : '<span class="stock-pill ok">OK</span>';
  if (c.warn) h += '<div style="font-size:12px;color:var(--warning,#d99a00);margin-top:4px;">' + escapeHtml(c.warn) + '</div>';
  if (c.autoNote) h += '<div style="font-size:12px;color:var(--ink-muted);margin-top:4px;">' + escapeHtml(c.autoNote) + '</div>';
  if (c.info) h += '<div style="font-size:12px;color:var(--ink-muted);margin-top:4px;">' + escapeHtml(c.info) + '</div>';
  if (c.note) h += '<div style="font-size:12px;color:var(--ink-muted);margin-top:4px;">' + escapeHtml(c.note) + '</div>';
  return h + '</div>';
}

function rowHtml(p) {
  const id = p.id;
  const s = summaryById[id];
  const e = entryOf(id);
  const c = checkEntry(id);
  let name = '<strong>' + escapeHtml(p.name || '') + '</strong>';
  if (p.size) name += ' <span style="color:var(--ink-muted);">' + escapeHtml(p.size) + '</span>';
  const meta = [p.sku, p.barcode].filter(Boolean).join(' / ');
  let figs;
  if (!s) {
    figs = ['0', '0', '0', DEFAULT_MAX_SHELF + ' (default)', String(DEFAULT_MAX_SHELF)];
  } else {
    const shelfText = String(s.shelf_stock) + (s.shelf_expired_stock > 0 ? ' (' + s.shelf_expired_stock + ' expired)' : '');
    const limitText = String(s.max_shelf_quantity) + (s.limits_configured ? '' : ' (default)');
    figs = [String(s.total_stock), String(s.warehouse_stock), shelfText, limitText, String(s.available_shelf_capacity)];
  }
  return '<tr data-row="' + escapeAttr(id) + '">' +
    '<td>' + name + '<div style="font-size:11.5px;color:var(--ink-muted);margin:2px 0 4px;">' + escapeHtml(meta) + '</div>' + statusHtml(s) + '</td>' +
    '<td><strong>' + escapeHtml(figs[0]) + '</strong></td><td>' + escapeHtml(figs[1]) + '</td><td>' + escapeHtml(figs[2]) + '</td><td>' + escapeHtml(figs[3]) + '</td><td>' + escapeHtml(figs[4]) + '</td>' +
    '<td>' + inputHtml(id, 'received', e.received, 80, 'numeric') + '</td>' +
    '<td>' + inputHtml(id, 'shelf', e.shelf, 80, 'numeric') + '</td>' +
    '<td data-cell="wh">' + escapeHtml(whText(c)) + '</td>' +
    '<td>' + inputHtml(id, 'cost', e.cost, 96, 'decimal') + '</td>' +
    '<td data-cell="check" style="min-width:200px;">' + checkHtml(c) + '</td></tr>';
}

function updateRowComputed(id) {
  const tr = el('rg-tbody').querySelector('tr[data-row="' + id + '"]');
  if (!tr) return;
  const c = checkEntry(id);
  tr.querySelector('[data-cell="wh"]').textContent = whText(c);
  tr.querySelector('[data-cell="check"]').innerHTML = checkHtml(c);
}

function refreshSummary() {
  const b = buildLines();
  let text;
  if (!b.lines.length && !b.errors) {
    text = 'No quantities entered yet.';
  } else {
    text = b.lines.length + ' line(s): ' + b.received + ' piece(s) received, ' + b.shelf + ' to shelf, ' + (b.received - b.shelf) + ' to warehouse.';
    if (b.errors) text += ' ' + b.errors + ' row(s) with errors.';
    if (b.warns) text += ' ' + b.warns + ' row(s) over the shelf limit.';
  }
  if (receipt) {
    text = (dirty ? 'Editing draft ' : 'Draft ') + shortId(receipt.id) + (dirty ? ' (unsaved changes). ' : ' is saved. ') + text;
  }
  el('rg-summary').textContent = text;
  el('rg-save').disabled = saving || b.errors > 0 || b.lines.length === 0 || (!!receipt && !dirty);
  el('rg-save').textContent = saving ? 'Saving...' : (receipt ? 'Save changes' : 'Save draft');
  el('rg-new').style.display = receipt ? '' : 'none';
}

function renderTable() {
  const q = el('rg-search').value.trim().toLowerCase();
  const filtered = products.filter((p) => {
    if (!q) return true;
    return [p.name, p.sku, p.barcode].some((v) => v && String(v).toLowerCase().indexOf(q) !== -1);
  });
  const shown = filtered.slice(0, MAX_ROWS);
  el('rg-tbody').innerHTML = shown.map(rowHtml).join('');
  show('rg-table', shown.length > 0);
  show('rg-empty', products.length === 0);
  show('rg-nomatch', products.length > 0 && filtered.length === 0);
  let note = products.length + ' product(s)';
  if (q) note = filtered.length + ' of ' + products.length + ' product(s)';
  if (filtered.length > MAX_ROWS) note += ' - showing the first ' + MAX_ROWS + ', narrow the search to see the rest';
  el('rg-count').textContent = products.length ? note : '';
  refreshSummary();
}

// ---------- messages, header, reset ----------
const HEADER_IDS = ['rg-supplier', 'rg-ref', 'rg-delivered', 'rg-notes'];
function clearHeader() { HEADER_IDS.forEach((id) => { el(id).value = ''; }); }

function showError(msg) {
  const box = el('rg-error');
  box.textContent = msg;
  box.style.display = msg ? '' : 'none';
}

function showNote(text, isSaved) {
  const box = el('rg-saved');
  if (!text) { box.style.display = 'none'; return; }
  box.innerHTML = (isSaved ? '<span class="stock-pill ok">Saved</span> ' : '') + escapeHtml(text);
  box.style.display = '';
}

function resetEntries() {
  entries = {};
  shelfAuto = {};
  receipt = null;
  dirty = false;
  idemKey = newKey();
  clearHeader();
  showNote('', false);
  showError('');
}

function markDirty() {
  if (receipt && !dirty) {
    dirty = true;
    refreshSummary();
    renderDrafts();
  }
}

function startNew() {
  if (hasEntries() && (!receipt || dirty) && !window.confirm('Discard the changes you have not saved?')) return;
  resetEntries();
  renderTable();
  renderDrafts();
}

// ---------- saved drafts ----------
function renderDrafts() {
  const box = el('rg-drafts-list');
  if (!drafts.length) {
    box.innerHTML = '<div style="font-size:13px;color:var(--ink-muted);">No saved drafts for this branch.</div>';
    return;
  }
  const names = {};
  products.forEach((p) => { names[p.id] = p.name; });
  box.innerHTML = drafts.map((d) => {
    const lines = d.lines || [];
    let pieces = 0;
    lines.forEach((l) => { pieces += Number(l.quantity_received) || 0; });
    const isOpen = !!(receipt && receipt.id === d.id);
    const what = lines.slice(0, 3).map((l) => escapeHtml(names[l.product_id] || 'unknown product')).join(', ') + (lines.length > 3 ? ', ...' : '');
    return '<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:8px 12px;border:1px solid var(--border);border-radius:10px;">' +
      '<div style="font-size:13px;"><strong>' + escapeHtml(shortId(d.id)) + '</strong> ' + escapeHtml(fmtTime(d.recorded_at)) +
      ' - ' + lines.length + ' line(s), ' + pieces + ' piece(s)' + (d.supplier_name ? ', ' + escapeHtml(d.supplier_name) : '') +
      (isOpen ? ' <span class="stock-pill ok">open</span>' : '') +
      '<div style="font-size:12px;color:var(--ink-muted);">' + what + '</div></div>' +
      '<button type="button" class="btn btn-ghost" style="width:auto;" data-open="' + escapeAttr(d.id) + '">' + (isOpen ? 'Reload' : 'Open') + '</button></div>';
  }).join('');
}

async function loadDrafts() {
  const branchId = el('rg-branch').value;
  const seq = ++draftSeq;
  let failed = '';
  let list = [];
  try {
    list = asList(await Api.listGoodsReceipts({ branch_id: branchId, status: 'draft' }));
  } catch (err) {
    failed = errText(err);
  }
  if (seq !== draftSeq) return;
  drafts = list;
  el('rg-drafts-note').textContent = failed ? 'Could not load drafts: ' + failed : (drafts.length ? drafts.length + ' draft(s)' : '');
  renderDrafts();
}

async function openDraft(id) {
  if (saving) return;
  if (hasEntries() && (!receipt || dirty) && !window.confirm('Opening a draft replaces what is on the page. Discard the changes you have not saved?')) return;
  let r;
  try {
    r = await Api.getGoodsReceipt(id);
  } catch (err) {
    showToast(errText(err), true);
    return;
  }
  if (!r || r.status !== 'draft') { showToast('That receipt is not a draft any more.', true); loadDrafts(); return; }
  if (r.branch_id !== el('rg-branch').value) { showToast('That draft belongs to a different branch.', true); return; }
  if (r.invoice_id) { showToast('This draft is linked to a supplier invoice. Editing it here is not supported yet.', true); return; }
  const known = {};
  products.forEach((p) => { known[p.id] = true; });
  const seen = {};
  const next = {};
  const lines = r.lines || [];
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (!known[l.product_id]) { showToast('Row ' + (i + 1) + ' of this draft uses a product that is not in the list (deleted?). It cannot be edited here.', true); return; }
    if (seen[l.product_id]) { showToast('This draft has more than one line for the same product. Editing it here is not supported yet.', true); return; }
    if (l.invoice_line_item_id || (l.notes && String(l.notes).trim())) { showToast('Row ' + (i + 1) + ' has an invoice link or a note that this page cannot keep. Editing it here is not supported yet.', true); return; }
    seen[l.product_id] = true;
    next[l.product_id] = { received: String(l.quantity_received), shelf: String(l.quantity_to_shelf), cost: koboToNgn(l.unit_cost_kobo) };
  }
  entries = next;
  shelfAuto = {};
  receipt = r;
  dirty = false;
  el('rg-supplier').value = r.supplier_name || '';
  el('rg-ref').value = r.invoice_reference || '';
  el('rg-delivered').value = r.delivered_at ? toLocalInput(r.delivered_at) : '';
  el('rg-notes').value = r.notes || '';
  showError('');
  showNote('Editing draft ' + shortId(r.id) + '. Changes are saved only when you click Save changes. Stock is not changed.', false);
  renderTable();
  renderDrafts();
}

// ---------- data loading ----------
async function loadBranchData() {
  const branchId = el('rg-branch').value;
  const seq = ++loadSeq;
  el('rg-tbody').innerHTML = '<tr class="loading-row"><td colspan="11">Loading stock...</td></tr>';
  show('rg-table', true);
  show('rg-empty', false);
  show('rg-nomatch', false);
  try {
    const results = await Promise.all([
      Api.listProducts({ branchId: branchId }),
      Api.goodsStockSummary({ branchId: branchId })
    ]);
    if (seq !== loadSeq) return;
    products = asList(results[0]);
    summaryById = {};
    asList(results[1]).forEach((r) => { summaryById[r.product_id] = r; });
    renderTable();
  } catch (err) {
    if (seq !== loadSeq) return;
    products = [];
    summaryById = {};
    renderTable();
    showToast(errText(err), true);
  }
  await loadDrafts();
}

function onBranchSelected() {
  const sel = el('rg-branch');
  if (hasEntries() && (!receipt || dirty) && !window.confirm('Changing the branch clears what you typed. Continue?')) {
    sel.value = activeBranch;
    return;
  }
  activeBranch = sel.value;
  resetEntries();
  loadBranchData();
}

// ---------- save draft / save changes ----------
async function saveDraft() {
  if (saving) return;
  showError('');
  const b = buildLines();
  if (b.errors > 0) { showToast('Fix the rows marked in red first.', true); return; }
  if (!b.lines.length) { showToast('Enter a quantity for at least one product.', true); return; }
  if (b.lines.length > MAX_LINES) { showError('A receipt can have at most ' + MAX_LINES + ' lines. Split this delivery.'); return; }
  if (b.warns > 0 && !window.confirm(b.warns + ' row(s) put more on the shelf than the shelf limit allows. The draft can be saved, but confirming will be blocked unless a manager overrides. Save anyway?')) return;
  let delivered = null;
  const dv = el('rg-delivered').value;
  if (dv) {
    const d = new Date(dv);
    if (isNaN(d.getTime())) { showError('The delivery date and time are not valid.'); return; }
    delivered = d.toISOString();
  }
  const payload = {
    branch_id: el('rg-branch').value,
    supplier_name: el('rg-supplier').value.trim() || null,
    invoice_reference: el('rg-ref').value.trim() || null,
    delivered_at: delivered,
    notes: el('rg-notes').value.trim() || null,
    reconciliation_notes: receipt ? (receipt.reconciliation_notes || null) : null,
    post_now: false,
    lines: b.lines
  };
  const updating = !!receipt;
  if (!updating) payload.idempotency_key = idemKey;
  saving = true;
  refreshSummary();
  try {
    const r = updating ? await Api.updateGoodsReceipt(receipt.id, payload) : await Api.createGoodsReceipt(payload);
    receipt = r;
    dirty = false;
    let text;
    if (r && r.duplicate) {
      text = 'This draft had already been saved by the same request (reference ' + shortId(r.id) + '). Nothing new was created. Stock was NOT changed.';
    } else {
      text = 'Draft ' + shortId(r.id) + (updating ? ' updated' : ' saved') + ' (status: ' + (r.status || '?') + '). Stock was NOT changed. Confirming the receipt is not on this page yet.';
    }
    showNote(text, true);
    showToast(updating ? 'Draft updated. Stock was not changed.' : 'Draft saved. Stock was not changed.');
    renderTable();
    await loadDrafts();
  } catch (err) {
    showError(errText(err));
    showToast('The draft was not saved.', true);
  } finally {
    saving = false;
    refreshSummary();
  }
}

// ---------- start-up ----------
async function init() {
  const cards = ['rg-toolbar', 'rg-header-card', 'rg-footer', 'rg-drafts-card'];
  if (!canReceive()) {
    show('rg-no-access', true);
    show('rg-table', false);
    cards.forEach((id) => show(id, false));
    return;
  }
  try {
    branches = asList(await Api.listBranches());
  } catch (err) {
    showToast(errText(err), true);
    return;
  }
  if (!branches.length) {
    show('rg-no-branch', true);
    cards.forEach((id) => show(id, false));
    return;
  }
  try { BranchContext.renderBadge('branch-badge'); } catch (e) { /* badge is cosmetic */ }
  const sel = el('rg-branch');
  sel.innerHTML = branches.map((b) => '<option value="' + escapeAttr(b.id) + '">' + escapeHtml(b.name || '') + '</option>').join('');
  const own = currentUser && currentUser.branch_id;
  let start = own || BranchContext.get() || branches[0].id;
  if (!branches.some((b) => b.id === start)) start = branches[0].id;
  sel.value = start;
  activeBranch = start;
  if (own) sel.disabled = true;
  sel.addEventListener('change', onBranchSelected);
  el('rg-search').addEventListener('input', renderTable);
  el('rg-save').addEventListener('click', saveDraft);
  el('rg-new').addEventListener('click', startNew);
  HEADER_IDS.forEach((id) => { el(id).addEventListener('input', markDirty); });
  el('rg-drafts-list').addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest ? ev.target.closest('[data-open]') : null;
    if (btn) openDraft(btn.dataset.open);
  });
  el('rg-tbody').addEventListener('input', (ev) => {
    const t = ev.target;
    if (!t || !t.dataset || !t.dataset.pid) return;
    const id = t.dataset.pid;
    const cur = Object.assign({ received: '', shelf: '', cost: '' }, entries[id] || {});
    cur[t.dataset.field] = t.value.trim();
    applyShelfSuggestion(id, cur, t.dataset.field);
    if (t.dataset.field === 'received') syncShelfBox(id, cur.shelf);
    if (isBlankEntry(cur)) { delete entries[id]; } else { entries[id] = cur; }
    showError('');
    updateRowComputed(id);
    if (receipt && !dirty) { dirty = true; renderDrafts(); }
    refreshSummary();
  });
  window.addEventListener('branch-context-changed', () => {
    const g = BranchContext.get();
    if (g && !own && g !== sel.value && branches.some((b) => b.id === g)) {
      sel.value = g;
      onBranchSelected();
    }
  });
  window.addEventListener('beforeunload', (ev) => {
    if (hasEntries() && (!receipt || dirty)) { ev.preventDefault(); ev.returnValue = ''; }
  });
  await loadBranchData();
}

init();