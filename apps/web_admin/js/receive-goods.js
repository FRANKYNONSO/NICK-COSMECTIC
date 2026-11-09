// Receive Goods (fix41, step 4): enter quantities, save a draft, reopen and edit saved drafts,
// and CONFIRM the receipt to add the stock (shelf + warehouse). Saving a draft never changes stock;
// only Confirm receipt does. Step 5: when the supplier invoice arrives, link it to the confirmed
// receipt (list "waiting for a supplier invoice"). Linking never adds stock; it only verifies costs.
// Step 6: "By pack" lets staff count in cartons, dozens, bags, rolls and loose pieces. The page turns
// that into pieces (the server and stock always work in pieces).
Auth.requireAuth();
Perms.applyNav();
const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier', store_assistant: 'Store assistant' }; // PATCHP3C-ROLE-LABEL
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
let posting = false;
let pending = [];
let pendingPack = null;
let pendingSeq = 0;
let linkBusy = false;
const LATE_AFTER_MS = 60 * 60 * 1000; // server treats a delivery older than 1 hour as a late entry
let receipt = null;
let dirty = false;
let activeBranch = '';
function newKey() { return 'rg-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); }
let idemKey = newKey();

function canReceive() {
  try { return Perms.can('manage_inventory'); } catch (e) { return false; }
}
function canCreateInvoice() {
  try { return Perms.can('manage_suppliers'); } catch (e) { return false; }
}
async function goCreateInvoice(receiptId) {
  // fix55: small window that creates a new invoice draft from a confirmed receipt.
  // Prices are left at 0 on purpose. Fill them in on the Invoice page, then use Link invoice.
  const rec = pending.find((r) => r.id === receiptId);
  if (!rec) { showToast('That receipt is not in the waiting list any more.', true); return; }
  const rlines = rec.lines || [];
  if (!rlines.length) { showToast('That receipt has no lines.', true); return; }
  ensureLinkCss();
  const pnames = productNames();
  const items = rlines.map((l) => ({
    item_name: pnames[l.product_id] || 'Item',
    product_id: l.product_id,
    quantity: Number(l.quantity_received) || 1,
    unit_label: 'pieces',
    rate_naira: 0
  }));

  const overlay = document.createElement('div');
  overlay.className = 'rgl-overlay';
  const card = document.createElement('div');
  card.className = 'rgl-card';
  card.style.width = 'min(480px,100%)';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  const what = items.slice(0, 5).map((it) => escapeHtml(it.item_name) + ' x ' + it.quantity).join(', ') + (items.length > 5 ? ', ...' : '');
  const btnStyle = 'style="width:100%;"';
  card.innerHTML =
    '<h3 class="rgl-title">Create an invoice for this receipt</h3>' +
    '<p class="rgl-sub">Receipt ' + escapeHtml(shortId(rec.id)) + ' - ' + items.length + ' line(s). The products and quantities are filled in. Prices stay at 0 until you fill them in on the Invoice page.</p>' +
    '<div style="font-size:12.5px;color:var(--ink-muted);margin:0 0 14px;">' + what + '</div>' +
    '<div id="rgc-err" class="rgl-err"></div>' +
    '<div id="rgc-new-wrap" style="margin-bottom:14px;">' +
    '<label class="rgl-label" for="rgc-supplier">Supplier (optional, but helps tell drafts apart)</label>' +
    '<input id="rgc-supplier" class="rgl-select" type="text" autocomplete="off" value="' + escapeAttr(rec.supplier_name || '') + '" placeholder="Type the supplier name" /></div>' +
    '<div style="display:flex;flex-direction:column;gap:10px;">' +
    '<button type="button" class="btn btn-primary" id="rgc-new-ok" style="width:100%;">Create draft</button>' +
    '<button type="button" class="btn btn-ghost" id="rgc-cancel" style="width:100%;">Cancel</button>' +
    '</div>';
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  const qc = (sel) => card.querySelector(sel);
  const errBox = qc('#rgc-err');
  const newOk = qc('#rgc-new-ok');
  const cancelBtn = qc('#rgc-cancel');
  let working = false;

  function closeIt() {
    document.removeEventListener('keydown', onKeyC);
    overlay.remove();
  }
  function onKeyC(e) { if (e.key === 'Escape' && !working) closeIt(); }
  document.addEventListener('keydown', onKeyC);
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay && !working) closeIt(); });

  cancelBtn.addEventListener('click', () => { if (!working) closeIt(); });
  qc('#rgc-supplier').focus();

  newOk.addEventListener('click', async () => {
    if (working) return;
    working = true;
    newOk.disabled = true;
    errBox.style.display = 'none';
    try {
      const typed = qc('#rgc-supplier').value.trim();
      let match = null;
      if (typed) {
        try {
          const found = asList(await Api.listSuppliers(typed));
          match = found.find((s) => String(s.name || '').toLowerCase() === typed.toLowerCase()) || null;
        } catch (e2) { match = null; }
      }
      await Api.createPurchaseInvoice({
        status: 'draft',
        branch_id: rec.branch_id,
        supplier_id: match ? match.id : undefined,
        supplier_name: typed || undefined,
        items: items
      });
      showToast('Invoice draft created. Open it from the Invoice page to fill in the prices.');
      closeIt();
    } catch (err) {
      working = false;
      newOk.disabled = false;
      errBox.textContent = errText(err);
      errBox.style.display = 'block';
    }
  });


}

