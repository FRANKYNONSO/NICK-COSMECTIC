Auth.requireAuth();
Perms.applyNav();
// Platform update spec section 5: staff have no Invoices access at
// all now (migration dd11ee22ff33 revoked the default grant a prior
// spec pass had given them) -- belt-and-braces redirect for a typed
// URL/stale bookmark, on top of the nav link already being hidden.
Perms.guardPage('view_purchase_invoices');
const currentUser = Auth.getUser();
// Branch managers/super admins get view_purchase_invoices by default
// but not manage_suppliers -- spec section 20/21: they can view
// invoice history/details but cannot add, edit, delete, or manage
// suppliers. The only mutating control on this page is the "Add
// purchase invoice" button, so that's the only thing gated here.
if (!Perms.can('manage_suppliers')) {
  document.getElementById('add-invoice-btn').style.display = 'none';
}
// Section 16: "All Branches" is view-only for a Super Admin -- adding
// an invoice needs one specific branch selected first, same reasoning
// as POS/Credit above. Reacts live since the branch picker doesn't
// require a page reload.
function updateAddInvoiceVisibility() {
  if (!Perms.can('manage_suppliers')) return; // stays hidden regardless
  document.getElementById('add-invoice-btn').style.display = BranchContext.get() ? '' : 'none';
}
updateAddInvoiceVisibility();
window.addEventListener('branch-context-changed', updateAddInvoiceVisibility);
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier', store_assistant: 'Store assistant' }; // PATCHP3C-ROLE-LABEL
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
function formatNaira(amount) {
  return `₦${Number(amount || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Sept 2026 fix: money input fields (Rate, Total, Amount, Amount
// Paid, Waybill/Transit/Handling cost) previously showed raw digits
// with no thousands separator while typing -- type="number" inputs
// can't display commas at all, so these are text inputs formatted
// live via wireMoneyInput below. parseMoneyInput strips the commas
// back out for every calculation/payload read.
function parseMoneyInput(value) {
  return Number(String(value == null ? '' : value).replace(/,/g, '')) || 0;
}
function formatMoneyInputValue(raw) {
  const str = String(raw == null ? '' : raw);
  const dotIndex = str.indexOf('.');
  const intPart = dotIndex === -1 ? str : str.slice(0, dotIndex);
  const decPart = dotIndex === -1 ? null : str.slice(dotIndex + 1);
  const cleanInt = intPart.replace(/^0+(?=\d)/, '');
  const formattedInt = cleanInt ? Number(cleanInt).toLocaleString('en-NG') : (decPart !== null ? '0' : '');
  return decPart !== null ? `${formattedInt}.${decPart}` : formattedInt;
}
function wireMoneyInput(input) {
  if (!input) return;
  input.setAttribute('inputmode', 'decimal');
  input.addEventListener('input', () => {
    const before = input.value;
    const cursorFromEnd = before.length - (input.selectionStart ?? before.length);
    let raw = before.replace(/[^\d.]/g, '');
    const firstDot = raw.indexOf('.');
    if (firstDot !== -1) {
      raw = raw.slice(0, firstDot + 1) + raw.slice(firstDot + 1).replace(/\./g, '');
    }
    input.value = raw === '' ? '' : formatMoneyInputValue(raw);
    const newPos = Math.max(0, input.value.length - cursorFromEnd);
    input.setSelectionRange(newPos, newPos);
  });
}
function formatDateTime(iso) {
  return new Date(iso).toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short', hour12: true, timeZone: 'Africa/Lagos' });
}

async function loadBranches() {
  // Branch is no longer a client-editable dropdown here (spec section
  // 13): it's always the active dashboard branch (or the signed-in
  // user's own fixed branch), shown as a read-only badge. We still
  // need the branches list once, to resolve BranchContext's id to a
  // human-readable name for that badge.
  let branches = [];
  try {
    branches = await Api.listBranches();
  } catch (err) { /* non-fatal */ }
  BranchContext.renderBadge('branch-badge');
  const activeBranchId = BranchContext.get() || currentUser?.branch_id || '';
  const activeBranch = branches.find((b) => b.id === activeBranchId);
  document.getElementById('invoice-branch-badge').textContent = activeBranch ? activeBranch.name : 'Store-wide';
}

function populateYearFilter() {
  const yearSelect = document.getElementById('year-filter');
  const thisYear = new Date().getFullYear();
  let opts = '<option value="">All years</option>';
  for (let y = thisYear; y >= thisYear - 5; y -= 1) opts += `<option value="${y}">${y}</option>`;
  yearSelect.innerHTML = opts;
}

let supplierFilterCache = [];
async function populateSupplierFilter() {
  const select = document.getElementById('supplier-filter');
  try {
    supplierFilterCache = await Api.listSuppliers();
    const current = select.value;
    select.innerHTML = '<option value="">All suppliers</option>' +
      supplierFilterCache.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
    select.value = current;
  } catch (err) { /* non-fatal -- filter still works without supplier options */ }
}

document.getElementById('filter-btn').addEventListener('click', loadPurchaseInvoices);
document.getElementById('month-filter').addEventListener('change', loadPurchaseInvoices);
document.getElementById('year-filter').addEventListener('change', loadPurchaseInvoices);
document.getElementById('supplier-filter').addEventListener('change', loadPurchaseInvoices);
window.addEventListener('branch-context-changed', loadPurchaseInvoices);

// ---------------------------------------------------------------- Purchase invoices
// Not a duplicate of Sales & Receipts (that's the customer-facing
// money coming IN) -- this is a super admin/branch manager's record
// of what was bought FROM a supplier, typed in and/or backed by a
// photo of the paper invoice, filterable by month/year.

let purchaseInvoiceCache = [];

const PAYMENT_METHOD_LABELS = {
  // Spec section 16: trimmed to three options. Older entries (cash/
  // pos/cheque/credit) are kept here only so historical invoices
  // filed under a since-removed option still display a readable
  // label instead of the raw code.
  not_specified: 'Not specified',
  bank_transfer: 'Bank transfer',
  on_credit: 'On credit',
  cash: 'Cash',
  pos: 'POS/Card',
  cheque: 'Cheque',
  credit: 'On credit',
};

const ADJUSTMENT_STATUS_LABELS = {
  pending: 'Pending with supplier',
  balanced: 'Balanced',
  replaced: 'Replaced',
  refunded: 'Refunded',
};

// Total of the Amount column for the invoices now showing (follows the filters).
function updateInvoiceTotal(invoices) {
  const total = invoices.reduce((sum, inv) => sum + Number(inv.total_landed_cost_naira ?? inv.amount_naira ?? 0), 0);
  const amountEl = document.getElementById('invoice-total-amount');
  const countEl = document.getElementById('invoice-total-count');
  if (amountEl) amountEl.textContent = formatNaira(total);
  if (countEl) countEl.textContent = `${invoices.length} invoice${invoices.length === 1 ? '' : 's'}`;
}

async function loadPurchaseInvoices() {
  const tbody = document.getElementById('purchase-invoices-tbody');
  const table = document.getElementById('purchase-invoices-table');
  const emptyState = document.getElementById('purchase-invoices-empty');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="8">Loading…</td></tr>';
  try {
    purchaseInvoiceCache = await Api.listPurchaseInvoices({
      branchId: BranchContext.get(),
      month: document.getElementById('month-filter').value || undefined,
      year: document.getElementById('year-filter').value || undefined,
      supplierId: document.getElementById('supplier-filter').value || undefined,
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }
  if (!purchaseInvoiceCache.length) {
    updateInvoiceTotal([]);
    table.style.display = 'none';
    emptyState.style.display = 'block';
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';
  tbody.innerHTML = purchaseInvoiceCache.map((inv) => `
    <tr>
      <td>${new Date(inv.invoice_date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}</td>
      <td>${inv.invoice_number ? escapeHtml(inv.invoice_number) : '<span style="color:var(--ink-faint)">—</span>'}</td>
      <td><strong>${escapeHtml(inv.supplier_name)}</strong></td>
      <td>${escapeHtml(inv.branch_name || 'Store-wide')}</td>
      <td>${(inv.total_landed_cost_naira ?? inv.amount_naira) != null ? formatNaira(inv.total_landed_cost_naira ?? inv.amount_naira) : '—'}</td>
      <td style="max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(inv.description || '—')}</td>
      <td>${escapeHtml(inv.created_by_name)}</td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-invoice-detail="${inv.id}">View invoice</button></td>
    </tr>`).join('');
  updateInvoiceTotal(purchaseInvoiceCache);

  tbody.querySelectorAll('[data-invoice-detail]').forEach((btn) => {
    btn.addEventListener('click', () => openInvoiceDetail(btn.dataset.invoiceDetail));
  });
}

const invoiceDetailBackdrop = document.getElementById('invoice-detail-backdrop');

// Nigerian-invoice convention: the total is written out in words too
// ("Naira: ... / In words: ..."), as a cross-check against the
// figures -- see the reference photos of physical invoice booklets.
const ONES = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
  'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];
function threeDigitsToWords(n) {
  let out = '';
  if (n >= 100) {
    out += `${ONES[Math.floor(n / 100)]} hundred`;
    n %= 100;
    if (n) out += ' and ';
  }
  if (n >= 20) {
    out += TENS[Math.floor(n / 10)];
    if (n % 10) out += `-${ONES[n % 10]}`;
  } else if (n > 0) {
    out += ONES[n];
  }
  return out;
}
function nairaToWords(amount) {
  const naira = Math.floor(amount);
  const kobo = Math.round((amount - naira) * 100);
  if (naira === 0 && kobo === 0) return 'Zero naira only';
  const scales = [[1e9, 'billion'], [1e6, 'million'], [1e3, 'thousand'], [1, '']];
  let remaining = naira;
  const parts = [];
  for (const [scale, label] of scales) {
    if (remaining >= scale) {
      const chunk = Math.floor(remaining / scale);
      remaining %= scale;
      parts.push(label ? `${threeDigitsToWords(chunk)} ${label}` : threeDigitsToWords(chunk));
    }
  }
  let words = parts.join(', ').replace(/,([^,]*)$/, ' and$1');
  words = `${words.charAt(0).toUpperCase()}${words.slice(1)} naira`;
  if (kobo > 0) words += ` and ${threeDigitsToWords(kobo)} kobo`;
  return `${words} only`;
}

let editingInvoiceId = null;
let editingInvoiceIdPending = null;

function openInvoiceDetail(invoiceId) {
  // TRASH3: also finds an invoice that is in the Trash / Review list (see trashInvoiceLookup).
  const inv = purchaseInvoiceCache.find((row) => row.id === invoiceId)
    || (typeof trashInvoiceLookup !== 'undefined' ? trashInvoiceLookup[invoiceId] : undefined);
  if (!inv) return;
  document.getElementById('invoice-detail-readout').textContent =
    `${inv.supplier_name} — ${new Date(inv.invoice_date).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' })}${inv.amount_naira != null ? ` — ${formatNaira(inv.amount_naira)}` : ''} — filed by ${inv.created_by_name}. ${inv.description || ''}`;

  const letterheadBits = [];
  if (inv.invoice_number) letterheadBits.push(`Invoice/Receipt No: ${escapeHtml(inv.invoice_number)}`);
  if (inv.supplier_address) letterheadBits.push(`Address: ${escapeHtml(inv.supplier_address)}`);
  if (inv.supplier_phone) letterheadBits.push(`Phone: ${escapeHtml(inv.supplier_phone)}`);
  if (inv.payment_method) letterheadBits.push(`Paid via: ${escapeHtml(PAYMENT_METHOD_LABELS[inv.payment_method] || inv.payment_method)}`);
  if (inv.amount_naira != null) letterheadBits.push(`Items total: ${formatNaira(inv.amount_naira)}`);
  if (inv.amount_paid_naira != null) letterheadBits.push(`Paid so far: ${formatNaira(inv.amount_paid_naira)} — Balance: ${formatNaira(inv.balance_naira ?? 0)}`);
  if (inv.waybill_cost_naira != null || inv.transit_cost_naira != null || inv.handling_cost_naira != null) {
    letterheadBits.push(
      `Shop→park: ${formatNaira(inv.waybill_cost_naira ?? 0)} · Waybill: ${formatNaira(inv.transit_cost_naira ?? 0)} · Park→shop: ${formatNaira(inv.handling_cost_naira ?? 0)} · Total landed cost: ${formatNaira(inv.total_landed_cost_naira ?? 0)}`
    );
  }
  if (inv.adjustment_amount_naira != null || inv.adjustment_status) {
    letterheadBits.push(
      `Check &amp; balance: ${formatNaira(inv.adjustment_amount_naira ?? 0)}${inv.adjustment_status ? ` — ${escapeHtml(ADJUSTMENT_STATUS_LABELS[inv.adjustment_status] || inv.adjustment_status)}` : ''}${inv.adjustment_note ? ` — ${escapeHtml(inv.adjustment_note)}` : ''}`
    );
  }
  if (inv.source === 'branch_transfer') letterheadBits.unshift('This invoice was made automatically by a branch transfer. It cannot be edited.');
  document.getElementById('invoice-detail-letterhead').innerHTML = letterheadBits.length
    ? letterheadBits.map((b) => `<div>${b}</div>`).join('')
    : '';

  document.getElementById('invoice-detail-words').textContent =
    inv.amount_naira != null ? `In words: ${nairaToWords(inv.amount_naira)}` : '';

  const itemsCard = document.getElementById('invoice-detail-items-card');
  const itemsTbody = document.getElementById('invoice-detail-items-tbody');
  if (inv.items && inv.items.length) {
    itemsTbody.innerHTML = inv.items.map((it, i) => `
      <tr>
        <td>${i + 1}</td>
        <td>${it.quantity} ${escapeHtml(it.unit_label || 'pieces')}</td>
        <td>${escapeHtml(it.item_name)}</td>
        <td>${it.rate_naira != null ? formatNaira(it.rate_naira) : '—'}</td>
        <td>${it.line_total_naira != null ? formatNaira(it.line_total_naira) : '—'}</td>
      </tr>`).join('');
    itemsCard.style.display = '';
  } else {
    itemsTbody.innerHTML = '';
    itemsCard.style.display = 'none';
  }

  const photo = document.getElementById('invoice-detail-photo');
  const video = document.getElementById('invoice-detail-video');
  const isVideo = inv.image_url && /\.(mp4|mov|webm)$/i.test(inv.image_url);
  if (inv.image_url && isVideo) {
    video.src = inv.image_url;
    video.style.display = '';
    photo.style.display = 'none';
  } else if (inv.image_url) {
    photo.src = inv.image_url;
    photo.dataset.enlargeImage = inv.image_url;
    photo.style.display = '';
    video.style.display = 'none';
    video.src = '';
  } else {
    photo.style.display = 'none';
    video.style.display = 'none';
    video.src = '';
  }
  const editBtn = document.getElementById('invoice-detail-edit');
  if (editBtn.dataset.shownDisplay === undefined) editBtn.dataset.shownDisplay = editBtn.style.display;
  // TRASH3: an invoice opened from the Trash / Review tab is view-only: no Edit, no Delete, and a note saying where it is.
  const viewingTrashed = !!inv.__trash;
  editBtn.style.display = (inv.source === 'branch_transfer' || viewingTrashed) ? 'none' : editBtn.dataset.shownDisplay;
  // TRASH2: Delete is for people who can manage invoices; a branch-transfer invoice is paperwork and cannot be deleted.
  const deleteBtn = document.getElementById('invoice-detail-delete');
  if (deleteBtn) deleteBtn.style.display = (Perms.can('manage_suppliers') && inv.source !== 'branch_transfer' && !viewingTrashed) ? '' : 'none';
  if (viewingTrashed) document.getElementById('invoice-detail-letterhead').insertAdjacentHTML('afterbegin', trashBanner(inv));
  editingInvoiceIdPending = invoiceId;
  invoiceDetailBackdrop.classList.add('visible');
}
document.getElementById('invoice-detail-close').addEventListener('click', () => invoiceDetailBackdrop.classList.remove('visible'));
invoiceDetailBackdrop.addEventListener('click', (e) => { if (e.target === invoiceDetailBackdrop) invoiceDetailBackdrop.classList.remove('visible'); });

// Edit: close the read-only detail view, reopen the same "Add
// purchase invoice" form pre-filled with this invoice's current
// values -- flipped into edit mode so Save calls PATCH instead of
// POST (spec: invoices should be editable after filing, not locked).
document.getElementById('invoice-detail-edit').addEventListener('click', () => {
  const inv = purchaseInvoiceCache.find((row) => row.id === editingInvoiceIdPending);
  if (!inv) return;
  invoiceDetailBackdrop.classList.remove('visible');
  openInvoiceEditForm(inv);
});

function openInvoiceEditForm(inv) {
  editingInvoiceId = inv.id;
  document.getElementById('invoice-add-form').reset();
  document.getElementById('invoice-branch-badge').textContent = inv.branch_name || '';
  document.getElementById('invoice-supplier').value = inv.supplier_name || '';
  document.getElementById('invoice-date').value = inv.invoice_date || '';
  supplierAddressInput.value = inv.supplier_address || '';
  supplierPhoneInput.value = inv.supplier_phone || '';
  document.getElementById('invoice-payment-method').value = inv.payment_method || 'not_specified';
  document.getElementById('invoice-amount').value = inv.amount_naira != null ? formatMoneyInputValue(String(inv.amount_naira)) : '';
  document.getElementById('invoice-amount-paid').value = inv.amount_paid_naira != null ? formatMoneyInputValue(String(inv.amount_paid_naira)) : '';
  document.getElementById('invoice-waybill-cost').value = inv.waybill_cost_naira != null ? formatMoneyInputValue(String(inv.waybill_cost_naira)) : '';
  document.getElementById('invoice-transit-cost').value = inv.transit_cost_naira != null ? formatMoneyInputValue(String(inv.transit_cost_naira)) : '';
  document.getElementById('invoice-handling-cost').value = inv.handling_cost_naira != null ? formatMoneyInputValue(String(inv.handling_cost_naira)) : '';
  uploadedInvoiceImageUrl = inv.image_url || null;
  recomputeInvoiceBalance();
  recomputeLandedCost();

  const tbody = document.getElementById('invoice-items-tbody');
  tbody.innerHTML = '';
  removedExistingItemIds = [];
  (inv.items || []).forEach((it) => {
    addInvoiceItemRow();
    const tr = tbody.lastElementChild;
    tr.querySelector('.item-qty').value = it.quantity;
    tr.querySelector('.item-name').value = it.item_name;
    tr.querySelector('.item-rate').value = it.rate_naira != null ? formatMoneyInputValue(String(it.rate_naira)) : '';
    // Sept 2026 rate/total override: a line saved with no rate was a
    // total-only entry -- load its total in and flag it as manually
    // edited so recomputeRowTotal doesn't overwrite it with 0.
    tr.querySelector('.item-total').value = it.line_total_naira != null ? formatMoneyInputValue(String(it.line_total_naira)) : '';
    if (it.rate_naira == null && it.line_total_naira != null) {
      tr.dataset.totalEdited = '1';
    }
    // September 2026 change -- prefill the cost-per-piece calculator
    // fields. Unit is set and its change event fired FIRST so the
    // pieces-per-carton/remainder controls (and remainder unit
    // options) are correct for this line's actual unit before the
    // remainder value itself is applied.
    if (it.unit_label) tr.querySelector('.item-unit').value = it.unit_label;
    tr.querySelector('.item-pieces-per-carton').value = it.pieces_per_carton ?? '';
    tr.querySelector('.item-remainder-qty').value = it.remainder_quantity ?? '';
    if (it.remainder_unit_label) tr.querySelector('.item-remainder-unit').value = it.remainder_unit_label;
    updateQtySummaryButton(tr);
    tr.dataset.existing = '1';
    // Sept 2026 fix: quantity/name/rate on an already-saved line can
    // now actually be corrected (see update_items on the PATCH
    // endpoint) -- these used to be permanently disabled here.
    tr.dataset.itemId = it.id;
    recomputeRowTotal(tr);
  });
  recomputeInvoiceItemsSubtotal();

  document.getElementById('invoice-add-form').closest('.modal').querySelector('h3').textContent = 'Edit purchase invoice';
  document.getElementById('invoice-add-submit').textContent = 'Save changes';
  invoiceAddBackdrop.classList.add('visible');
}

// Click-to-enlarge lightbox for the invoice photo -- same shared
// pattern as Inventory/Low Stock.
const imageLightboxBackdrop = document.getElementById('image-lightbox-backdrop');
const imageLightboxImg = document.getElementById('image-lightbox-img');
function openImageLightbox(url) {
  imageLightboxImg.src = url;
  imageLightboxBackdrop.classList.add('visible');
}
document.getElementById('image-lightbox-close')?.addEventListener('click', () => {
  imageLightboxBackdrop.classList.remove('visible');
  imageLightboxImg.src = '';
});
imageLightboxBackdrop?.addEventListener('click', (e) => {
  if (e.target === imageLightboxBackdrop) { imageLightboxBackdrop.classList.remove('visible'); imageLightboxImg.src = ''; }
});
document.getElementById('invoice-detail-photo')?.addEventListener('click', (e) => {
  if (e.target.dataset.enlargeImage) openImageLightbox(e.target.dataset.enlargeImage);
});

// ---------------------------------------------------------------- Add purchase invoice

const invoiceAddBackdrop = document.getElementById('invoice-add-backdrop');
const invoiceAddError = document.getElementById('invoice-add-error');
let uploadedInvoiceImageUrl = null;

// -------------------------------------------------------- Item rows
// "The table should keep giving you a drop-down menu to continue
// filling until you get to the total balance" -- an unlimited,
// keep-adding-rows table of S/N | Quantity (unit + count) | Item
// (catalog autocomplete) | Rate | Total, matching the Nigerian-market
// carton/dozen/piece purchasing pattern (spec sections 3/5/6). Total is
// always quantity x rate, computed live here and re-verified
// server-side -- never a free-typed figure.
const invoiceItemsTbody = document.getElementById('invoice-items-tbody');
let invoiceItemRowSeq = 0;
const canManageProducts = Perms.can('manage_products');

// True once the person has actually typed into the Total amount field
// themselves -- distinguishes that from us auto-filling it from the
// items subtotal below, so a manual figure is never silently
// overwritten as more rows are filled in.
let invoiceAmountManuallyEdited = false;

// Sept 2026 fix: ids of already-saved line items removed during this
// edit session, sent as remove_item_ids on save. Reset whenever the
// Edit Invoice form is (re)populated -- see below.
let removedExistingItemIds = [];

function renumberInvoiceItemRows() {
  invoiceItemsTbody.querySelectorAll('tr').forEach((tr, i) => {
    tr.querySelector('.row-sn').textContent = String(i + 1);
  });
}

const DOZEN_PIECES = 12;
function unitToPieces(unitLabel, count, piecesPerCarton) {
  // Half-pack labels have no quantity box -- count is ignored, mirrors
  // the backend's _unit_pieces (api\v1\routers\purchase_invoices.py).
  if (unitLabel === 'half_dozen') return Math.floor(DOZEN_PIECES / 2);
  if (unitLabel === 'half_carton') return piecesPerCarton ? Math.floor(piecesPerCarton / 2) : null;
  if (!count) return 0;
  if (unitLabel === 'cartons') return piecesPerCarton ? count * piecesPerCarton : null;
  // Oct 2026: bags behave like cartons -- size varies per product, so the
  // pieces-per-bag figure is entered at the warehouse stage.
  if (unitLabel === 'bags') return piecesPerCarton ? count * piecesPerCarton : null;
  if (unitLabel === 'rolls') return piecesPerCarton ? count * piecesPerCarton : null; // PATCH5B-INVOICE-PACKS
  if (unitLabel === 'dozens') return count * DOZEN_PIECES;
  if (unitLabel === 'pieces') return count;
  return null;
}
function recomputeRowTotal(tr) {
  const qty = Number(tr.querySelector('.item-qty').value || 0);
  const rate = parseMoneyInput(tr.querySelector('.item-rate').value);
  const total = qty * rate;
  // A manually-typed total (for invoices with no per-unit rate) wins
  // over the calculated figure; clearing the total field goes back
  // to trusting quantity x rate. Same override pattern as
  // warehouse.js's quantity_pieces_override.
  if (tr.dataset.totalEdited !== '1') {
    tr.querySelector('.item-total').value = formatMoneyInputValue(total.toFixed(2));
  }

  // September 2026 change -- live cost-per-piece readout as the
  // quantity/unit/pieces-per-carton/remainder inputs are filled in.
  // Sept 2026 change -- the per-row cost-per-piece / total-pieces
  // readout has been removed from the invoice screen entirely; that
  // conversion belongs at the warehouse stage. The backend still
  // computes and stores total_pieces independently.
  const readout = tr.querySelector('.item-cost-per-piece-readout');
  readout.textContent = '';

  recomputeInvoiceItemsSubtotal();
}

// Live running subtotal across every item row, updated as soon as any
// row's quantity or rate changes -- "immediately I'm done filling
// everything... it should sum it up automatically." Also auto-fills
// the Total amount field from this subtotal, unless the person has
// typed their own figure into it (see invoiceAmountManuallyEdited).
function recomputeInvoiceItemsSubtotal() {
  const rows = Array.from(invoiceItemsTbody.querySelectorAll('tr'));
  // Read each row's actual Total field rather than recalculating
  // qty x rate here -- a row can now carry a manually-typed total
  // with no rate at all (Sept 2026 rate/total override), so
  // recalculating from rate alone silently drops those rows to 0.
  const subtotal = rows.reduce((sum, tr) => {
    return sum + parseMoneyInput(tr.querySelector('.item-total').value);
  }, 0);
  document.getElementById('invoice-items-subtotal-readout').textContent = formatNaira(subtotal);

  if (!invoiceAmountManuallyEdited) {
    const amountInput = document.getElementById('invoice-amount');
    amountInput.value = subtotal > 0 ? subtotal.toFixed(2) : '';
    const wordsEl = document.getElementById('invoice-amount-words');
    wordsEl.textContent = subtotal > 0 ? `In words: ${nairaToWords(subtotal)}` : '';
    recomputeInvoiceBalance();
    recomputeLandedCost();
  }
}

let productSearchDebounce = null;
function wireItemAutocomplete(tr) {
  const nameInput = tr.querySelector('.item-name');
  const variantIdInput = tr.querySelector('.item-variant-id');
  const dropdown = tr.querySelector('.item-autocomplete-dropdown');

  function hideDropdown() { dropdown.style.display = 'none'; dropdown.innerHTML = ''; }

  function selectProduct(product) {
    nameInput.value = product.name;
    variantIdInput.value = product.product_id || product.id || '';
    // PATCH5B-INVOICE-PACKS: remember the product's default pieces per pack on this row.
    tr.dataset.ppCarton = product.default_pieces_per_carton || '';
    tr.dataset.ppBag = product.default_pieces_per_bag || '';
    tr.dataset.ppRoll = product.default_pieces_per_roll || '';
    // September 2026 change spec section 13: selecting an item only
    // ever autofills the name -- rate and total are always entered
    // manually per invoice, since cost can change invoice to invoice.
    // (Previously this also prefilled Rate from the product's current
    // selling price when the field was empty -- that's the bug being
    // fixed here.)
    hideDropdown();
  }

  async function addNewProduct(name) {
    if (!canManageProducts) {
      showToast('You do not have permission to add new products.', true);
      return;
    }
    // Optional, but asked for up front -- see the matching comment in
    // category-variants.js's quick-add. Defaults to the rate already
    // typed on this invoice line, since that's very likely this
    // product's actual cost and saves retyping it.
    const rateInput = tr.querySelector('.item-rate')?.value;
    const suggestedCost = rateInput ? parseMoneyInput(rateInput) : undefined;
    const costInput = await showPromptModal(
      'Leave blank to skip for now.',
      { title: 'Cost price per unit (₦)', defaultValue: suggestedCost !== undefined ? String(suggestedCost) : '', confirmLabel: 'Save' }
    );
    const costPriceNaira = costInput && !Number.isNaN(Number(costInput)) ? Number(costInput) : undefined;
    try {
      const product = await Api.createProduct({
        name,
        ...(costPriceNaira !== undefined ? { cost_price_naira: costPriceNaira } : {}),
      });
      // Bug fix (found while investigating item 12): this object only
      // ever set `product_id`, never `id` -- and selectProduct() reads
      // `product.product_id || product.id`. Api.createProduct's
      // response uses `id` (see ProductOut), so `product_id` here was
      // always undefined and the newly-created row's hidden variant-id
      // field was silently left blank, meaning this invoice line could
      // never be matched back to the product it was just created for.
      selectProduct({ name: product.name, id: product.id, price_naira: product.price_naira });
      showToast(`Product "${name}" added to inventory.`);
    } catch (err) {
      showToast(err.message, true);
    }
  }

  nameInput.addEventListener('input', () => {
    // Any manual edit invalidates a previously-selected link -- the
    // typed text no longer necessarily matches that catalog item.
    variantIdInput.value = '';
    const query = nameInput.value.trim();
    clearTimeout(productSearchDebounce);
    if (query.length < 2) { hideDropdown(); return; }
    productSearchDebounce = setTimeout(async () => {
      let results = [];
      try {
        results = await Api.listProducts({ search: query });
      } catch (err) { /* non-fatal -- autocomplete just stays empty */ }
      const rowsHtml = results.slice(0, 8).map((p) => `
        <div class="item-autocomplete-row" data-product-id="${p.id}">
          <span>${escapeHtml(p.name)}${p.size ? ` <span style="color:var(--ink-faint);">(${escapeHtml(p.size)})</span>` : ''}${p.category_name ? ` <span style="color:var(--ink-faint);">— ${escapeHtml(p.category_name)}</span>` : ''}</span>
          <span class="price">${p.price_naira != null ? formatNaira(p.price_naira) : ''}</span>
        </div>`).join('');
      const addRowHtml = canManageProducts
        ? `<div class="item-autocomplete-row item-autocomplete-add" data-add-product="1">+ Add "${escapeHtml(query)}" as a new product</div>`
        : '';
      dropdown.innerHTML = rowsHtml || `<div class="item-autocomplete-empty">No matching products.</div>`;
      dropdown.innerHTML += addRowHtml;
      dropdown.style.display = results.length || canManageProducts ? '' : 'none';
      dropdown.querySelectorAll('[data-product-id]').forEach((el) => {
        const product = results.find((p) => p.id === el.dataset.productId);
        el.addEventListener('mousedown', (e) => { e.preventDefault(); if (product) selectProduct(product); });
      });
      const addEl = dropdown.querySelector('[data-add-product]');
      if (addEl) addEl.addEventListener('mousedown', (e) => { e.preventDefault(); addNewProduct(query); });
    }, 250);
  });
  // Auto-create-on-blur: if the person types a brand-new item name and
  // tabs/clicks away without picking a dropdown match or using "+ Add",
  // silently create a bare (name-only) product and link this row to it,
  // instead of leaving the line unmatched. Dropdown-item and "+ Add"
  // mousedown handlers above call e.preventDefault(), which suppresses
  // the browser's default focus-shift -- so this blur only actually
  // fires for genuine tab-away/click-elsewhere, never for a dropdown
  // selection (that path is already handled by selectProduct/addNewProduct).
  async function autoCreateProductOnBlur() {
    const currentName = nameInput.value.trim();
    if (!currentName || variantIdInput.value || !canManageProducts) return;
    try {
      const product = await Api.createProduct({ name: currentName });
      variantIdInput.value = product.id;
      showToast(`"${product.name}" added to inventory.`);
    } catch (err) {
      showToast(err.message, true);
    }
  }
  nameInput.addEventListener('blur', () => setTimeout(() => { hideDropdown(); autoCreateProductOnBlur(); }, 150));
}

function addInvoiceItemRow() {
  const rowId = `item-row-${invoiceItemRowSeq++}`;
  const tr = document.createElement('tr');
  tr.id = rowId;
  tr.innerHTML = `
    <td class="row-sn" style="text-align:center;color:var(--ink-faint);"></td>
    <td>
      <button type="button" class="btn btn-ghost btn-sm item-qty-summary-btn" style="width:100%;text-align:left;white-space:normal;">1 piece</button>
      <div class="item-cost-per-piece-readout" style="font-size:11px;color:var(--ink-faint);margin-top:4px;"></div>
      <input type="hidden" class="item-unit" value="pieces" />
      <input type="hidden" class="item-qty" value="1" />
      <input type="hidden" class="item-pieces-per-carton" />
      <input type="hidden" class="item-remainder-qty" />
      <input type="hidden" class="item-remainder-unit" value="pieces" />
    </td>
    <td class="item-autocomplete-wrap">
      <input type="text" class="item-name" maxlength="200" placeholder="Type to search products…" autocomplete="off" />
      <input type="hidden" class="item-variant-id" />
      <div class="item-autocomplete-dropdown" style="display:none;"></div>
    </td>
    <td><input type="text" inputmode="decimal" class="item-rate" placeholder="0.00" style="width:100%;min-width:110px;" /></td>
    <td><input type="text" inputmode="decimal" class="item-total" placeholder="0.00" style="width:100%;min-width:110px;" /></td>
    <td><button type="button" class="btn btn-ghost btn-sm remove-item-row" title="Remove row">✕</button></td>`;
  invoiceItemsTbody.appendChild(tr);
  tr.querySelector('.remove-item-row').addEventListener('click', () => {
    if (tr.dataset.existing === '1' && tr.dataset.itemId) {
      removedExistingItemIds.push(tr.dataset.itemId);
    }
    tr.remove();
    renumberInvoiceItemRows();
    recomputeInvoiceItemsSubtotal();
  });
  wireMoneyInput(tr.querySelector('.item-rate'));
  wireMoneyInput(tr.querySelector('.item-total'));
  tr.querySelector('.item-rate').addEventListener('input', () => recomputeRowTotal(tr));
  tr.querySelector('.item-total').addEventListener('input', (e) => {
    // Empty box goes back to trusting quantity x rate; anything else
    // is a manual override that recomputeRowTotal will leave alone.
    tr.dataset.totalEdited = e.target.value === '' ? '' : '1';
    recomputeInvoiceItemsSubtotal();
  });
  // September 2026 change -- the unit/quantity/pieces-per-carton/
  // partial-pack controls used to live directly in this table cell
  // (too cramped, especially on mobile). They now open in a
  // dedicated popup (see openQtyDetailPopup below); this button just
  // shows a compact summary of whatever was last saved there.
  tr.querySelector('.item-qty-summary-btn').addEventListener('click', () => openQtyDetailPopup(tr));
  updateQtySummaryButton(tr);
  recomputeRowTotal(tr);
  wireItemAutocomplete(tr);
  renumberInvoiceItemRows();
  recomputeInvoiceItemsSubtotal();
}
// ---------------------------------------------------- Quantity popup
// Moves the unit/qty/partial-pack controls for one
// invoice line out of the (very cramped on mobile) table cell into a
// proper full-width panel. The row itself only ever shows a compact
// summary button + the live cost-per-piece readout.
const qtyDetailBackdrop = document.getElementById('qty-detail-backdrop');
const qtyDetailUnit = document.getElementById('qty-detail-unit');
const qtyDetailQty = document.getElementById('qty-detail-qty');
const qtyDetailToggleRemainder = document.getElementById('qty-detail-toggle-remainder');
const qtyDetailRemainderWrap = document.getElementById('qty-detail-remainder-wrap');
const qtyDetailRemainderKind = document.getElementById('qty-detail-remainder-kind');
const qtyDetailRemainderCountWrap = document.getElementById('qty-detail-remainder-count-wrap');
const qtyDetailRemainderQty = document.getElementById('qty-detail-remainder-qty');
const qtyDetailRemainderUnit = document.getElementById('qty-detail-remainder-unit');
const qtyDetailReadout = document.getElementById('qty-detail-readout');
let qtyDetailEditingRow = null;

function formatQtySummary(unit, qty, ppc, remainderQty, remainderUnit) {
  const singular = { cartons: 'carton', dozens: 'dozen', bags: 'bag', rolls: 'roll', pieces: 'piece' };
  const isHalfPack = remainderUnit === 'half_carton' || remainderUnit === 'half_dozen';
  let summary = `${qty} ${singular[unit] || unit}${qty === 1 ? '' : 's'}`;
  if (isHalfPack) {
    summary += remainderUnit === 'half_carton' ? ` + ½ carton` : ` + ½ dozen`;
  } else if (remainderQty) {
    summary += ` + ${remainderQty} ${singular[remainderUnit] || remainderUnit}${remainderQty === 1 ? '' : 's'}`;
  }
  // Sept 2026 change -- total-pieces conversion is no longer shown on
  // the invoice screen at all (it belongs at the warehouse stage);
  // the backend still computes and stores it independently.
  return summary;
}

function updateQtySummaryButton(tr) {
  const unit = tr.querySelector('.item-unit').value || 'pieces';
  const qty = Number(tr.querySelector('.item-qty').value || 1);
  const ppc = null;
  const remainderQty = Number(tr.querySelector('.item-remainder-qty').value || 0) || null;
  const remainderUnit = tr.querySelector('.item-remainder-unit').value || 'pieces';
  tr.querySelector('.item-qty-summary-btn').textContent = formatQtySummary(unit, qty, ppc, remainderQty, remainderUnit);
}

// The half-pack option means "half a carton" when the primary unit is
// cartons, and "half a dozen" when the primary unit is dozens -- so
// the remainder-kind dropdown's "half" value has to resolve to the
// right one of the two backend labels depending on context.
function halfPackLabelFor(primaryUnit) {
  return primaryUnit === 'cartons' ? 'half_carton' : 'half_dozen';
}

function updateQtyDetailRemainderUnitFields() {
  const unit = qtyDetailUnit.value;
  const kind = qtyDetailRemainderKind.value;
  if (kind === 'half') {
    qtyDetailRemainderCountWrap.style.display = 'none';
    qtyDetailRemainderQty.value = '';
  } else {
    qtyDetailRemainderCountWrap.style.display = '';
    qtyDetailRemainderUnit.innerHTML = unit === 'cartons'
      ? '<option value="dozens">Dozens</option><option value="pieces">Pieces</option>'
      : '<option value="pieces">Pieces</option>';
    updateRemainderQtyPlaceholder();
  }
  recomputeQtyDetailReadout();
}

// Sept 2026 change -- the partial quantity box's placeholder now names
// the unit it counts ("Qty of dozens"/"Qty of pieces") instead of a
// generic "Qty", since the number typed there is a count of whichever
// unit is selected in the dropdown beside it, not some unrelated
// overall quantity.
function updateRemainderQtyPlaceholder() {
  qtyDetailRemainderQty.placeholder = qtyDetailRemainderUnit.value === 'dozens' ? 'Qty of dozens' : 'Qty of pieces';
}

function updateQtyDetailPopupFields() {
  const unit = qtyDetailUnit.value;
  // Sept 2026 change -- label the half-pack option for whichever unit
  // is primary ("Half carton"/"Half dozen") instead of a generic
  // "half pack", so it is unambiguous which half is meant.
  const halfOption = qtyDetailRemainderKind.querySelector('option[value="half"]');
  if (halfOption) halfOption.textContent = unit === 'cartons' ? 'Half carton (1/2)' : 'Half dozen (1/2)';
  if (unit === 'pieces' || unit === 'bags' || unit === 'rolls') {
    qtyDetailToggleRemainder.style.display = 'none';
    qtyDetailRemainderWrap.style.display = 'none';
    qtyDetailRemainderQty.value = '';
  } else {
    qtyDetailToggleRemainder.style.display = '';
    updateQtyDetailRemainderUnitFields();
  }
  recomputeQtyDetailReadout();
}

function recomputeQtyDetailReadout() {
  // Sept 2026 change -- the cost-per-piece / total-pieces readout has
  // been removed from the invoice screen entirely (see the matching
  // change in recomputeRowTotal). The backend still computes and
  // stores total_pieces independently.
  if (qtyDetailReadout) qtyDetailReadout.textContent = '';
}

// PATCH5B-INVOICE-PACKS: pieces in one pack for cartons, bags and rolls.
const QTY_PACK_WORDS = { cartons: 'carton', bags: 'bag', rolls: 'roll' };
const qtyDetailPackWrap = document.getElementById('qty-detail-pack-wrap');
const qtyDetailPack = document.getElementById('qty-detail-pack');
const qtyDetailPackLabel = document.getElementById('qty-detail-pack-label');
const qtyDetailPackHint = document.getElementById('qty-detail-pack-hint');

function qtyPackDefault(tr, unit) {
  const key = { cartons: 'ppCarton', bags: 'ppBag', rolls: 'ppRoll' }[unit];
  const n = key && tr ? Number(tr.dataset[key] || 0) : 0;
  return n > 0 ? n : null;
}

function updateQtyDetailPackField() {
  const tr = qtyDetailEditingRow;
  const unit = qtyDetailUnit.value;
  const word = QTY_PACK_WORDS[unit];
  if (!word) {
    qtyDetailPackWrap.style.display = 'none';
    qtyDetailPack.value = '';
    qtyDetailPackHint.textContent = '';
    return;
  }
  qtyDetailPackWrap.style.display = '';
  qtyDetailPackLabel.textContent = 'Pieces in one ' + word;
  const savedUnit = tr ? tr.querySelector('.item-unit').value : '';
  const savedValue = tr ? tr.querySelector('.item-pieces-per-carton').value : '';
  if (savedValue && unit === savedUnit) {
    qtyDetailPack.value = savedValue;
    qtyDetailPackHint.textContent = '';
    return;
  }
  const fromProduct = qtyPackDefault(tr, unit);
  qtyDetailPack.value = fromProduct === null ? '' : String(fromProduct);
  qtyDetailPackHint.textContent = fromProduct === null ? '' : 'From the product. You can change it for this invoice.';
}

function openQtyDetailPopup(tr) {
  qtyDetailEditingRow = tr;
  qtyDetailUnit.value = tr.querySelector('.item-unit').value || 'pieces';
  qtyDetailQty.value = tr.querySelector('.item-qty').value || 1;
  updateQtyDetailPopupFields();
  updateQtyDetailPackField();
  const savedRemainderUnit = tr.querySelector('.item-remainder-unit').value;
  const savedRemainderQty = tr.querySelector('.item-remainder-qty').value;
  const savedIsHalfPack = savedRemainderUnit === 'half_carton' || savedRemainderUnit === 'half_dozen';
  if (savedIsHalfPack) {
    qtyDetailRemainderWrap.style.display = '';
    qtyDetailRemainderKind.value = 'half';
    updateQtyDetailRemainderUnitFields();
  } else if (savedRemainderQty) {
    qtyDetailRemainderWrap.style.display = '';
    qtyDetailRemainderKind.value = 'count';
    updateQtyDetailRemainderUnitFields();
    qtyDetailRemainderQty.value = savedRemainderQty;
    if (savedRemainderUnit) qtyDetailRemainderUnit.value = savedRemainderUnit;
  } else {
    qtyDetailRemainderWrap.style.display = 'none';
    qtyDetailRemainderKind.value = 'half';
    qtyDetailRemainderQty.value = '';
  }
  recomputeQtyDetailReadout();
  qtyDetailBackdrop.classList.add('visible');
}

qtyDetailUnit.addEventListener('change', () => {
  updateQtyDetailPopupFields();
  updateQtyDetailPackField();
  // If a half-pack partial is showing, "half carton" vs "half dozen"
  // depends on the primary unit -- re-resolve on every unit change.
  recomputeQtyDetailReadout();
});
qtyDetailQty.addEventListener('input', recomputeQtyDetailReadout);
qtyDetailRemainderKind.addEventListener('change', updateQtyDetailRemainderUnitFields);
qtyDetailRemainderQty.addEventListener('input', recomputeQtyDetailReadout);
qtyDetailRemainderUnit.addEventListener('change', () => {
  updateRemainderQtyPlaceholder();
  recomputeQtyDetailReadout();
});
qtyDetailToggleRemainder.addEventListener('click', () => {
  const isOpen = qtyDetailRemainderWrap.style.display !== 'none';
  qtyDetailRemainderWrap.style.display = isOpen ? 'none' : '';
  if (isOpen) {
    qtyDetailRemainderQty.value = '';
  } else {
    qtyDetailRemainderKind.value = 'half';
    updateQtyDetailRemainderUnitFields();
  }
  recomputeQtyDetailReadout();
});
document.getElementById('qty-detail-cancel').addEventListener('click', () => { qtyDetailBackdrop.classList.remove('visible'); qtyDetailEditingRow = null; });
qtyDetailBackdrop.addEventListener('click', (e) => { if (e.target === qtyDetailBackdrop) { qtyDetailBackdrop.classList.remove('visible'); qtyDetailEditingRow = null; } });
document.getElementById('qty-detail-save').addEventListener('click', () => {
  if (!qtyDetailEditingRow) return;
  const tr = qtyDetailEditingRow;
  // PATCH5B-INVOICE-PACKS: check the pieces-in-a-pack number before anything is changed.
  const packWord = QTY_PACK_WORDS[qtyDetailUnit.value];
  const packText = packWord ? qtyDetailPack.value.trim() : '';
  const packNumber = packText === '' ? null : Number(packText);
  if (packNumber !== null && (!Number.isInteger(packNumber) || packNumber < 1)) {
    showToast('Pieces in a pack must be a whole number, 1 or more.', true);
    return;
  }
  const packValue = packNumber === null ? '' : String(packNumber);
  tr.querySelector('.item-unit').value = qtyDetailUnit.value;
  tr.querySelector('.item-qty').value = qtyDetailQty.value || 1;
  tr.querySelector('.item-pieces-per-carton').value = packValue;
  const remainderOpen = qtyDetailRemainderWrap.style.display !== 'none';
  const isHalfPack = remainderOpen && qtyDetailRemainderKind.value === 'half';
  if (isHalfPack) {
    tr.querySelector('.item-remainder-qty').value = '';
    tr.querySelector('.item-remainder-unit').value = halfPackLabelFor(qtyDetailUnit.value);
  } else {
    tr.querySelector('.item-remainder-qty').value = remainderOpen ? (qtyDetailRemainderQty.value || '') : '';
    tr.querySelector('.item-remainder-unit').value = remainderOpen ? qtyDetailRemainderUnit.value : 'pieces';
  }
  updateQtySummaryButton(tr);
  recomputeRowTotal(tr);
  qtyDetailBackdrop.classList.remove('visible');
  qtyDetailEditingRow = null;
});

document.getElementById('invoice-add-item-row').addEventListener('click', addInvoiceItemRow);

// ---------------------------------------------------------------- Supplier autofill
// September 2026 change spec section 13: a proper custom dropdown
// (matching how Category works on Add Product, and how the Item name
// column on this same form already works) -- not the browser's native
// <datalist>/autofill suggestion, which is what used to show here and
// didn't reliably surface the saved supplier list. Picking a result
// (or typing the exact name again) autofills address/phone. If the
// person then edits those, submit asks whether to also update the
// supplier's saved record -- editing the invoice's own copy never
// silently rewrites the shared registry on its own.

const supplierInput = document.getElementById('invoice-supplier');
const supplierDropdown = document.getElementById('invoice-supplier-dropdown');
const supplierAddressInput = document.getElementById('invoice-supplier-address');
const supplierPhoneInput = document.getElementById('invoice-supplier-phone');
const supplierHint = document.getElementById('invoice-supplier-hint');

let supplierSearchResults = [];
let matchedSupplier = null; // the exact-name match currently backing the address/phone fields

function hideSupplierDropdown() { supplierDropdown.style.display = 'none'; supplierDropdown.innerHTML = ''; }

function selectSupplier(supplier) {
  supplierInput.value = supplier.name;
  applyMatchedSupplier(supplier);
  hideSupplierDropdown();
}

function applyMatchedSupplier(supplier) {
  matchedSupplier = supplier;
  // Autofill -- still editable; the user can modify it -- but only
  // overwrite what's currently empty so we don't clobber something
  // already typed for a brand-new supplier.
  if (!supplierAddressInput.value) supplierAddressInput.value = supplier.address || '';
  if (!supplierPhoneInput.value) supplierPhoneInput.value = supplier.contact_phone || '';
  supplierHint.textContent = `Using saved details for ${supplier.name}. Edit below if anything's changed.`;
}

async function searchSuppliersForInvoice(query) {
  if (!query || !query.trim()) { hideSupplierDropdown(); return; }
  try {
    supplierSearchResults = await Api.listSuppliers(query.trim());
  } catch (err) { return; }
  const rowsHtml = supplierSearchResults.slice(0, 8).map((s) => `
    <div class="item-autocomplete-row" data-supplier-id="${s.id}">
      <span>${escapeHtml(s.name)}</span>
      <span class="price">${escapeHtml(s.contact_phone || '')}</span>
    </div>`).join('');
  supplierDropdown.innerHTML = rowsHtml || `<div class="item-autocomplete-empty">No matching suppliers -- will be saved as new.</div>`;
  supplierDropdown.style.display = '';
  supplierDropdown.querySelectorAll('[data-supplier-id]').forEach((el) => {
    const supplier = supplierSearchResults.find((s) => s.id === el.dataset.supplierId);
    el.addEventListener('mousedown', (e) => { e.preventDefault(); if (supplier) selectSupplier(supplier); });
  });
}

function resolveTypedSupplierName() {
  const typed = supplierInput.value.trim();
  const exact = supplierSearchResults.find((s) => s.name.toLowerCase() === typed.toLowerCase()) || null;
  if (exact) {
    applyMatchedSupplier(exact);
  } else {
    matchedSupplier = null;
    supplierHint.textContent = typed ? 'New supplier — will be saved for autofill next time.' : '';
  }
}

let supplierSearchDebounce;
supplierInput.addEventListener('input', () => {
  matchedSupplier = null;
  supplierHint.textContent = '';
  clearTimeout(supplierSearchDebounce);
  supplierSearchDebounce = setTimeout(() => searchSuppliersForInvoice(supplierInput.value), 250);
});
supplierInput.addEventListener('blur', () => {
  resolveTypedSupplierName();
  setTimeout(hideSupplierDropdown, 150);
});

// ---------------------------------------------------------------- Live balance / landed-cost readouts
function recomputeInvoiceBalance() {
  const total = parseMoneyInput(document.getElementById('invoice-amount').value);
  const paidInput = document.getElementById('invoice-amount-paid').value;
  const readout = document.getElementById('invoice-balance-readout');
  if (!paidInput) { readout.textContent = '—'; return; }
  const paid = parseMoneyInput(paidInput);
  readout.textContent = formatNaira(Math.max(0, total - paid));
}
document.getElementById('invoice-amount-paid').addEventListener('input', recomputeInvoiceBalance);

function recomputeLandedCost() {
  const total = parseMoneyInput(document.getElementById('invoice-amount').value);
  const waybill = parseMoneyInput(document.getElementById('invoice-waybill-cost').value);
  const transit = parseMoneyInput(document.getElementById('invoice-transit-cost').value);
  const handling = parseMoneyInput(document.getElementById('invoice-handling-cost').value);
  const logisticsSubtotal = waybill + transit + handling;
  document.getElementById('invoice-logistics-subtotal-readout').textContent = formatNaira(logisticsSubtotal);
  document.getElementById('invoice-landed-cost-readout').textContent = formatNaira(total + logisticsSubtotal);
}
document.getElementById('invoice-waybill-cost').addEventListener('input', recomputeLandedCost);
document.getElementById('invoice-transit-cost').addEventListener('input', recomputeLandedCost);
document.getElementById('invoice-handling-cost').addEventListener('input', recomputeLandedCost);


function collectInvoiceItemRows() {
  return Array.from(invoiceItemsTbody.querySelectorAll('tr'))
    .map((tr) => ({
      item_name: tr.querySelector('.item-name').value.trim(),
      product_id: tr.querySelector('.item-variant-id').value || null,
      quantity: Number(tr.querySelector('.item-qty').value || 1),
      unit_label: tr.querySelector('.item-unit').value,
      // September 2026 change -- cost-per-piece calculator fields.
      pieces_per_carton: Number(tr.querySelector('.item-pieces-per-carton').value || 0) || null,
      remainder_quantity: Number(tr.querySelector('.item-remainder-qty').value || 0) || null,
      remainder_unit_label: tr.querySelector('.item-remainder-unit').value || null,
      // Rate is optional -- some invoices come with a total and no
      // per-unit rate. Total is only sent when manually typed (Sept
      // 2026 rate/total override); otherwise the server computes it
      // as quantity x rate, same as before.
      rate_naira: tr.querySelector('.item-rate').value === '' ? null : parseMoneyInput(tr.querySelector('.item-rate').value),
      line_total_naira: tr.dataset.totalEdited === '1'
        ? parseMoneyInput(tr.querySelector('.item-total').value)
        : null,
      // True for a row that was pre-filled from an already-saved
      // invoice item when Edit opened it -- lets the submit handler
      // route it to update_items (edit in place) instead of add_items
      // (which would otherwise duplicate it as a brand-new line).
      _existing: tr.dataset.existing === '1',
      _itemId: tr.dataset.itemId || null,
    }))
    // A row someone opened but never typed an item name into is just
    // noise -- drop it rather than sending an invalid row to the API.
    .filter((row) => row.item_name.length > 0);
}

document.getElementById('add-invoice-btn').addEventListener('click', () => {
  editingInvoiceId = null;
  document.getElementById('invoice-add-form').closest('.modal').querySelector('h3').textContent = 'Add purchase invoice';
  document.getElementById('invoice-add-submit').textContent = 'Save invoice';
  if (!BranchContext.get()) {
    showToast('Pick a specific branch on the Dashboard to add an invoice — "All Branches" is view-only.', true);
    return;
  }
  document.getElementById('invoice-add-form').reset();
  invoiceItemsTbody.innerHTML = '';
  invoiceAmountManuallyEdited = false;
  addInvoiceItemRow();
  uploadedInvoiceImageUrl = null;
  document.getElementById('invoice-photo-status').textContent = '';
  document.getElementById('invoice-amount-words').textContent = '';
  document.getElementById('invoice-items-subtotal-readout').textContent = '₦0.00';
  document.getElementById('invoice-balance-readout').textContent = '—';
  document.getElementById('invoice-logistics-subtotal-readout').textContent = '—';
  document.getElementById('invoice-landed-cost-readout').textContent = '—';
  matchedSupplier = null;
  supplierSearchResults = [];
  supplierHint.textContent = '';
  hideSupplierDropdown();
  invoiceAddError.classList.remove('visible');
  invoiceAddBackdrop.classList.add('visible');
});
document.getElementById('invoice-add-cancel').addEventListener('click', () => { editingInvoiceId = null; invoiceAddBackdrop.classList.remove('visible'); });
invoiceAddBackdrop.addEventListener('click', (e) => { if (e.target === invoiceAddBackdrop) invoiceAddBackdrop.classList.remove('visible'); });

// Live "amount in words" readout as the total is typed -- matches the
// "Naira: ... / In words: ..." line on the reference invoice photos.
document.getElementById('invoice-amount').addEventListener('input', (e) => {
  invoiceAmountManuallyEdited = true;
  const wordsEl = document.getElementById('invoice-amount-words');
  const val = parseMoneyInput(e.target.value);
  wordsEl.textContent = val > 0 ? `In words: ${nairaToWords(val)}` : '';
  recomputeInvoiceBalance();
  recomputeLandedCost();
});
[
  'invoice-amount', 'invoice-amount-paid', 'invoice-waybill-cost',
  'invoice-transit-cost', 'invoice-handling-cost',
].forEach((id) => wireMoneyInput(document.getElementById(id)));

document.getElementById('invoice-photo').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const statusEl = document.getElementById('invoice-photo-status');
  statusEl.textContent = 'Uploading…';
  try {
    const { url } = await Api.uploadPurchaseInvoiceImage(file);
    uploadedInvoiceImageUrl = url;
    statusEl.textContent = file.type.startsWith('video/') ? 'Video attached ✓' : 'Photo attached ✓';
  } catch (err) {
    uploadedInvoiceImageUrl = null;
    statusEl.textContent = `Upload failed: ${err.message}`;
  }
});

