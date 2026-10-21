'use strict';
// Checks PATCH6A-SHELF-FILL. Cuts the REAL validation code, typing handler and checkHtml
// out of apps/web_admin/js/receive-goods.js and runs them in a vm sandbox with a fake page.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'receive-goods.js');
const src = fs.readFileSync(file, 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

const aStart = src.indexOf('function parseWhole(');
const aEnd = src.indexOf('function buildLines()');
const hStart = src.indexOf("el('rg-tbody').addEventListener('input'");
const hEnd = src.indexOf("window.addEventListener('branch-context-changed'");
const cStart = src.indexOf('function checkHtml(c)');
const cEnd = src.indexOf('function rowHtml(p)');
if (aStart < 0 || aEnd <= aStart || hStart < 0 || hEnd <= hStart || cStart < 0 || cEnd <= cStart) {
  console.log('FAIL could not cut the real code out of receive-goods.js');
  process.exit(1);
}

const handlers = {};
const boxes = {};
const tbody = {
  addEventListener: (type, fn) => { handlers[type] = fn; },
  querySelector: (sel) => {
    const m = /data-row="([^"]+)"/.exec(sel);
    if (!m || !boxes[m[1]]) return null;
    return { querySelector: (s2) => (s2.indexOf('data-field="shelf"') >= 0 ? boxes[m[1]] : null) };
  },
};
const sandbox = {
  console: console,
  el: (id) => (id === 'rg-tbody' ? tbody : {}),
};
vm.createContext(sandbox);

const prefix = [
  'const MAX_QTY = 1000000;',
  'const DEFAULT_MAX_SHELF = 24;',
  'let entries = {};',
  'let summaryById = {};',
  'let receipt = null;',
  'let dirty = false;',
  'function showError() {}',
  'function updateRowComputed() {}',
  'function renderDrafts() {}',
  'function refreshSummary() {}',
  'function escapeHtml(s) { return String(s); }',
].join('\n') + '\n';
const helpers = [
  'function __set(e, s) { entries = e; summaryById = s; shelfAuto = {}; }',
  'function __setEntries(e) { entries = e; shelfAuto = {}; }',
  'function __entries() { return entries; }',
  'function __auto() { return shelfAuto; }',
  'function __check(id) { return checkEntry(id); }',
  'function __html(c) { return checkHtml(c); }',
].join('\n') + '\n';

try {
  vm.runInContext(
    prefix + src.slice(aStart, aEnd) + '\n' + helpers + src.slice(cStart, cEnd) + '\n' + src.slice(hStart, hEnd),
    sandbox
  );
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}
if (typeof handlers.input !== 'function') {
  console.log('FAIL the typing handler was not installed');
  process.exit(1);
}

function reset() {
  ['p1', 'p2', 'p3', 'p4'].forEach((id) => { boxes[id] = { value: '' }; });
  sandbox.__set({}, {
    p1: { limits_configured: true, available_shelf_capacity: 19 },
    p2: { limits_configured: false, available_shelf_capacity: 24 },
    p3: { limits_configured: true, available_shelf_capacity: 0 },
  });
}
function type(id, field, value) {
  if (field === 'shelf') boxes[id].value = value;
  handlers.input({ target: { dataset: { pid: id, field: field }, value: value } });
}
function entry(id) { return sandbox.__entries()[id]; }

let r;

check('1 marker is in receive-goods.js', src.indexOf('PATCH6A-SHELF-FILL') >= 0);

reset(); type('p1', 'received', '24');
check('2 received 24, room 19: shelf box and entry are 19', boxes.p1.value === '19' && entry('p1').shelf === '19');
r = sandbox.__check('p1');
check('3 the note says 19 fit and 5 go to the warehouse',
  r.shelf === 19 && r.warehouse === 5 &&
  r.autoNote === 'Shelf filled in: 19 fit under the limit, the other 5 go to the warehouse. You can change it.');
check('4 no over-limit warning for the filled-in number', r.warn === null);