function canOverride() {
  const r = currentUser && currentUser.role;
  return r === 'super_admin' || r === 'branch_manager';
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
    '<td>' + inputHtml(id, 'received', e.received, 80, 'numeric') +
      '<div style="margin-top:4px;"><button type="button" class="btn btn-ghost" style="width:auto;padding:2px 8px;font-size:12px;" data-pack="' + escapeAttr(id) + '">By pack</button></div>' +
      '<div data-cell="packnote" style="font-size:11.5px;color:var(--ink-muted);margin-top:2px;max-width:150px;white-space:normal;">' + escapeHtml(packSummary(e.pack)) + '</div></td>' +
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
  const pn = tr.querySelector('[data-cell="packnote"]');
  if (pn) pn.textContent = packSummary((entries[id] || {}).pack);
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
  el('rg-discard').style.display = (hasEntries() && (!receipt || dirty)) ? '' : 'none';
  const cb = el('rg-confirm');
  if (cb) {
    cb.disabled = saving || posting || b.errors > 0 || b.lines.length === 0;
    cb.textContent = posting ? 'Confirming...' : 'Confirm receipt';
  }
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

async function discardChanges() {
  if (saving || posting) return;
  const editing = !!receipt;
  const msg = editing
    ? 'Discard your unsaved changes? The draft goes back to how it was last saved.'
    : 'Discard everything you have typed? This clears the page.';
  if (!await uiConfirm(msg, { title: 'Discard changes', okText: 'Discard' })) return;
  if (editing) {
    // reload the saved draft from the server (changes already confirmed above)
    await openDraft(receipt.id, true);
  } else {
    resetEntries();
    renderTable();
    renderDrafts();
  }
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

async function openDraft(id, skipConfirm) {
  if (saving) return;
  if (!skipConfirm && hasEntries() && (!receipt || dirty) && !await uiConfirm('Opening a draft replaces what is on the page. Discard the changes you have not saved?')) return;
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
  await loadPending();
}

async function onBranchSelected() {
  const sel = el('rg-branch');
  if (hasEntries() && (!receipt || dirty) && !await uiConfirm('Changing the branch clears what you typed. Continue?')) {
    sel.value = activeBranch;
    return;
  }
  activeBranch = sel.value;
  resetEntries();
  loadBranchData();
}

// ---------- save draft / save changes ----------
// Returns true when the draft is stored on the server. opts: { quiet, skipWarnPrompt, reconNotes }
async function persistDraft(opts) {
  opts = opts || {};
  if (saving) return false;
  showError('');
  const b = buildLines();
  if (b.errors > 0) { showToast('Fix the rows marked in red first.', true); return false; }
  if (!b.lines.length) { showToast('Enter a quantity for at least one product.', true); return false; }
  if (b.lines.length > MAX_LINES) { showError('A receipt can have at most ' + MAX_LINES + ' lines. Split this delivery.'); return false; }
  if (!opts.skipWarnPrompt && b.warns > 0 && !await uiConfirm(b.warns + ' row(s) put more on the shelf than the shelf limit allows. The draft can be saved, but confirming will be blocked unless a manager overrides. Save anyway?', { title: 'Over the shelf limit', okText: 'Save anyway' })) return false;
  let delivered = null;
  const dv = el('rg-delivered').value;
  if (dv) {
    const d = new Date(dv);
    if (isNaN(d.getTime())) { showError('The delivery date and time are not valid.'); return false; }
    delivered = d.toISOString();
  }
  let recon = receipt ? (receipt.reconciliation_notes || null) : null;
  if (opts.reconNotes !== undefined && opts.reconNotes !== null) recon = opts.reconNotes;
  const payload = {
    branch_id: el('rg-branch').value,
    supplier_name: el('rg-supplier').value.trim() || null,
    invoice_reference: el('rg-ref').value.trim() || null,
    delivered_at: delivered,
    notes: el('rg-notes').value.trim() || null,
    reconciliation_notes: recon,
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
    if (!opts.quiet) {
      let text;
      if (r && r.duplicate) {
        text = 'This draft had already been saved by the same request (reference ' + shortId(r.id) + '). Nothing new was created. Stock was NOT changed.';
      } else {
        text = 'Draft ' + shortId(r.id) + (updating ? ' updated' : ' saved') + ' (status: ' + (r.status || '?') + '). Stock was NOT changed yet. Click Confirm receipt to add it to stock.';
      }
      showNote(text, true);
      showToast(updating ? 'Draft updated. Stock was not changed.' : 'Draft saved. Stock was not changed.');
    }
    renderTable();
    await loadDrafts();
    return true;
  } catch (err) {
    showError(errText(err));
    showToast('The draft was not saved.', true);
    return false;
  } finally {
    saving = false;
    refreshSummary();
  }
}

async function saveDraft() {
  await persistDraft();
}

// ---------- confirm receipt (adds the stock) ----------
async function confirmReceipt() {
  if (saving || posting) return;
  showError('');
  const b = buildLines();
  if (b.errors > 0) { showToast('Fix the rows marked in red first.', true); return; }
  if (!b.lines.length) { showToast('Enter a quantity for at least one product.', true); return; }
  if (b.lines.length > MAX_LINES) { showError('A receipt can have at most ' + MAX_LINES + ' lines. Split this delivery.'); return; }

  let delivered = null;
  const dv = el('rg-delivered').value;
  if (dv) {
    delivered = new Date(dv);
    if (isNaN(delivered.getTime())) { showError('The delivery date and time are not valid.'); return; }
  }
  const options = {};

  // 1) shelf limit: only a manager / super admin can go over it, with a reason
  if (b.warns > 0) {
    if (!canOverride()) {
      await uiAlert(b.warns + ' row(s) put more on the shelf than the shelf limit allows. Lower the To shelf number (the rest goes to the warehouse), or ask a branch manager or super admin to confirm this receipt.', { title: 'Over the shelf limit' });
      return;
    }
    if (!await uiConfirm(b.warns + ' row(s) put more on the shelf than the shelf limit allows. Override the shelf limit for this receipt?', { title: 'Over the shelf limit', okText: 'Override' })) return;
    const why = await uiPrompt('Reason for overriding the shelf limit (required):', '', { title: 'Override reason' });
    if (why === null) return;
    if (!why.trim()) { await uiAlert('A reason is required to override the shelf limit.', { title: 'Reason needed' }); return; }
    options.override_shelf_limit = true;
    options.override_reason = why.trim();
  }

  // 2) late entry: delivery stated as more than an hour ago -> must say what was checked
  let reconNotes = null;
  if (delivered && (Date.now() - delivered.getTime()) > LATE_AFTER_MS) {
    const existing = (receipt && receipt.reconciliation_notes) || '';
    const n = await uiPrompt('The delivery time is more than an hour ago, so this stock may already have been added another way. Write what you checked in existing stock and sales so it is not counted twice:', existing, { title: 'Late entry check' });
    if (n === null) return;
    if (!n.trim()) { await uiAlert('Please write what you checked before confirming a late entry.', { title: 'Notes needed' }); return; }
    reconNotes = n.trim();
    if (!await uiConfirm('I have checked the existing stock and sales, and this delivery has not already been recorded.', { title: 'Late entry', okText: 'Yes, I checked' })) return;
    options.reconciliation_confirmed = true;
  }

  // 3) final summary
  const whPieces = b.received - b.shelf;
  const noCost = b.lines.filter((l) => l.unit_cost_kobo === undefined).length;
  let msg = 'Add ' + b.received + ' piece(s) to stock now?\n\n' + b.shelf + ' to the shelf\n' + whPieces + ' to the warehouse';
  if (noCost > 0) msg += '\n\n' + noCost + ' line(s) have no cost. They stay marked unverified until the supplier invoice is linked.';
  msg += '\n\nOnce confirmed, this receipt can no longer be edited here.';
  if (!await uiConfirm(msg, { title: 'Confirm receipt', okText: 'Confirm receipt' })) return;

  posting = true;
  refreshSummary();
  try {
    // 4) make sure the server holds exactly what is on screen
    const notesChanged = reconNotes !== null && reconNotes !== ((receipt && receipt.reconciliation_notes) || '');
    if (!receipt || dirty || notesChanged) {
      const ok = await persistDraft({ quiet: true, skipWarnPrompt: true, reconNotes: reconNotes });
      if (!ok) return;
    }
    // 5) post (add the stock)
    let result;
    try {
      result = await Api.postGoodsReceipt(receipt.id, options);
    } catch (err) {
      const d = err && err.detail;
      if (d && typeof d === 'object' && d.code === 'RECEIPT_RECONCILIATION_DISCREPANCY') {
        const found = (d.discrepancies || []).map((x) => x.message).join('\n\n');
        if (!canOverride()) {
          await uiAlert(found + '\n\nNothing was posted. Count the stock, then ask a branch manager or super admin to confirm.', { title: 'Possible double entry' });
          return;
        }
        if (!await uiConfirm(found + '\n\nNothing has been posted yet. Confirm anyway?', { title: 'Possible double entry', okText: 'Confirm anyway' })) return;
        const why = await uiPrompt('Reason for confirming despite this (required):', '', { title: 'Reason needed' });
        if (why === null) return;
        if (!why.trim()) { await uiAlert('A reason is required.', { title: 'Reason needed' }); return; }
        options.confirm_discrepancy = true;
        options.discrepancy_reason = why.trim();
        result = await Api.postGoodsReceipt(receipt.id, options);
      } else {
        throw err;
      }
    }
    const done = result || receipt;
    const costWord = done.cost_status === 'verified' ? 'verified' : 'unverified until the supplier invoice is linked';
    resetEntries();
    showNote('Receipt ' + shortId(done.id) + ' confirmed: ' + b.received + ' piece(s) added (' + b.shelf + ' to the shelf, ' + whPieces + ' to the warehouse). Costs are ' + costWord + '. When the supplier invoice arrives, link it from the list below.', true);
    showToast('Receipt confirmed. Stock updated.');
    await loadBranchData();
  } catch (err) {
    showError(errText(err));
    showToast('The receipt was not confirmed.', true);
  } finally {
    posting = false;
    refreshSummary();
  }
}

// ---------- confirmed receipts waiting for a supplier invoice ----------
function productNames() {
  const names = {};
  products.forEach((p) => { names[p.id] = p.name; });
  return names;
}

function renderPending() {
  const box = el('rg-pending-list');
  if (!box) return;
  if (!pending.length) {
    box.innerHTML = '<div style="font-size:13px;color:var(--ink-muted);">Nothing is waiting. Every confirmed receipt has its supplier invoice linked.</div>';
    return;
  }
  const names = productNames();
  box.innerHTML = pending.map((r) => {
    const lines = r.lines || [];
    let pieces = 0;
    lines.forEach((l) => { pieces += Number(l.quantity_received) || 0; });
    const what = lines.slice(0, 3).map((l) => escapeHtml(names[l.product_id] || 'unknown product')).join(', ') + (lines.length > 3 ? ', ...' : '');
    return '<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:8px 12px;border:1px solid var(--border);border-radius:10px;">' +
      '<div style="font-size:13px;"><strong>' + escapeHtml(shortId(r.id)) + '</strong> confirmed ' + escapeHtml(fmtTime(r.posted_at || r.recorded_at)) +
      ' - ' + lines.length + ' line(s), ' + pieces + ' piece(s)' + (r.supplier_name ? ', ' + escapeHtml(r.supplier_name) : '') +
      ' <span class="stock-pill low">cost unverified</span>' +
      '<div style="font-size:12px;color:var(--ink-muted);">' + what + '</div></div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      (canCreateInvoice() ? '<button type="button" class="btn btn-ghost" style="width:auto;" data-create="' + escapeAttr(r.id) + '">Create invoice</button>' : '') +
      '<button type="button" class="btn btn-primary" style="width:auto;" data-link="' + escapeAttr(r.id) + '">Link invoice</button></div></div>';
  }).join('');
}

async function loadPending() {
  const branchId = el('rg-branch').value;
  const seq = ++pendingSeq;
  let failed = '';
  let list = [];
  try {
    list = asList(await Api.listGoodsReceipts({ branch_id: branchId, status: 'posted' }));
  } catch (err) {
    failed = errText(err);
  }
  if (seq !== pendingSeq) return;
  pending = list.filter((r) => r.status === 'posted' && !r.invoice_id);
  const note = el('rg-pending-note');
  if (note) note.textContent = failed ? 'Could not load: ' + failed : (pending.length ? pending.length + ' waiting' : '');
  renderPending();
}

function ensureLinkCss() {
  if (document.getElementById('rgl-css')) return;
  const st = document.createElement('style');
  st.id = 'rgl-css';
  st.textContent = [
    '.rgl-overlay{position:fixed;inset:0;z-index:2147482000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(4,9,20,.68);}',
    '.rgl-card{box-sizing:border-box;width:min(760px,100%);max-height:calc(100vh - 32px);overflow:auto;background:var(--surface,#111b30);border:1px solid var(--border,#1f2b45);border-radius:16px;padding:24px;box-shadow:0 24px 60px rgba(0,0,0,.45);color:var(--ink,#e8edf7);font-family:inherit;}',
    '.rgl-title{margin:0 0 6px;font-size:18px;font-weight:600;}',
    '.rgl-sub{margin:0 0 16px;font-size:13.5px;line-height:1.5;color:var(--ink-muted,#9aa6bd);}',
    '.rgl-label{display:block;margin:0 0 6px;font-size:12.5px;color:var(--ink-muted,#9aa6bd);}',
    '.rgl-select{box-sizing:border-box;width:100%;padding:10px 12px;border:1px solid var(--border,#22304d);border-radius:10px;background:var(--surface,#0c1527);color:var(--ink,#e8edf7);font:inherit;font-size:14px;}',
    '.rgl-table{width:100%;border-collapse:collapse;margin:14px 0;font-size:13.5px;}',
    '.rgl-table th{text-align:left;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--ink-muted,#9aa6bd);padding:6px 8px;}',
    '.rgl-table td{padding:8px;border-top:1px solid var(--border,#1f2b45);vertical-align:middle;}',
    '.rgl-err{display:none;margin:0 0 12px;font-size:13.5px;color:var(--danger,#e5484d);white-space:pre-wrap;}',
    '.rgl-actions{display:flex;gap:12px;margin-top:8px;}',
    '.rgl-actions .btn{flex:1;width:auto;}'
  ].join('\n');
  document.head.appendChild(st);
}

function invoiceLabel(inv) {
  const d = inv.invoice_date ? new Date(inv.invoice_date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric' }) : '';
  const amt = inv.total_landed_cost_naira != null ? inv.total_landed_cost_naira : inv.amount_naira;
  return [d, inv.invoice_number || 'no number', inv.supplier_name || '', amt != null ? 'NGN ' + Number(amt).toLocaleString('en-NG') : ''].filter(Boolean).join(' - ');
}

async function openLinkModal(receiptId, preInvoiceId) {
  if (linkBusy) return;
  const rec = pending.find((r) => r.id === receiptId);
  if (!rec) return;
  linkBusy = true;
  let invoices = [];
  try {
    invoices = asList(await Api.listPurchaseInvoices({ branchId: rec.branch_id, excludeTransfers: true }));
  } catch (err) {
    linkBusy = false;
    await uiAlert(errText(err), { title: 'Could not load the invoices' });
    return;
  }
  linkBusy = false;
  if (!invoices.length) {
    if (canCreateInvoice()) {
      if (await uiConfirm('There is no supplier invoice for this branch yet. Create one now? The products and quantities will be filled in from this receipt, and you only add the prices.', { title: 'No invoice yet', okText: 'Create invoice' })) await goCreateInvoice(rec.id);
    } else {
      await uiAlert('There is no supplier invoice for this branch yet. Ask someone who can record purchase invoices to add it, then come back and link it here.', { title: 'No invoice yet' });
    }
    return;
  }
  invoices.sort((a, b) => String(b.invoice_date || '').localeCompare(String(a.invoice_date || '')));
  ensureLinkCss();
  const names = productNames();
  const lines = rec.lines || [];
  let pieces = 0;
  lines.forEach((l) => { pieces += Number(l.quantity_received) || 0; });

  const overlay = document.createElement('div');
  overlay.className = 'rgl-overlay';
  const card = document.createElement('div');
  card.className = 'rgl-card';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.innerHTML =
    '<h3 class="rgl-title">Link supplier invoice</h3>' +
    '<p class="rgl-sub">Receipt ' + escapeHtml(shortId(rec.id)) + ' - ' + lines.length + ' line(s), ' + pieces + ' piece(s). Linking attaches the invoice and fixes the costs. It does NOT add any stock.</p>' +
    '<label class="rgl-label" for="rgl-invoice">Supplier invoice</label>' +
    '<select id="rgl-invoice" class="rgl-select"><option value="">Choose the invoice...</option>' +
    invoices.map((inv) => '<option value="' + escapeAttr(inv.id) + '">' + escapeHtml(invoiceLabel(inv)) + '</option>').join('') +
    '</select>' +
    (canCreateInvoice() ? '<div style="margin:8px 0 0;"><button type="button" class="btn btn-ghost" id="rgl-new" style="width:auto;">Invoice not recorded yet? Create it now</button></div>' : '') +
    '<div id="rgl-lines"></div>' +
    '<label style="display:flex;align-items:center;gap:8px;font-size:13.5px;margin:6px 0 14px;"><input type="checkbox" id="rgl-verify" checked /> Use the invoice prices as the real cost</label>' +
    '<div id="rgl-err" class="rgl-err"></div>' +
    '<div class="rgl-actions"><button type="button" class="btn btn-ghost" id="rgl-cancel">Cancel</button>' +
    '<button type="button" class="btn btn-primary" id="rgl-ok" disabled>Link invoice</button></div>';
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  const qs = (sel) => card.querySelector(sel);
  const invSel = qs('#rgl-invoice');
  const okBtn = qs('#rgl-ok');
  const errBox = qs('#rgl-err');
  let working = false;
  const prevFocus = document.activeElement;

  function close() {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) { /* ignore */ }
  }
  function onKey(e) { if (e.key === 'Escape' && !working) close(); }
  document.addEventListener('keydown', onKey);
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay && !working) close(); });
  qs('#rgl-cancel').addEventListener('click', () => { if (!working) close(); });
  const newBtn = qs('#rgl-new');
  if (newBtn) newBtn.addEventListener('click', () => { if (working) return; close(); goCreateInvoice(rec.id); });

  function currentInvoice() {
    return invoices.find((i) => String(i.id) === String(invSel.value)) || null;
  }

  function renderLines() {
    errBox.style.display = 'none';
    const inv = currentInvoice();
    okBtn.disabled = !inv;
    const box = qs('#rgl-lines');
    if (!inv) { box.innerHTML = ''; return; }
    const items = inv.items || [];
    box.innerHTML = '<table class="rgl-table"><thead><tr><th>Product received</th><th>Pieces</th><th>Matches this invoice line</th></tr></thead><tbody>' +
      lines.map((l) => {
        const fits = items.filter((it) => !it.product_id || String(it.product_id) === String(l.product_id));
        fits.sort((a, b) => (String(b.product_id) === String(l.product_id) ? 1 : 0) - (String(a.product_id) === String(l.product_id) ? 1 : 0));
        const auto = fits.find((it) => String(it.product_id) === String(l.product_id));
        const opts = '<option value="">Do not link this line</option>' + fits.map((it) =>
          '<option value="' + escapeAttr(it.id) + '"' + (auto && auto.id === it.id ? ' selected' : '') + '>' +
          escapeHtml((it.item_name || 'item') + (it.quantity != null ? ' (' + it.quantity + ' ' + (it.unit_label || '') + ')' : '')) + '</option>').join('');
        return '<tr><td>' + escapeHtml(names[l.product_id] || 'unknown product') + '</td><td>' + (Number(l.quantity_received) || 0) +
          '</td><td><select class="rgl-select" data-line="' + escapeAttr(l.id) + '">' + opts + '</select></td></tr>';
      }).join('') + '</tbody></table>';
  }
  invSel.addEventListener('change', renderLines);

  okBtn.addEventListener('click', async () => {
    if (working) return;
    const inv = currentInvoice();
    if (!inv) return;
    const links = {};
    let n = 0;
    card.querySelectorAll('select[data-line]').forEach((sel) => {
      if (sel.value) { links[sel.getAttribute('data-line')] = sel.value; n++; }
    });
    if (!n && !await uiConfirm('No receipt line is matched to an invoice line. The invoice will be attached, but the costs stay unverified. Continue?', { title: 'Nothing matched', okText: 'Continue' })) return;
    working = true;
    okBtn.disabled = true;
    okBtn.textContent = 'Linking...';
    errBox.style.display = 'none';
    try {
      const res = await Api.linkGoodsReceiptInvoice(rec.id, {
        invoice_id: inv.id,
        verify_costs: !!qs('#rgl-verify').checked,
        line_links: n ? links : null
      });
      const rl = (res && res.lines) || [];
      const verified = rl.filter((l) => l.cost_verified).length;
      close();
      showNote('Invoice linked to receipt ' + shortId(rec.id) + '. ' + verified + ' of ' + rl.length + ' line(s) now have a verified cost. No stock was changed.', true);
      showToast('Invoice linked. Stock was not changed.');
      await loadPending();
    } catch (err) {
      working = false;
      okBtn.disabled = false;
      okBtn.textContent = 'Link invoice';
      errBox.textContent = errText(err);
      errBox.style.display = 'block';
    }
  });
  if (preInvoiceId && invoices.some((i) => String(i.id) === String(preInvoiceId))) {
    invSel.value = String(preInvoiceId);
    renderLines();
  }
  invSel.focus();
}