document.getElementById('invoice-add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const submitBtn = document.getElementById('invoice-add-submit');
  submitBtn.disabled = true;
  invoiceAddError.classList.remove('visible');
  try {
    const items = collectInvoiceItemRows();
    // The typed Amount field, if given, overrides the items' sum
    // (matches how a cashier might just want the invoice's stated
    // grand total on file without every line adding up exactly).
    // Otherwise, fall back to summing whatever line totals were
    // filled in, so the invoice still shows a total at a glance.
    const explicitAmount = document.getElementById('invoice-amount').value
      ? parseMoneyInput(document.getElementById('invoice-amount').value)
      : null;
    const summedAmount = items.reduce((sum, it) => sum + (it.quantity * it.rate_naira || 0), 0) || null;
    const typedAddress = supplierAddressInput.value.trim() || null;
    const typedPhone = supplierPhoneInput.value.trim() || null;

    // Spec section 14: "when the information is modified, the system
    // should ask whether the supplier's saved database information
    // should also be updated." Only asks if there's an actual
    // difference from what's on file for a matched supplier -- a
    // brand-new supplier is registered automatically by the invoice
    // create call itself, no prompt needed.
    if (matchedSupplier) {
      const addressChanged = typedAddress !== (matchedSupplier.address || null);
      const phoneChanged = typedPhone !== (matchedSupplier.contact_phone || null);
      if (addressChanged || phoneChanged) {
        const shouldUpdate = await showConfirmModal(
          `Update ${matchedSupplier.name}'s saved address/phone with what you typed here, so it autofills this way next time?`,
          { title: 'Update supplier details?', confirmLabel: 'Update' }
        );
        if (shouldUpdate) {
          try {
            await Api.updateSupplier(matchedSupplier.id, { address: typedAddress, contact_phone: typedPhone });
          } catch (err) { /* non-fatal -- the invoice itself still saves with what was typed */ }
        }
      }
    }

    const invoicePayload = {
      branch_id: BranchContext.get() || currentUser?.branch_id || null,
      supplier_id: matchedSupplier ? matchedSupplier.id : null,
      supplier_name: document.getElementById('invoice-supplier').value.trim(),
      invoice_date: document.getElementById('invoice-date').value,
      amount_naira: explicitAmount ?? summedAmount,
      image_url: uploadedInvoiceImageUrl,
      supplier_address: typedAddress,
      supplier_phone: typedPhone,
      payment_method: document.getElementById('invoice-payment-method').value || null,
      amount_paid_naira: document.getElementById('invoice-amount-paid').value
        ? parseMoneyInput(document.getElementById('invoice-amount-paid').value) : null,
      waybill_cost_naira: document.getElementById('invoice-waybill-cost').value
        ? parseMoneyInput(document.getElementById('invoice-waybill-cost').value) : null,
      transit_cost_naira: document.getElementById('invoice-transit-cost').value
        ? parseMoneyInput(document.getElementById('invoice-transit-cost').value) : null,
      handling_cost_naira: document.getElementById('invoice-handling-cost').value
        ? parseMoneyInput(document.getElementById('invoice-handling-cost').value) : null,
      items: editingInvoiceId ? undefined : items,
      add_items: editingInvoiceId ? items.filter((it) => !it._existing) : undefined,
      update_items: editingInvoiceId
        ? items.filter((it) => it._existing && it._itemId).map((it) => ({
            id: it._itemId,
            item_name: it.item_name,
            product_id: it.product_id,
            quantity: it.quantity,
            unit_label: it.unit_label,
            rate_naira: it.rate_naira,
            line_total_naira: it.line_total_naira,
            pieces_per_carton: it.pieces_per_carton,
            remainder_quantity: it.remainder_quantity,
            remainder_unit_label: it.remainder_unit_label,
          }))
        : undefined,
      remove_item_ids: editingInvoiceId ? removedExistingItemIds : undefined,
    };
    let createdInvoice = null;
    if (editingInvoiceId) {
      await Api.updatePurchaseInvoice(editingInvoiceId, invoicePayload);
    } else {
      createdInvoice = await Api.createPurchaseInvoice(invoicePayload);
    }
    showToast('Purchase invoice saved.');
    invoiceAddBackdrop.classList.remove('visible');
    await loadPurchaseInvoices();
    // Opened from Receive Goods ("Create invoice"): offer to go straight back and link it.
    if (prefillReceiptId && !editingInvoiceId) {
      const backTo = prefillReceiptId;
      prefillReceiptId = null;
      const go = await showConfirmModal(
        'Invoice saved. Go back to Receive Goods to link it to the receipt you confirmed? Linking fixes the costs. It does not add any stock.',
        { title: 'Link it to the receipt', confirmLabel: 'Go to Receive Goods' }
      );
      if (go) {
        window.location.href = 'receive-goods.html?link_receipt=' + encodeURIComponent(backTo) +
          (createdInvoice && createdInvoice.id ? '&invoice=' + encodeURIComponent(createdInvoice.id) : '');
      }
    }
  } catch (err) {
    invoiceAddError.textContent = err.message;
    invoiceAddError.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});

