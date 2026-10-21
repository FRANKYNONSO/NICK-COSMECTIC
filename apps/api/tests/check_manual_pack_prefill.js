'use strict';
// Checks PATCH5D-MANUAL-PACK-PREFILL. Cuts the REAL whBlankComponents,
// whApplyManualPackDefaults and the REAL Add button code out of
// apps/web_admin/js/warehouse.js and runs them in a vm sandbox with a fake page.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'warehouse.js');
const src = fs.readFileSync(file, 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}
function cut(name) {
  const m = src.match(new RegExp('function ' + name + '\\([\\s\\S]*?\\r?\\n}\\r?\\n'));
  return m ? m[0] : null;
}

const parts = ['whBlankComponents', 'whApplyManualPackDefaults'].map(cut);
if (parts.some((p) => !p)) {
  console.log('FAIL could not cut the real functions out of warehouse.js');
  process.exit(1);
}
const hm = src.match(/document\.getElementById\('wh-manual-add-btn'\)\.addEventListener\([\s\S]*?\r?\n\}\);\r?\n/);
if (!hm) {
  console.log('FAIL could not cut the real Add button code out of warehouse.js');
  process.exit(1);
}

const toasts = [];
let renders = 0;
let fetches = 0;
let handler = null;
let nextProduct = null;
let failFetch = false;
const els = {
  'wh-manual-variant': { value: 'p-1' },
  'wh-manual-product': { value: 'p-1', selectedOptions: [{ textContent: 'Gloss' }] },
  'wh-manual-add-btn': { addEventListener: (ev, fn) => { handler = fn; } },
};
const sandbox = {
  console: console,
  document: { getElementById: (id) => els[id] },
  showToast: (msg, isError) => { toasts.push({ msg: msg, isError: isError }); },
  renderWhStagingTable: () => { renders++; },
  whStagedItems: [],
  Api: {
    getProduct: async (id) => {
      fetches++;
      if (failFetch) throw new Error('network down');
      return nextProduct;
    },
  },
};
vm.createContext(sandbox);
try {
  vm.runInContext(parts.join('\n') + '\n' + hm[0], sandbox);
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}

function fresh() { return sandbox.whBlankComponents(true, true, true); }
function fill(product) { return sandbox.whApplyManualPackDefaults(fresh(), product); }

(async () => {
  check('1 marker is in warehouse.js', src.indexOf('PATCH5D-MANUAL-PACK-PREFILL') >= 0);

  let r = fill({ default_pieces_per_carton: 48, default_pieces_per_bag: null, default_pieces_per_roll: null });
  check('2 the carton default fills the carton box', r.cartons_pieces_per_unit === 48);

  r = fill({ default_pieces_per_carton: null, default_pieces_per_bag: null, default_pieces_per_roll: 30 });
  check('3 the roll default fills the carton box when there is no carton default', r.cartons_pieces_per_unit === 30);

  r = fill({ default_pieces_per_carton: 48, default_pieces_per_bag: null, default_pieces_per_roll: 30 });
  check('4 the carton default beats the roll default', r.cartons_pieces_per_unit === 48);

  r = fill({ default_pieces_per_carton: null, default_pieces_per_bag: 20, default_pieces_per_roll: null });
  check('5 the bag default fills the bag box', r.bags_pieces_per_unit === 20);

  r = fill({ default_pieces_per_carton: null, default_pieces_per_bag: null, default_pieces_per_roll: null });
  check('6 the dozen box is 12', r.dozens_pieces_per_unit === 12);

  check('7 no defaults leaves the carton and bag boxes empty', r.cartons_pieces_per_unit === null && r.bags_pieces_per_unit === null);

  check('8 counts stay 0 and the three boxes stay shown',
    r.cartons_count === 0 && r.dozens_count === 0 && r.bags_count === 0 && r.pieces_count === 0 &&
    r.show_cartons === true && r.show_dozens === true && r.show_bags === true);

  const before = JSON.stringify(fresh());
  const same = sandbox.whApplyManualPackDefaults(fresh(), null);
  check('9 a null product leaves the row as it was', JSON.stringify(same) === before);

  check('10a the Add button code was found and registered', typeof handler === 'function');
  nextProduct = { default_pieces_per_carton: 24, default_pieces_per_bag: 10, default_pieces_per_roll: null };
  failFetch = false;
  sandbox.whStagedItems.length = 0;
  const renders0 = renders;
  await handler();
  let row = sandbox.whStagedItems[0];
  check('10 clicking Add fills the row from the fetched product',
    sandbox.whStagedItems.length === 1 && row.cartons_pieces_per_unit === 24 &&
    row.bags_pieces_per_unit === 10 && row.dozens_pieces_per_unit === 12 &&
    row.source === 'manual' && row.product_id === 'p-1' && row.item_name === 'Gloss' &&
    row.key.indexOf('manual:p-1:') === 0 && renders === renders0 + 1);

  failFetch = true;
  sandbox.whStagedItems.length = 0;
  const renders1 = renders;
  await handler();
  row = sandbox.whStagedItems[0];
  check('11 clicking Add with a failed fetch still adds an empty row',
    sandbox.whStagedItems.length === 1 && row.cartons_pieces_per_unit === null &&
    row.bags_pieces_per_unit === null && renders === renders1 + 1);

  failFetch = false;
  els['wh-manual-variant'].value = '';
  els['wh-manual-product'].value = '';
  sandbox.whStagedItems.length = 0;
  const fetches0 = fetches;
  const toasts0 = toasts.length;
  await handler();
  check('12 clicking Add with no product picked adds nothing and shows a toast',
    sandbox.whStagedItems.length === 0 && fetches === fetches0 &&
    toasts.length === toasts0 + 1 && toasts[toasts.length - 1].isError === true);

  check('13 the grey sentence for manual rows is unchanged', src.indexOf('nothing on an invoice to cap against') >= 0);

  console.log('passed ' + passed + ' failed ' + failed);
  process.exit(failed ? 1 : 0);
})();
