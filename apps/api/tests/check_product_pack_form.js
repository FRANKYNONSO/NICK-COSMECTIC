'use strict';
// Checks PATCH5A-PACK-SIZES (pieces-in-a-pack boxes). Cuts the REAL product form
// submit handler out of apps/web_admin/js/inventory.js and runs it in a vm sandbox
// with a fake page.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'inventory.js');
const src = fs.readFileSync(file, 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

const start = src.indexOf("productForm.addEventListener('submit', async (e) => {");
const end = start < 0 ? -1 : src.indexOf('// ------', start);
if (start < 0 || end < 0) {
  console.log('FAIL could not find the product form submit handler in inventory.js');
  process.exit(1);
}
const handlerSrc = src.slice(start, end);

const els = {};
function el(id) {
  if (!els[id]) {
    els[id] = {
      id: id, value: '', checked: false, disabled: false, textContent: '',
      style: {}, dataset: {}, files: [],
      classList: { add() {}, remove() {}, contains: () => false },
    };
  }
  return els[id];
}

let handler = null;
const calls = { create: [], update: [], toasts: [] };
const sandbox = {
  console: console,
  document: { getElementById: (id) => el(id) },
  productForm: { addEventListener: (type, fn) => { if (type === 'submit') handler = fn; } },
  modalError: el('modal-error'),
  modalBackdrop: el('product-modal-backdrop'),
  imageFileInput: el('p-image-file'),
  initialQtyInput: el('p-initial-qty'),
  brandHidden: el('p-brand'),
  skuElement: el('p-sku'),
  currentUser: { role: 'branch_manager' },
  BranchContext: { get: () => 'branch-1' },
  toProductNameCase: (s) => s,
  showToast: (m) => { calls.toasts.push(m); },
  stopBarcodeScan: () => {},
  loadProducts: () => {},
  openInvoicePrompt: () => {},
  Api: {
    createProduct: async (payload) => { calls.create.push(payload); return { id: 'new1' }; },
    updateProduct: async (id, payload) => { calls.update.push({ id: id, payload: payload }); return { id: id }; },
    receiveStock: async () => ({}),
    uploadProductImage: async () => ({}),
  },
};
vm.createContext(sandbox);
try {
  vm.runInContext(handlerSrc, sandbox);
} catch (err) {
  console.log('FAIL the handler code did not load: ' + err.message);
  process.exit(1);
}
if (typeof handler !== 'function') {
  console.log('FAIL the handler was not attached');
  process.exit(1);
}

function setup(carton, bag, roll, productId) {
  calls.create.length = 0;
  calls.update.length = 0;
  el('product-id').value = productId || '';
  el('p-name').value = 'Test item';
  el('p-price').value = '100';
  el('p-initial-qty').value = '0';
  el('p-pack-carton').value = carton;
  el('p-pack-bag').value = bag;
  el('p-pack-roll').value = roll;
  el('modal-error').textContent = '';
  el('product-modal-save').disabled = false;
}
async function submit() {
  try {
    await handler({ preventDefault() {} });
  } catch (err) {
    console.log('NOTE the handler threw: ' + err.message);
  }
}

(async () => {
  // 1. all blank, create
  setup('', '', '');
  await submit();
  check('1 blank boxes: createProduct called once', calls.create.length === 1);
  const p1 = calls.create[0] || {};
  check('1 blank carton is sent as null', p1.default_pieces_per_carton === null);
  check('1 blank bag is sent as null', p1.default_pieces_per_bag === null);
  check('1 blank roll is sent as null', p1.default_pieces_per_roll === null);

  // 2. numbers typed, create
  setup('48', '24', '12');
  await submit();
  const p2 = calls.create[0] || {};
  check('2 carton 48 is sent as number 48', p2.default_pieces_per_carton === 48);
  check('2 bag 24 is sent as number 24', p2.default_pieces_per_bag === 24);
  check('2 roll 12 is sent as number 12', p2.default_pieces_per_roll === 12);

  // 3. zero is refused
  setup('0', '', '');
  await submit();
  check('3 zero: nothing is saved', calls.create.length === 0 && calls.update.length === 0);
  check('3 zero: the approved error text is shown',
    el('modal-error').textContent === 'Pieces in a pack must be a whole number, 1 or more.');
  check('3 zero: the Save button is enabled again', el('product-modal-save').disabled === false);

  // 4. decimal is refused
  setup('', '2.5', '');
  await submit();
  check('4 decimal: nothing is saved', calls.create.length === 0 && calls.update.length === 0);

  // 5. edit
  setup('30', '', '', 'p1');
  await submit();
  check('5 edit: updateProduct called with the product id', calls.update.length === 1 && calls.update[0].id === 'p1');
  const p5 = (calls.update[0] || {}).payload || {};
  check('5 edit: carton 30 is sent', p5.default_pieces_per_carton === 30);
  check('5 edit: blank bag is sent as null (clears it)', p5.default_pieces_per_bag === null);

  // 6. negative is refused
  setup('', '', '-5');
  await submit();
  check('6 negative: nothing is saved', calls.create.length === 0 && calls.update.length === 0);

  console.log('');
  console.log('Passed ' + passed + ', failed ' + failed + ' (expected 15 checks)');
  process.exit(failed === 0 && passed === 15 ? 0 : 1);
})();