loadBranches().then(() => { populateYearFilter(); populateSupplierFilter(); loadPurchaseInvoices(); });

// ---------------------------------------------------------------- Prefill from Receive Goods
// Receive Goods -> "Create invoice" opens invoice.html?from_receipt=<receipt id>. The form opens
// with one row per received product (name, product, quantity in pieces). The person only adds the
// prices, checks the supplier and date, and saves.
let prefillReceiptId = null;
(async function prefillFromReceipt() {
  const rid = new URLSearchParams(window.location.search).get('from_receipt');
  if (!rid) return;
  try { window.history.replaceState(null, '', window.location.pathname); } catch (e) { /* cosmetic */ }
  if (!Perms.can('manage_suppliers')) {
    showToast('Your role cannot add purchase invoices. Ask a super admin to record it.', true);
    return;
  }
  let rec;
  try {
    rec = await Api.getGoodsReceipt(rid);
  } catch (err) {
    showToast(err.message, true);
    return;
  }
  if (!rec || !rec.lines || !rec.lines.length) return;
  if (BranchContext.get() && BranchContext.get() !== rec.branch_id) {
    showToast('That receipt belongs to a different branch. Switch to its branch first.', true);
    return;
  }
  let list = [];
  try {
    const res = await Api.listProducts({ branchId: rec.branch_id });
    list = Array.isArray(res) ? res : (res && res.items) || [];
  } catch (e) { /* names are a convenience; rows still get the product id */ }
  const byId = {};
  list.forEach((p) => { byId[p.id] = p; });

  const addBtn = document.getElementById('add-invoice-btn');
  addBtn.addEventListener('click', () => {
    const old = document.getElementById('invoice-receipt-note');
    if (old) old.remove();
    prefillReceiptId = null;
  });
  addBtn.click();
  if (!invoiceAddBackdrop.classList.contains('visible')) return;
  prefillReceiptId = rid;

  invoiceItemsTbody.innerHTML = '';
  rec.lines.forEach((l) => {
    addInvoiceItemRow();
    const tr = invoiceItemsTbody.lastElementChild;
    const p = byId[l.product_id];
    tr.querySelector('.item-name').value = p ? p.name : '';
    tr.querySelector('.item-variant-id').value = l.product_id;
    tr.dataset.ppCarton = p && p.default_pieces_per_carton ? p.default_pieces_per_carton : '';
    tr.querySelector('.item-unit').value = 'pieces';
    tr.querySelector('.item-qty').value = l.quantity_received;
    updateQtySummaryButton(tr);
    recomputeRowTotal(tr);
  });
  if (rec.supplier_name) supplierInput.value = rec.supplier_name;
  const when = rec.delivered_at ? new Date(rec.delivered_at) : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  document.getElementById('invoice-date').value = when.getFullYear() + '-' + pad(when.getMonth() + 1) + '-' + pad(when.getDate());

  const note = document.createElement('p');
  note.id = 'invoice-receipt-note';
  note.className = 'hint';
  note.style.marginBottom = '10px';
  note.textContent = 'Filled in from receipt ' + String(rid).slice(0, 8) + ': products and quantities (in pieces) come from what you received. ' +
    'Add the prices, check the supplier and date, then save. To change a quantity to cartons, click it.';
  const form = document.getElementById('invoice-add-form');
  form.insertBefore(note, form.firstChild);
})();


