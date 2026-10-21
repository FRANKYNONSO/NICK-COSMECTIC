Auth.requireAuth();
StoreBranding.refresh();
Perms.applyNav();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
const currentUser = Auth.getUser();
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

// September 2026 change spec section 3: the discount field is never
// manually typeable -- it only ever gets a value from an applied
// coupon (see coupon-apply-btn below). Locked here at script start,
// not just after the first coupon apply/remove cycle, so it can never
// sit free-typing on first load of the page.
document.getElementById('discount-input').disabled = true;


// Coupon generation is gated on manage_coupons -- super_admin and
// branch_manager have it, staff don't (server enforces this too, this
// just keeps the button from being visible-then-403 for staff).
if (Perms.can('manage_coupons')) {
  document.getElementById('generate-coupon-btn').style.display = '';
}

// PATCH3C-COUPON-POS-BEGIN
// "Reference code" box of the POS code dialog. A customer who kept their
// change as store credit (CHG-xxxxxx) gets a code for the FULL credit.
const PosCreditCode = (function () {
  const MISSING = 'No outstanding credit with this reference code.';
  const STILL_VALID = 'A discount code for this credit is still valid. Try again when it expires.';
  const LOOKUP_FAILED = 'Could not check the reference code. Try again.';
  const state = { checkedRef: '', credit: null, missing: false, locked: false, seq: 0 };

  function el(id) { return document.getElementById(id); }
  function typedRef() { return String(el('gc-reference').value || '').trim().toUpperCase(); }
  function showInfo(text, isError) {
    const info = el('gc-reference-info');
    info.textContent = text || '';
    info.style.display = text ? '' : 'none';
    info.style.color = isError ? '#b3261e' : '';
  }
  function setGenerateDisabled(flag) { el('generate-coupon-submit').disabled = !!flag; }
  function unlockAmount(clearValue) {
    const amount = el('gc-amount');
    amount.readOnly = false;
    if (clearValue && state.locked) amount.value = '';
    state.locked = false;
  }
  function invalidate() {
    state.seq += 1;
    state.checkedRef = '';
    state.credit = null;
    state.missing = false;
    unlockAmount(true);
    showInfo('', false);
    setGenerateDisabled(false);
  }
  function creditText(credit) {
    const amount = formatNaira(credit.amount_naira);
    return 'Credit for ' + credit.customer_name + ': ' + amount +
      '. The customer must buy items worth at least ' + amount + '.';
  }
  async function check() {
    const ref = typedRef();
    if (!ref) { invalidate(); return { blocked: false, credit: null }; }
    if (state.checkedRef === ref && state.credit) return { blocked: false, credit: state.credit };
    if (state.checkedRef === ref && state.missing) return { blocked: true, message: MISSING };
    state.seq += 1;
    const mySeq = state.seq;
    showInfo('Checking...', false);
    setGenerateDisabled(true);
    let result;
    try {
      result = await Api.listPendingChanges({ status: 'outstanding' });
    } catch (err) {
      if (mySeq === state.seq) {
        showInfo(LOOKUP_FAILED, true);
        setGenerateDisabled(false);
      }
      return { blocked: true, message: LOOKUP_FAILED };
    }
    if (mySeq !== state.seq) return check();
    const rows = Array.isArray(result) ? result : ((result && result.items) || []);
    const found = rows.find(function (row) {
      return String(row.reference_code || '').toUpperCase() === ref && row.status === 'outstanding';
    });
    state.checkedRef = ref;
    if (!found) {
      state.credit = null;
      state.missing = true;
      unlockAmount(true);
      showInfo(MISSING, true);
      setGenerateDisabled(true);
      return { blocked: true, message: MISSING };
    }
    state.credit = found;
    state.missing = false;
    const amount = el('gc-amount');
    amount.value = String(found.amount_naira);
    amount.readOnly = true;
    state.locked = true;
    showInfo(creditText(found), false);
    setGenerateDisabled(false);
    return { blocked: false, credit: found };
  }
  async function issue(payload) {
    try {
      return await Api.issueCoupon(payload);
    } catch (err) {
      const text = (err && err.message) || '';
      if (state.credit && ((err && err.status === 409) || /still valid/i.test(text))) {
        throw new Error(STILL_VALID);
      }
      throw err;
    }
  }

  const refInput = el('gc-reference');
  refInput.addEventListener('change', function () { check(); });
  refInput.addEventListener('input', function () { invalidate(); });

  return { ensureChecked: check, reset: invalidate, issue: issue };
})();
// PATCH3C-COUPON-POS-END

const generateCouponBackdrop = document.getElementById('generate-coupon-backdrop');
const couponResultBackdrop = document.getElementById('coupon-result-backdrop');

document.getElementById('generate-coupon-btn').addEventListener('click', () => {
  document.getElementById('generate-coupon-form').reset();
  PosCreditCode.reset();
  document.getElementById('generate-coupon-error').classList.remove('visible');
  generateCouponBackdrop.classList.add('visible');
});
document.getElementById('generate-coupon-cancel').addEventListener('click', () => {
  generateCouponBackdrop.classList.remove('visible');
});
document.getElementById('generate-coupon-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('generate-coupon-error');
  errorEl.classList.remove('visible');
  const creditNow = await PosCreditCode.ensureChecked();
  if (creditNow.blocked) {
    errorEl.textContent = creditNow.message;
    errorEl.classList.add('visible');
    return;
  }
  const submitBtn = document.getElementById('generate-coupon-submit');
  submitBtn.disabled = true;
  try {
    // Every coupon is fixed at 30 minutes' validity server-side now --
    // there's no "how many days" choice to send anymore.
    const coupon = await PosCreditCode.issue({
      discount_amount_naira: Number(document.getElementById('gc-amount').value),
      reference_code: creditNow.credit ? creditNow.credit.reference_code : undefined,
    });
    generateCouponBackdrop.classList.remove('visible');
    document.getElementById('coupon-result-code').textContent = coupon.code;
    document.getElementById('coupon-result-detail').textContent =
      `${formatNaira(coupon.discount_amount_naira)} off — expires ${new Date(coupon.expires_at).toLocaleString('en-NG', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}`;
    if (creditNow.credit) {
      document.getElementById('coupon-result-detail').textContent +=
        ' - Credit of ' + creditNow.credit.customer_name + ' (' + creditNow.credit.reference_code + ')';
    }
    couponResultBackdrop.classList.add('visible');
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});
document.getElementById('coupon-result-close').addEventListener('click', () => {
  couponResultBackdrop.classList.remove('visible');
});
document.getElementById('coupon-result-copy').addEventListener('click', async (e) => {
  const code = document.getElementById('coupon-result-code').textContent;
  const btn = e.currentTarget;
  try {
    await navigator.clipboard.writeText(code);
    const original = btn.textContent;
    btn.textContent = '✓ Copied';
    setTimeout(() => { btn.textContent = original; }, 1500);
  } catch (err) {
    // Clipboard API needs a secure context (HTTPS or localhost) --
    // falls back to select-all-and-hint if it's blocked for any reason,
    // rather than silently doing nothing.
    const range = document.createRange();
    range.selectNodeContents(document.getElementById('coupon-result-code'));
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    showToast('Copy blocked by the browser — code is selected, press Ctrl+C.', true);
  }
});

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

// ------------------------------------------------------------ Branch context
// POS used to have its own branch dropdown; that let a cashier serve a
// branch other than the one selected on the Dashboard, which is exactly
// the confusion the global branch context was built to remove. POS now
// just reads the branch chosen on the Dashboard (BranchContext, api.js)
// and shows it read-only -- see the "Branch: X" badge in pos.html.

// Platform update spec section 2.6 (known staff-side bug): a staff/
// branch_manager account is pinned to exactly one branch server-side
// and can never work another, but BranchContext is a *shared* value
// (persisted in localStorage, last set by whoever last touched the
// Dashboard's branch picker on this device/browser -- possibly a
// super_admin browsing a different branch, or a previous staffer's
// session). Reading BranchContext.get() first, as this used to, let
// that stale/foreign value win briefly on load -- exactly the
// "briefly shows a branch that isn't assigned to them" bug, which
// only cleared once some other page's staff-pin logic (e.g.
// dashboard.js) got a chance to overwrite BranchContext back to the
// right value. Fix: anyone with a fixed branch_id (staff or branch
// manager) is pinned to it immediately and unconditionally; only a
// super_admin (no fixed branch_id) actually reads the shared context.
let currentBranchId = currentUser?.branch_id || BranchContext.get() || null;
let posBranchCache = [];

