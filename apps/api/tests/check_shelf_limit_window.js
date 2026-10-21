'use strict';
// Checks the PATCH4A Shelf limit window. Loads the REAL window code from
// the end of apps/web_admin/js/inventory.js into a vm sandbox with a fake page.
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
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// A fake page element. value is always a string, like a real input.
function makeEl(id) {
  const cls = new Set();
  let v = '';
  const e = {
    id: id,
    textContent: '',
    disabled: false,
    handlers: {},
    classList: {
      add: (c) => { cls.add(c); },
      remove: (c) => { cls.delete(c); },
      contains: (c) => cls.has(c),
    },
    addEventListener: (t, f) => { e.handlers[t] = f; },
  };
  Object.defineProperty(e, 'value', { get: () => v, set: (x) => { v = String(x); } });
  return e;
}

const IDS = ['shelf-limit-backdrop', 'shelf-limit-title', 'shelf-limit-branch', 'shelf-limit-error',
  'shelf-limit-status', 'shelf-limit-form', 'shelf-limit-max', 'shelf-limit-alert',
  'shelf-limit-cancel', 'shelf-limit-submit'];
const els = {};
IDS.forEach((id) => { els[id] = makeEl(id); });
const E = (id) => els[id];
const visible = (id) => E(id).classList.contains('visible');

const state = { branchId: 'b1' };
const toasts = [];
const listCalls = [];
const saveCalls = [];
const sandbox = {
  console: console,
  document: { getElementById: (id) => { if (!els[id]) throw new Error('no element ' + id); return els[id]; } },
  BranchContext: { get: () => state.branchId, getName: () => 'Main Shop' },
  currentUser: { branch_id: 'ub' },
  Api: {},
  showToast: (m) => { toasts.push(m); },
};
vm.createContext(sandbox);

const idx = src.lastIndexOf('// PATCH4A-SHELF-LIMIT-WINDOW');
if (idx < 0) { console.log('FAIL marker not found in inventory.js'); process.exit(1); }
vm.runInContext(src.slice(idx), sandbox);

function useList(rows) {
  listCalls.length = 0;
  sandbox.Api.listShelfSettings = async (arg) => {
    listCalls.push(arg);
    if (rows instanceof Error) throw rows;
    return rows;
  };
}
function useSave(behaviour) {
  saveCalls.length = 0;
  sandbox.Api.saveShelfSetting = (payload) => { saveCalls.push(payload); return behaviour(); };
}
const submit = () => E('shelf-limit-form').handlers.submit({ preventDefault() {} });
const open = (id, name) => sandbox.openShelfLimitModal(id, name);