// =====================================================================
// TRASH2 -- invoice Trash screens (Oct 2026)
// Tabs on the Invoice page: Invoices | Trash | Review (super admin) |
// Final deletions (super admin) | Activity. The server does all the work and
// all the permission checks (see routers/purchase_invoices.py, TRASH1); this
// file only shows the lists and asks for confirmation and a reason.
// =====================================================================
const canManageInvoices = Perms.can('manage_suppliers');
const isSuperAdminUser = Perms.isSuperAdmin();

function trashEsc(v) { return escapeHtml(v == null ? '' : String(v)); }
function trashDate(iso) {
  if (!iso) return '\u2014';
  return new Date(iso).toLocaleDateString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' });
}
function trashWhen(iso) { return iso ? formatDateTime(iso) : '\u2014'; }
function trashMoney(n) { return n != null ? formatNaira(n) : '\u2014'; }
function trashSub(text) { return text ? `<span class="trash-sub">${trashEsc(text)}</span>` : ''; }

function trashButton(action, id, label) {
  return `<button type="button" class="btn btn-ghost btn-sm" data-trash-action="${action}" data-id="${trashEsc(id)}">${label}</button>`;
}

const TRASH_TABS = {
  trash: {
    note: 'Deleted invoices stay here until they are restored or deleted permanently.',
    empty: 'The Trash is empty.',
    columns: ['Deleted on', 'Supplier', 'Branch', 'Amount', 'Deleted by', 'Reason', ''],
    load: () => Api.listTrashedInvoices(BranchContext.get()),
    row: (r) => `
      <tr>
        <td>${trashWhen(r.trashed_at)}</td>
        <td><strong>${trashEsc(r.supplier_name || 'No supplier')}</strong>${trashSub(trashDate(r.invoice_date))}</td>
        <td>${trashEsc(r.branch_name || 'Store-wide')}</td>
        <td>${trashMoney(r.amount_naira)}</td>
        <td>${trashEsc(r.trashed_by_name || '\u2014')}</td>
        <td style="max-width:240px;">${trashEsc(r.trash_reason || '\u2014')}</td>
        <td><div class="trash-actions">${trashButton('view', r.id, 'View invoice')}${trashButton('restore', r.id, 'Restore')}${trashButton('delete-permanently', r.id, 'Delete permanently')}</div></td>
      </tr>`,
  },
  review: {
    note: 'Invoices that branch managers deleted permanently wait here. Nothing is removed until you decide.',
    empty: 'Nothing is waiting for review.',
    columns: ['Sent on', 'Supplier', 'Branch', 'Amount', 'Deleted by', 'Sent by', 'Their reason', ''],
    load: () => Api.listInvoicesForReview(BranchContext.get()),
    row: (r) => `
      <tr>
        <td>${trashWhen(r.review_sent_at)}</td>
        <td><strong>${trashEsc(r.supplier_name || 'No supplier')}</strong>${trashSub(trashDate(r.invoice_date))}</td>
        <td>${trashEsc(r.branch_name || 'Store-wide')}</td>
        <td>${trashMoney(r.amount_naira)}</td>
        <td>${trashEsc(r.trashed_by_name || '\u2014')}${trashSub(r.trash_reason)}</td>
        <td>${trashEsc(r.review_sent_by_name || '\u2014')}</td>
        <td style="max-width:240px;">${trashEsc(r.review_reason || '\u2014')}</td>
        <td><div class="trash-actions">${trashButton('view', r.id, 'View invoice')}${trashButton('restore', r.id, 'Restore')}${trashButton('delete-permanently', r.id, 'Delete for good')}${trashButton('history', r.id, 'History')}</div></td>
      </tr>`,
  },
  final: {
    note: 'A private record of every invoice a super admin deleted for good. Branch managers cannot see this list.',
    empty: 'No invoice has been deleted for good yet.',
    columns: ['Deleted on', 'Deleted by', 'Invoice date', 'Supplier', 'Branch', 'Amount', 'First deleted by', 'Sent for review by', 'Reasons', ''],
    load: () => Api.listFinalDeletions(),
    row: (r) => `
      <tr>
        <td>${trashWhen(r.deleted_at)}</td>
        <td><strong>${trashEsc(r.deleted_by_name || '\u2014')}</strong></td>
        <td>${trashDate(r.invoice_date)}</td>
        <td>${trashEsc(r.supplier_name || 'No supplier')}</td>
        <td>${trashEsc(r.branch_name || 'Store-wide')}</td>
        <td>${trashMoney(r.amount_naira)}</td>
        <td>${trashEsc(r.trashed_by_name || '\u2014')}${trashSub(trashWhen(r.trashed_at))}</td>
        <td>${trashEsc(r.review_sent_by_name || '\u2014')}${r.review_sent_at ? trashSub(trashWhen(r.review_sent_at)) : ''}</td>
        <td style="max-width:280px;">${trashSub('Final: ' + (r.deleted_reason || '\u2014'))}${trashSub('Deleted: ' + (r.trash_reason || '\u2014'))}${r.review_reason ? trashSub('Review: ' + r.review_reason) : ''}</td>
        <td>${trashButton('view-final', r.id, 'View')}</td>
      </tr>`,
  },
  activity: {
    note: 'Every delete, restore and send-for-review step recorded for your branches. The final permanent deletion by a super admin is not listed here.',
    empty: 'Nothing has been recorded yet.',
    columns: ['When', 'What', 'Invoice', 'Branch', 'Amount', 'By', 'Reason'],
    load: () => Api.listInvoiceTrashActivity(BranchContext.get()),
    row: (e) => `
      <tr>
        <td>${trashWhen(e.created_at)}</td>
        <td><strong>${trashEsc(e.label)}</strong></td>
        <td>${trashEsc(e.supplier_name || 'No supplier')}${trashSub(e.invoice_date ? trashDate(e.invoice_date) : '')}</td>
        <td>${trashEsc(e.branch_name || 'Store-wide')}</td>
        <td>${trashMoney(e.amount_naira)}</td>
        <td>${trashEsc(e.user_name || '\u2014')}</td>
        <td style="max-width:260px;">${trashEsc(e.reason || '\u2014')}</td>
      </tr>`,
  },
};