// ---------- count by pack (cartons, dozens, bags, rolls, loose pieces) ----------
const PACK_ROWS = [
  { key: 'carton', label: 'Cartons', word: 'carton', per: true },
  { key: 'dozen', label: 'Dozens', word: 'dozen', fixed: 12 },
  { key: 'bag', label: 'Bags', word: 'bag', per: true },
  { key: 'roll', label: 'Rolls', word: 'roll', per: true }
];

// whole numbers, or a whole number plus a half (2.5 cartons)
function parseCount(v) {
  if (v === '' || v === undefined || v === null) return 0;
  return /^\d+(\.5)?$/.test(String(v)) ? parseFloat(v) : null;
}

function packSummary(pk) {
  if (!pk) return '';
  const parts = [];
  PACK_ROWS.forEach((r) => {
    const x = pk[r.key];
    const n = x ? parseCount(x.n) : 0;
    if (n && n > 0) parts.push(x.n + ' ' + r.word + (n === 1 ? '' : 's') + (r.per && x.per ? ' x ' + x.per : ''));
  });
  if (pk.loose && Number(pk.loose) > 0) parts.push(pk.loose + ' loose');
  return parts.length ? 'From ' + parts.join(' + ') : '';
}

function applyPack(id, total, pack) {
  const inp = el('rg-tbody').querySelector('input[data-pid="' + id + '"][data-field="received"]');
  if (!inp) return;
  pendingPack = { id: id, pack: pack };
  inp.value = String(total);
  inp.dispatchEvent(new Event('input', { bubbles: true }));
  pendingPack = null;
}