async function main() {
  // --- file text checks (2)
  const btnLine = src.split(/\r?\n/).find((l) => l.includes('data-shelf-limit="${p.id}"')) || '';
  check('01 button line needs manage_inventory and a picked branch',
    btnLine.includes("Perms.can('manage_inventory') && branchId") && btnLine.includes('>Shelf limit</button>'));
  check('02 click wiring calls openShelfLimitModal',
    src.includes("[data-shelf-limit=\"${p.id}\"]`)?.addEventListener('click', () => openShelfLimitModal(p.id, p.name));"));

  // --- A: no saved limit (8)
  useList([]);
  await open('p1', 'Fanta');
  check('03 window is shown', visible('shelf-limit-backdrop'));
  check('04 title', E('shelf-limit-title').textContent === 'Shelf limit: Fanta');
  check('05 branch line', E('shelf-limit-branch').textContent === 'Branch: Main Shop');
  check('06 suggested max is 24', E('shelf-limit-max').value === '24');
  check('07 suggested alert is 5', E('shelf-limit-alert').value === '5');
  check('08 status says no limit set yet',
    E('shelf-limit-status').textContent === 'No limit set yet. The suggested numbers are filled in.');
  check('09 Save is enabled', E('shelf-limit-submit').disabled === false);
  check('10 limit was read for this branch and product', same(listCalls[0], { branchId: 'b1', productId: 'p1' }));

  // --- B: saved limit, another product ignored (3)
  useList([
    { product_id: 'p2', max_shelf_quantity: 99, replenish_alert_level: 9 },
    { product_id: 'p1', max_shelf_quantity: 30, replenish_alert_level: 6 },
  ]);
  await open('p1', 'Fanta');
  check('11 saved max is filled in', E('shelf-limit-max').value === '30');
  check('12 saved alert is filled in', E('shelf-limit-alert').value === '6');
  check('13 status says saved limit', E('shelf-limit-status').textContent === 'Saved limit for this branch.');

  // --- C: the saved limit cannot be read (4)
  useList(new Error('boom'));
  await open('p1', 'Fanta');
  check('14 error is shown', visible('shelf-limit-error'));
  check('15 error text is the server text', E('shelf-limit-error').textContent === 'boom');
  check('16 Save stays disabled', E('shelf-limit-submit').disabled === true);
  check('17 status line is empty', E('shelf-limit-status').textContent === '');

  // --- D: bad numbers (5)
  const badMsg = 'Type whole numbers, 0 or more.';
  useList([]);
  await open('p1', 'Fanta');
  useSave(() => Promise.resolve({}));
  E('shelf-limit-error').textContent = '';
  E('shelf-limit-max').value = '';
  E('shelf-limit-alert').value = '5';
  await submit();
  check('18 empty box gives the message', E('shelf-limit-error').textContent === badMsg);
  check('19 empty box: nothing saved', saveCalls.length === 0);
  E('shelf-limit-error').textContent = '';
  E('shelf-limit-max').value = '2.5';
  E('shelf-limit-alert').value = '1';
  await submit();
  check('20 2.5 gives the message', E('shelf-limit-error').textContent === badMsg);
  check('21 2.5: nothing saved', saveCalls.length === 0);
  E('shelf-limit-error').textContent = '';
  E('shelf-limit-max').value = '-1';
  E('shelf-limit-alert').value = '0';
  await submit();
  check('22 -1 gives the message', E('shelf-limit-error').textContent === badMsg);

  // --- E: alert higher than max (2)
  E('shelf-limit-error').textContent = '';
  E('shelf-limit-max').value = '5';
  E('shelf-limit-alert').value = '10';
  await submit();
  check('23 alert above max gives the message',
    E('shelf-limit-error').textContent === 'The alert level cannot be higher than the most pieces on the shelf.');
  check('24 alert above max: nothing saved', saveCalls.length === 0);

  // --- F: a good save (7)
  useList([{ product_id: 'p1', max_shelf_quantity: 30, replenish_alert_level: 6 }]);
  await open('p1', 'Fanta');
  let release;
  const pending = new Promise((r) => { release = r; });
  useSave(() => pending);
  E('shelf-limit-max').value = '30';
  E('shelf-limit-alert').value = '6';
  toasts.length = 0;
  const run = submit();
  check('25 Save is disabled while the request runs', E('shelf-limit-submit').disabled === true);
  release({});
  await run;
  check('26 saved exactly once', saveCalls.length === 1);
  check('27 payload is right', same(saveCalls[0],
    { branch_id: 'b1', product_id: 'p1', max_shelf_quantity: 30, replenish_alert_level: 6 }));
  check('28 Save is enabled again', E('shelf-limit-submit').disabled === false);
  check('29 window is closed', !visible('shelf-limit-backdrop'));
  check('30 toast text', same(toasts, ['Shelf limit saved.']));
  check('31 no error shown', !visible('shelf-limit-error'));

  // --- G: the server refuses (4)
  useList([]);
  await open('p1', 'Fanta');
  useSave(() => Promise.reject(new Error('Not allowed')));
  E('shelf-limit-max').value = '30';
  E('shelf-limit-alert').value = '6';
  toasts.length = 0;
  await submit();
  check('32 server text is shown', E('shelf-limit-error').textContent === 'Not allowed');
  check('33 window stays open', visible('shelf-limit-backdrop'));
  check('34 Save is enabled again after the error', E('shelf-limit-submit').disabled === false);
  check('35 no success toast', toasts.length === 0);

  // --- H: Cancel (1)
  E('shelf-limit-cancel').handlers.click();
  check('36 Cancel closes the window', !visible('shelf-limit-backdrop'));

  // --- I: zeros are allowed (1)
  useList([]);
  await open('p1', 'Fanta');
  useSave(() => Promise.resolve({}));
  E('shelf-limit-max').value = '0';
  E('shelf-limit-alert').value = '0';
  await submit();
  check('37 zero and zero can be saved', saveCalls.length === 1 && same(saveCalls[0],
    { branch_id: 'b1', product_id: 'p1', max_shelf_quantity: 0, replenish_alert_level: 0 }));

  // --- J: no branch picked, use the user's own branch (2)
  state.branchId = null;
  useList([]);
  await open('p1', 'Fanta');
  check('38 read uses the user branch', listCalls[0].branchId === 'ub');
  useSave(() => Promise.resolve({}));
  E('shelf-limit-max').value = '24';
  E('shelf-limit-alert').value = '5';
  await submit();
  check('39 save uses the user branch', saveCalls.length === 1 && saveCalls[0].branch_id === 'ub');
  state.branchId = 'b1';

  console.log(passed + '/' + (passed + failed) + ' checks passed');
  process.exit(failed ? 1 : 0);
}
main().catch((err) => { console.log('CRASH ' + err.stack); process.exit(1); });