async function loadBranches() {
  try {
    posBranchCache = await Api.listBranches();
    if (currentUser?.branch_id) {
      // Keep the shared context itself in sync too, so any other tab/
      // page reading BranchContext.get() on this device sees this
      // staffer's real branch rather than propagating a stale value
      // forward into the next page they visit.
      const b = posBranchCache.find((x) => x.id === currentUser.branch_id);
      BranchContext.set(currentUser.branch_id, b ? b.name : undefined);
    }
    BranchContext.renderBadge('branch-badge');
    if (!currentBranchId) {
      showToast('Pick a branch on the Dashboard before opening the till.', true);
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

window.addEventListener('branch-context-changed', (e) => {
  // A staffer/branch manager can't actually change branches (the
  // picker is hidden for them everywhere -- see dashboard.js), so an
  // incoming change event from elsewhere on the page/another tab must
  // never override their own fixed branch here.
  currentBranchId = currentUser?.branch_id || e.detail.branchId || null;
  loadProducts();
});

// -------------------------------------------------------------- Products

let productCache = [];
let debounceTimer = null;

// September 2026 change spec section 9 (POS live indicator):
// product_id -> { reserved_quantity, booking_ids }. Refreshed once
// per loadProducts() call so addToCart/changeQty/renderGrid can warn
// the cashier before checkout even runs -- the server still
// hard-blocks an oversell either way, this is just the early warning.
let bookingHolds = {};

async function loadBookingHolds() {
  bookingHolds = {};
  if (!currentBranchId || !productCache.length) return;
  try {
    // Bookings/stock are tracked by product.product_id (the underlying
    // variant/stock record), not product.id (the catalog listing key
    // the cart uses) -- see the missingVariant checks elsewhere in this
    // file for the same distinction.
    const variantIds = [...new Set(productCache.map((p) => p.product_id).filter(Boolean))];
    bookingHolds = await Api.getBookingHolds(currentBranchId, variantIds);
  } catch {
    // Non-critical -- see comment above.
  }
}

async function loadProducts() {
  const grid = document.getElementById('pos-grid');
  // Section 16: "All Branches" is browse-only for a Super Admin --
  // Api.listProducts({ branchId: undefined }) already returns
  // aggregated stock across every branch (same aggregation Inventory
  // uses), so products still load; addToCart below is what actually
  // blocks a transaction from being rung up while no specific branch
  // is selected.
  if (!currentBranchId && currentUser?.role !== 'super_admin') {
    grid.innerHTML = '<p style="color:var(--ink-muted);">Pick a branch to load products.</p>';
    return;
  }
  const search = document.getElementById('scan-input').value.trim() || undefined;
  grid.innerHTML = '<p style="color:var(--ink-muted);">Loading…</p>';
  try {
    productCache = await Api.listProducts({ branchId: currentBranchId || undefined, search });
    await loadBookingHolds();
    renderGrid();
  } catch (err) {
    grid.innerHTML = '';
    showToast(err.message, true);
  }
}

function renderGrid() {
  const grid = document.getElementById('pos-grid');
  if (!productCache.length) {
    grid.innerHTML = '<p style="color:var(--ink-muted);">No products match.</p>';
    return;
  }
  // Section 16: viewing "All Branches" shows everything (with combined
  // stock across branches) but a Super Admin still can't transact from
  // here -- tiles render as disabled/browse-only rather than hiding the
  // catalog entirely.
  const browseOnly = !currentBranchId;
  grid.innerHTML = productCache.map((p) => {
    const available = p.quantity_available ?? 0;
    const reserved = bookingHolds[p.product_id]?.reserved_quantity ?? 0;
    const sellable = Math.max(available - reserved, 0);
    const availClass = sellable <= 0 ? 'out' : (sellable <= (p.reorder_level || 5) ? 'low' : '');
    // Wholesale indicator (spec: staff should be able to glance at a
    // tile and quote a customer the wholesale price + the quantity it
    // kicks in at, without adding it to the cart first) -- only shown
    // for products that actually have a wholesale tier configured.
    const wholesaleRow = (p.wholesale_price_naira != null && p.wholesale_min_quantity != null)
      ? `<div class="wholesale-hint" style="font-size:11px;color:var(--ink-muted);">Wholesale: ${formatNaira(p.wholesale_price_naira)} ea. from ${p.wholesale_min_quantity}+</div>`
      : '';
    // September 2026 change spec section 9 (POS live indicator): the
    // on-hand total still shows in full while a booking sits open --
    // this just flags how many of those units aren't sellable right now.
    const bookingRow = reserved > 0
      ? `<div class="booking-hint" style="font-size:11px;color:var(--warn,#b45309);">${reserved} on open booking — only ${sellable} sellable</div>`
      : '';
    return `
      <button class="pos-tile" data-add="${p.id}" ${(sellable <= 0 || browseOnly) ? 'disabled' : ''} ${browseOnly ? 'title="Pick a specific branch to sell this"' : ''}>
        <div class="name">${escapeHtml(p.name)}${p.size ? ` <span class="category-badge" style="font-size:11px;">${escapeHtml(p.size)}</span>` : ''}</div>
        <div class="barcode">${p.barcode ? escapeHtml(p.barcode) : '—'}</div>
        <div class="price-row">
          <span class="price">${formatNaira(p.price_naira)}</span>
          <span class="avail ${availClass}">${available} left${browseOnly ? ' (all branches)' : ''}</span>
        </div>
        ${wholesaleRow}
        ${bookingRow}
      </button>`;
  }).join('');

  if (!browseOnly) {
    productCache.forEach((p) => {
      document.querySelector(`[data-add="${p.id}"]`)?.addEventListener('click', () => addToCart(p));
    });
  }
}

// -------------------------------------------------------- Barcode scan / search

let searchDebounce = null;
document.getElementById('scan-input').addEventListener('input', () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(loadProducts, 250);
});
document.getElementById('scan-input').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  const value = e.target.value.trim();
  if (!value) return;
  // Exact barcode match on Enter — the common path for a USB scanner.
  const exact = productCache.find((p) => p.barcode === value);
  if (exact) {
    addToCart(exact);
    e.target.value = '';
    loadProducts();
  }
});

// -------------------------------------------------------------------- Cart

let cart = {}; // product_id -> { product, quantity }

function addToCart(product) {
  const available = product.quantity_available ?? 0;
  const reserved = bookingHolds[product.product_id]?.reserved_quantity ?? 0;
  const sellable = Math.max(available - reserved, 0);
  const existing = cart[product.id];
  const nextQty = (existing?.quantity || 0) + 1;
  if (nextQty > sellable) {
    const reservedNote = reserved > 0 ? ` ${reserved} unit(s) are on open booking -- cancel it to free them up.` : '';
    showToast(`Only ${sellable} of "${product.name}" available.${reservedNote}`, true);
    return;
  }
  cart[product.id] = { product, quantity: nextQty };
  renderCart();
}

function changeQty(productId, delta) {
  const entry = cart[productId];
  if (!entry) return;
  const available = entry.product.quantity_available ?? 0;
  const reserved = bookingHolds[entry.product.product_id]?.reserved_quantity ?? 0;
  const sellable = Math.max(available - reserved, 0);
  const next = entry.quantity + delta;
  if (next <= 0) {
    delete cart[productId];
  } else if (next > sellable) {
    const reservedNote = reserved > 0 ? ` ${reserved} unit(s) are on open booking -- cancel it to free them up.` : '';
    showToast(`Only ${sellable} available.${reservedNote}`, true);
    return;
  } else {
    entry.quantity = next;
  }
  renderCart();
}

function removeFromCart(productId) {
  delete cart[productId];
  renderCart();
}

// "Sold Below Original Price" removed as a checkout path (September
// 2026 change spec section 3) -- all discounts now flow through
// super-admin-generated coupon codes only, so cart lines are always
// rung up at the product's normal price.

// September 2026 change spec section 22: a standing quantity-
// threshold rule on the product itself -- once a cart line's quantity
// reaches wholesale_min_quantity, it's re-priced at wholesale_price
// automatically, no coupon code needed. Supersedes the earlier idea
// of generated coupon codes for wholesale customers.
function lineUnitPrice(entry) {
  const { product, quantity } = entry;
  if (
    product.wholesale_price_naira != null &&
    product.wholesale_min_quantity != null &&
    quantity >= product.wholesale_min_quantity
  ) {
    return product.wholesale_price_naira;
  }
  return product.price_naira;
}

function cartSubtotal() {
  return Object.values(cart).reduce((sum, entry) => sum + lineUnitPrice(entry) * entry.quantity, 0);
}

function renderCart() {
  const body = document.getElementById('cart-body');
  const entries = Object.entries(cart);
  const checkoutBtn = document.getElementById('checkout-btn');
  const bookBtn = document.getElementById('book-btn');
  const openCreditBtn = document.getElementById('open-credit-btn');

  if (!entries.length) {
    body.innerHTML = '<div class="cart-empty">Cart is empty</div>';
    checkoutBtn.disabled = true;
    if (bookBtn) bookBtn.disabled = true;
    if (openCreditBtn) openCreditBtn.disabled = true;
  } else {
    body.innerHTML = entries.map(([id, entry]) => {
      const { product, quantity } = entry;
      const unitPrice = lineUnitPrice(entry);
      const isWholesale = unitPrice !== product.price_naira;
      return `
      <div class="cart-row">
        <div class="info">
          <div class="name">${escapeHtml(product.name)}${isWholesale ? ' <span class="stock-pill" style="background:var(--brand,#6b3fa0);color:#fff;">Wholesale</span>' : ''}</div>
          <div class="unit">${isWholesale
            ? `<s>${formatNaira(product.price_naira)}</s> ${formatNaira(unitPrice)} each (${product.wholesale_min_quantity}+ pieces)`
            : `${formatNaira(unitPrice)} each${product.wholesale_min_quantity ? ` — ${product.wholesale_min_quantity}+ pieces gets ${formatNaira(product.wholesale_price_naira)} each` : ''}`}</div>
        </div>
        <div class="qty">
          <button type="button" data-dec="${id}">−</button>
          <span>${quantity}</span>
          <button type="button" data-inc="${id}">+</button>
        </div>
        <div class="line-total">${formatNaira(unitPrice * quantity)}</div>
        <button type="button" class="remove" data-remove="${id}">×</button>
      </div>`;
    }).join('');

    entries.forEach(([id]) => {
      document.querySelector(`[data-inc="${id}"]`)?.addEventListener('click', () => changeQty(id, 1));
      document.querySelector(`[data-dec="${id}"]`)?.addEventListener('click', () => changeQty(id, -1));
      document.querySelector(`[data-remove="${id}"]`)?.addEventListener('click', () => removeFromCart(id));
    });
    checkoutBtn.disabled = !currentShift;
    if (bookBtn) bookBtn.disabled = !currentShift;
    if (openCreditBtn) openCreditBtn.disabled = !currentShift;
  }

  // A coupon's discount IS the discount while it's applied -- it isn't
  // stacked on top of a separately-entered manual amount. The manual
  // field gets locked to the coupon's value the moment one is applied
  // (see coupon-apply-btn below) so what's on screen always matches
  // what actually gets subtracted here.
  const discount = appliedCoupon ? 0 : Number(document.getElementById('discount-input').value || 0);
  const total = Math.max(cartSubtotal() - discount - (appliedCoupon?.discount_amount_naira || 0), 0);
  document.getElementById('cart-total').textContent = formatNaira(total);
  renderTenders(total);
  checkoutBtn.disabled = checkoutBtn.disabled || !isFullyTendered(total);
}

