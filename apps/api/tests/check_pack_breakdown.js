'use strict';
// Checks PATCH9A-UNITS on the receipt: pulls the REAL packBreakdownText, mergePackLines
// and packLineHtml out of apps/web_admin/js/receipt.js and runs them in a vm sandbox.
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

const start = src.indexOf('// PATCH9A-UNITS -- "30 pieces"');
const end = src.indexOf('const paymentLabels = {');
if (start < 0 || end < 0 || end < start) {
  console.log('FAIL could not find the PATCH9A helpers in receipt.js');
  process.exit(1);
}
const code = src.slice(start, end);
const sandbox = { escapeHtml: (s) => String(s) };
vm.runInNewContext(code + '\nthis.packBreakdownText = packBreakdownText; this.mergePackLines = mergePackLines; this.packLineHtml = packLineHtml;', sandbox);
const { packBreakdownText, mergePackLines, packLineHtml } = sandbox;

const carton = { code: 'carton', label: 'Carton', plural_label: 'Cartons', pieces: 24 };
const dozen = { code: 'dozen', label: 'Dozen', plural_label: 'Dozens', pieces: 12 };

check('1 helpers are in receipt.js', src.indexOf('PATCH9A-UNITS') >= 0);
check('2 30 pieces = 1 carton + 6 pieces', packBreakdownText(30, [dozen, carton]) === '1 carton + 6 pieces');
check('3 100 pieces = 4 cartons + 4 pieces', packBreakdownText(100, [carton, dozen]) === '4 cartons + 4 pieces');
check('4 exactly one carton', packBreakdownText(24, [carton]) === '1 carton');
check('5 under one unit shows nothing extra', packBreakdownText(11, [carton, dozen]) === '');
check('6 no pack sizes shows nothing extra', packBreakdownText(30, []) === '' && packBreakdownText(30, undefined) === '');
check('7 dozen counts in too', packBreakdownText(40, [carton, dozen]) === '1 carton + 1 dozen + 4 pieces');

const rows = [
  { product_id: 'a', unit_price_naira: 100, quantity: 24, line_total_naira: 2400, pack_units: [carton] },
  { product_id: 'a', unit_price_naira: 100, quantity: 6, line_total_naira: 600, pack_units: [carton] },
  { product_id: 'b', unit_price_naira: 50, quantity: 2, line_total_naira: 100, pack_units: [] },
  { unit_price_naira: 10, quantity: 1, line_total_naira: 10 },
  { unit_price_naira: 10, quantity: 1, line_total_naira: 10 },
];
const merged = mergePackLines(rows);
check('8 two batch rows of one product become one line', merged.length === 4 && merged[0].quantity === 30 && merged[0].line_total_naira === 3000);
check('9 rows with no product id are never merged', merged.filter((m) => !m.product_id).length === 2);
check('10 the original rows are not changed', rows[0].quantity === 24);
check('11 merged line shows 1 carton + 6 pieces', packLineHtml(merged[0]).indexOf('1 carton + 6 pieces') >= 0);
check('12 a line with no whole unit adds nothing', packLineHtml(merged[1]) === '');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
