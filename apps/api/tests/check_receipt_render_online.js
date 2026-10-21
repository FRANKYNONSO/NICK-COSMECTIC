'use strict';
// Runs the REAL Receipt.render from apps/web_admin/js/receipt.js in a vm
// sandbox with a small fake page, and checks the payment words it prints.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'receipt.js');
const src = fs.readFileSync(file, 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

const cutMatch = src.match(/const Receipt = \(\(\) => \{[\s\S]*?\r?\n\}\)\(\);/);
if (!cutMatch) {
  console.log('FAIL could not cut the Receipt code out of receipt.js');
  process.exit(1);
}

const fakeDocument = {
  createElement: function () {
    let t = '';
    return {
      set textContent(v) { t = v; },
      get innerHTML() { return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
    };
  },
  getElementById: function () { return null; },
};
const sandbox = { console: console, window: {}, document: fakeDocument };
let Receipt;
try {
  Receipt = vm.runInNewContext(cutMatch[0] + '\nReceipt;', sandbox);
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}

function draw(sale) {
  const c = { innerHTML: '' };
  Receipt.render(c, sale, {});
  return c.innerHTML;
}
function baseSale(o) {
  return Object.assign({
    id: 'abc12345-0000',
    receipt_number: 'R-1001',
    created_at: '2026-10-18T10:00:00Z',
    cashier_name: 'Ada',
    status: 'completed',
    payment_method: 'cash',
    total_naira: 2500,
    paid_naira: 2500,
    items: [{ quantity: 1, product_name: 'Lip Gloss', line_total_naira: 2500, unit_price_naira: 2500, unit_label: 'piece' }],
    payments: [],
  }, o || {});
}

check('1 the real Receipt.render loads', typeof Receipt.render === 'function');

let html = draw(baseSale({ payment_method: 'online' }));
check('2 a paid-online sale shows Online on the total line', html.indexOf('<span>Online</span>') >= 0);
check('3 the lower-case word online is not shown', html.indexOf('<span>online</span>') < 0);

html = draw(baseSale({ payment_method: 'cash' }));
check('4 a cash sale still shows Cash', html.indexOf('<span>Cash</span>') >= 0 && html.indexOf('<span>Online</span>') < 0);

html = draw(baseSale({ payment_method: 'airtime' }));
check('5 an unknown method is shown as typed', html.indexOf('<span>airtime</span>') >= 0);

html = draw(baseSale({
  payment_method: 'split',
  total_naira: 5000,
  paid_naira: 5000,
  payments: [
    { method: 'online', amount_naira: 2000, created_at: '2026-10-18T09:00:00Z', recorded_by_name: 'Ada' },
    { method: 'cash', amount_naira: 3000, created_at: '2026-10-18T10:00:00Z' },
  ],
}));
check('6 the payment history shows Online and Cash with their amounts',
  /<span>Online<\/span>\s*<span>\u20a62,000<\/span>/.test(html) &&
  /<span>Cash<\/span>\s*<span>\u20a63,000<\/span>/.test(html));

html = draw(baseSale({ status: 'booking', payment_method: 'online', total_naira: 10000, paid_naira: 4000 }));
check('7 a booking paid online shows Online in the method row', html.indexOf('<span>Online</span><span></span>') >= 0);

const untouched = { innerHTML: 'untouched' };
let threw = false;
try { Receipt.render(untouched, null, {}); } catch (err) { threw = true; }
check('8 with no sale nothing is drawn and there is no error', !threw && untouched.innerHTML === 'untouched');

console.log('passed ' + passed + ' failed ' + failed);
process.exit(failed ? 1 : 0);