// ------------------------------------------------------------- Tenders

let tenders = []; // { method, amount_naira }

function tenderedTotal() {
  return tenders.reduce((t, x) => t + x.amount_naira, 0);
}

function isFullyTendered(total) {
  // A tender that exceeds the total is fine -- that's a customer paying
  // with a bigger note than the item costs (e.g. ₦500 for a ₦250 item).
  // Checkout should never be blocked on that; the excess becomes change
  // due, handled after checkout completes (see openGiveChangePrompt).
  // Only an actual shortfall keeps checkout disabled.
  return tenders.length > 0 && tenderedTotal() - total > -0.5;
}

function renderTenders(total) {
  const list = document.getElementById('tender-list');
  list.innerHTML = tenders.map((t, i) => `
    <div class="row"><span style="text-transform:capitalize;">${t.method}</span>
      <span>${formatNaira(t.amount_naira)} <a data-remove-tender="${i}" role="button" style="margin-left:8px;cursor:pointer;">Remove</a></span>
    </div>`).join('');
  tenders.forEach((_, i) => {
    document.querySelector(`[data-remove-tender="${i}"]`)?.addEventListener('click', (e) => {
      e.preventDefault();
      tenders.splice(i, 1);
      renderCart();
    });
  });
  const remaining = total - tenderedTotal();
  const remainingEl = document.getElementById('tender-remaining');
  const remainingLabelEl = document.getElementById('tender-remaining-label');
  remainingEl.textContent = formatNaira(Math.abs(remaining));
  remainingEl.style.color = remaining > 0.5 ? 'var(--danger)' : (remaining < -0.5 ? 'var(--warning, orange)' : 'var(--success, green)');
  // Relabel so it reads "Change due" once the tender exceeds the total,
  // instead of a confusing "Remaining" that goes negative.
  if (remainingLabelEl) remainingLabelEl.textContent = remaining < -0.5 ? 'Change due' : 'Remaining';
}

document.getElementById('tender-add-btn').addEventListener('click', () => {
  const method = document.getElementById('tender-method-select').value;
  const amount = Number(document.getElementById('tender-amount-input').value || 0);
  if (amount <= 0) return;
  tenders.push({ method, amount_naira: amount });
  document.getElementById('tender-amount-input').value = '';
  renderCart();
});

document.getElementById('discount-input').addEventListener('input', renderCart);
document.getElementById('clear-cart').addEventListener('click', (e) => {
  e.preventDefault();
  cart = {};
  tenders = [];
  document.getElementById('discount-input').value = 0;
  clearAppliedCoupon();
  renderCart();
});

// ------------------------------------------------------------------ Coupons

let appliedCoupon = null; // { code, discount_amount_naira }

function clearAppliedCoupon() {
  appliedCoupon = null;
  document.getElementById('coupon-applied-row').style.display = 'none';
  document.getElementById('coupon-input').value = '';
  // The discount field is never manually editable -- it stays locked
  // whether or not a coupon is currently applied. It only ever gets
  // a value from a successfully-applied coupon code (see
  // coupon-apply-btn below), never from direct staff input.
  const discountInput = document.getElementById('discount-input');
  discountInput.disabled = true;
  discountInput.value = 0;
}