const trashRowCache = {};
// TRASH3: full copies of the invoices in the Trash / Review lists, so "View invoice" can open them.
const trashInvoiceLookup = {};
function trashBanner(inv) {
  const t = inv.__trash || {};
  if (t.state === 'pending_final') {
    return `<div><strong>Waiting for a super admin to decide.</strong> Sent for permanent deletion by ${trashEsc(t.review_sent_by_name || 'unknown')} \u00b7 ${trashWhen(t.review_sent_at)} \u00b7 Reason: ${trashEsc(t.review_reason || '\u2014')}</div>`;
  }
  return `<div><strong>This invoice is in the Trash.</strong> Deleted by ${trashEsc(t.trashed_by_name || 'unknown')} \u00b7 ${trashWhen(t.trashed_at)} \u00b7 Reason: ${trashEsc(t.trash_reason || '\u2014')}</div>`;
}
let activeInvTab = 'invoices';
const INV_TAB_HIDE_ON_OTHER_TABS = ['purchase-invoices-filters', 'invoice-total-card', 'purchase-invoices-section', 'add-invoice-btn'];

function showInvTab(name) {
  activeInvTab = name;
  document.querySelectorAll('[data-inv-tab]').forEach((b) => b.classList.toggle('active', b.dataset.invTab === name));
  const onInvoices = name === 'invoices';
  INV_TAB_HIDE_ON_OTHER_TABS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('inv-hidden', !onInvoices);
  });
  document.getElementById('trash-pane').classList.toggle('inv-hidden', onInvoices);
  if (!onInvoices) loadTrashTab(name);
}

