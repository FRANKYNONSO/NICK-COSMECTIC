'use strict';
// Checks PATCH6E-ONLINE-LABEL. Cuts the REAL paymentLabels table out of
// apps/web_admin/js/receipt.js and runs it in a vm sandbox.
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

const m = src.match(/const paymentLabels = \{[\s\S]*?\r?\n\s*\};/);
if (!m) {
  console.log('FAIL could not cut paymentLabels out of receipt.js');
  process.exit(1);
}
let labels;
try {
  labels = vm.runInNewContext(m[0] + '\npaymentLabels;', {});
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}

check('1 marker is in receipt.js', src.indexOf('PATCH6E-ONLINE-LABEL') >= 0);
check('2 online is shown as Online', labels.online === 'Online');
check('3 cash, card and transfer are unchanged',
  labels.cash === 'Cash' && labels.card === 'Card' && labels.transfer === 'Transfer');
check('4 split is unchanged', labels.split === 'Split payment');

console.log('passed ' + passed + ' failed ' + failed);
process.exit(failed ? 1 : 0);