document.getElementById('coupon-apply-btn').addEventListener('click', async () => {
  const code = document.getElementById('coupon-input').value.trim().toUpperCase();
  if (!code) return;
  try {
    const coupons = await Api.listCoupons();
    const match = coupons.find((c) => c.code.toUpperCase() === code && c.status === 'active');
    if (!match) {
      showToast('Discount code not found or not active.', true);
      return;
    }
    appliedCoupon = { code: match.code, discount_amount_naira: match.discount_amount_naira };
    document.getElementById('coupon-applied-code').textContent = match.code;
    document.getElementById('coupon-applied-amount').textContent = formatNaira(match.discount_amount_naira);
    document.getElementById('coupon-applied-row').style.display = '';
    // The coupon amount now IS the discount -- reflect it straight into
    // the discount field and lock it so it can't be double-stacked with
    // a separately-entered manual discount.
    const discountInput = document.getElementById('discount-input');
    discountInput.value = match.discount_amount_naira;
    discountInput.disabled = true;
    renderCart();
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('coupon-remove').addEventListener('click', (e) => {
  e.preventDefault();
  clearAppliedCoupon();
  renderCart();
});

// -------------------------------------------------------- Hold/resume sale
//
// Held sales are in-progress carts, not completed transactions --
// stored on this device only, never sent to the server, same model as
// desktop's local_db/held_sales.py. They don't survive a browser data
// wipe or follow the cashier to another device; that's an accepted
// trade-off for something that's meant to be a quick "serve someone
// else, come back to this in a minute" pause, not a durable record.

const HELD_SALES_KEY = `pos_held_sales_${currentUser?.id || 'anon'}`;

function getHeldSales() {
  try {
    return JSON.parse(localStorage.getItem(HELD_SALES_KEY) || '[]');
  } catch {
    return [];
  }
}

function setHeldSales(list) {
  localStorage.setItem(HELD_SALES_KEY, JSON.stringify(list));
  document.getElementById('held-count').textContent = list.length;
}

document.getElementById('hold-sale-btn').addEventListener('click', () => {
  const entries = Object.entries(cart);
  if (!entries.length) {
    showToast('Cart is empty.', true);
    return;
  }
  const held = getHeldSales();
  held.push({
    id: crypto.randomUUID(),
    heldAt: new Date().toISOString(),
    cart,
    discount: Number(document.getElementById('discount-input').value || 0),
    coupon: appliedCoupon,
  });
  setHeldSales(held);
  cart = {};
  document.getElementById('discount-input').value = 0;
  clearAppliedCoupon();
  renderCart();
  showToast('Sale held.');
});

function renderHeldSalesList() {
  const held = getHeldSales();
  const container = document.getElementById('held-sales-list');
  if (!held.length) {
    container.innerHTML = '<p style="color:var(--ink-faint);">No held sales.</p>';
    return;
  }
  container.innerHTML = held.map((h) => {
    const itemCount = Object.values(h.cart).reduce((n, l) => n + l.quantity, 0);
    return `
      <div class="cart-row">
        <div class="info">
          <div class="name">${itemCount} item${itemCount === 1 ? '' : 's'}</div>
          <div class="unit">Held ${new Date(h.heldAt).toLocaleTimeString('en-NG', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Africa/Lagos' })}</div>
        </div>
        <button type="button" class="btn btn-ghost btn-sm" data-resume="${h.id}">Resume</button>
        <button type="button" class="remove" data-discard="${h.id}">×</button>
      </div>`;
  }).join('');

  held.forEach((h) => {
    document.querySelector(`[data-resume="${h.id}"]`)?.addEventListener('click', () => {
      cart = h.cart;
      const discountInput = document.getElementById('discount-input');
      appliedCoupon = h.coupon || null;
      // Discount field stays locked either way -- a held sale from
      // before this field was locked down might carry an old manual
      // h.discount value, but it's only ever displayed here, never
      // re-enabled for editing.
      discountInput.disabled = true;
      if (appliedCoupon) {
        document.getElementById('coupon-applied-code').textContent = appliedCoupon.code;
        document.getElementById('coupon-applied-amount').textContent = formatNaira(appliedCoupon.discount_amount_naira);
        document.getElementById('coupon-applied-row').style.display = '';
        discountInput.value = appliedCoupon.discount_amount_naira;
      } else {
        document.getElementById('coupon-applied-row').style.display = 'none';
        discountInput.value = h.discount || 0;
      }
      setHeldSales(held.filter((x) => x.id !== h.id));
      renderCart();
      document.getElementById('resume-sale-backdrop').classList.remove('visible');
    });
    document.querySelector(`[data-discard="${h.id}"]`)?.addEventListener('click', () => {
      setHeldSales(held.filter((x) => x.id !== h.id));
      renderHeldSalesList();
    });
  });
}

document.getElementById('resume-sale-btn').addEventListener('click', () => {
  renderHeldSalesList();
  document.getElementById('resume-sale-backdrop').classList.add('visible');
});
document.getElementById('resume-sale-cancel').addEventListener('click', () => {
  document.getElementById('resume-sale-backdrop').classList.remove('visible');
});

setHeldSales(getHeldSales()); // init the "Resume held (N)" counter on load

// ---------------------------------------------------------------- Checkout

// Step 1: validate + build a local preview of what checkout would produce,
// then show it for confirmation. Nothing is sent to the server yet -- a
// stray tap here only opens a preview, it never completes a sale.
document.getElementById('checkout-btn').addEventListener('click', () => {
  const entries = Object.entries(cart);
  if (!entries.length) return;
  if (!currentBranchId) {
    showToast('Pick a specific branch on the Dashboard to complete a sale — "All Branches" is view-only.', true);
    return;
  }
  if (!currentShift) {
    showToast('Open a shift before checking out.', true);
    return;
  }
  const missingVariant = entries.find(([, { product }]) => !product.product_id);
  if (missingVariant) {
    showToast(`"${missingVariant[1].product.name}" is unavailable — remove it to continue.`, true);
    return;
  }
  const discountNow = appliedCoupon ? 0 : Number(document.getElementById('discount-input').value || 0);
  const totalNow = Math.max(cartSubtotal() - discountNow - (appliedCoupon?.discount_amount_naira || 0), 0);
  if (!isFullyTendered(totalNow)) {
    showToast('Add payment tender(s) that add up to the total before checking out.', true);
    return;
  }

  openPreCheckoutConfirm();
});

function buildCheckoutPreview() {
  const entries = Object.entries(cart);
  const discountNow = appliedCoupon ? appliedCoupon.discount_amount_naira : Number(document.getElementById('discount-input').value || 0);
  const subtotal = cartSubtotal();
  const totalNow = Math.max(subtotal - discountNow, 0);
  const paidNow = tenders.reduce((sum, t) => sum + Number(t.amount_naira || 0), 0);
  return {
    receipt_number: 'PREVIEW',
    created_at: new Date().toISOString(),
    status: 'draft',
    total_naira: totalNow,
    paid_naira: paidNow,
    discount_naira: discountNow,
    payment_method: tenders.length === 1 ? tenders[0].method : 'split',
    payments: tenders,
    items: entries.map(([, entry]) => ({
      product_name: entry.product.name,
      quantity: entry.quantity,
      unit_price_naira: lineUnitPrice(entry),
      unit_label: entry.product.unit_label,
      line_total_naira: lineUnitPrice(entry) * entry.quantity,
    })),
  };
}

const preCheckoutBackdrop = document.getElementById('pre-checkout-confirm-backdrop');

function openPreCheckoutConfirm() {
  const branch = posBranchCache.find((b) => b.id === currentBranchId);
  const branding = StoreBranding.current();
  if (branch) branding.name = branch.name || branding.name;
  Receipt.render(document.getElementById('pre-checkout-receipt-sheet'), buildCheckoutPreview(), branding);
  preCheckoutBackdrop.classList.add('visible');
}

document.getElementById('pre-checkout-cancel').addEventListener('click', () => {
  preCheckoutBackdrop.classList.remove('visible');
});

// Step 2: only this explicit confirm click actually finalizes the sale.
document.getElementById('pre-checkout-confirm').addEventListener('click', async () => {
  const entries = Object.entries(cart);
  const discountNow = appliedCoupon ? 0 : Number(document.getElementById('discount-input').value || 0);

  const btn = document.getElementById('pre-checkout-confirm');
  btn.disabled = true;
  const originalBtnText = btn.textContent;
  btn.textContent = 'Processing…';

  try {
    if (appliedCoupon) {
      const freshCoupons = await Api.listCoupons();
      const stillValid = freshCoupons.some((c) => c.code === appliedCoupon.code && c.status === 'active');
      if (!stillValid) {
        showToast('This discount code has expired since it was applied. Please ask for a new code.', true);
        clearAppliedCoupon();
        renderCart();
        btn.disabled = false;
        btn.textContent = originalBtnText;
        return;
      }
    }
    const totalDiscount = appliedCoupon ? appliedCoupon.discount_amount_naira : discountNow;
    const sale = await Api.checkout({
      branch_id: currentBranchId,
      channel: 'pos',
      local_ref_id: crypto.randomUUID(),
      shift_id: currentShift.id,
      payments: tenders,
      discount_naira: totalDiscount,
      items: entries.map(([, entry]) => ({
        product_id: entry.product.product_id,
        quantity: entry.quantity,
        unit_price_naira: lineUnitPrice(entry),
      })),
    });

    if (appliedCoupon) {
      try {
        await Api.redeemCoupon({
          code: appliedCoupon.code,
          sale_id: sale.id,
          sale_total_naira: sale.total_naira,
        });
      } catch (err) {
        // Sale already succeeded -- a coupon redeem failure shouldn't look
        // like the whole checkout failed. Surface it separately.
        showToast(`Sale completed, but the discount code could not be redeemed: ${err.message}`, true);
      }
    }

    preCheckoutBackdrop.classList.remove('visible');
    showToast('Sale completed.');
    cart = {};
    tenders = [];
    document.getElementById('discount-input').value = 0;
    document.getElementById('scan-input').value = '';
    clearAppliedCoupon();
    renderCart();
    loadProducts();
    openPostCheckoutReceipt(sale);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirm & complete checkout';
  }
});

// ------------------------------------------------------------------ Booking

const bookingBackdrop = document.getElementById('booking-modal-backdrop');

document.getElementById('book-btn').addEventListener('click', () => {
  const entries = Object.entries(cart);
  if (!entries.length) return;
  if (!currentBranchId) {
    showToast('Pick a specific branch on the Dashboard to take a booking — "All Branches" is view-only.', true);
    return;
  }
  if (!currentShift) {
    showToast('Open a shift before taking a booking.', true);
    return;
  }
  const missingVariant = entries.find(([, { product }]) => !product.product_id);
  if (missingVariant) {
    showToast(`"${missingVariant[1].product.name}" is unavailable — remove it to continue.`, true);
    return;
  }
  document.getElementById('booking-error').classList.remove('visible');
  document.getElementById('booking-customer-name').value = '';
  document.getElementById('booking-customer-phone').value = '';
  document.getElementById('booking-deposit').value = '';
  document.getElementById('booking-pickup-note').value = '';
  bookingBackdrop.classList.add('visible');
});

document.getElementById('booking-cancel').addEventListener('click', () => {
  bookingBackdrop.classList.remove('visible');
});

// Step 1: validate the booking form, then show a receipt-style preview for
// confirmation. The booking is not opened yet -- only the second, explicit
// confirm click (below) calls the API.
let pendingBooking = null;

document.getElementById('booking-confirm').addEventListener('click', () => {
  const errorEl = document.getElementById('booking-error');
  errorEl.classList.remove('visible');

  const entries = Object.entries(cart);
  // Full legal name + phone are mandatory before a booking can be
  // opened -- checked client-side for a fast error, but the API
  // enforces this too (spec: "it should be a must that you must take
  // the customer's full legal name and the phone number").
  const customerName = document.getElementById('booking-customer-name').value.trim();
  const customerPhone = document.getElementById('booking-customer-phone').value.trim();
  if (!customerName || !customerPhone) {
    errorEl.textContent = "The customer's full legal name and phone number are required to open a booking.";
    errorEl.classList.add('visible');
    return;
  }
  const depositNaira = Number(document.getElementById('booking-deposit').value || 0);
  if (!depositNaira || depositNaira <= 0) {
    errorEl.textContent = 'Enter a deposit amount.';
    errorEl.classList.add('visible');
    return;
  }
  const itemTotal = cartSubtotal();
  const discountNaira = appliedCoupon ? appliedCoupon.discount_amount_naira : 0;
  const discountedTotal = itemTotal - discountNaira;
  if (depositNaira > discountedTotal) {
    errorEl.textContent = `Deposit can't exceed the item total after discount (${formatNaira(discountedTotal)}).`;
    errorEl.classList.add('visible');
    return;
  }

  pendingBooking = {
    branch_id: currentBranchId,
    local_ref_id: crypto.randomUUID(),
    customer_full_name: customerName,
    customer_phone: customerPhone,
    deposit_amount_naira: depositNaira,
    deposit_method: document.getElementById('booking-deposit-method').value,
    pickup_note: document.getElementById('booking-pickup-note').value || null,
    discount_naira: discountNaira,
    items: entries.map(([, { product, quantity }]) => ({
      product_id: product.product_id,
      quantity,
      unit_price_naira: product.price_naira,
    })),
  };

  const preview = {
    receipt_number: 'PREVIEW',
    created_at: new Date().toISOString(),
    status: 'booking',
    total_naira: discountedTotal,
    discount_naira: discountNaira,
    paid_naira: depositNaira,
    payment_method: pendingBooking.deposit_method,
    customer_full_name: customerName,
    customer_phone: customerPhone,
    pickup_note: pendingBooking.pickup_note,
    items: entries.map(([, { product, quantity }]) => ({
      product_name: product.name,
      quantity,
      unit_price_naira: product.price_naira,
      unit_label: product.unit_label,
      line_total_naira: product.price_naira * quantity,
    })),
  };
  const branch = posBranchCache.find((b) => b.id === currentBranchId);
  const branding = StoreBranding.current();
  if (branch) branding.name = branch.name || branding.name;
  Receipt.render(document.getElementById('pre-booking-receipt-sheet'), preview, branding);
  preBookingBackdrop.classList.add('visible');
});

const preBookingBackdrop = document.getElementById('pre-booking-confirm-backdrop');

document.getElementById('pre-booking-cancel').addEventListener('click', () => {
  preBookingBackdrop.classList.remove('visible');
});

// Step 2: only this explicit confirm click actually opens the booking.
document.getElementById('pre-booking-confirm').addEventListener('click', async () => {
  if (!pendingBooking) return;
  const errorEl = document.getElementById('booking-error');
  const btn = document.getElementById('pre-booking-confirm');
  btn.disabled = true;
  btn.textContent = 'Booking…';

  try {
    const booking = await Api.createBooking(pendingBooking);

    if (appliedCoupon) {
      try {
        await Api.redeemCoupon({
          code: appliedCoupon.code,
          sale_id: booking.id,
          sale_total_naira: booking.total_naira,
        });
      } catch (err) {
        // Booking already succeeded -- a coupon redeem failure shouldn't
        // look like the whole booking failed. Surface it separately, same
        // as the checkout flow does.
        showToast(`Booking confirmed, but the discount code could not be redeemed: ${err.message}`, true);
      }
    }

    showToast('Booking confirmed — item reserved.');
    cart = {};
    tenders = [];
    document.getElementById('discount-input').value = 0;
    document.getElementById('scan-input').value = '';
    clearAppliedCoupon();
    renderCart();
    loadProducts();
    pendingBooking = null;
    preBookingBackdrop.classList.remove('visible');
    bookingBackdrop.classList.remove('visible');
    openPostCheckoutReceipt(booking);
  } catch (err) {
    preBookingBackdrop.classList.remove('visible');
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirm booking';
  }
});

// -------------------------------------------------------------- Open Credit
// Customer takes the cart now, pays later (or partly now). The backend
// /credit endpoint is single-product per call (see api/api/v1/routers/
// credit.py::create_credit), so one Credit record is created per cart
// line, all sharing the same customer/branch/deposit-method -- then
// combined into one physical ticket via Receipt.renderCreditBatch
// (already built for exactly this in js/receipt.js).

const openCreditBackdrop = document.getElementById('open-credit-backdrop');
const preOpenCreditBackdrop = document.getElementById('pre-open-credit-confirm-backdrop');
const postOpenCreditBackdrop = document.getElementById('post-open-credit-receipt-backdrop');
let pendingOpenCredit = null;

document.getElementById('open-credit-btn').addEventListener('click', () => {
  const entries = Object.entries(cart);
  if (!entries.length) return;
  if (!currentBranchId) {
    showToast('Pick a specific branch on the Dashboard to open credit — "All Branches" is view-only.', true);
    return;
  }
  if (!currentShift) {
    showToast('Open a shift before opening credit.', true);
    return;
  }
  const missingVariant = entries.find(([, { product }]) => !product.product_id);
  if (missingVariant) {
    showToast(`"${missingVariant[1].product.name}" is unavailable — remove it to continue.`, true);
    return;
  }
  document.getElementById('open-credit-error').classList.remove('visible');
  document.getElementById('open-credit-customer-name').value = '';
  document.getElementById('open-credit-customer-phone').value = '';
  document.getElementById('open-credit-customer-address').value = '';
  document.getElementById('open-credit-amount-paid').value = '0';
  openCreditBackdrop.classList.add('visible');
});

document.getElementById('open-credit-cancel').addEventListener('click', () => {
  openCreditBackdrop.classList.remove('visible');
});

// Splits a single deposit across cart lines in order (waterfall) --
// simplest allocation that keeps each line's own amount_paid_naira <=
// its amount_naira, which the backend requires per-record.
function allocateDeposit(lines, depositNaira) {
  let remaining = depositNaira;
  return lines.map((line) => {
    const netAmount = line.amount_naira - (line.discount_naira || 0);
    const applied = Math.round(Math.min(remaining, netAmount) * 100) / 100;
    remaining = Math.round((remaining - applied) * 100) / 100;
    return { ...line, amount_paid_naira: applied };
  });
}

document.getElementById('open-credit-review').addEventListener('click', () => {
  const errorEl = document.getElementById('open-credit-error');
  errorEl.classList.remove('visible');

  const entries = Object.entries(cart);
  const customerName = document.getElementById('open-credit-customer-name').value.trim();
  const customerPhone = document.getElementById('open-credit-customer-phone').value.trim();
  if (!customerName || !customerPhone) {
    errorEl.textContent = "The customer's full legal name and phone number are required to open credit.";
    errorEl.classList.add('visible');
    return;
  }
  const itemTotal = cartSubtotal();
  const discountNaira = appliedCoupon ? appliedCoupon.discount_amount_naira : 0;
  const discountedTotal = itemTotal - discountNaira;
  const depositNaira = Math.max(0, Number(document.getElementById('open-credit-amount-paid').value || 0));
  if (depositNaira > discountedTotal) {
    errorEl.textContent = `Amount paid can't exceed the item total after discount (${formatNaira(discountedTotal)}).`;
    errorEl.classList.add('visible');
    return;
  }
  const customerAddress = document.getElementById('open-credit-customer-address').value.trim() || null;
  const paymentMethod = document.getElementById('open-credit-payment-method').value;

  const lines = entries.map(([, { product, quantity }]) => ({
    branch_id: currentBranchId,
    customer_full_name: customerName,
    customer_phone: customerPhone,
    customer_address: customerAddress,
    category_id: product.category_id || null,
    product_id: product.product_id,
    quantity,
    amount_naira: lineUnitPrice({ product, quantity }) * quantity,
    discount_naira: 0,
    payment_method: paymentMethod,
  }));

  // A coupon can only be redeemed against one Sale record, and each
  // Open Credit line is its own Sale -- so the whole discount lands on
  // whichever single line is large enough to absorb it (the highest-
  // value line), rather than being split across lines.
  let discountedLineIndex = -1;
  if (discountNaira > 0) {
    discountedLineIndex = lines.reduce(
      (maxIdx, line, idx) => (line.amount_naira > lines[maxIdx].amount_naira ? idx : maxIdx),
      0,
    );
    if (discountNaira > lines[discountedLineIndex].amount_naira) {
      errorEl.textContent = `This discount code's amount (${formatNaira(discountNaira)}) exceeds the largest item's price -- it can't be applied to Open Credit.`;
      errorEl.classList.add('visible');
      return;
    }
    lines[discountedLineIndex].discount_naira = discountNaira;
  }

  pendingOpenCredit = {
    customerName,
    customerPhone,
    lines: allocateDeposit(lines, depositNaira),
    depositNaira,
    itemTotal,
    discountNaira,
    discountedTotal,
    discountedLineIndex,
    paymentMethod,
  };

  const preview = {
    receipt_number: 'PREVIEW',
    created_at: new Date().toISOString(),
    status: 'credit',
    total_naira: discountedTotal,
    discount_naira: discountNaira,
    paid_naira: depositNaira,
    payment_method: paymentMethod,
    customer_full_name: customerName,
    customer_phone: customerPhone,
    items: entries.map(([, { product, quantity }]) => ({
      product_name: product.name,
      quantity,
      unit_price_naira: lineUnitPrice({ product, quantity }),
      unit_label: product.unit_label,
      line_total_naira: lineUnitPrice({ product, quantity }) * quantity,
    })),
  };
  const branch = posBranchCache.find((b) => b.id === currentBranchId);
  const branding = StoreBranding.current();
  if (branch) branding.name = branch.name || branding.name;
  Receipt.render(document.getElementById('pre-open-credit-receipt-sheet'), preview, branding);
  openCreditBackdrop.classList.remove('visible');
  preOpenCreditBackdrop.classList.add('visible');
});

document.getElementById('pre-open-credit-cancel').addEventListener('click', () => {
  preOpenCreditBackdrop.classList.remove('visible');
});

document.getElementById('pre-open-credit-confirm').addEventListener('click', async () => {
  if (!pendingOpenCredit) return;
  const errorEl = document.getElementById('open-credit-error');
  const btn = document.getElementById('pre-open-credit-confirm');
  btn.disabled = true;
  btn.textContent = 'Opening…';

  const createdCredits = [];
  try {
    // Sequential, not parallel -- each call deducts stock immediately
    // (spec: "Open Credit must deduct inventory immediately"), and if
    // one line runs out of stock partway through, we want the error to
    // clearly identify which line failed rather than a jumbled
    // Promise.all rejection.
    for (const line of pendingOpenCredit.lines) {
      const { amount_naira, amount_paid_naira, payment_method, ...rest } = line;
      const credit = await Api.createCredit({
        ...rest,
        amount_naira,
        amount_paid_naira,
        payment_method,
      });
      createdCredits.push(credit);
    }

    if (appliedCoupon && pendingOpenCredit.discountedLineIndex >= 0) {
      const discountedCredit = createdCredits[pendingOpenCredit.discountedLineIndex];
      const discountedLine = pendingOpenCredit.lines[pendingOpenCredit.discountedLineIndex];
      try {
        await Api.redeemCoupon({
          code: appliedCoupon.code,
          sale_id: discountedCredit.sale_id,
          sale_total_naira: discountedLine.amount_naira - pendingOpenCredit.discountNaira,
        });
      } catch (err) {
        // Credit already succeeded -- a coupon redeem failure shouldn't
        // look like the whole credit failed. Surface it separately, same
        // as checkout/booking do.
        showToast(`Credit opened, but the discount code could not be redeemed: ${err.message}`, true);
      }
    }

    showToast('Credit opened — item(s) deducted from inventory.');
    cart = {};
    tenders = [];
    document.getElementById('discount-input').value = 0;
    document.getElementById('scan-input').value = '';
    clearAppliedCoupon();
    renderCart();
    loadProducts();

    const branch = posBranchCache.find((b) => b.id === currentBranchId);
    const branding = StoreBranding.current();
    if (branch) branding.name = branch.name || branding.name;
    Receipt.renderCreditBatch(
      document.getElementById('post-open-credit-receipt-sheet'),
      createdCredits,
      pendingOpenCredit.depositNaira,
      branding,
    );
    pendingOpenCredit = null;
    preOpenCreditBackdrop.classList.remove('visible');
    postOpenCreditBackdrop.classList.add('visible');
  } catch (err) {
    preOpenCreditBackdrop.classList.remove('visible');
    if (createdCredits.length) {
      // Some lines already succeeded (stock already deducted for those)
      // before this one failed -- surface that plainly rather than
      // implying nothing happened.
      showToast(
        `${createdCredits.length} item(s) were already put on credit before this failed: ${err.message}. Check Credit List before retrying the rest.`,
        true,
      );
    } else {
      errorEl.textContent = err.message;
      errorEl.classList.add('visible');
      openCreditBackdrop.classList.add('visible');
    }
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirm & open credit';
  }
});

document.getElementById('post-open-credit-skip').addEventListener('click', () => {
  postOpenCreditBackdrop.classList.remove('visible');
});
document.getElementById('post-open-credit-print').addEventListener('click', () => window.print());

// ------------------------------------------------------------------ Shift

let currentShift = null;

const shiftStatusPill = document.getElementById('shift-status-pill');
const shiftActionBtn = document.getElementById('shift-action-btn');
const openShiftBackdrop = document.getElementById('open-shift-backdrop');
const openShiftForm = document.getElementById('open-shift-form');
const openShiftError = document.getElementById('open-shift-error');
const closeShiftBackdrop = document.getElementById('close-shift-backdrop');
const closeShiftForm = document.getElementById('close-shift-form');
const closeShiftError = document.getElementById('close-shift-error');
const pendingClosureBackdrop = document.getElementById('pending-closure-backdrop');
const pendingClosureForm = document.getElementById('pending-closure-form');
const pendingClosureError = document.getElementById('pending-closure-error');
let pendingClosureShiftId = null;

// Blocks opening a shift (and therefore ringing up any sale) until a
// shift the system auto-closed at midnight has its real counted cash
// confirmed. Checked as soon as POS loads, not just when the cashier
// tries to open a shift, so the prompt appears immediately (spec:
// "once they just click on the POS icon, the close shift should come
// up, telling them they have to close their yesterday shift").
async function checkPendingClosure() {
  try {
    const pending = await Api.getPendingShiftClosure();
    if (pending) {
      pendingClosureShiftId = pending.shift.id;
      document.getElementById('pending-closure-sub').textContent =
        `You didn't close your shift from ${new Date(pending.opened_at_date).toLocaleDateString('en-NG', { dateStyle: 'long', timeZone: 'UTC' })} -- it was closed automatically at midnight. Enter what was actually counted in the drawer at close to continue.`;
      pendingClosureForm.reset();
      pendingClosureError.classList.remove('visible');
      pendingClosureBackdrop.classList.add('visible');
      return true;
    }
  } catch (err) { /* non-fatal -- POST /shifts still enforces this server-side */ }
  return false;
}

pendingClosureForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await Api.closeShift(pendingClosureShiftId, Number(document.getElementById('pending-closure-counted').value || 0));
    pendingClosureBackdrop.classList.remove('visible');
    pendingClosureShiftId = null;
    showToast('Yesterday\'s shift confirmed.');
    loadCurrentShift();
  } catch (err) {
    pendingClosureError.textContent = err.message;
    pendingClosureError.classList.add('visible');
  }
});