async function loadTrashTab(name) {
  const cfg = TRASH_TABS[name];
  if (!cfg) return;
  const thead = document.getElementById('trash-thead');
  const tbody = document.getElementById('trash-tbody');
  const empty = document.getElementById('trash-empty');
  document.getElementById('trash-note').textContent = cfg.note;
  thead.innerHTML = '<tr>' + cfg.columns.map((c) => `<th>${c}</th>`).join('') + '</tr>';
  tbody.innerHTML = `<tr class="loading-row"><td colspan="${cfg.columns.length}">Loading\u2026</td></tr>`;
  empty.style.display = 'none';
  let rows = [];
  try {
    rows = await cfg.load();
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
    return;
  }
  if (name !== activeInvTab) return; // the person already moved to another tab
  trashRowCache[name] = rows;
  rows.forEach((r) => {
    if (r.invoice) {
      trashInvoiceLookup[r.id] = Object.assign({}, r.invoice, {
        __trash: {
          state: r.trash_state,
          trashed_by_name: r.trashed_by_name,
          trashed_at: r.trashed_at,
          trash_reason: r.trash_reason,
          review_sent_by_name: r.review_sent_by_name,
          review_sent_at: r.review_sent_at,
          review_reason: r.review_reason,
        },
      });
    }
  });
  if (!rows.length) {
    tbody.innerHTML = '';
    document.getElementById('trash-empty-text').textContent = cfg.empty;
    empty.style.display = 'block';
    return;
  }
  tbody.innerHTML = rows.map(cfg.row).join('');
  tbody.querySelectorAll('[data-trash-action]').forEach((btn) => {
    btn.addEventListener('click', () => handleTrashAction(btn.dataset.trashAction, btn.dataset.id));
  });
}

