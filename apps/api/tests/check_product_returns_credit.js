"use strict";
// Checks PATCH8A-PRODUCT-RETURNS-CREDIT. Cuts the REAL functions out of
// apps/web_admin/js/inventory.js and runs them in a vm sandbox with a fake page.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = path.resolve(__dirname, '..', '..', 'web_admin');
const invSrc = fs.readFileSync(path.join(webDir, 'js', 'inventory.js'), 'utf8');
const htmlSrc = fs.readFileSync(path.join(webDir, 'inventory.html'), 'utf8');
const apiSrc = fs.readFileSync(path.join(webDir, 'js', 'api.js'), 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

const rowM = invSrc.match(/function productReturnCreditRowHtml\(r\) \{[\s\S]*?\r?\n\}/);
const loadM = invSrc.match(/async function loadProductReturnsAndCredit\(productId\) \{[\s\S]*?\r?\n\}/);
if (!rowM || !loadM) {
  console.log('FAIL could not cut the new functions out of inventory.js');
  process.exit(1);
}

const tbody = { innerHTML: '' };
let rowsToReturn = [];
let failWith = null;
let changeIdDuring = false;
let ctx = null;
const sandbox = {
  escapeHtml: (s) => String(s).replace(/</g, '&lt;'),
  Api: {
    productReturnsAndCredit: async () => {
      if (failWith) throw new Error(failWith);
      if (changeIdDuring) ctx.setId('other');
      return rowsToReturn;
    },
  },
  document: { getElementById: (id) => (id === 'history-returns-tbody' ? tbody : null) },
};
const code = 'let currentHistoryProductId = "p1";\n' + rowM[0] + '\n' + loadM[0] + '\n' +
  '({ run: loadProductReturnsAndCredit, rowHtml: productReturnCreditRowHtml, setId: (v) => { currentHistoryProductId = v; } });';
try {
  ctx = vm.runInNewContext(code, sandbox);
} catch (err) {
  console.log('FAIL the cut code did not load: ' + err.message);
  process.exit(1);
}

(async () => {
  const refundRow = { happened_at: '2026-10-18T10:00:00Z', branch_name: 'Main', what: 'Refund, put back on shelf', quantity: 2, detail: 'Reason: changed mind' };
  const creditRow = { happened_at: '2026-10-17T10:00:00Z', branch_name: 'Main', what: 'Taken on credit', quantity: 3, detail: 'Ada Obi, outstanding' };

  check('1 marker is in inventory.js', invSrc.indexOf('PATCH8A-PRODUCT-RETURNS-CREDIT') >= 0);

  const h1 = ctx.rowHtml(refundRow);
  check('2 refund row shows branch, what, quantity and detail',
    h1.indexOf('Main') >= 0 && h1.indexOf('Refund, put back on shelf') >= 0 &&
    h1.indexOf('<td>2</td>') >= 0 && h1.indexOf('Reason: changed mind') >= 0);

  const h2 = ctx.rowHtml(creditRow);
  check('3 credit row shows Taken on credit and the customer with status',
    h2.indexOf('Taken on credit') >= 0 && h2.indexOf('Ada Obi, outstanding') >= 0);

  const h3 = ctx.rowHtml({ happened_at: '2026-10-18T10:00:00Z', branch_name: null, what: 'Refund, not put back', quantity: 1, detail: null });
  check('4 missing branch and detail show a dash', h3.split('&mdash;').length - 1 === 2);

  const h4 = ctx.rowHtml({ happened_at: '2026-10-18T10:00:00Z', branch_name: 'Main', what: '<b>', quantity: 1, detail: null });
  check('5 text is escaped', h4.indexOf('&lt;b>') >= 0 && h4.indexOf('<b>') < 0);

  check('6 the date shows the year', h1.indexOf('2026') >= 0);

  check('7 openHistoryModal calls the loader',
    /currentHistoryProductName = productName;\s*loadProductReturnsAndCredit\(productId\);\s*const backdrop/.test(invSrc));

  check('8 inventory.html has the new table body', htmlSrc.indexOf('id="history-returns-tbody"') >= 0);

  check('9 api.js has the new function and route',
    apiSrc.indexOf('productReturnsAndCredit(') >= 0 && apiSrc.indexOf('/inventory/products/${productId}/returns-and-credit') >= 0);

  rowsToReturn = [];
  await ctx.run('p1');
  check('10 empty list shows the empty message', tbody.innerHTML.indexOf('No refunds or credit for this product yet.') >= 0);

  rowsToReturn = [refundRow, creditRow];
  await ctx.run('p1');
  check('11 two rows are drawn', tbody.innerHTML.split('<tr>').length - 1 === 2);

  failWith = 'Server said no';
  await ctx.run('p1');
  check('12 an error is shown in the table', tbody.innerHTML.indexOf('Server said no') >= 0);

  failWith = null;
  changeIdDuring = true;
  rowsToReturn = [{ happened_at: '2026-10-18T10:00:00Z', branch_name: 'Main', what: 'LATE ROW', quantity: 1, detail: null }];
  await ctx.run('p1');
  check('13 a late answer for another product is ignored', tbody.innerHTML.indexOf('LATE ROW') < 0);

  console.log('passed ' + passed + ' failed ' + failed);
  process.exit(failed ? 1 : 0);
})();