function openPackModal(id) {
  const p = products.find((x) => x.id === id);
  if (!p) return;
  const prev = (entries[id] && entries[id].pack) || {};
  const defs = { carton: p.default_pieces_per_carton, bag: p.default_pieces_per_bag, roll: p.default_pieces_per_roll };
  ensureLinkCss();
  const overlay = document.createElement('div');
  overlay.className = 'rgl-overlay';
  const card = document.createElement('div');
  card.className = 'rgl-card';
  card.style.width = 'min(560px,100%)';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  const grid = 'display:grid;grid-template-columns:90px 1fr 18px 1fr 90px;gap:8px;align-items:center;margin-bottom:10px;';
  let rowsHtml = '';
  PACK_ROWS.forEach((r) => {
    const pv = prev[r.key] || {};
    const perVal = r.fixed ? String(r.fixed) : (pv.per !== undefined && pv.per !== '' ? pv.per : (defs[r.key] != null ? String(defs[r.key]) : ''));
    rowsHtml += '<div style="' + grid + '"><span>' + r.label + '</span>' +
      '<input class="rgl-select" inputmode="decimal" autocomplete="off" data-k="' + r.key + '" data-f="n" value="' + escapeAttr(pv.n || '') + '" placeholder="0" />' +
      '<span style="text-align:center;">x</span>' +
      (r.fixed
        ? '<input class="rgl-select" value="12" readonly tabindex="-1" style="opacity:.7;" />'
        : '<input class="rgl-select" inputmode="numeric" autocomplete="off" data-k="' + r.key + '" data-f="per" value="' + escapeAttr(perVal) + '" placeholder="pieces per ' + r.word + '" />') +
      '<span data-res="' + r.key + '" style="font-size:13px;color:var(--ink-muted);">0 pcs</span></div>';
  });
  rowsHtml += '<div style="' + grid + '"><span>Loose pieces</span>' +
    '<input class="rgl-select" inputmode="numeric" autocomplete="off" data-k="loose" data-f="n" value="' + escapeAttr(prev.loose || '') + '" placeholder="0" />' +
    '<span></span><span></span><span data-res="loose" style="font-size:13px;color:var(--ink-muted);">0 pcs</span></div>';
  card.innerHTML =
    '<h3 class="rgl-title">Count by pack</h3>' +
    '<p class="rgl-sub">' + escapeHtml(p.name || '') + '. Fill in what arrived. You can mix, for example 5 cartons and 3 loose pieces. Half a carton or dozen is allowed (2.5).</p>' +
    rowsHtml +
    '<div style="font-size:15px;margin:6px 0 12px;"><strong>Total: <span id="rgp-total">0</span> piece(s)</strong></div>' +
    '<div id="rgp-err" class="rgl-err"></div>' +
    '<div class="rgl-actions"><button type="button" class="btn btn-ghost" id="rgp-cancel">Cancel</button>' +
    '<button type="button" class="btn btn-primary" id="rgp-ok" disabled>Use this total</button></div>';
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  const qa = (sel) => card.querySelector(sel);
  const okBtn = qa('#rgp-ok');
  const errBox = qa('#rgp-err');
  const prevFocus = document.activeElement;
  let result = null;

  function val(k, f) {
    const i = card.querySelector('input[data-k="' + k + '"][data-f="' + f + '"]');
    return i ? i.value.trim() : '';
  }
  function compute() {
    let total = 0;
    let problem = '';
    const pack = {};
    PACK_ROWS.forEach((r) => {
      const nText = val(r.key, 'n');
      const perText = r.fixed ? String(r.fixed) : val(r.key, 'per');
      pack[r.key] = r.fixed ? { n: nText } : { n: nText, per: perText };
      const n = parseCount(nText);
      let pcs = 0;
      if (n === null) {
        problem = problem || (r.label + ': use a whole number, or a half like 2.5.');
      } else if (n > 0) {
        const per = parseWhole(perText);
        if (per === null || per < 1) problem = problem || ('Enter the pieces per ' + r.word + '.');
        else {
          pcs = n * per;
          if (!Number.isInteger(pcs)) { problem = problem || (r.label + ': that is not a whole number of pieces. Use a pieces-per-' + r.word + ' that splits evenly.'); pcs = 0; }
        }
      }
      qa('[data-res="' + r.key + '"]').textContent = pcs + ' pcs';
      total += pcs;
    });
    const looseText = val('loose', 'n');
    pack.loose = looseText;
    let loose = 0;
    if (looseText !== '') {
      loose = parseWhole(looseText);
      if (loose === null) { problem = problem || 'Loose pieces must be a whole number.'; loose = 0; }
    }
    qa('[data-res="loose"]').textContent = loose + ' pcs';
    total += loose;
    if (total > MAX_QTY) problem = problem || 'That total is too large.';
    qa('#rgp-total').textContent = String(total);
    errBox.textContent = problem;
    errBox.style.display = problem ? 'block' : 'none';
    okBtn.disabled = !!problem || total < 1;
    result = { total: total, pack: pack };
  }

  function close() {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) { /* ignore */ }
  }
  function use() {
    if (okBtn.disabled || !result) return;
    const out = { total: result.total, pack: result.pack };
    close();
    applyPack(id, out.total, out.pack);
  }
  function onKey(e) {
    if (e.key === 'Escape') close();
    else if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT') { e.preventDefault(); use(); }
  }
  document.addEventListener('keydown', onKey);
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
  qa('#rgp-cancel').addEventListener('click', close);
  okBtn.addEventListener('click', use);
  card.addEventListener('input', compute);
  compute();
  const first = card.querySelector('input[data-k="carton"][data-f="n"]');
  if (first) first.focus();
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
  const sub = document.querySelector('.page-head p');
  if (sub) sub.textContent = 'Type the quantities received for each product. Total = Warehouse + Shelf. Save a draft to finish later (stock does not change), or click Confirm receipt to add the stock now. When the supplier invoice arrives, link it to the confirmed receipt in the list below.';
  const cbtn = document.createElement('button');
  cbtn.type = 'button';
  cbtn.id = 'rg-confirm';
  cbtn.className = 'btn btn-primary';
  cbtn.style.width = 'auto';
  cbtn.textContent = 'Confirm receipt';
  cbtn.disabled = true;
  el('rg-save').insertAdjacentElement('afterend', cbtn);
  cbtn.addEventListener('click', confirmReceipt);
  const pcard = document.createElement('div');
  pcard.className = 'table-card';
  pcard.id = 'rg-pending-card';
  pcard.innerHTML = '<div style="padding:16px;"><div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;"><strong>Confirmed receipts waiting for a supplier invoice</strong><span id="rg-pending-note" style="font-size:12.5px;color:var(--ink-muted);"></span></div><div id="rg-pending-list" style="margin-top:10px;display:flex;flex-direction:column;gap:8px;"></div></div>';
  el('rg-drafts-card').insertAdjacentElement('afterend', pcard);
  el('rg-pending-list').addEventListener('click', (e) => {
    const c = e.target && e.target.closest ? e.target.closest('[data-create]') : null;
    if (c) { goCreateInvoice(c.getAttribute('data-create')); return; }
    const t = e.target && e.target.closest ? e.target.closest('[data-link]') : null;
    if (t) openLinkModal(t.getAttribute('data-link'));
  });
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
  el('rg-discard').addEventListener('click', discardChanges);
  HEADER_IDS.forEach((id) => { el(id).addEventListener('input', markDirty); });
  el('rg-drafts-list').addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest ? ev.target.closest('[data-open]') : null;
    if (btn) openDraft(btn.dataset.open);
  });
  el('rg-tbody').addEventListener('click', (ev) => {
    const b = ev.target && ev.target.closest ? ev.target.closest('[data-pack]') : null;
    if (b) openPackModal(b.getAttribute('data-pack'));
  });
  const ths = document.querySelectorAll('#rg-table thead th');
  if (ths[6]) ths[6].textContent = 'Received (pieces)';
  el('rg-tbody').addEventListener('input', (ev) => {
    const t = ev.target;
    if (!t || !t.dataset || !t.dataset.pid) return;
    const id = t.dataset.pid;
    const cur = Object.assign({ received: '', shelf: '', cost: '' }, entries[id] || {});
    cur[t.dataset.field] = t.value.trim();
    if (t.dataset.field === 'received') {
      if (pendingPack && pendingPack.id === id) cur.pack = pendingPack.pack; else delete cur.pack;
    }
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
  const back = new URLSearchParams(window.location.search);
  const linkId = back.get('link_receipt');
  if (linkId) {
    try { window.history.replaceState(null, '', window.location.pathname); } catch (e) { /* cosmetic */ }
    if (pending.some((r) => r.id === linkId)) openLinkModal(linkId, back.get('invoice'));
  }
}

init();