'use strict';
// Script test for the receipt wording (Patch 3b-2). No browser, no API, no database.
// Run with: node apps\api\tests\check_receipt_balance.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'receipt.js');
const text = fs.readFileSync(SRC, 'utf8');
const NAIRA = '\u20a6';

function fakeDiv() {
  const d = { _t: '' };
  Object.defineProperty(d, 'textContent', { set: function (v) { this._t = String(v); }, get: function () { return this._t; } });
  Object.defineProperty(d, 'innerHTML', {
    get: function () { return this._t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  });
  return d;
}
const store = {};
const ctx = {
  console: console,
  document: { createElement: fakeDiv, getElementById: function () { return null; } },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  window: {}
};
vm.createContext(ctx);
vm.runInContext(text + '\n;globalThis.__R = Receipt;', ctx);
const Receipt = ctx.__R;
if (!Receipt || typeof Receipt.render !== 'function') { console.log('FAIL: Receipt.render not found'); process.exit(1); }

function baseSale(over) {
  return Object.assign({
    id: 'abcdef12-0000-0000-0000-000000000000',
    created_at: '2026-10-13T10:00:00Z',
    status: 'completed',
    total_naira: 200,
    paid_naira: 200,
    discount_naira: 0,
    payment_method: 'transfer',
    items: [{ quantity: 1, product_name: 'Test Item', line_total_naira: 200, unit_price_naira: 200, unit_label: 'piece' }],
    payments: [],
    refunds: [],
    item_swap_history: [],
    pending_changes: [],
    storage_fee_naira: 0
  }, over || {});
}
function render(sale) {
  const c = { innerHTML: '' };
  Receipt.render(c, sale, {});
  return c.innerHTML;
}

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('PASS ' + name); }
  else { fail++; console.log('FAIL ' + name + ' -> ' + detail); }
}

// 1. extra money still owed
let h = render(baseSale({ paid_naira: 250, pending_changes: [
  { id: '1', status: 'outstanding', settlement_method: null, reference_code: 'CHG-449561', amount_naira: 50 }] }));
check('1a shows Balance we owe you', h.indexOf('Balance we owe you</span><span>' + NAIRA + '50<') >= 0, 'not found');
check('1b shows the reference code line', h.indexOf('Ref CHG-449561 - show this code when you come back.') >= 0, 'not found');
check('1c old Change due row is gone', h.indexOf('Change due') < 0, 'still there');
check('1d old Change pending line is gone', h.indexOf('Change pending') < 0, 'still there');

// 2. settled by bank transfer
h = render(baseSale({ paid_naira: 250, pending_changes: [
  { id: '1', status: 'settled', settlement_method: 'transfer', reference_code: 'CHG-449561', amount_naira: 50 }] }));
check('2a settled line', h.indexOf('Balance of ' + NAIRA + '50 paid by bank transfer.') >= 0, 'not found');
check('2b no owe line when settled', h.indexOf('Balance we owe you') < 0, 'still there');

// 3. waiting for approval
h = render(baseSale({ paid_naira: 250, pending_changes: [
  { id: '1', status: 'pending', settlement_method: 'transfer', reference_code: 'CHG-449561', amount_naira: 50 }] }));
check('3 awaiting approval wording', h.indexOf('Balance we owe you (bank transfer, awaiting approval)') >= 0, 'not found');

// 4. completed, paid more, no pending record: old Change due stays
h = render(baseSale({ paid_naira: 250 }));
check('4a old Change due kept', h.indexOf('Change due</span><span>' + NAIRA + '50<') >= 0, 'not found');
check('4b no owe line', h.indexOf('Balance we owe you') < 0, 'found');

// 5. open booking
h = render(baseSale({ status: 'booking', paid_naira: 100 }));
check('5a booking shows Balance due', h.indexOf('Balance due') >= 0, 'not found');
check('5b booking has no owe line', h.indexOf('Balance we owe you') < 0, 'found');

// 6. exact payment
h = render(baseSale({ paid_naira: 200 }));
check('6 exact payment shows neither line', h.indexOf('Change due') < 0 && h.indexOf('Balance we owe you') < 0, 'found');

console.log('RESULT: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);