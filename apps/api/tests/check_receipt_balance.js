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
  const c = { innerHTML: '', dataset: {} };
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
check('2a settled transfer says approved', h.indexOf('Bank transfer - approved</span><span>' + NAIRA + '50<') >= 0, 'not found');
check('2b no owe line when settled', h.indexOf('Balance we owe you') < 0, 'still there');

// 3. waiting for approval
h = render(baseSale({ paid_naira: 250, pending_changes: [
  { id: '1', status: 'pending', settlement_method: 'transfer', reference_code: 'CHG-449561', amount_naira: 50 }] }));
check('3 awaiting approval wording', h.indexOf('Bank transfer - being processed, awaiting approval') >= 0, 'not found');

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

// 7. airtime waiting for approval
h = render(baseSale({ paid_naira: 300, pending_changes: [
  { id: '1', status: 'pending', settlement_method: 'airtime', reference_code: 'CHG-AAA111', amount_naira: 100 }] }));
check('7a airtime waiting', h.indexOf('Airtime - being processed, awaiting approval</span><span>' + NAIRA + '100<') >= 0, 'not found');
check('7b waiting row keeps come-back line', h.indexOf('Ref CHG-AAA111 - show this code when you come back.') >= 0, 'not found');

// 8. airtime approved
h = render(baseSale({ paid_naira: 300, pending_changes: [
  { id: '1', status: 'settled', settlement_method: 'airtime', reference_code: 'CHG-AAA111', amount_naira: 100 }] }));
check('8a airtime approved', h.indexOf('Airtime - approved</span><span>' + NAIRA + '100<') >= 0, 'not found');
check('8b approved row keeps ref, no come-back line', h.indexOf('Ref CHG-AAA111') >= 0 && h.indexOf('show this code when you come back') < 0, 'wrong');
check('8c no owe line when approved', h.indexOf('Balance we owe you') < 0, 'found');

// 9. transfer approved
h = render(baseSale({ paid_naira: 300, pending_changes: [
  { id: '1', status: 'settled', settlement_method: 'transfer', reference_code: 'CHG-BBB222', amount_naira: 100 }] }));
check('9 transfer approved', h.indexOf('Bank transfer - approved</span><span>' + NAIRA + '100<') >= 0, 'not found');

// 10. cash collected
h = render(baseSale({ paid_naira: 400, pending_changes: [
  { id: '1', status: 'settled', settlement_method: 'cash', reference_code: 'CHG-CCC333', amount_naira: 200 }] }));
check('10a cash collected', h.indexOf('Balance - collected, completed</span><span>' + NAIRA + '200<') >= 0, 'not found');
check('10b no owe line after collect', h.indexOf('Balance we owe you') < 0, 'found');

// 11. discount code used
h = render(baseSale({ paid_naira: 400, pending_changes: [
  { id: '1', status: 'settled', settlement_method: 'coupon', reference_code: 'CHG-DDD444', amount_naira: 200 }] }));
check('11 coupon used', h.indexOf('Balance - turned into a discount code, used, completed</span><span>' + NAIRA + '200<') >= 0, 'not found');

// 12. mixed receipt: airtime approved, transfer waiting, cash waiting
h = render(baseSale({ paid_naira: 600, pending_changes: [
  { id: '1', status: 'settled', settlement_method: 'airtime', reference_code: 'CHG-AAA111', amount_naira: 100 },
  { id: '2', status: 'pending', settlement_method: 'transfer', reference_code: 'CHG-BBB222', amount_naira: 100 },
  { id: '3', status: 'outstanding', settlement_method: null, reference_code: 'CHG-CCC333', amount_naira: 200 }] }));
check('12a mixed airtime approved', h.indexOf('Airtime - approved') >= 0, 'not found');
check('12b mixed transfer waiting', h.indexOf('Bank transfer - being processed, awaiting approval') >= 0, 'not found');
check('12c mixed cash waiting', h.indexOf('Balance we owe you</span><span>' + NAIRA + '200<') >= 0, 'not found');
check('12d owe line appears once', h.split('Balance we owe you').length - 1 === 1, 'wrong count');
check('12e old wording gone', h.indexOf('Balance we owe you (') < 0, 'found');

console.log('RESULT: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);