Auth.requireAuth();
Perms.applyNav();
Perms.guardPage('view_restock_list');

const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
document.getElementById('current-user-name').textContent = (currentUser && currentUser.full_name) || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser && currentUser.role] || (currentUser && currentUser.role) || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

const canManage = Perms.can('manage_restock_list');
const NAIRA = '\u20A6';
const UNIT_TEXT = { pieces: 'pieces', dozens: 'dozens', cartons: 'cartons', bags: 'bags', half_dozen: 'half dozen', half_carton: 'half carton' };

let toastTimer = null;
function showToast(message, isError) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.toggle('error', !!isError);
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 4200);
}

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function naira(amount) {
  return NAIRA + Number(amount || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-NG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Africa/Lagos' });
}

function qtyText(r) {
  // PATCH1-CUSTOMER-ORDER-FIELDS
  const piecesNote = (r.order_pieces && r.unit_label !== 'pieces') ? ' (' + r.order_pieces + ' pieces)' : '';
  if (r.unit_label === 'half_dozen' || r.unit_label === 'half_carton') return UNIT_TEXT[r.unit_label] + piecesNote;
  return r.quantity + ' ' + (UNIT_TEXT[r.unit_label] || r.unit_label) + piecesNote;
}

// WhatsApp wants an international number without "+": 0803... -> 234803...
function waNumber(phone) {
  const digits = String(phone || '').replace(/\D/g, '');
  if (digits.startsWith('234')) return digits;
  if (digits.startsWith('0')) return '234' + digits.slice(1);
  return digits;
}

function thumb(url, name) {
  if (url) {
    return '<img src="' + esc(url) + '" alt="' + esc(name) + '" data-enlarge-image="' + esc(url) + '" style="width:48px;height:48px;object-fit:cover;border-radius:10px;cursor:zoom-in;border:1px solid var(--border);" />';
  }
  return '<div title="No photo" style="width:48px;height:48px;border-radius:10px;background:var(--bg-soft,#f2f2f2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="var(--ink-faint,#9aa3b2)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="M21 15l-5-5-9 9"/></svg></div>';
}

// ------------------------------------------------------------------ state
const TABS = ['arrived', 'pending', 'collected', 'cancelled'];
let allRequests = [];
let activeTab = TABS.indexOf(location.hash.replace('#', '')) >= 0 ? location.hash.replace('#', '') : 'arrived';

function setTab(tab) {
  activeTab = tab;
  document.querySelectorAll('#cr-tabs [data-cr-tab]').forEach((b) => b.classList.toggle('active', b.dataset.crTab === tab));
  render();
}
document.getElementById('cr-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-cr-tab]');
  if (btn) { history.replaceState(null, '', '#' + btn.dataset.crTab); setTab(btn.dataset.crTab); }
});

async function load() {
  const branchId = BranchContext.get() || undefined;
  try {
    allRequests = await Api.customerRequests.list(branchId);
  } catch (err) {
    showToast(err.message || 'Could not load customer orders.', true);
    allRequests = [];
  }
  render();
}

function statusCell(r) {
  if (r.status === 'arrived') {
    // PATCH3A-BOOKING-DRIVES-ORDER
    const hold = r.booking_id
      ? '<div style="font-size:12px;color:var(--ink-faint);">Open booking holds this stock. Finish it in Transactions &gt; Open Bookings.</div>'
      : (r.needs_pieces
        ? '<div><span class="stock-pill low">Set how many pieces to hold</span></div>'
        : '<div style="font-size:12px;color:var(--ink-faint);">Holding ' + esc(r.hold_pieces) + ' piece(s) for this customer</div>');
    return '<span class="stock-pill ok">Arrived</span><div style="font-size:12px;color:var(--ink-faint);">' + esc(fmtDate(r.arrived_at)) + '</div>' + hold;
  }
  if (r.status === 'pending') {
    return '<span class="stock-pill low">Waiting for item</span><div style="font-size:12px;color:var(--ink-faint);">' +
      (r.ordered_at ? 'Sent to supplier ' + esc(fmtDate(r.ordered_at)) : 'On the restock list') + '</div>';
  }
  if (r.status === 'collected') {
    return '<span class="stock-pill ok">Collected</span><div style="font-size:12px;color:var(--ink-faint);">' + esc(fmtDate(r.collected_at)) + '</div>' +
      (r.resolution_note ? '<div style="font-size:12px;color:var(--ink-faint);">' + esc(r.resolution_note) + '</div>' : '');
  }
  const refund = Number(r.deposit_naira) === 0
    ? ''
    : (r.deposit_refunded
      ? '<div style="font-size:12px;color:var(--ink-faint);">Deposit refunded</div>'
      : '<div style="font-size:12px;color:var(--danger,#d64545);font-weight:600;">Deposit NOT refunded (' + esc(naira(r.deposit_naira)) + ' still held)</div>');
  return '<span class="stock-pill out">Cancelled</span><div style="font-size:12px;color:var(--ink-faint);">' + esc(fmtDate(r.cancelled_at)) + '</div>' + refund +
    (r.resolution_note ? '<div style="font-size:12px;color:var(--ink-faint);">' + esc(r.resolution_note) + '</div>' : '');
}