reset(); type('p1', 'received', '10');
check('5 received 10 fits: box is 10 and there is no note', boxes.p1.value === '10' && sandbox.__check('p1').autoNote === undefined);

reset(); type('p1', 'received', '19');
check('6 received equal to the room: box is 19 and there is no note', boxes.p1.value === '19' && sandbox.__check('p1').autoNote === undefined);

reset(); type('p3', 'received', '7');
check('7 full shelf: box is 0', boxes.p3.value === '0');
check('8 full shelf: the shelf-is-full note', sandbox.__check('p3').autoNote === 'The shelf is full, so all of them go to the warehouse.');

reset(); type('p2', 'received', '24');
check('9 no saved limit: nothing is filled in', boxes.p2.value === '' && entry('p2').shelf === '');
reset(); type('p4', 'received', '24');
check('10 no figures at all: nothing is filled in', boxes.p4.value === '' && entry('p4').shelf === '');

reset(); type('p1', 'received', '24'); type('p1', 'shelf', '10');
check('11 typing in To shelf marks it as staff-typed', sandbox.__auto().p1 === false);
type('p1', 'received', '12');
check('12 a later change of Received keeps the staff number', boxes.p1.value === '10' && entry('p1').shelf === '10');

reset(); type('p1', 'shelf', '5'); type('p1', 'received', '24');
check('13 staff typed To shelf first: it stays 5', boxes.p1.value === '5' && entry('p1').shelf === '5');

reset(); type('p1', 'received', '24'); type('p1', 'received', '');
check('14 clearing Received removes the filled-in number', boxes.p1.value === '' && entry('p1') === undefined);
reset(); type('p1', 'received', '24'); type('p1', 'received', 'abc');
check('15 an invalid Received removes the filled-in number', boxes.p1.value === '');
reset(); type('p1', 'received', '24'); type('p1', 'received', '0');
check('16 Received 0 removes the filled-in number', boxes.p1.value === '');

reset(); type('p1', 'received', '24'); type('p1', 'received', '15');
check('17 changing Received refills the box (15)', boxes.p1.value === '15');
reset(); type('p1', 'received', '24'); type('p1', 'shelf', ''); type('p1', 'received', '15');
check('18 after staff cleared the shelf box, a new Received refills it', boxes.p1.value === '15');

reset(); type('p1', 'received', '24'); type('p1', 'cost', '150');
check('19 typing a cost leaves the shelf number alone', entry('p1').shelf === '19' && boxes.p1.value === '19');

reset();
sandbox.__setEntries({ p1: { received: '24', shelf: '19', cost: '' } });
boxes.p1.value = '19';
type('p1', 'received', '30');
check('20 a number loaded from a saved draft is never overwritten', entry('p1').shelf === '19');

reset(); type('p1', 'received', '1000001');
check('21 a too large Received gives no suggestion', boxes.p1.value === '');

reset(); type('p1', 'shelf', '24'); type('p1', 'received', '24');
r = sandbox.__check('p1');
check('22 staff typed 24: the old warning "Only 19 unit(s) fit" is still shown', typeof r.warn === 'string' && r.warn.indexOf('Only 19 unit(s) fit') === 0);
check('23 staff typed 24: no automatic note', r.autoNote === undefined);

check('24 starting a new entry clears the memory of automatic numbers',
  /function resetEntries\(\) \{\s*entries = \{\};\s*shelfAuto = \{\};/.test(src));
check('25 opening a draft clears the memory of automatic numbers',
  /entries = next;\s*shelfAuto = \{\};/.test(src));

const html = sandbox.__html({ blank: false, error: null, warn: null, info: 'INFO TEXT', note: null, autoNote: 'AUTO TEXT' });
check('26 the automatic note is shown in the check cell', html.indexOf('AUTO TEXT') >= 0);
check('27 the old info text is still shown', html.indexOf('INFO TEXT') >= 0);

console.log('RESULT: passed ' + passed + ' of ' + (passed + failed));
process.exit(failed ? 1 : 0);
