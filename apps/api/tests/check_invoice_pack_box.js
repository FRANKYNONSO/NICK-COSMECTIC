'use strict';
// Checks PATCH5B-INVOICE-PACKS. Cuts the REAL pack-box code, unitToPieces and the
// popup Save handler out of apps/web_admin/js/invoice.js and runs them in a vm
// sandbox with a fake page.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'invoice.js');
const src = fs.readFileSync(file, 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

const blockStart = src.indexOf('// PATCH5B-INVOICE-PACKS: pieces in one pack for cartons');
const blockEnd = blockStart < 0 ? -1 : src.indexOf('function openQtyDetailPopup(tr) {', blockStart);
const saveStart = src.indexOf("document.getElementById('qty-detail-save').addEventListener('click', () => {");
const saveEnd = saveStart < 0 ? -1 : src.indexOf("document.getElementById('invoice-add-item-row')", saveStart);
const u = src.match(/function unitToPieces\([\s\S]*?\r?\n}\r?\n/);
if (blockStart < 0 || blockEnd < 0 || saveStart < 0 || saveEnd < 0 || !u) {
  console.log('FAIL could not cut the real code out of invoice.js');
  process.exit(1);
}

const els = {};
let saveHandler = null;
function el(id) {
  if (!els[id]) {
    els[id] = {
      id: id, value: '', textContent: '', style: { display: '' }, dataset: {},
      classList: { add() {}, remove() {}, contains: () => false },
      addEventListener: (type, fn) => { if (id === 'qty-detail-save' && type === 'click') saveHandler = fn; },
    };
  }
  return els[id];
}
const toasts = [];
const sandbox = {
  console: console,
  document: { getElementById: (id) => el(id) },
  showToast: (m) => { toasts.push(m); },
};
vm.createContext(sandbox);

const prefix = [
  'const DOZEN_PIECES = 12;',
  'let qtyDetailEditingRow = null;',
  "const qtyDetailUnit = document.getElementById('qty-detail-unit');",
  "const qtyDetailQty = document.getElementById('qty-detail-qty');",
  "const qtyDetailRemainderWrap = document.getElementById('qty-detail-remainder-wrap');",
  "const qtyDetailRemainderKind = document.getElementById('qty-detail-remainder-kind');",
  "const qtyDetailRemainderQty = document.getElementById('qty-detail-remainder-qty');",
  "const qtyDetailRemainderUnit = document.getElementById('qty-detail-remainder-unit');",
  "const qtyDetailBackdrop = document.getElementById('qty-detail-backdrop');",
  "function halfPackLabelFor(u) { return u === 'cartons' ? 'half_carton' : 'half_dozen'; }",
  'function updateQtySummaryButton() {}',
  'function recomputeRowTotal() {}',
  'function __setRow(r) { qtyDetailEditingRow = r; }',
].join('\n') + '\n';

try {
  vm.runInContext(prefix + u[0] + '\n' + src.slice(blockStart, blockEnd) + '\n' + src.slice(saveStart, saveEnd), sandbox);
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}
if (typeof saveHandler !== 'function') {
  console.log('FAIL the Save handler was not attached');
  process.exit(1);
}

function makeRow(opts) {
  const f = {
    '.item-unit': { value: opts.unit || 'pieces' },
    '.item-qty': { value: '1' },
    '.item-pieces-per-carton': { value: opts.saved || '' },
    '.item-remainder-qty': { value: '' },
    '.item-remainder-unit': { value: 'pieces' },
  };
  return {
    dataset: { ppCarton: opts.ppCarton || '', ppBag: opts.ppBag || '', ppRoll: opts.ppRoll || '' },
    querySelector: (sel) => f[sel],
    f: f,
  };
}
function open(row, unit) {
  sandbox.__setRow(row);
  el('qty-detail-unit').value = unit;
  sandbox.updateQtyDetailPackField();
}
const wrap = () => el('qty-detail-pack-wrap');
const box = () => el('qty-detail-pack');

// unitToPieces
check('1 rolls 3 x 50 = 150', sandbox.unitToPieces('rolls', 3, 50) === 150);
check('2 rolls without a pack number give null', sandbox.unitToPieces('rolls', 3, null) === null);
check('3 cartons 2 x 48 = 96 still works', sandbox.unitToPieces('cartons', 2, 48) === 96);

// pack box
open(makeRow({ ppCarton: '48' }), 'cartons');
check('4 cartons: the box is shown', wrap().style.display === '');
check('5 cartons: filled from the product (48)', box().value === '48');
check('6 cartons: label says carton', el('qty-detail-pack-label').textContent === 'Pieces in one carton');
check('7 cartons: hint says it is from the product',
  el('qty-detail-pack-hint').textContent === 'From the product. You can change it for this invoice.');
open(makeRow({ ppBag: '24' }), 'bags');
check('8 bags: label and number from the bag default',
  el('qty-detail-pack-label').textContent === 'Pieces in one bag' && box().value === '24');
open(makeRow({}), 'rolls');
check('9 rolls without default: label says roll, box and hint empty',
  el('qty-detail-pack-label').textContent === 'Pieces in one roll' && box().value === '' && el('qty-detail-pack-hint').textContent === '');
open(makeRow({ ppCarton: '48' }), 'dozens');
check('10 dozens: the box is hidden and empty', wrap().style.display === 'none' && box().value === '');
open(makeRow({ unit: 'cartons', saved: '30', ppCarton: '48' }), 'cartons');
check('11 a number already on the line wins and shows no hint',
  box().value === '30' && el('qty-detail-pack-hint').textContent === '');
open(makeRow({ unit: 'cartons', saved: '30', ppBag: '24' }), 'bags');
check('12 a saved number for another unit is not reused', box().value === '24');

// save
function trySave(row, unit, typed) {
  sandbox.__setRow(row);
  el('qty-detail-unit').value = unit;
  el('qty-detail-qty').value = '2';
  el('qty-detail-remainder-wrap').style.display = 'none';
  box().value = typed;
  toasts.length = 0;
  saveHandler();
}
const r13 = makeRow({ unit: 'pieces' });
trySave(r13, 'cartons', '40');
check('13 save stores the typed 40', r13.f['.item-pieces-per-carton'].value === '40');
const r14 = makeRow({ unit: 'pieces', saved: '99' });
trySave(r14, 'dozens', '40');
check('14 save with dozens stores nothing', r14.f['.item-pieces-per-carton'].value === '');
const r15 = makeRow({ unit: 'pieces', saved: '7' });
trySave(r15, 'cartons', '0');
check('15 zero: the approved toast is shown',
  toasts.length === 1 && toasts[0] === 'Pieces in a pack must be a whole number, 1 or more.');
check('16 zero: the line is not changed', r15.f['.item-unit'].value === 'pieces' && r15.f['.item-pieces-per-carton'].value === '7');
const r17 = makeRow({ unit: 'pieces' });
trySave(r17, 'bags', '2.5');
check('17 decimal is refused, line not changed', r17.f['.item-unit'].value === 'pieces');
const r18 = makeRow({ unit: 'pieces', saved: '7' });
trySave(r18, 'rolls', '');
check('18 blank box stores nothing', r18.f['.item-pieces-per-carton'].value === '' && r18.f['.item-unit'].value === 'rolls');

console.log('');
console.log('Passed ' + passed + ', failed ' + failed + ' (expected 18 checks)');
process.exit(failed === 0 && passed === 18 ? 0 : 1);
