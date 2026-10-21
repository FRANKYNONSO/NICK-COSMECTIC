'use strict';
// Checks PATCH4B (grey stock split line). Pulls the REAL loadProducts, stockPill
// and stockSplitLine out of apps/web_admin/js/inventory.js and runs them in a
// vm sandbox with a fake page.
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
const count = (text, part) => text.split(part).length - 1;

// --- cut the real code out of the file
const lpStart = src.indexOf('async function loadProducts() {');
const lpEnd = src.indexOf('// ------', lpStart);
const pillMatch = src.match(/function stockPill\([\s\S]*?\r?\n}\r?\n/);
const blockStart = src.lastIndexOf('// PATCH4B-STOCK-SPLIT-LINE');
if (lpStart < 0 || lpEnd < 0 || !pillMatch || blockStart < 0) {
  console.log('FAIL could not find loadProducts / stockPill / stockSplitLine in inventory.js');
  process.exit(1);
}
const loadProductsSrc = src.slice(lpStart, lpEnd);
const stockPillSrc = pillMatch[0];
const splitSrc = src.slice(blockStart);

// --- fake page
function makeEl(id) {
  return {
    id: id, value: '', checked: false, innerHTML: '', textContent: '',
    style: {},
    querySelector: () => null,
    classList: { add() {}, remove() {}, contains: () => false },
  };
}
const ids = ['category-filter', 'search-input', 'low-stock-only', 'out-of-stock-only',
  'needs-invoice-only', 'products-tbody', 'products-table', 'empty-state'];
const els = {};
const state = { perms: new Set(), branchId: null, products: [], summary: null, summaryThrows: false };
const calls = { list: [], summary: [], toasts: [] };
const sandbox = {
  console: console,
  document: {
    getElementById: (id) => { if (!els[id]) throw new Error('no element ' + id); return els[id]; },
    querySelector: () => null,
  },
  Perms: { can: (n) => state.perms.has(n) },
  BranchContext: { get: () => state.branchId },
  showActionsColumn: true,
  canBulkAddRestock: false,
  restockSelected: new Set(),
  escapeHtml: (s) => String(s),
  formatNaira: (n) => 'N' + n,
  productMetaLine: () => '',
  wireRestockCheckboxes: () => {},
  showToast: (m) => { calls.toasts.push(m); },
  openEditModal() {}, deleteProduct() {}, openRestockModal() {}, openShelfLimitModal() {},
  openHistoryModal() {}, openInvoicePrompt() {},
  Api: {
    listProducts: async (arg) => { calls.list.push(arg); return state.products; },
    goodsStockSummary: async (arg) => {
      calls.summary.push(arg);
      if (state.summaryThrows) throw new Error('summary down');
      return state.summary;
    },
  },
};
vm.createContext(sandbox);
vm.runInContext(stockPillSrc, sandbox);
vm.runInContext(splitSrc, sandbox);
vm.runInContext(loadProductsSrc, sandbox);

const P1 = { id: 'p1', name: 'Fanta', quantity_available: 5, reorder_level: 2, price_naira: 100 };
const P2 = { id: 'p2', name: 'Soap', quantity_available: 0, reorder_level: 2, price_naira: 50 };

async function run(opts) {
  ids.forEach((id) => { els[id] = makeEl(id); });
  state.perms = new Set(opts.perms || []);
  state.branchId = opts.branchId === undefined ? 'b1' : opts.branchId;
  state.products = opts.products || [P1, P2];
  state.summary = opts.summary === undefined ? [] : opts.summary;
  state.summaryThrows = !!opts.throws;
  calls.list.length = 0;
  calls.summary.length = 0;
  calls.toasts.length = 0;
  await sandbox.loadProducts();
  return els['products-tbody'].innerHTML;
}

const GOOD = [{ product_id: 'p1', shelf_stock: 5, shelf_sellable_stock: 5, warehouse_stock: 19,
  total_stock: 24, total_sellable_stock: 24 }];