function renderShiftStatus() {
  shiftStatusPill.style.display = '';
  shiftActionBtn.style.display = '';
  const checkoutBtn = document.getElementById('checkout-btn');
  const hint = document.getElementById('checkout-hint');

  if (currentShift) {
    shiftStatusPill.className = 'status-pill online';
    shiftStatusPill.innerHTML = `<span class="dot"></span>Shift open · ${formatNaira(currentShift.opening_cash_naira)} opening cash`;
    shiftActionBtn.textContent = 'Close shift';
    shiftActionBtn.onclick = openCloseShiftModal;
    hint.textContent = '';
  } else {
    shiftStatusPill.className = 'status-pill offline';
    shiftStatusPill.innerHTML = '<span class="dot"></span>No shift open';
    shiftActionBtn.textContent = 'Open shift';
    shiftActionBtn.onclick = () => {
      openShiftForm.reset();
      openShiftError.classList.remove('visible');
      openShiftBackdrop.classList.add('visible');
    };
    hint.textContent = 'Open a shift before checking out.';
  }
  // Re-evaluate checkout eligibility (needs both an open shift and items in cart).
  renderCart();
}

async function loadCurrentShift() {
  const isPending = await checkPendingClosure();
  if (isPending) {
    currentShift = null;
    renderShiftStatus();
    return;
  }
  try {
    currentShift = await Api.getCurrentShift();
  } catch (err) {
    currentShift = null;
  }
  renderShiftStatus();
}

openShiftForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!currentBranchId) {
    openShiftError.textContent = 'Pick a branch first.';
    openShiftError.classList.add('visible');
    return;
  }
  try {
    currentShift = await Api.openShift({
      branch_id: currentBranchId,
      opening_cash_naira: Number(document.getElementById('opening-cash').value || 0),
    });
    openShiftBackdrop.classList.remove('visible');
    showToast('Shift opened.');
    renderShiftStatus();
  } catch (err) {
    openShiftError.textContent = err.message;
    openShiftError.classList.add('visible');
  }
});

async function openCloseShiftModal() {
  if (!currentShift) return;
  closeShiftForm.reset();
  closeShiftError.classList.remove('visible');
  document.getElementById('close-shift-opening').textContent = '—';
  document.getElementById('close-shift-cash-sales').textContent = '—';
  document.getElementById('close-shift-expected').textContent = '—';
  closeShiftBackdrop.classList.add('visible');
  try {
    const summary = await Api.shiftCashSummary(currentShift.id);
    document.getElementById('close-shift-opening').textContent = formatNaira(summary.opening_cash_naira);
    document.getElementById('close-shift-cash-sales').textContent = formatNaira(summary.cash_sales_naira);
    document.getElementById('close-shift-expected').textContent = formatNaira(summary.expected_naira);
  } catch (err) { /* non-fatal — form still usable without the summary */ }
}

document.getElementById('close-shift-cancel').addEventListener('click', () => {
  closeShiftBackdrop.classList.remove('visible');
});

closeShiftForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await Api.closeShift(currentShift.id, Number(document.getElementById('closing-counted').value || 0));
    currentShift = null;
    closeShiftBackdrop.classList.remove('visible');
    showToast('Shift closed.');
    renderShiftStatus();
  } catch (err) {
    closeShiftError.textContent = err.message;
    closeShiftError.classList.add('visible');
  }
});

// ---------------------------------------------------------- Post-checkout receipt

const postCheckoutBackdrop = document.getElementById('post-checkout-receipt-backdrop');

let postCheckoutChangeDue = 0;

function openPostCheckoutReceipt(sale) {
  const branch = posBranchCache.find((b) => b.id === (sale.branch_id || currentBranchId));
  const branding = StoreBranding.current();
  if (branch) branding.name = branch.name || branding.name;

  Receipt.render(document.getElementById('post-checkout-receipt-sheet'), sale, branding);
  postCheckoutSaleId = sale.id;

  // The customer may have tendered more than the total (e.g. paid ₦500
  // cash for a ₦250 item) -- that excess is change due, computed from
  // what was actually collected (sale.paid_naira), never something the
  // cashier has to calculate or retype.
  postCheckoutChangeDue = Math.max((sale.paid_naira || 0) - (sale.total_naira || 0), 0);
  const banner = document.getElementById('post-checkout-change-banner');
  const giveChangeBtn = document.getElementById('post-checkout-owed-change');
  if (postCheckoutChangeDue > 0.5) {
    banner.textContent = `Change due to customer: ${formatNaira(postCheckoutChangeDue)}`;
    banner.style.display = '';
    giveChangeBtn.style.display = '';
  } else {
    banner.style.display = 'none';
    giveChangeBtn.style.display = 'none';
  }

  postCheckoutBackdrop.classList.add('visible');

  // Straight to the point where the cashier actually hands back change
  // -- no extra click needed to get there.
  if (postCheckoutChangeDue > 0.5) openGiveChangeModal();
}

let postCheckoutSaleId = null;
document.getElementById('post-checkout-skip').addEventListener('click', () => {
  postCheckoutBackdrop.classList.remove('visible');
});
document.getElementById('post-checkout-print').addEventListener('click', () => window.print());

// September 2026 change spec (reworked again, for speed): two
// separate places now. "Give cash now" is its own fast screen -- just
// an amount, no customer name, since cash already in the customer's
// hand needs no lookup later. Everything that *does* need tracking
// (cash to collect later, transfer, airtime) lives together on a
// second screen, since all three genuinely need a customer name.
// Every method chosen still becomes its own pending-change row tied
// to this exact sale (postCheckoutSaleId, never a bare name string)
// -- including cash handed over on the spot, which is created and
// immediately settled so it's still documented in Transactions >
// Pending Change > Settled instead of leaving no record behind.
const owedChangeBackdrop = document.getElementById('owed-change-backdrop');
const owedChangeChoiceScreen = document.getElementById('owed-change-choice-screen');
const owedChangeCashNowForm = document.getElementById('owed-change-cashnow-form');
const owedChangeTrackedForm = document.getElementById('owed-change-form');
const owedChangeRowsContainer = document.getElementById('owed-change-rows');
const owedChangeRowTemplate = document.getElementById('owed-change-row-template');

// How much of the change due is left to account for on the tracked
// screen -- equals the full change due if reached directly from the
// choice screen, or whatever's left over if the cashier already gave
// part of it as cash just now.
let owedChangeTrackedDue = 0;
// True once a cash-now amount has actually been created+settled this
// round -- once that's happened, "Back" to the choice screen no
// longer makes sense (that cash is already handed over and recorded),
// so it's hidden rather than offering an undo that doesn't exist.
let owedChangeCashAlreadyGiven = false;

// Nigerian mobile prefixes -> network, for the same auto-detect the
// server does (spec 2.3: "auto-detect network... or let cashier
// confirm it") -- shown immediately as the cashier types rather than
// waiting for the create call to come back.
const NIGERIAN_NETWORK_PREFIXES = {
  '0803': 'MTN', '0806': 'MTN', '0703': 'MTN', '0706': 'MTN', '0813': 'MTN', '0816': 'MTN', '0810': 'MTN', '0814': 'MTN', '0903': 'MTN', '0906': 'MTN', '0913': 'MTN', '0916': 'MTN',
  '0802': 'Airtel', '0808': 'Airtel', '0708': 'Airtel', '0812': 'Airtel', '0701': 'Airtel', '0902': 'Airtel', '0901': 'Airtel', '0904': 'Airtel', '0907': 'Airtel', '0912': 'Airtel',
  '0805': 'Glo', '0807': 'Glo', '0705': 'Glo', '0815': 'Glo', '0811': 'Glo', '0905': 'Glo', '0915': 'Glo',
  '0809': '9mobile', '0817': '9mobile', '0818': '9mobile', '0908': '9mobile', '0909': '9mobile',
};

function owedChangeShowScreen(screen) {
  owedChangeChoiceScreen.style.display = screen === 'choice' ? '' : 'none';
  owedChangeCashNowForm.style.display = screen === 'cashnow' ? '' : 'none';
  owedChangeTrackedForm.style.display = screen === 'tracked' ? '' : 'none';
}

function openGiveChangeModal() {
  document.getElementById('owed-change-error').classList.remove('visible');
  document.getElementById('owed-change-due-line').textContent = `Change due: ${formatNaira(postCheckoutChangeDue)}`;
  document.getElementById('owed-change-cashnow-amount').value = '';
  owedChangeCashAlreadyGiven = false;
  owedChangeTrackedDue = postCheckoutChangeDue;
  owedChangeShowScreen('choice');
  owedChangeBackdrop.classList.add('visible');
}
document.getElementById('post-checkout-owed-change').addEventListener('click', openGiveChangeModal);