function actionsCell(r) {
  if (!canManage) return '';
  const btn = (action, label, extra) =>
    '<button type="button" class="btn ' + (extra || 'btn-ghost') + ' btn-sm" data-act="' + action + '" data-id="' + esc(r.id) + '" style="width:auto;">' + label + '</button>';
  // PATCH3A-BOOKING-DRIVES-ORDER
  if (r.status === 'arrived' && r.booking_id) {
    return '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">' +
      btn('cancel', 'Cancel order') + '</div>';
  }
  if (r.status === 'arrived') {
    return '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">' +
      btn('collect', 'Collected', 'btn-primary') + btn('hold', 'Edit hold') + btn('cancel', 'Cancel order') + '</div>';
  }
  if (r.status === 'pending') {
    return '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">' +
      btn('arrive', 'Mark arrived', 'btn-primary') + btn('cancel', 'Cancel order') + '</div>';
  }
  return '';
}

function render() {
  TABS.forEach((t) => {
    const el = document.getElementById('cr-count-' + t);
    if (el) el.textContent = allRequests.filter((r) => r.status === t).length;
  });
  const rows = allRequests.filter((r) => r.status === activeTab);
  const tbody = document.getElementById('cr-tbody');
  const table = document.getElementById('cr-table');
  const empty = document.getElementById('cr-empty');
  const emptyText = {
    arrived: 'No customers are waiting to collect right now.',
    pending: 'No customer orders are waiting for stock.',
    collected: 'No collected orders yet.',
    cancelled: 'No cancelled orders.'
  };
  if (!rows.length) {
    table.style.display = 'none';
    empty.style.display = 'block';
    document.getElementById('cr-empty-text').textContent = emptyText[activeTab];
    tbody.innerHTML = '';
    return;
  }
  table.style.display = '';
  empty.style.display = 'none';
  tbody.innerHTML = rows.map((r) => {
    const wa = waNumber(r.customer_phone);
    return '<tr>' +
      '<td class="thumb-cell">' + thumb(r.item_image_url, r.item_name) + '</td>' +
      '<td><div style="font-weight:600;">' + esc(r.item_name) + '</div><div style="font-size:12px;color:var(--ink-faint);">' + esc(qtyText(r)) + '</div>' +
        (r.customer_pieces ? '<div style="font-size:12px;color:var(--ink-faint);">Customer needs ' + esc(r.customer_pieces) + ' piece(s)</div>' : '') +
        (r.booking_id ? '<div style="font-size:12px;color:var(--ink-faint);">Booked: open booking made for this customer</div>' : ((r.status === 'arrived' && r.customer_pieces && r.resolution_note) ? '<div style="font-size:12px;color:var(--danger,#d64545);">' + esc(r.resolution_note) + '</div>' : '')) +
        (r.notes ? '<div style="font-size:12px;color:var(--ink-faint);max-width:260px;">' + esc(r.notes) + '</div>' : '') + '</td>' +
      '<td><div style="font-weight:600;">' + esc(r.customer_name) + '</div>' +
        '<div style="font-size:13px;"><a href="tel:' + esc(r.customer_phone) + '">' + esc(r.customer_phone) + '</a>' +
        (wa ? ' &middot; <a href="https://wa.me/' + esc(wa) + '" target="_blank" rel="noopener">WhatsApp</a>' : '') + '</div>' +
        (r.customer_address ? '<div style="font-size:12px;color:var(--ink-faint);max-width:240px;">' + esc(r.customer_address) + '</div>' : '') + '</td>' +
      '<td><span class="cell-strong">' + esc(naira(r.deposit_naira)) + '</span>' +
        (Number(r.deposit_naira) > 0
          ? '<div style="font-size:12px;color:var(--ink-faint);">paid ' + esc(fmtDate(r.created_at)) + (r.deposit_method ? ' &middot; ' + esc(r.deposit_method) : '') + '</div>'
          : '<div style="font-size:12px;color:var(--ink-faint);">no deposit</div>') + '</td>' +
      '<td>' + statusCell(r) + '</td>' +
      '<td>' + actionsCell(r) + '</td>' +
    '</tr>';
  }).join('');
}

