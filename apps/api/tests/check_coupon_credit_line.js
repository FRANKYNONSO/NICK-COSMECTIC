"use strict";
// Checks PATCH7A-COUPON-CREDIT-LINE. Cuts the REAL couponCreditLineHtml function
// out of apps/web_admin/js/reports.js and runs it in a vm sandbox.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.resolve(__dirname, '..', '..', 'web_admin', 'js', 'reports.js');
const src = fs.readFileSync(file, 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

const m = src.match(/function couponCreditLineHtml\(c\) \{[\s\S]*?\r?\n\}/);
if (!m) {
  console.log('FAIL could not cut couponCreditLineHtml out of reports.js');
  process.exit(1);
}
let fn;
try {
  const sandbox = { escapeHtml: (s) => String(s).replace(/</g, '&lt;') };
  fn = vm.runInNewContext(m[0] + '\ncouponCreditLineHtml;', sandbox);
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}

check('1 marker is in reports.js', src.indexOf('PATCH7A-COUPON-CREDIT-LINE') >= 0);
check('2 credit coupon shows the grey line with the CHG code',
  fn({ credit_reference_code: 'CHG-482913' }).indexOf('From store credit CHG-482913') >= 0);
check('3 hand-made coupon (null) shows nothing', fn({ credit_reference_code: null }) === '');
check('4 missing field or missing coupon shows nothing', fn({}) === '' && fn(null) === '');
check('5 the code is escaped',
  fn({ credit_reference_code: '<b>' }).indexOf('&lt;b>') >= 0 && fn({ credit_reference_code: '<b>' }).indexOf('<b>') < 0);
check('6 the Code cell uses the function',
  /<strong>\$\{escapeHtml\(c\.code\)\}<\/strong>\$\{couponCreditLineHtml\(c\)\}<\/td>/.test(src));

console.log('passed ' + passed + ' failed ' + failed);
process.exit(failed ? 1 : 0);