document.getElementById('owed-change-choice-cash-now').addEventListener('click', () => {
  owedChangeShowScreen('cashnow');
  document.getElementById('owed-change-cashnow-amount').focus();
});

function openTrackedScreen() {
  document.getElementById('owed-change-form').reset();
  owedChangeRowsContainer.innerHTML = '';
  addOwedChangeRow();
  updateOwedChangeRemainingLine();
  document.getElementById('owed-change-back-to-choice').style.display = owedChangeCashAlreadyGiven ? 'none' : '';
  owedChangeShowScreen('tracked');
  document.getElementById('owed-change-name').focus();
}
document.getElementById('owed-change-choice-track').addEventListener('click', openTrackedScreen);

document.querySelectorAll('[data-owed-back]').forEach((btn) => {
  btn.addEventListener('click', () => owedChangeShowScreen('choice'));
});

document.getElementById('owed-change-cancel').addEventListener('click', async () => {
  // "Log as pending" exists for exactly the case where the customer
  // isn't around to give their name/phone -- but that doesn't mean
  // the money they're owed should just vanish with no trace. Whatever
  // is still unaccounted for (the full change due, or whatever's left
  // after a cash-now amount already given) gets logged as an
  // outstanding pending change with a placeholder name, so it shows
  // up in Transactions > Pending Change > Outstanding and a cashier
  // can open it later to fill in the real customer details and settle
  // it -- instead of it being marked settled, or forgotten entirely.
  const outstanding = owedChangeCashAlreadyGiven ? owedChangeTrackedDue : postCheckoutChangeDue;
  const btn = document.getElementById('owed-change-cancel');
  if (outstanding > 0.5) {
    btn.disabled = true;
    try {
      await Api.createPendingChange({
        sale_id: postCheckoutSaleId,
        customer_name: 'Customer details pending — filled at Skip/close',
        amount_naira: outstanding,
        settlement_method: 'cash',
        notes: 'Recorded via "Log as pending" because the customer was not available. Edit this record with the real name/phone and settlement method once known.',
      });
      showToast(`₦${formatNaira(outstanding)} logged as outstanding — fill in the customer's details later from Transactions > Pending Change.`);
    } catch (err) {
      const errorEl = document.getElementById('owed-change-error');
      errorEl.textContent = `Could not log the outstanding change: ${err.message}. The modal was kept open so nothing is lost — try again, or use Settle later instead.`;
      errorEl.classList.add('visible');
      btn.disabled = false;
      return;
    }
    btn.disabled = false;
  }
  owedChangeBackdrop.classList.remove('visible');
});

// -------------------------------------------------------- Fast path: cash now
document.getElementById('owed-change-cashnow-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('owed-change-error');
  errorEl.classList.remove('visible');
  const amount = Number(document.getElementById('owed-change-cashnow-amount').value || 0);
  if (!(amount > 0)) {
    errorEl.textContent = 'Enter an amount greater than ₦0.';
    errorEl.classList.add('visible');
    return;
  }
  if (amount - postCheckoutChangeDue > 0.5) {
    errorEl.textContent = `That's more than the ₦${formatNaira(postCheckoutChangeDue)} due — check the amount.`;
    errorEl.classList.add('visible');
    return;
  }
  const submitBtn = e.target.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  try {
    // Cash handed to the customer right now -- no name needed, since
    // nothing about this needs to be looked up later. Created and
    // immediately settled in the same step so it's still documented
    // in Transactions > Pending Change > Settled.
    const created = await Api.createPendingChange({
      sale_id: postCheckoutSaleId,
      customer_name: 'Cash given at checkout',
      amount_naira: amount,
      settlement_method: 'cash',
    });
    await Api.settlePendingChange(created.id, { amount_naira: amount, settlement_method: 'cash' });
    owedChangeCashAlreadyGiven = true;

    const remainder = postCheckoutChangeDue - amount;
    if (remainder <= 0.5) {
      showToast('Change given in full.');
      owedChangeBackdrop.classList.remove('visible');
    } else {
      owedChangeTrackedDue = remainder;
      showToast(`₦${formatNaira(amount)} given now — log the rest below.`);
      openTrackedScreen();
    }
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});

// ------------------------------------------------------- Tracked path: rows
function addOwedChangeRow() {
  const frag = owedChangeRowTemplate.content.cloneNode(true);
  const row = frag.querySelector('.owed-change-row');
  owedChangeRowsContainer.appendChild(row);

  const methodSelect = row.querySelector('.owed-row-method');
  const amountInput = row.querySelector('.owed-row-amount');
  const amountLabel = row.querySelector('.owed-row-amount-label');
  const amountHint = row.querySelector('.owed-row-amount-hint');
  const cashLaterFields = row.querySelector('.owed-row-cashlater-fields');
  const cashGivenInput = row.querySelector('.owed-row-cash-given');
  const transferFields = row.querySelector('.owed-row-transfer-fields');
  const airtimeFields = row.querySelector('.owed-row-airtime-fields');
  const bankSelect = row.querySelector('.owed-row-bank-name');
  const airtimePhone = row.querySelector('.owed-row-airtime-phone');
  const airtimeNetwork = row.querySelector('.owed-row-airtime-network');
  const removeBtn = row.querySelector('.owed-row-remove');

  // For "Cash — customer will collect later": since this row is the
  // only one on screen (see updateOwedChangeAddRowVisibility), the
  // full amount still to account for is owedChangeTrackedDue. As the
  // cashier types how much was already given, the remaining pickup
  // amount fills in automatically -- still a normal editable field
  // afterward, in case it needs a manual tweak.
  function recalcCashLaterAmount() {
    const given = Number(cashGivenInput.value || 0);
    const remaining = Math.max(owedChangeTrackedDue - given, 0);
    amountInput.value = String(remaining);
    updateOwedChangeRemainingLine();
  }

  methodSelect.addEventListener('change', () => {
    const method = methodSelect.value;
    const isCashLater = method === 'cash-later';
    cashLaterFields.style.display = isCashLater ? '' : 'none';
    amountHint.style.display = isCashLater ? '' : 'none';
    amountLabel.textContent = isCashLater ? 'Amount to collect later (₦) *' : 'Amount for this method (₦) *';
    transferFields.style.display = method === 'transfer' ? '' : 'none';
    airtimeFields.style.display = method === 'airtime' ? '' : 'none';
    if (method === 'transfer') populateBankSelect(bankSelect);
    if (isCashLater) recalcCashLaterAmount();
    updateOwedChangeAddRowVisibility();
  });
  cashGivenInput.addEventListener('input', recalcCashLaterAmount);
  amountInput.addEventListener('input', updateOwedChangeRemainingLine);
  airtimePhone.addEventListener('input', () => {
    const digits = airtimePhone.value.replace(/\D/g, '');
    const detected = NIGERIAN_NETWORK_PREFIXES[digits.slice(0, 4)];
    if (detected && !airtimeNetwork.value) airtimeNetwork.value = detected;
  });
  removeBtn.addEventListener('click', () => {
    row.remove();
    updateOwedChangeRowRemoveButtons();
    updateOwedChangeRemainingLine();
    updateOwedChangeAddRowVisibility();
  });

  updateOwedChangeRowRemoveButtons();
  updateOwedChangeAddRowVisibility();
  return row;
}
document.getElementById('owed-change-add-row').addEventListener('click', () => addOwedChangeRow());

// Only offer "Remove this method" once there's more than one row --
// at least one settlement method is always required.
function updateOwedChangeRowRemoveButtons() {
  const rows = owedChangeRowsContainer.querySelectorAll('.owed-change-row');
  rows.forEach((r) => {
    r.querySelector('.owed-row-remove').style.display = rows.length > 1 ? '' : 'none';
  });
}

// "Cash — customer will collect later" is a single lump sum handed
// over in full on pickup -- it can't be split across multiple
// methods, so once any row has it selected, hide the option to add
// another row. Bank transfer and airtime top-up can still be freely
// combined with each other (or more of themselves).
function updateOwedChangeAddRowVisibility() {
  const rows = owedChangeRowsContainer.querySelectorAll('.owed-change-row');
  const hasCashLater = Array.from(rows).some((r) => r.querySelector('.owed-row-method').value === 'cash-later');
  document.getElementById('owed-change-add-row').style.display = hasCashLater ? 'none' : '';
}

// Running total across every row, checked against whatever's left to
// account for (owedChangeTrackedDue -- the full change due, or
// whatever remained after a partial cash-now amount). For a
// "Cash — collect later" row, the amount already given now is money
// that's genuinely been handed over, so it counts toward this total
// too -- only the amount field alone would make the given-now portion
// look "unaccounted for" when it isn't.
function sumOwedChangeRows() {
  let sum = 0;
  owedChangeRowsContainer.querySelectorAll('.owed-change-row').forEach((row) => {
    sum += Number(row.querySelector('.owed-row-amount').value || 0);
    if (row.querySelector('.owed-row-method').value === 'cash-later') {
      sum += Number(row.querySelector('.owed-row-cash-given').value || 0);
    }
  });
  return sum;
}
function updateOwedChangeRemainingLine() {
  const sum = sumOwedChangeRows();
  const remaining = owedChangeTrackedDue - sum;
  const line = document.getElementById('owed-change-remaining-line');
  const dueText = owedChangeCashAlreadyGiven
    ? `${formatNaira(owedChangeTrackedDue)} left to account for (₦${formatNaira(postCheckoutChangeDue - owedChangeTrackedDue)} already given as cash)`
    : `${formatNaira(owedChangeTrackedDue)} to account for`;
  if (Math.abs(remaining) <= 0.5) {
    line.textContent = `Fully accounted for. (${dueText})`;
    line.style.color = 'var(--success, #2e7d32)';
  } else if (remaining > 0) {
    line.textContent = `${formatNaira(remaining)} still unaccounted for. (${dueText})`;
    line.style.color = 'var(--danger)';
  } else {
    line.textContent = `${formatNaira(-remaining)} over the amount due — reduce an amount above.`;
    line.style.color = 'var(--danger)';
  }
}