// ----------------------------------------------------------------- modals
function openModal(html) {
  closeModal();
  const host = document.getElementById('cr-modal-host');
  host.innerHTML = '<div class="modal-backdrop visible" id="cr-modal-backdrop"><div class="modal" style="max-width:480px;">' + html + '</div></div>';
  document.getElementById('cr-modal-backdrop').addEventListener('click', (e) => { if (e.target.id === 'cr-modal-backdrop') closeModal(); });
}
function closeModal() { document.getElementById('cr-modal-host').innerHTML = ''; }
function byId(id) { return allRequests.find((r) => r.id === id); }

function openArriveModal(r) {
  openModal(
    '<h3>Mark arrived</h3>' +
    '<p class="modal-sub">' + esc(r.item_name) + ' for ' + esc(r.customer_name) + '</p>' +
    '<div class="field"><label>Find the matching product in inventory</label>' +
    '<input type="text" id="cr-prod-q" placeholder="Type part of the product name" value="' + esc(r.item_name) + '" autocomplete="off" />' +
    '<div id="cr-prod-results" style="margin-top:8px;max-height:180px;overflow-y:auto;"></div></div>' +
    '<p class="hint" id="cr-prod-picked" style="font-weight:600;margin:6px 0;"></p>' +
    '<div class="field"><label>Is this item already in your inventory stock?</label>' +
    '<label style="display:block;font-weight:400;"><input type="radio" name="cr-stock-mode" value="counted" checked /> Yes, it is already counted. I only need to link it.</label>' +
    '<label style="display:block;font-weight:400;"><input type="radio" name="cr-stock-mode" value="add" /> No, add it to the shelf now.</label></div>' +
    '<div id="cr-counted-fields">' +
    '<div class="field"><label for="cr-hold">Pieces to hold for this customer</label>' +
    '<input type="number" id="cr-hold" min="0" value="' + esc(defaultPieces(r)) + '" /></div>' +
    '<p class="hint">Held pieces cannot be sold at POS until the customer collects or the order is cancelled. You can arrive without a product, but then nothing is held.</p></div>' +
    '<div id="cr-add-fields" style="display:none;">' +
    '<div class="field"><label for="cr-add-pieces">How many pieces arrived?</label>' +
    '<input type="number" id="cr-add-pieces" min="1" value="' + esc(r.order_pieces || defaultPieces(r) || '') + '" /></div>' +
    '<div class="field"><label for="cr-add-price">Selling price per piece (only needed if this item has no price yet)</label>' +
    '<input type="number" id="cr-add-price" min="0" step="0.01" /></div>' +
    '<div class="field"><label for="cr-add-cost">Cost price per piece (optional)</label>' +
    '<input type="number" id="cr-add-cost" min="0" step="0.01" />' +
    '<p class="hint">Leave empty if you do not know it yet. Correct it when the invoice is added.</p></div>' +
    '<p class="hint">These pieces are added to the shelf now. The product shows the &quot;Add to invoice&quot; button in Inventory until a supplier invoice is added.</p></div>' +
    '<div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">' +
    '<button type="button" class="btn btn-ghost" id="cr-modal-cancel">Back</button>' +
    '<button type="button" class="btn btn-primary" id="cr-modal-go" style="flex:1;">Mark arrived</button></div>'
  );
  let pickedId = r.product_id || null;
  if (pickedId) document.getElementById('cr-prod-picked').textContent = 'Linked product: ' + r.item_name;
  const results = document.getElementById('cr-prod-results');
  let timer = null;
  async function search() {
    const q = document.getElementById('cr-prod-q').value.trim();
    try {
      const picks = await Api.customerRequests.searchProducts(q, r.branch_id);
      results.innerHTML = picks.length
        ? picks.map((p) => '<button type="button" class="btn btn-ghost btn-sm" data-pick="' + esc(p.id) + '" data-name="' + esc(p.name) + '" style="width:100%;text-align:left;margin-bottom:4px;">' + esc(p.name) + ' <span style="color:var(--ink-faint);">- ' + esc(p.on_hand) + ' in stock here</span></button>').join('')
        : '<div class="hint">No matching product yet.</div>';
    } catch (err) { results.innerHTML = '<div class="hint">' + esc(err.message || 'Search failed') + '</div>'; }
  }
  results.addEventListener('click', (e) => {
    const b = e.target.closest('[data-pick]');
    if (!b) return;
    pickedId = b.dataset.pick;
    document.getElementById('cr-prod-picked').textContent = 'Linked product: ' + b.dataset.name;
  });
  document.getElementById('cr-prod-q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  search();
  function stockMode() {
    const checked = document.querySelector('input[name="cr-stock-mode"]:checked');
    return checked ? checked.value : 'counted';
  }
  document.querySelectorAll('input[name="cr-stock-mode"]').forEach((radio) => radio.addEventListener('change', () => {
    const add = stockMode() === 'add';
    document.getElementById('cr-counted-fields').style.display = add ? 'none' : '';
    document.getElementById('cr-add-fields').style.display = add ? '' : 'none';
  }));
  document.getElementById('cr-modal-cancel').addEventListener('click', closeModal);
  document.getElementById('cr-modal-go').addEventListener('click', async () => {
    const addToShelf = stockMode() === 'add';
    const hold = addToShelf ? 0 : Number(document.getElementById('cr-hold').value);
    let payload = { product_id: pickedId, hold_pieces: Number.isFinite(hold) ? hold : 0 };
    if (addToShelf) {
      const pieces = Number(document.getElementById('cr-add-pieces').value);
      if (!Number.isFinite(pieces) || pieces < 1) { showToast('Enter how many pieces arrived.', true); return; }
      payload = { product_id: pickedId, add_to_shelf: true, pieces_arrived: Math.floor(pieces) };
      const priceText = String(document.getElementById('cr-add-price').value).trim();
      const costText = String(document.getElementById('cr-add-cost').value).trim();
      if (priceText !== '') payload.selling_price_naira = Number(priceText);
      if (costText !== '') payload.cost_price_naira = Number(costText);
    }
    document.getElementById('cr-modal-go').disabled = true;
    try {
      const updated = await Api.customerRequests.markArrived(r.id, payload);
      closeModal();
      showToast(addToShelf ? 'Marked arrived. ' + payload.pieces_arrived + ' piece(s) added to the shelf.' : updated.hold_pieces < hold ? 'Marked arrived. Only ' + updated.hold_pieces + ' piece(s) are free to hold right now.' : 'Marked arrived - the bell will remind you to contact ' + r.customer_name + '.');
      await load();
    } catch (err) {
      const goBtn = document.getElementById('cr-modal-go');
      if (goBtn) goBtn.disabled = false;
      showToast(err.message || 'Could not mark it arrived.', true);
    }
  });
}

function defaultPieces(r) {
  if (r.hold_pieces) return r.hold_pieces;
  const per = { pieces: 1, dozens: 12, half_dozen: 6 }[r.unit_label];
  return per ? r.quantity * per : 0;
}

function openHoldModal(r) {
  openModal(
    '<h3>Edit stock hold</h3>' +
    '<p class="modal-sub">' + esc(r.item_name) + ' for ' + esc(r.customer_name) + '</p>' +
    '<div class="field"><label for="cr-hold">Pieces to hold for this customer</label>' +
    '<input type="number" id="cr-hold" min="0" value="' + esc(defaultPieces(r)) + '" /></div>' +
    '<p class="hint">Ordered: ' + esc(qtyText(r)) + '. Cartons and bags vary by product, so enter the number of pieces they contain.</p>' +
    '<div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">' +
    '<button type="button" class="btn btn-ghost" id="cr-modal-cancel">Back</button>' +
    '<button type="button" class="btn btn-primary" id="cr-modal-go" style="flex:1;">Save</button></div>'
  );
  document.getElementById('cr-modal-cancel').addEventListener('click', closeModal);
  document.getElementById('cr-modal-go').addEventListener('click', async () => {
    const hold = Number(document.getElementById('cr-hold').value);
    try {
      const updated = await Api.customerRequests.update(r.id, { hold_pieces: Number.isFinite(hold) ? hold : 0 });
      closeModal();
      showToast(updated.hold_pieces < hold ? 'Only ' + updated.hold_pieces + ' piece(s) are free to hold right now.' : 'Hold updated.');
      await load();
    } catch (err) { showToast(err.message || 'Could not update the hold.', true); }
  });
}

function openCollectModal(r) {
  openModal(
    '<h3>Customer is collecting</h3>' +
    '<p class="modal-sub">' + esc(r.item_name) + ' - ' + esc(r.customer_name) + ' (' + esc(r.customer_phone) + ')</p>' +
    '<div style="padding:12px;border:1px solid var(--border);border-radius:10px;margin-bottom:12px;">' +
    'They already paid a deposit of <strong>' + esc(naira(r.deposit_naira)) + '</strong>. ' +
    'Confirming releases the held stock so you can ring the sale up at POS - take only the <strong>balance</strong> after this deposit.</div>' +
    '<div class="field"><label for="cr-note">Note (optional)</label><input type="text" id="cr-note" maxlength="500" placeholder="e.g. paid balance in cash" /></div>' +
    '<div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">' +
    '<button type="button" class="btn btn-ghost" id="cr-modal-cancel">Back</button>' +
    '<button type="button" class="btn btn-primary" id="cr-modal-go" style="flex:1;">Confirm collected</button></div>'
  );
  document.getElementById('cr-modal-cancel').addEventListener('click', closeModal);
  document.getElementById('cr-modal-go').addEventListener('click', async () => {
    try {
      await Api.customerRequests.collect(r.id, { note: document.getElementById('cr-note').value });
      closeModal();
      showToast('Marked collected. Ring up the sale at POS and take only the balance after the ' + naira(r.deposit_naira) + ' deposit.');
      await load();
    } catch (err) { showToast(err.message || 'Could not mark it collected.', true); }
  });
}

function openCancelModal(r) {
  const hasDeposit = Number(r.deposit_naira) > 0;
  openModal(
    '<h3>Cancel this order?</h3>' +
    '<p class="modal-sub">' + esc(r.item_name) + ' - ' + esc(r.customer_name) + '</p>' +
    (hasDeposit
      ? '<div style="padding:12px;border:1px solid var(--border);border-radius:10px;margin-bottom:12px;">' +
        'The customer paid a deposit of <strong>' + esc(naira(r.deposit_naira)) + '</strong>.' +
        '<label style="display:flex;gap:8px;align-items:center;margin-top:10px;cursor:pointer;"><input type="checkbox" id="cr-refunded" /> I have refunded the deposit to the customer</label></div>'
      : '') +
    '<div class="field"><label for="cr-note">Reason (optional)</label><input type="text" id="cr-note" maxlength="500" placeholder="e.g. customer no longer interested" /></div>' +
    '<p class="hint">Any stock held for this customer goes back on sale.</p>' +
    '<div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">' +
    '<button type="button" class="btn btn-ghost" id="cr-modal-cancel">Keep order</button>' +
    '<button type="button" class="btn btn-primary" id="cr-modal-go" style="flex:1;">Cancel order</button></div>'
  );
  document.getElementById('cr-modal-cancel').addEventListener('click', closeModal);
  document.getElementById('cr-modal-go').addEventListener('click', async () => {
    const refunded = document.getElementById('cr-refunded');
    try {
      await Api.customerRequests.cancel(r.id, { deposit_refunded: !!(refunded && refunded.checked), note: document.getElementById('cr-note').value });
      closeModal();
      showToast(hasDeposit && !(refunded && refunded.checked) ? 'Cancelled. The deposit is recorded as NOT refunded yet.' : 'Order cancelled.');
      await load();
    } catch (err) { showToast(err.message || 'Could not cancel the order.', true); }
  });
}

document.getElementById('cr-tbody').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const r = byId(btn.dataset.id);
  if (!r) return;
  if (btn.dataset.act === 'arrive') openArriveModal(r);
  else if (btn.dataset.act === 'hold') openHoldModal(r);
  else if (btn.dataset.act === 'collect') openCollectModal(r);
  else if (btn.dataset.act === 'cancel') {
    // PATCH3A-BOOKING-DRIVES-ORDER
    if (r.booking_id) {
      openModal(
        '<h3>Cancel this order in Open Bookings</h3>' +
        '<p class="modal-sub">' + esc(r.item_name) + ' - ' + esc(r.customer_name) + '</p>' +
        '<p class="hint">This order has an open booking. Cancel the booking in Transactions &gt; Open Bookings. The refund is handled there, and this order will then be cancelled by itself.</p>' +
        '<div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">' +
        '<button type="button" class="btn btn-primary" id="cr-modal-cancel" style="flex:1;">OK</button></div>'
      );
      document.getElementById('cr-modal-cancel').addEventListener('click', closeModal);
    } else {
      openCancelModal(r);
    }
  }
});

// Click-to-enlarge photo lightbox.
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
window.addEventListener('branch-context-changed', load);
setTab(activeTab);
load();

// Pick up arrivals without a manual reload.
setInterval(() => {
  if (document.hidden || document.getElementById('cr-modal-backdrop')) return;
  load();
}, 30000);
// PATCH2B-MARK-ARRIVED-WINDOW
