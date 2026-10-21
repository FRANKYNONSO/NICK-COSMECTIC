const fs = require('fs');
const path = require('path');

const adminDir = path.join(__dirname, '..', '..', 'web_admin');
const html = fs.readFileSync(path.join(adminDir, 'sales.html'), 'utf8');
const salesJs = fs.readFileSync(path.join(adminDir, 'js', 'sales.js'), 'utf8');
const receiptJs = fs.readFileSync(path.join(adminDir, 'js', 'receipt.js'), 'utf8');
const salesPy = fs.readFileSync(path.join(__dirname, '..', 'api', 'v1', 'routers', 'sales.py'), 'utf8');

function count(text, s) { return text.split(s).length - 1; }

let passed = 0;
let total = 0;
function check(name, cond) {
  total += 1;
  if (cond) { passed += 1; console.log('PASS ' + name); }
  else { console.log('FAIL ' + name); }
}

check('html: Swap option text', count(html, '<option value="exchange">Swap Item(s) for another product</option>') === 1);
check('html: option value exchange kept', count(html, '<option value="exchange">') === 1);
check('html: Item to give instead', count(html, 'Item to give instead') === 1);
check('html: old option text gone', count(html, 'Exchange for another product') === 0);
check('html: swapped item note', count(html, 'a returned item or a swapped item.') === 1);

check('sales.js: new error text', count(salesJs, 'Pick the item to swap for.') === 1);
check('sales.js: payload action exchange kept', count(salesJs, "payload.action = 'exchange';") === 1);

check('receipt.js: Swap Item(s) label twice', count(receiptJs, "exchange: 'Swap Item(s)'") === 2);
check('receipt.js: Even swap text', count(receiptJs, 'Even swap, no balance due') === 1);
check('receipt.js: old label gone', count(receiptJs, "exchange: 'Exchange'") === 0);

check('sales.py: swapping items error', count(salesPy, 'is required when swapping items.') === 1);
check('sales.py: Swap item not found', count(salesPy, 'Swap item not found') === 1);
check('sales.py: stock error text', count(salesPy, 'Not enough stock of the swap item') === 1);
check('sales.py: sale note text', count(salesPy, 'Swap Item(s) against refund on sale') === 1);
check('sales.py: action exchange kept', count(salesPy, 'body.action == "exchange"') >= 1);
check('sales.py: cash payout text', count(salesPy, 'for a swap that owes the customer money') === 1);

check('marker in sales.html', count(html, 'PATCH3E-SWAP-LABELS') === 1);
check('marker in sales.js', count(salesJs, 'PATCH3E-SWAP-LABELS') === 1);
check('marker in receipt.js', count(receiptJs, 'PATCH3E-SWAP-LABELS') === 1);
check('marker in sales.py', count(salesPy, 'PATCH3E-SWAP-LABELS') === 1);

console.log(passed + '/' + total + ' checks passed');
process.exit(passed === total ? 0 : 1);