function setTabCount(elementId, n) {
  const el = document.getElementById(elementId);
  if (el) el.textContent = `(${n})`;
}

async function refreshTrashCounts() {
  if (!canManageInvoices) return;
  try {
    const trashed = await Api.listTrashedInvoices(BranchContext.get());
    setTabCount('trash-count', trashed.length);
  } catch (err) { /* counts are cosmetic */ }
  if (isSuperAdminUser) {
    try {
      const waiting = await Api.listInvoicesForReview(BranchContext.get());
      setTabCount('review-count', waiting.length);
    } catch (err) { /* counts are cosmetic */ }
  }
}

// After any delete / restore: the invoice list, its total, the tab counts and
// the open tab all need to be fresh. Never throws.
async function afterTrashChange() {
  try {
    await loadPurchaseInvoices();
    await refreshTrashCounts();
    if (activeInvTab !== 'invoices') await loadTrashTab(activeInvTab);
  } catch (err) { /* the action itself already succeeded */ }
}

// ------------------------------------------------------- reason window
const reasonBackdrop = document.getElementById('trash-reason-backdrop');
const reasonInput = document.getElementById('trash-reason-input');
const reasonError = document.getElementById('trash-reason-error');
const reasonConfirmBtn = document.getElementById('trash-reason-confirm');
let reasonRun = null;

