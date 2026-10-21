'use strict';
// Checks PATCH6C-LEFT-FIGURE. Cuts the REAL functions out of apps/web_admin/js/warehouse.js
// and runs them in a vm sandbox with small stubs for the page helpers.
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

const names = ['normalizeWhUnitLabel', 'whBlankComponents', 'whFmtHalves', 'whStageRowTotal',
  'whComponentsForInvoiceItem', 'whReceivedNote', 'whLeftHint', 'whLineReference', 'whStagingRowHtml'];
const parts = names.map(cut);
if (parts.some((p) => !p)) {
  console.log('FAIL could not cut the real functions out of warehouse.js: '
    + names.filter((n, i) => !parts[i]).join(', '));
  process.exit(1);
}
const sandbox = {
  console: console,
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'),
  whFormatQtySummary: () => 'QTY',
  whMultiplierSummary: () => 'MULT',
};
vm.createContext(sandbox);
try {
  vm.runInContext(parts.join('\n'), sandbox);
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}
const build = sandbox.whComponentsForInvoiceItem;

function item(o) {
  return Object.assign({
    unit_label: 'cartons',
    cartons_total_halves: 4, cartons_remaining_halves: 4,
    dozens_total_halves: 0, dozens_remaining_halves: 0,
    bags_total_halves: 0, bags_remaining_halves: 0,
    pieces_total: 0, pieces_remaining: 0,
    pieces_per_carton: null,
    default_pieces_per_carton: null,
    default_pieces_per_bag: null,
    default_pieces_per_roll: null,
  }, o || {});
}
function mkRow(o) {
  const r = build(item(o));
  r.source = 'invoice';
  r.key = 'k1';
  r.invoice_date = '2026-10-17';
  r.supplier_name = 'Sup';
  r.item_name = 'Soap';
  return r;
}
const SENT = '40 piece(s) of this line were already received in Receive Goods, so only 60 piece(s) can still be pushed.';

check('1 marker is in warehouse.js', src.indexOf('PATCH6C-LEFT-FIGURE') >= 0);

let r = mkRow({ received_pieces: 40, outstanding_pieces: 60 });
check('2 the two numbers are copied into row.line', r.line.received_pieces === 40 && r.line.outstanding_pieces === 60);

r = mkRow();
check('3 no receipts: received 0 and outstanding null', r.line.received_pieces === 0 && r.line.outstanding_pieces === null);

r = mkRow({ received_pieces: 40, outstanding_pieces: 60 });
check('4 note text for 40 received and 60 outstanding', sandbox.whReceivedNote(r) === SENT);

r = mkRow({ received_pieces: 0, outstanding_pieces: null });
check('5 note is empty when nothing was received', sandbox.whReceivedNote(r) === '');

r = mkRow({ received_pieces: 40, outstanding_pieces: null });
check('6 note is empty when outstanding is unknown', sandbox.whReceivedNote(r) === '');

r = mkRow();
r.line = null;
check('7 note is empty for a manual row without a line', sandbox.whReceivedNote(r) === '');

r = mkRow({ received_pieces: 40, outstanding_pieces: 60 });
let ref = sandbox.whLineReference(r);
check('8 popup reference keeps the old text and adds the note',
  ref.indexOf('On the invoice line') === 0 && ref.indexOf(SENT) > 0);

r = mkRow();
check('9 popup reference is unchanged for a line without receipts',
  sandbox.whLineReference(r) === 'On the invoice line \u2014 Cartons: 2 on invoice, 2 left');

r = mkRow();
r.line = null;
check('10 popup reference is unchanged for a manual row',
  sandbox.whLineReference(r) === 'Manual push \u2014 nothing on an invoice to cap against.');

r = mkRow({ received_pieces: 40, outstanding_pieces: 60 });
let html = sandbox.whStagingRowHtml(r);
check('11 row html shows the old left line and the new note',
  html.indexOf('left on invoice:') > 0 && html.indexOf(SENT) > 0);

r = mkRow();
html = sandbox.whStagingRowHtml(r);
check('12 row html has no note for a line without receipts',
  html.indexOf('left on invoice:') > 0 && html.indexOf('already received') < 0);

r = mkRow({ received_pieces: 100, outstanding_pieces: 0 });
check('13 note is still shown when outstanding is 0',
  sandbox.whReceivedNote(r) === '100 piece(s) of this line were already received in Receive Goods, so only 0 piece(s) can still be pushed.');

r = mkRow();
check('14 the old left hint text is unchanged', sandbox.whLeftHint(r) === 'left on invoice: 2 carton(s)');

console.log('passed ' + passed + ' failed ' + failed);
process.exit(failed ? 1 : 0);
