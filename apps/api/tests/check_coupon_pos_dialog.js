'use strict';
// Script test for Patch 3c POS part: "Reference code" box of the POS code dialog.
// Loads the real pos.html / pos.js text. Runs the patched part of pos.js in a
// sandbox with a fake page and a fake Api. No browser, no API, no database.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.join(__dirname, '..', '..', 'web_admin');
const html = fs.readFileSync(path.join(WEB, 'pos.html'), 'utf8');
const js = fs.readFileSync(path.join(WEB, 'js', 'pos.js'), 'utf8');

let passed = 0;
let failed = 0;
function check(name, cond, extra) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name + (extra !== undefined ? '  -> ' + extra : '')); }
}

const start = js.indexOf('// PATCH3C-COUPON-POS-BEGIN');
const end = js.indexOf("document.getElementById('coupon-result-close')");
const fm = js.match(/function formatNaira\([\s\S]*?\n\}/);
if (start < 0 || end < 0 || end < start || !fm) {
  console.log('FAIL cannot find the patched block or formatNaira in pos.js');
  process.exit(1);
}
const code = js.slice(start, end);

const STILL_VALID = 'A discount code for this credit is still valid. Try again when it expires.';
const MISSING = 'No outstanding credit with this reference code.';
const LOOKUP_FAILED = 'Could not check the reference code. Try again.';
const ROW = { id: 'x1', reference_code: 'CHG-ABC123', status: 'outstanding', customer_name: 'Ada', amount_naira: 50 };
const ROW2 = { id: 'x2', reference_code: 'CHG-BBB222', status: 'outstanding', customer_name: 'Bola', amount_naira: 75 };

function makeEl(id) {
  const handlers = {};
  const el = {
    id: id, value: '', readOnly: false, disabled: false, textContent: '', style: {}, handlers: handlers,
    classes: new Set(),
    classList: {
      add: (c) => el.classes.add(c),
      remove: (c) => el.classes.delete(c),
      toggle: (c, f) => { if (f === undefined ? !el.classes.has(c) : f) el.classes.add(c); else el.classes.delete(c); },
      contains: (c) => el.classes.has(c),
    },
    addEventListener: (type, fn) => { handlers[type] = fn; },
    reset: () => {},
  };
  return el;
}

function setup(opts) {
  opts = opts || {};
  const calls = { list: [], issue: [] };
  const els = {};
  const formResets = { n: 0 };
  function getEl(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; }
  const ctx = {
    document: { getElementById: getEl },
    Api: {
      listPendingChanges: async (arg) => {
        calls.list.push(arg);
        if (opts.list) return opts.list(arg);
        return [];
      },
      issueCoupon: async (payload) => {
        calls.issue.push(payload);
        if (opts.issueError) throw opts.issueError;
        return { code: 'SAVE-TEST01', discount_amount_naira: payload.discount_amount_naira, expires_at: '2026-10-16T13:37:06Z' };
      },
    },
    console: console,
  };
  vm.createContext(ctx);
  getEl('generate-coupon-form').reset = function () {
    formResets.n++;
    getEl('gc-amount').value = '';
    getEl('gc-reference').value = '';
  };
  vm.runInContext(fm[0], ctx);
  vm.runInContext(code, ctx);
  return { ctx: ctx, els: els, calls: calls, formResets: formResets, el: getEl };
}

async function settle() { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)); }
async function typeRef(s, value) {
  s.el('gc-reference').value = value;
  s.el('gc-reference').handlers.change();
  await settle();
}
async function submit(s) {
  await s.el('generate-coupon-form').handlers.submit({ preventDefault() {} });
  await settle();
}
function visible(s, id) { return s.el(id).classList.contains('visible'); }