function openReasonModal(opts) {
  document.getElementById('trash-reason-title').textContent = opts.title;
  document.getElementById('trash-reason-text').textContent = opts.text;
  reasonInput.value = '';
  reasonError.textContent = '';
  reasonError.classList.remove('visible');
  reasonConfirmBtn.textContent = opts.confirmLabel;
  reasonConfirmBtn.disabled = false;
  if (opts.danger) {
    reasonConfirmBtn.style.background = '#c0392b';
    reasonConfirmBtn.style.borderColor = 'transparent';
    reasonConfirmBtn.style.color = '#fff';
  } else {
    reasonConfirmBtn.style.background = '';
    reasonConfirmBtn.style.borderColor = '';
    reasonConfirmBtn.style.color = '';
  }
  reasonRun = opts.run;
  reasonBackdrop.classList.add('visible');
  setTimeout(() => reasonInput.focus(), 50);
}
function closeReasonModal() {
  reasonBackdrop.classList.remove('visible');
  reasonRun = null;
}
function showReasonError(message) {
  reasonError.textContent = message;
  reasonError.classList.add('visible');
}
document.getElementById('trash-reason-cancel').addEventListener('click', closeReasonModal);
reasonBackdrop.addEventListener('click', (e) => { if (e.target === reasonBackdrop) closeReasonModal(); });
reasonConfirmBtn.addEventListener('click', async () => {
  if (!reasonRun) return;
  const reason = reasonInput.value.trim();
  if (!reason) { showReasonError('A reason is required.'); return; }
  reasonConfirmBtn.disabled = true;
  try {
    await reasonRun(reason);
    closeReasonModal();
  } catch (err) {
    // The window stays open so the person can read why (for example "this invoice is already used").
    showReasonError(err.message);
  } finally {
    reasonConfirmBtn.disabled = false;
  }
});

// ------------------------------------------------- Delete (invoice detail window)
document.getElementById('invoice-detail-delete').addEventListener('click', () => {
  const inv = purchaseInvoiceCache.find((row) => row.id === editingInvoiceIdPending);
  if (!inv) return;
  invoiceDetailBackdrop.classList.remove('visible');
  const amount = inv.total_landed_cost_naira ?? inv.amount_naira;
  openReasonModal({
    title: 'Delete this invoice?',
    text: `${inv.supplier_name || 'No supplier'} \u00b7 ${trashDate(inv.invoice_date)} \u00b7 ${trashMoney(amount)}. It moves to the Trash tab and its amount is taken out of your totals right away. You can restore it from the Trash.`,
    confirmLabel: 'Delete invoice',
    danger: true,
    run: async (reason) => {
      await Api.trashPurchaseInvoice(inv.id, reason);
      showToast('Invoice moved to the Trash.');
      afterTrashChange();
    },
  });
});

// ----------------------------------------------------- Trash / Review actions
function findTrashRow(id) {
  for (const key of ['trash', 'review', 'final']) {
    const hit = (trashRowCache[key] || []).find((r) => r.id === id);
    if (hit) return hit;
  }
  return null;
}

async function handleTrashAction(action, id) {
  if (action === 'history') { if (isSuperAdminUser) openInvoiceHistory(id); return; }
  if (action === 'view-final') { openFinalView(id); return; }
  if (action === 'view') { openInvoiceDetail(id); return; }

  if (action === 'restore') {
    const yes = await showConfirmModal(
      'It goes back to the Invoices list exactly as it was, and its amount is added back to your totals.',
      { title: 'Restore this invoice?', confirmLabel: 'Restore' }
    );
    if (!yes) return;
    try {
      await Api.restorePurchaseInvoice(id);
      showToast('Invoice restored.');
      afterTrashChange();
    } catch (err) {
      showToast(err.message, true);
    }
    return;
  }

  if (action === 'delete-permanently') {
    const settings = isSuperAdminUser
      ? {
          text: 'This removes the invoice and all its items for good. It cannot be restored. A private record is kept that only super admins can see.',
          confirmLabel: 'Delete for good',
          success: 'Invoice deleted permanently.',
        }
      : {
          text: 'This sends the invoice to a super admin for the final decision. It leaves your Trash, but it is not removed until a super admin decides.',
          confirmLabel: 'Send for permanent deletion',
          success: 'Sent to a super admin for review.',
        };
    openReasonModal({
      title: 'Delete permanently?',
      text: settings.text,
      confirmLabel: settings.confirmLabel,
      danger: true,
      run: async (reason) => {
        await Api.deletePurchaseInvoicePermanently(id, reason);
        showToast(settings.success);
        afterTrashChange();
      },
    });
  }
}

// ------------------------------------------------------------ History window
const historyBackdrop = document.getElementById('inv-history-backdrop');
async function openInvoiceHistory(id) {
  const row = findTrashRow(id);
  const list = document.getElementById('inv-history-list');
  document.getElementById('inv-history-sub').textContent = row
    ? `${row.supplier_name || 'No supplier'} \u00b7 ${trashDate(row.invoice_date)} \u00b7 ${trashMoney(row.amount_naira)}`
    : '';
  list.innerHTML = '<div class="trash-sub">Loading\u2026</div>';
  historyBackdrop.classList.add('visible');
  try {
    const events = await Api.getInvoiceTrashHistory(id);
    list.innerHTML = events.length
      ? events.map((ev) => `
          <div class="inv-history-row">
            <strong>${trashEsc(ev.label)}</strong> by ${trashEsc(ev.user_name || 'unknown')} \u00b7 ${trashWhen(ev.created_at)}${ev.reason ? ` \u00b7 Reason: ${trashEsc(ev.reason)}` : ''}
          </div>`).join('')
      : '<div class="trash-sub">No steps are recorded for this invoice.</div>';
  } catch (err) {
    list.innerHTML = '';
    showToast(err.message, true);
  }
}
document.getElementById('inv-history-close').addEventListener('click', () => historyBackdrop.classList.remove('visible'));
historyBackdrop.addEventListener('click', (e) => { if (e.target === historyBackdrop) historyBackdrop.classList.remove('visible'); });

// ----------------------------------------------------- Final-deletion record window
const finalViewBackdrop = document.getElementById('final-view-backdrop');
function openFinalView(id) {
  const r = (trashRowCache.final || []).find((x) => x.id === id);
  if (!r) return;
  const snap = r.snapshot || {};
  const items = Array.isArray(snap.items) ? snap.items : [];
  const koboToNaira = (k) => (k == null ? null : Number(k) / 100);
  const lines = [];
  lines.push(`<div><strong>${trashEsc(r.supplier_name || 'No supplier')}</strong> \u00b7 ${trashDate(r.invoice_date)} \u00b7 ${trashEsc(r.branch_name || 'Store-wide')} \u00b7 ${trashMoney(r.amount_naira)}</div>`);
  lines.push(`<div class="trash-sub">Deleted for good by ${trashEsc(r.deleted_by_name || 'unknown')} \u00b7 ${trashWhen(r.deleted_at)} \u00b7 Reason: ${trashEsc(r.deleted_reason || '\u2014')}</div>`);
  lines.push(`<div class="trash-sub">First deleted by ${trashEsc(r.trashed_by_name || 'unknown')} \u00b7 ${trashWhen(r.trashed_at)} \u00b7 Reason: ${trashEsc(r.trash_reason || '\u2014')}</div>`);
  if (r.review_sent_at || r.review_sent_by_name) {
    lines.push(`<div class="trash-sub">Sent for review by ${trashEsc(r.review_sent_by_name || 'unknown')} \u00b7 ${trashWhen(r.review_sent_at)} \u00b7 Reason: ${trashEsc(r.review_reason || '\u2014')}</div>`);
  }
  if (snap.description) lines.push(`<div class="trash-sub">Notes: ${trashEsc(snap.description)}</div>`);
  if (items.length) {
    lines.push('<div class="table-card" style="padding:8px;margin:12px 0;overflow-x:auto;"><table style="width:100%;"><thead><tr><th>S/N</th><th>Quantity</th><th>Item</th><th>Rate</th><th>Total</th></tr></thead><tbody>' +
      items.map((it, i) => `<tr><td>${i + 1}</td><td>${trashEsc(it.quantity)} ${trashEsc(it.unit_label || 'pieces')}</td><td>${trashEsc(it.item_name)}</td><td>${trashMoney(koboToNaira(it.rate_kobo))}</td><td>${trashMoney(koboToNaira(it.line_total_kobo))}</td></tr>`).join('') +
      '</tbody></table></div>');
  }
  if (snap.image_url) {
    lines.push(`<div class="trash-sub"><a href="${trashEsc(snap.image_url)}" target="_blank" rel="noopener">Photo of the paper invoice</a></div>`);
  }
  document.getElementById('final-view-body').innerHTML = lines.join('');
  finalViewBackdrop.classList.add('visible');
}
document.getElementById('final-view-close').addEventListener('click', () => finalViewBackdrop.classList.remove('visible'));
finalViewBackdrop.addEventListener('click', (e) => { if (e.target === finalViewBackdrop) finalViewBackdrop.classList.remove('visible'); });

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  closeReasonModal();
  historyBackdrop.classList.remove('visible');
  finalViewBackdrop.classList.remove('visible');
});

// ------------------------------------------------------------------- start up
(function initInvoiceTrash() {
  const bar = document.getElementById('inv-tabs');
  if (!bar) return;
  // A view-only account (no manage_suppliers) sees no Trash at all; the server refuses them anyway.
  if (!canManageInvoices) { bar.classList.add('inv-hidden'); return; }
  if (isSuperAdminUser) {
    document.getElementById('inv-tab-review').classList.remove('inv-hidden');
    document.getElementById('inv-tab-final').classList.remove('inv-hidden');
    document.getElementById('inv-tab-activity').classList.remove('inv-hidden');
  }
  bar.querySelectorAll('[data-inv-tab]').forEach((b) => b.addEventListener('click', () => showInvTab(b.dataset.invTab)));
  window.addEventListener('branch-context-changed', () => {
    refreshTrashCounts();
    if (activeInvTab !== 'invoices') loadTrashTab(activeInvTab);
  });
  refreshTrashCounts();
})();