document.getElementById('owed-change-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('owed-change-error');
  const submitBtn = document.getElementById('owed-change-submit');
  errorEl.classList.remove('visible');

  const customerName = document.getElementById('owed-change-name').value.trim();
  const customerPhone = document.getElementById('owed-change-phone').value.trim() || null;
  const notes = document.getElementById('owed-change-notes').value.trim() || null;

  if (!customerName) {
    errorEl.textContent = 'Customer name is required.';
    errorEl.classList.add('visible');
    return;
  }

  const rowEls = Array.from(owedChangeRowsContainer.querySelectorAll('.owed-change-row'));
  if (!rowEls.length) {
    errorEl.textContent = 'Add at least one settlement method.';
    errorEl.classList.add('visible');
    return;
  }

  const sum = sumOwedChangeRows();
  if (Math.abs(owedChangeTrackedDue - sum) > 0.5) {
    errorEl.textContent = `The methods above must add up to ${formatNaira(owedChangeTrackedDue)}. Currently ${formatNaira(sum)}.`;
    errorEl.classList.add('visible');
    return;
  }

  // Validate each row before sending anything -- so a mistake on row 2
  // doesn't leave row 1 already created against the sale.
  const rows = [];
  for (const row of rowEls) {
    const rawMethod = row.querySelector('.owed-row-method').value;
    const amount = Number(row.querySelector('.owed-row-amount').value || 0);
    if (!rawMethod) {
      errorEl.textContent = 'Choose a settlement method for every row.';
      errorEl.classList.add('visible');
      return;
    }
    if (!(amount > 0)) {
      errorEl.textContent = 'Enter an amount greater than ₦0 for every method.';
      errorEl.classList.add('visible');
      return;
    }
    const settlementMethod = rawMethod === 'cash-later' ? 'cash' : rawMethod;
    const entry = { settlementMethod, amount };
    if (rawMethod === 'cash-later') {
      // Optional, documentary only -- e.g. change due was ₦2,400, ₦2,000
      // was already handed over, and this row's "amount" (₦400) is what
      // the customer is coming back to collect. Doesn't affect the
      // ₦-total check above, which is only ever the amount still owed.
      const givenRaw = row.querySelector('.owed-row-cash-given').value;
      const given = Number(givenRaw || 0);
      if (given < 0) {
        errorEl.textContent = 'The amount already given cannot be negative.';
        errorEl.classList.add('visible');
        return;
      }
      if (given > 0) entry.cashGivenNow = given;
    }
    if (settlementMethod === 'transfer') {
      entry.bankName = row.querySelector('.owed-row-bank-name').value;
      entry.accountNumber = row.querySelector('.owed-row-account-number').value.trim();
      entry.accountName = row.querySelector('.owed-row-account-name').value.trim();
      if (!(entry.bankName && entry.accountNumber && entry.accountName)) {
        errorEl.textContent = 'Bank name, account number, and account name are required for a transfer.';
        errorEl.classList.add('visible');
        return;
      }
    } else if (settlementMethod === 'airtime') {
      entry.airtimePhone = row.querySelector('.owed-row-airtime-phone').value.trim();
      entry.airtimeNetwork = row.querySelector('.owed-row-airtime-network').value || null;
      if (!entry.airtimePhone) {
        errorEl.textContent = 'A phone number is required for an airtime top-up.';
        errorEl.classList.add('visible');
        return;
      }
    }
    rows.push(entry);
  }

  submitBtn.disabled = true;
  try {
    for (const entry of rows) {
      const payload = {
        sale_id: postCheckoutSaleId,
        customer_name: customerName,
        customer_phone: customerPhone,
        amount_naira: entry.amount,
        notes,
        settlement_method: entry.settlementMethod,
      };
      if (entry.cashGivenNow) {
        const givenNote = `₦${formatNaira(entry.cashGivenNow)} already given now; ₦${formatNaira(entry.amount)} remaining to collect.`;
        payload.notes = notes ? `${givenNote} ${notes}` : givenNote;
      }
      if (entry.settlementMethod === 'transfer') {
        payload.bank_name = entry.bankName;
        payload.account_number = entry.accountNumber;
        payload.account_name = entry.accountName;
      } else if (entry.settlementMethod === 'airtime') {
        payload.airtime_phone_number = entry.airtimePhone;
        payload.airtime_network = entry.airtimeNetwork;
      }
      // Cash to collect later, transfer, and airtime all still need
      // Transactions > Pending Change to settle/approve them -- none
      // of these are resolved on the spot.
      await Api.createPendingChange(payload);
    }
    const msg = owedChangeCashAlreadyGiven
      ? `₦${formatNaira(postCheckoutChangeDue - owedChangeTrackedDue)} given now, the rest recorded — settle/approve later from Transactions > Pending Change.`
      : 'Change recorded — settle/approve later from Transactions > Pending Change.';
    showToast(msg);
    owedChangeBackdrop.classList.remove('visible');
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    submitBtn.disabled = false;
  }
});


// -------------------------------------------------- Shift compliance banner
// Spec section 10: a notification/warning area on the staff dashboard
// (POS is where staff actually spend their time) that stays visible
// until every pending warning is acknowledged -- it can't just be
// closed or ignored.

const complianceDetailBackdrop = document.getElementById('compliance-detail-backdrop');
let pendingComplianceEvents = [];
let activeComplianceEvent = null;

const COMPLIANCE_COPY = {
  1: {
    title: 'Shift Closing Reminder',
    body: 'Your previous shift was automatically closed because you did not manually close your shift before leaving. Please remember to manually close your shift at the end of every working day.',
  },
  2: {
    title: 'First Formal Warning',
    body: 'You have failed to manually close your POS shift on multiple occasions. Proper shift closing is required at the end of every working day. Continued failure to follow this procedure may result in further disciplinary action.',
  },
  3: {
    title: 'Repeated Shift Compliance Violation',
    body: 'You have repeatedly failed to manually close your POS shift. Your shift compliance record has been escalated for management review.',
  },
  4: {
    title: 'Final Warning',
    body: 'You have continued to fail to manually close your POS shift after prior warnings and a management review. This is a final warning.',
  },
};

async function loadComplianceNotifications() {
  const banner = document.getElementById('compliance-notifications');
  try {
    pendingComplianceEvents = await Api.myPendingComplianceEvents();
  } catch (err) {
    banner.style.display = 'none'; // e.g. logged out, or a role this doesn't apply to
    return;
  }
  if (!pendingComplianceEvents.length) {
    banner.style.display = 'none';
    banner.innerHTML = '';
    return;
  }
  banner.style.display = 'block';
  banner.innerHTML = pendingComplianceEvents.map((e) => {
    const copy = COMPLIANCE_COPY[e.level] || COMPLIANCE_COPY[1];
    return `
      <div class="error-banner visible" style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px;">
        <span>🔴 ${escapeHtml(copy.title)} — occurrence ${e.occurrence_number}. Please acknowledge this warning.</span>
        <div style="display:flex;gap:8px;flex-shrink:0;">
          <button type="button" class="btn btn-ghost btn-sm" data-compliance-view="${e.id}">View Details</button>
          <button type="button" class="btn btn-primary btn-sm" data-compliance-ack="${e.id}">Acknowledge</button>
        </div>
      </div>`;
  }).join('');

  banner.querySelectorAll('[data-compliance-view]').forEach((btn) => {
    btn.addEventListener('click', () => openComplianceDetail(btn.dataset.complianceView));
  });
  banner.querySelectorAll('[data-compliance-ack]').forEach((btn) => {
    btn.addEventListener('click', () => acknowledgeCompliance(btn.dataset.complianceAck));
  });
}

function openComplianceDetail(eventId) {
  activeComplianceEvent = pendingComplianceEvents.find((e) => e.id === eventId);
  if (!activeComplianceEvent) return;
  const copy = COMPLIANCE_COPY[activeComplianceEvent.level] || COMPLIANCE_COPY[1];
  document.getElementById('compliance-detail-title').textContent = copy.title;
  document.getElementById('compliance-detail-body').textContent = copy.body;
  document.getElementById('compliance-detail-facts').innerHTML = `
    <div style="display:flex;justify-content:space-between;"><span>Shift opened</span><strong>${new Date(activeComplianceEvent.shift_opened_at).toLocaleString()}</strong></div>
    <div style="display:flex;justify-content:space-between;"><span>Opening Cash</span><strong>${formatNaira(activeComplianceEvent.opening_cash_naira)}</strong></div>
    ${activeComplianceEvent.closing_counted_naira != null ? `<div style="display:flex;justify-content:space-between;"><span>Closing counted</span><strong>${formatNaira(activeComplianceEvent.closing_counted_naira)}</strong></div>` : ''}
    <div style="display:flex;justify-content:space-between;"><span>Occurrence</span><strong>${activeComplianceEvent.occurrence_number}</strong></div>
  `;
  document.getElementById('compliance-detail-ack').textContent = activeComplianceEvent.level === 1 ? 'I Understand / Acknowledge' : 'Acknowledge Warning';
  complianceDetailBackdrop.classList.add('visible');
}
document.getElementById('compliance-detail-later').addEventListener('click', () => {
  complianceDetailBackdrop.classList.remove('visible');
});
document.getElementById('compliance-detail-ack').addEventListener('click', async () => {
  if (!activeComplianceEvent) return;
  await acknowledgeCompliance(activeComplianceEvent.id);
  complianceDetailBackdrop.classList.remove('visible');
});

async function acknowledgeCompliance(eventId) {
  try {
    await Api.acknowledgeComplianceEvent(eventId);
    showToast('Acknowledged.');
    await loadComplianceNotifications();
  } catch (err) {
    showToast(err.message, true);
  }
}

// ------------------------------------------------------------------ Init

loadBranches().then(() => {
  loadProducts();
  loadCurrentShift();
  loadComplianceNotifications();
});