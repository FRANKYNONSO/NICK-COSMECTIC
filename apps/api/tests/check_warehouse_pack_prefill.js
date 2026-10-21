'use strict';
// Checks PATCH5C-WAREHOUSE-PACK-PREFILL. Cuts the REAL whComponentsForInvoiceItem,
// whBlankComponents and normalizeWhUnitLabel out of apps/web_admin/js/warehouse.js
// and runs them in a vm sandbox.
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

const parts = ['normalizeWhUnitLabel', 'whBlankComponents', 'whComponentsForInvoiceItem'].map(cut);
if (parts.some((p) => !p)) {
  console.log('FAIL could not cut the real functions out of warehouse.js');
  process.exit(1);
}
const sandbox = { console: console };
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
function bagsItem(o) { return item(Object.assign({ unit_label: 'bags', cartons_total_halves: 0, cartons_remaining_halves: 0, bags_total_halves: 6, bags_remaining_halves: 6 }, o || {})); }
function dozensItem(o) { return item(Object.assign({ unit_label: 'dozens', cartons_total_halves: 0, cartons_remaining_halves: 0, dozens_total_halves: 4, dozens_remaining_halves: 4 }, o || {})); }

check('1 marker is in warehouse.js', src.indexOf('PATCH5C-WAREHOUSE-PACK-PREFILL') >= 0);

let r = build(item({ pieces_per_carton: 24, default_pieces_per_carton: 48 }));
check('2 cartons line: the line number wins over the product default', r.cartons_pieces_per_unit === 24);
r = build(item({ default_pieces_per_carton: 48 }));
check('3 cartons line: product carton default when the line has none', r.cartons_pieces_per_unit === 48);
r = build(item());
check('4 cartons line: nothing known gives an empty box', r.cartons_pieces_per_unit === null);

r = build(item({ default_pieces_per_bag: 20 }));
check('5 hidden bag box stays empty even with a bag default', r.show_bags === false && r.bags_pieces_per_unit === null);
r = build(item({ default_pieces_per_bag: 20 }));
check('6 hidden dozen box stays empty', r.show_dozens === false && r.dozens_pieces_per_unit === null);

r = build(bagsItem({ pieces_per_carton: 20, default_pieces_per_bag: 30 }));
check('7 bags line: the line number wins', r.bags_pieces_per_unit === 20);
r = build(bagsItem({ default_pieces_per_bag: 30 }));
check('8 bags line: product bag default when the line has none', r.bags_pieces_per_unit === 30);
r = build(bagsItem({ default_pieces_per_carton: 48 }));
check('9 bags line: carton box gets the product carton default', r.cartons_pieces_per_unit === 48);
r = build(bagsItem());
check('10 bags line: nothing known gives an empty bag box', r.bags_pieces_per_unit === null);

r = build(item({ unit_label: 'rolls', pieces_per_carton: 30, default_pieces_per_roll: 12, default_pieces_per_carton: 48 }));
check('11 rolls line: the line number wins', r.cartons_pieces_per_unit === 30);
r = build(item({ unit_label: 'rolls', default_pieces_per_roll: 12, default_pieces_per_carton: 48 }));
check('12 rolls line: product roll default when the line has none', r.cartons_pieces_per_unit === 12);
r = build(item({ unit_label: 'rolls', default_pieces_per_carton: 48 }));
check('13 rolls line: only the roll default counts, not the carton default', r.cartons_pieces_per_unit === null);
r = build(item({ unit_label: 'Rolls', default_pieces_per_roll: 12 }));
check('14 capital letters in "Rolls" still count as a rolls line', r.cartons_pieces_per_unit === 12);

r = build(dozensItem());
check('15 dozens line: dozen box is 12', r.show_dozens === true && r.dozens_pieces_per_unit === 12);
r = build(dozensItem({ pieces_per_carton: 24 }));
check('16 dozens line with a line number: dozen box is still 12', r.dozens_pieces_per_unit === 12);
r = build(item({ dozens_total_halves: 2, dozens_remaining_halves: 2 }));
check('17 dozens on a cartons line: dozen box is 12', r.show_dozens === true && r.dozens_pieces_per_unit === 12);

r = build(item());
check('18 show flags of a cartons line', r.show_cartons === true && r.show_dozens === false && r.show_bags === false);
check('19 the invoice reference object is kept', r.line !== null && r.line.cartons_total_halves === 4 && r.line.pieces_total === 0);

r = build({ unit_label: 'cartons', pieces_per_carton: 24 });
check('20 old server answer (no halves): carton box 24 and no reference', r.show_cartons === true && r.cartons_pieces_per_unit === 24 && r.line === null);
const old = item({ pieces_per_carton: 24 });
delete old.default_pieces_per_carton; delete old.default_pieces_per_bag; delete old.default_pieces_per_roll;
r = build(old);
check('21 no default fields at all: nothing breaks, carton box keeps the line number', r.cartons_pieces_per_unit === 24 && r.bags_pieces_per_unit === null);
r = build(item({ default_pieces_per_carton: 0 }));
check('22 a default of 0 is treated as none', r.cartons_pieces_per_unit === null);
r = build(item({ pieces_per_carton: 24, default_pieces_per_carton: 48 }));
check('23 counts are never prefilled', r.cartons_count === 0 && r.dozens_count === 0 && r.bags_count === 0 && r.pieces_count === 0 && r.cartons_half === false);

console.log('RESULT: passed ' + passed + ' of ' + (passed + failed));
process.exit(failed ? 1 : 0);
