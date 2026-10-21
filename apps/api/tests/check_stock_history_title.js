"use strict";
// Checks PATCH8B-STOCK-HISTORY-TITLE against the REAL inventory.html and inventory.js.
const fs = require('fs');
const path = require('path');

const webDir = path.resolve(__dirname, '..', '..', 'web_admin');
const htmlSrc = fs.readFileSync(path.join(webDir, 'inventory.html'), 'utf8');
const jsSrc = fs.readFileSync(path.join(webDir, 'js', 'inventory.js'), 'utf8');
let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS ' + name); }
  else { failed++; console.log('FAIL ' + name); }
}

check('1 marker is in inventory.html', htmlSrc.indexOf('PATCH8B-STOCK-HISTORY-TITLE') >= 0);
check('2 the window starting title says Stock history',
  htmlSrc.indexOf('<h3 id="history-modal-title">Stock history</h3>') >= 0);
check('3 the title built in the script uses Stock history and the product name',
  /textContent = `Stock history \S+ \$\{productName\}`/.test(jsSrc));
check('4 the old visible title is gone from both files',
  htmlSrc.indexOf('>Restock history</h3>') < 0 && jsSrc.indexOf('textContent = `Restock history') < 0);
check('5 the Record loss text points to the Stock history',
  htmlSrc.indexOf('batch from the Stock history above:') >= 0 && htmlSrc.indexOf('batch from the Restock history above:') < 0);

console.log('passed ' + passed + ' failed ' + failed);
process.exit(failed ? 1 : 0);