async function main() {
  // --- 1: branch manager, branch picked (5)
  let html = await run({ perms: ['manage_inventory'], summary: GOOD });
  check('01 summary called once for the picked branch', calls.summary.length === 1 && calls.summary[0].branchId === 'b1');
  check('02 grey line text is exact',
    html.includes('<span>Shelf 5</span><span>Warehouse 19</span><span>Total 24</span>'));
  check('03 only the listed product gets a grey line', count(html, 'data-stock-split="1"') === 1);
  check('04 grey line sits right after the pill in the same cell',
    html.includes('5 in stock</span><div class="cell-sub" data-stock-split="1">'));
  check('05 the product that is not listed keeps its pill and has no line',
    html.includes('Out of stock') && html.indexOf('Out of stock') > html.indexOf('data-stock-split="1"') &&
    count(html.slice(html.indexOf('Soap')), 'data-stock-split') === 0);

  // --- 2: staff (3)
  html = await run({ perms: [], summary: GOOD });
  check('06 staff: summary not called', calls.summary.length === 0);
  check('07 staff: no grey line', count(html, 'data-stock-split') === 0);
  check('08 staff: rows still show', html.includes('Fanta') && html.includes('Soap'));

  // --- 3: no branch picked (2)
  html = await run({ perms: ['manage_inventory'], branchId: null, summary: GOOD });
  check('09 no branch: summary not called', calls.summary.length === 0);
  check('10 no branch: no grey line', count(html, 'data-stock-split') === 0);

  // --- 4: summary fails (4)
  html = await run({ perms: ['manage_inventory'], throws: true });
  check('11 summary fails: rows still show', html.includes('Fanta') && html.includes('Soap'));
  check('12 summary fails: no grey line', count(html, 'data-stock-split') === 0);
  check('13 summary fails: no error message shown', calls.toasts.length === 0);
  check('14 summary fails: table is not hidden', els['products-table'].style.display === '');

  // --- 5: empty answer (1)
  html = await run({ perms: ['manage_inventory'], summary: null });
  check('15 null answer: rows still show', html.includes('Fanta') && count(html, 'data-stock-split') === 0);

  // --- 6: the grey line itself (3)
  check('16 nothing for a missing product', sandbox.stockSplitLine(undefined) === '');
  const noTotal = sandbox.stockSplitLine({ shelf_sellable_stock: 2, warehouse_stock: 3 });
  check('17 total falls back to shelf plus warehouse', noTotal.includes('Shelf 2') && noTotal.includes('Total 5'));
  const expired = sandbox.stockSplitLine({ shelf_stock: 10, shelf_sellable_stock: 4, warehouse_stock: 0, total_sellable_stock: 4 });
  check('18 expired pieces are left out of Shelf', expired.includes('Shelf 4') && !expired.includes('Shelf 10'));

  // --- 7: a summary for another product (1)
  html = await run({ perms: ['manage_inventory'],
    summary: [{ product_id: 'zz', shelf_sellable_stock: 9, warehouse_stock: 9, total_sellable_stock: 18 }] });
  check('19 another product summary is not shown on this one', count(html, 'data-stock-split') === 0);

  // --- 8: product list request (1)
  await run({ perms: ['manage_inventory'], summary: GOOD });
  check('20 product list is still requested for the picked branch', calls.list.length === 1 && calls.list[0].branchId === 'b1');

  // --- 9: file text (2)
  check('21 stock cell has pill then grey line',
    src.includes('${stockPill(p.quantity_available, p.reorder_level ?? 0)}${stockSplitLine(stockSplit.get(p.id))}</td>'));
  check('22 stockSplitLine is defined once', count(src, 'function stockSplitLine') === 1);

  console.log(passed + '/' + (passed + failed) + ' checks passed');
  process.exit(failed ? 1 : 0);
}
main().catch((err) => { console.log('CRASH ' + err.stack); process.exit(1); });