(async function main() {
  // ---- static text checks on the real files
  check('html: says 30 minutes', html.includes('Valid for 30 minutes from generation.'));
  check('html: no 20 minutes text left', !html.includes('Valid for 20 minutes'));
  check('html: label Reference code (optional)', html.includes('Reference code (optional)'));
  check('html: placeholder CHG-______', html.includes('placeholder="CHG-______"'));
  check('html: help line', html.includes('Only for a customer who kept their change as credit. Leave this empty for a normal code.'));
  check('html: reference box is above the amount box', html.indexOf('id="gc-reference"') > 0 && html.indexOf('id="gc-reference"') < html.indexOf('id="gc-amount"'));
  check('js: stale 20-minute comment fixed', !js.includes('fixed at 20 minutes') && js.includes("fixed at 30 minutes"));

  // ---- S1: normal code, reference box empty
  let s = setup();
  s.el('gc-amount').value = '10';
  await submit(s);
  check('S1 normal: one coupon call', s.calls.issue.length === 1, s.calls.issue.length);
  check('S1 normal: amount typed by hand (10)', s.calls.issue[0] && s.calls.issue[0].discount_amount_naira === 10);
  check('S1 normal: no reference_code sent', s.calls.issue[0] && s.calls.issue[0].reference_code === undefined);
  check('S1 normal: no credit lookup', s.calls.list.length === 0);
  check('S1 normal: no credit line in result', !s.el('coupon-result-detail').textContent.includes('Credit of'));
  check('S1 normal: result dialog shown', visible(s, 'coupon-result-backdrop'));

  // ---- S2: credit found, amount filled and locked, then generate
  s = setup({ list: () => [ROW] });
  await typeRef(s, 'chg-abc123');
  const amt = s.ctx.formatNaira(50);
  check('S2 found: lookup asked for outstanding only', s.calls.list.length === 1 && s.calls.list[0] && s.calls.list[0].status === 'outstanding', JSON.stringify(s.calls.list));
  check('S2 found: amount filled with the full credit', s.el('gc-amount').value === '50', s.el('gc-amount').value);
  check('S2 found: amount is locked', s.el('gc-amount').readOnly === true);
  check('S2 found: info line text', s.el('gc-reference-info').textContent === 'Credit for Ada: ' + amt + '. The customer must buy items worth at least ' + amt + '.', s.el('gc-reference-info').textContent);
  check('S2 found: Generate stays enabled', s.el('generate-coupon-submit').disabled === false);
  await submit(s);
  check('S2 generate: amount sent is the credit (50)', s.calls.issue[0] && s.calls.issue[0].discount_amount_naira === 50);
  check('S2 generate: reference code sent in capitals', s.calls.issue[0] && s.calls.issue[0].reference_code === 'CHG-ABC123', JSON.stringify(s.calls.issue[0]));
  check('S2 generate: result line has the credit owner and code', s.el('coupon-result-detail').textContent.endsWith(' - Credit of Ada (CHG-ABC123)'), s.el('coupon-result-detail').textContent);
  check('S2 generate: no second lookup', s.calls.list.length === 1, s.calls.list.length);
  check('S2 generate: result dialog shown', visible(s, 'coupon-result-backdrop'));
  check('S2 generate: input dialog closed', !visible(s, 'generate-coupon-backdrop'));

  // ---- S3: code not found
  s = setup({ list: () => [] });
  await typeRef(s, 'CHG-NOPE00');
  check('S3 missing: message under the box', s.el('gc-reference-info').textContent === MISSING, s.el('gc-reference-info').textContent);
  check('S3 missing: Generate disabled', s.el('generate-coupon-submit').disabled === true);
  check('S3 missing: amount not locked', s.el('gc-amount').readOnly === false);
  s.el('gc-amount').value = '10';
  await submit(s);
  check('S3 missing: no coupon call', s.calls.issue.length === 0, s.calls.issue.length);
  check('S3 missing: error banner text', s.el('generate-coupon-error').textContent === MISSING, s.el('generate-coupon-error').textContent);
  check('S3 missing: error banner visible', visible(s, 'generate-coupon-error'));

  // ---- S3b: same code but not outstanding
  s = setup({ list: () => [Object.assign({}, ROW, { status: 'settled' })] });
  await typeRef(s, 'CHG-ABC123');
  check('S3b settled row counts as not found', s.el('gc-reference-info').textContent === MISSING, s.el('gc-reference-info').textContent);

  // ---- S4: fast click, no change event
  s = setup({ list: () => [ROW] });
  s.el('gc-reference').value = 'CHG-ABC123';
  await submit(s);
  check('S4 fast click: reference code sent', s.calls.issue.length === 1 && s.calls.issue[0].reference_code === 'CHG-ABC123', JSON.stringify(s.calls.issue));
  check('S4 fast click: full credit amount sent', s.calls.issue[0] && s.calls.issue[0].discount_amount_naira === 50);

  // ---- S5: server answers 409 and other errors
  const err409 = Object.assign(new Error('Something else'), { status: 409 });
  s = setup({ list: () => [ROW], issueError: err409 });
  await typeRef(s, 'CHG-ABC123');
  await submit(s);
  check('S5 409 by status: approved text', s.el('generate-coupon-error').textContent === STILL_VALID, s.el('generate-coupon-error').textContent);
  check('S5 409: Generate button enabled again', s.el('generate-coupon-submit').disabled === false);
  s = setup({ list: () => [ROW], issueError: new Error('A discount code for this credit is still valid. Try again when it expires.') });
  await typeRef(s, 'CHG-ABC123');
  await submit(s);
  check('S5 409 by words only: approved text', s.el('generate-coupon-error').textContent === STILL_VALID, s.el('generate-coupon-error').textContent);
  s = setup({ issueError: Object.assign(new Error('Other problem'), { status: 409 }) });
  s.el('gc-amount').value = '10';
  await submit(s);
  check('S5 plain code with 409: message unchanged', s.el('generate-coupon-error').textContent === 'Other problem', s.el('generate-coupon-error').textContent);
  s = setup({ list: () => [ROW], issueError: Object.assign(new Error('Boom'), { status: 500 }) });
  await typeRef(s, 'CHG-ABC123');
  await submit(s);
  check('S5 credit with a 500: message unchanged', s.el('generate-coupon-error').textContent === 'Boom', s.el('generate-coupon-error').textContent);

  // ---- S6: edit the reference code after it was found
  s = setup({ list: () => [ROW] });
  await typeRef(s, 'CHG-ABC123');
  s.el('gc-reference').value = 'CHG-ABC12';
  s.el('gc-reference').handlers.input();
  check('S6 edit: amount cleared', s.el('gc-amount').value === '', s.el('gc-amount').value);
  check('S6 edit: amount unlocked', s.el('gc-amount').readOnly === false);
  check('S6 edit: info line hidden', s.el('gc-reference-info').style.display === 'none' && s.el('gc-reference-info').textContent === '');

  // ---- S7: reopen the dialog after a locked credit
  s = setup({ list: () => [ROW] });
  await typeRef(s, 'CHG-ABC123');
  s.el('generate-coupon-error').classList.add('visible');
  s.el('generate-coupon-btn').handlers.click();
  check('S7 reopen: amount unlocked', s.el('gc-amount').readOnly === false);
  check('S7 reopen: amount empty', s.el('gc-amount').value === '', s.el('gc-amount').value);
  check('S7 reopen: info line hidden', s.el('gc-reference-info').style.display === 'none' && s.el('gc-reference-info').textContent === '');
  check('S7 reopen: form reset and dialog shown', s.formResets.n === 1 && visible(s, 'generate-coupon-backdrop'));
  check('S7 reopen: error banner hidden', !visible(s, 'generate-coupon-error'));

  // ---- S8: the lookup itself fails
  s = setup({ list: () => { throw new Error('network'); } });
  await typeRef(s, 'CHG-ABC123');
  check('S8 lookup fails: message under the box', s.el('gc-reference-info').textContent === LOOKUP_FAILED, s.el('gc-reference-info').textContent);
  s.el('gc-amount').value = '10';
  await submit(s);
  check('S8 lookup fails: no coupon call', s.calls.issue.length === 0, s.calls.issue.length);
  check('S8 lookup fails: error banner text', s.el('generate-coupon-error').textContent === LOOKUP_FAILED, s.el('generate-coupon-error').textContent);

  // ---- S9: two lookups at the same time, the slow old one must not win
  let resolve1;
  const d1 = new Promise((r) => { resolve1 = r; });
  let n = 0;
  s = setup({ list: () => { n++; if (n === 1) return d1; return [ROW2]; } });
  s.el('gc-reference').value = 'CHG-AAA111';
  s.el('gc-reference').handlers.change();
  s.el('gc-reference').value = 'CHG-BBB222';
  s.el('gc-reference').handlers.input();
  s.el('gc-reference').handlers.change();
  resolve1([ROW]);
  await settle();
  check('S9 race: amount is the second code (75)', s.el('gc-amount').value === '75', s.el('gc-amount').value);
  check('S9 race: amount locked', s.el('gc-amount').readOnly === true);
  check('S9 race: info line is for Bola', s.el('gc-reference-info').textContent.includes('Bola'), s.el('gc-reference-info').textContent);

  console.log('RESULT: ' + passed + ' passed, ' + failed + ' failed, ' + (passed + failed) + ' checks');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('TEST CRASHED: ' + (e && e.stack || e)); process.exit(1); });