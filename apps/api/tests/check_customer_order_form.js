'use strict';
// Script test for the customer order form logic (Patch 2c). No browser, no API, no database.
// Run with: node apps\api\tests\check_customer_order_form.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'restock-list.js');
const text = fs.readFileSync(SRC, 'utf8');
const start = text.indexOf('var CustomerOrder = (function () {');
if (start < 0) { console.log('FAIL: cannot find the CustomerOrder code in restock-list.js'); process.exit(1); }
const endMark = '\n})();';
const endAt = text.indexOf(endMark, start);
if (endAt < 0) { console.log('FAIL: cannot find the end of the CustomerOrder code'); process.exit(1); }
const code = text.slice(start, endAt + endMark.length);

function fakeEl(id) {
  return {
    id: id, value: '', dataset: {}, style: {}, readOnly: false, placeholder: '', checked: false, listeners: {},
    addEventListener: function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    appendChild: function () {}
  };
}
function fire(el, type) {
  (el.listeners[type] || []).forEach(function (f) { f({ target: el }); });
}

function makeEnv() {
  const toasts = [];
  const host = fakeEl('restock-add-custom');
  const qty = fakeEl('custom-item-qty');
  const unit = fakeEl('custom-item-unit');
  unit.value = 'pieces';
  const panel = fakeEl('panel');
  panel.ids = {};
  Object.defineProperty(panel, 'innerHTML', {
    get: function () { return this._html || ''; },
    set: function (h) {
      this._html = h;
      const re = /\sid="([^"]+)"/g;
      let m;
      while ((m = re.exec(h)) !== null) { this.ids[m[1]] = fakeEl(m[1]); }
    }
  });
  panel.querySelector = function (sel) { return this.ids[sel.slice(1)] || null; };
  const ctx = {
    console: console,
    document: {
      getElementById: function (id) {
        if (id === 'restock-add-custom') return host;
        if (id === 'custom-item-qty') return qty;
        if (id === 'custom-item-unit') return unit;
        return null;
      },
      createElement: function () { return panel; }
    },
    showToast: function (msg) { toasts.push(msg); },
    Api: { customerRequests: {} }
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  const env = { CO: ctx.CustomerOrder, el: panel.ids, qty: qty, unit: unit, toasts: toasts, panel: panel };
  return env;
}

function setVal(el, v, type) { el.value = String(v); fire(el, type || 'input'); }
function tick(env) { env.el['co-toggle'].checked = true; fire(env.el['co-toggle'], 'change'); }
function fill(env) { env.el['co-name'].value = 'Test Name'; env.el['co-phone'].value = '08012345678'; }
function last(env) { return env.toasts[env.toasts.length - 1]; }

let pass = 0;
let fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('PASS ' + name); }
  else { fail++; console.log('FAIL ' + name + ' -> ' + detail); }
}

// 1. labels and hints
let env = makeEnv();
const html = env.panel.innerHTML;
check('1a label for total pieces', html.indexOf('>Total pieces in this order</label>') >= 0, 'label missing');
check('1b label for customer needs', html.indexOf('>Customer needs (pieces)</label>') >= 0, 'label missing');
check('1c new hint under the boxes', html.indexOf('starts equal to the total') >= 0, 'hint missing');
check('1d old optional text is gone', html.indexOf('is optional') < 0, 'old text still there');
check('1e new bottom hint', html.indexOf('a booking is made for the') >= 0, 'bottom hint missing');

// 2. need follows the total
env = makeEnv(); tick(env);
setVal(env.qty, 3);
check('2a total 3', env.el['co-total-pieces'].value === '3', 'total is ' + env.el['co-total-pieces'].value);
check('2b need follows to 3', env.el['co-need-pieces'].value === '3', 'need is ' + env.el['co-need-pieces'].value);
setVal(env.el['co-need-pieces'], 2);
check('2c lowered need stays 2', env.el['co-need-pieces'].value === '2', 'need is ' + env.el['co-need-pieces'].value);
setVal(env.qty, 5);
check('2d quantity 5: total 5, need stays 2', env.el['co-total-pieces'].value === '5' && env.el['co-need-pieces'].value === '2', 'total ' + env.el['co-total-pieces'].value + ' need ' + env.el['co-need-pieces'].value);
setVal(env.qty, 1);
check('2e quantity 1: need drops to 1', env.el['co-need-pieces'].value === '1', 'need is ' + env.el['co-need-pieces'].value);

// 3. cartons with no total
env = makeEnv(); tick(env); fill(env);
setVal(env.unit, 'cartons', 'change');
setVal(env.qty, 2);
let res = env.CO.collect();
check('3 cartons, no total: refused', res === false && last(env) === 'Type the total pieces in this order.', 'result ' + JSON.stringify(res) + ' toast ' + last(env));

// 4. cartons with total typed
env = makeEnv(); tick(env); fill(env);
setVal(env.unit, 'cartons', 'change');
setVal(env.qty, 2);
setVal(env.el['co-total-pieces'], 48);
res = env.CO.collect();
check('4 cartons, total 48: sends 48 and 48', res && res.order_pieces === 48 && res.customer_pieces === 48, JSON.stringify(res) + ' toast ' + last(env));

// 5. need above total
env = makeEnv(); tick(env); fill(env);
setVal(env.qty, 3);
setVal(env.el['co-need-pieces'], 5);
res = env.CO.collect();
check('5 need above total: refused', res === false && last(env) === 'The customer cannot need more pieces than the whole order.', 'result ' + JSON.stringify(res) + ' toast ' + last(env));

// 6. empty need
env = makeEnv(); tick(env); fill(env);
setVal(env.qty, 3);
setVal(env.el['co-need-pieces'], '');
res = env.CO.collect();
check('6 empty need: refused', res === false && last(env) === 'Type how many pieces the customer needs.', 'result ' + JSON.stringify(res) + ' toast ' + last(env));

// 7. valid payload with a lowered need and a deposit
env = makeEnv(); tick(env); fill(env);
setVal(env.qty, 3);
setVal(env.el['co-need-pieces'], 2);
setVal(env.el['co-deposit'], 500);
env.el['co-method'].value = 'cash';
res = env.CO.collect();
check('7 valid payload', res && res.order_pieces === 3 && res.customer_pieces === 2 && res.deposit_naira === 500 && res.deposit_method === 'cash' && res.customer_name === 'Test Name', JSON.stringify(res) + ' toast ' + last(env));

// 9. reset (uses the form from test 7)
env.CO.reset();
check('9 reset clears the need box', env.el['co-need-pieces'].value === '' && !env.el['co-need-pieces'].dataset.edited && env.el['co-toggle'].checked === false, 'need "' + env.el['co-need-pieces'].value + '" edited "' + env.el['co-need-pieces'].dataset.edited + '"');

// 8. dozens
env = makeEnv(); tick(env); fill(env);
setVal(env.unit, 'dozens', 'change');
setVal(env.qty, 2);
res = env.CO.collect();
check('8 dozens 2: total 24, need 24', res && res.order_pieces === 24 && res.customer_pieces === 24, JSON.stringify(res) + ' toast ' + last(env));

// 10. not ticked
env = makeEnv();
res = env.CO.collect();
check('10 form not ticked: nothing is sent', res === null, 'result ' + JSON.stringify(res));

console.log('RESULT: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
