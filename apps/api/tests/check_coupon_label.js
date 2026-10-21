const fs = require('fs');
const path = require('path');

const file = path.join(__dirname, '..', '..', 'web_admin', 'js', 'pending-change.js');
const text = fs.readFileSync(file, 'utf8');

let passed = 0;
let total = 0;
function check(name, cond) {
  total += 1;
  if (cond) { passed += 1; console.log('PASS ' + name); }
  else { console.log('FAIL ' + name); }
}

const m = text.match(/const methodLabels = (\{[^}]*\});/);
check('methodLabels line found', !!m);
const labels = m ? (new Function('return ' + m[1]))() : {};
check('cash label is Cash', labels.cash === 'Cash');
check('transfer label is Bank transfer', labels.transfer === 'Bank transfer');
check('airtime label is Airtime', labels.airtime === 'Airtime');
check('coupon label is Discount code', labels.coupon === 'Discount code');
check('marker appears exactly once', text.split('PATCH3D-COUPON-LABEL').length - 1 === 1);

console.log(passed + '/' + total + ' checks passed');
process.exit(passed === total ? 0 : 1);
