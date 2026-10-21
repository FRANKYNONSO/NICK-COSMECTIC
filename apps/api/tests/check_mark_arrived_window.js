const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', '..', 'web_admin', 'js', 'customer-requests.js');
const text = fs.readFileSync(file, 'utf8');

let passed = 0;
let total = 0;
function check(name, cond) {
  total += 1;
  if (cond) { passed += 1; console.log('PASS ' + name); }
  else { console.log('FAIL ' + name); }
}

const start = text.indexOf('function openArriveModal(r) {');
const end = text.indexOf('function defaultPieces(r) {');
if (start < 0 || end <= start) {
  console.log('FAIL cannot find openArriveModal in the file');
  process.exit(1);
}
const src = text.slice(start, end);

const els = {};
function makeEl(id) {
  return {
    id: id, value: '', style: {}, disabled: false, textContent: '', innerHTML: '', listeners: {},
    addEventListener(t, f) { this.listeners[t] = f; },
    closest() { return null; },
  };
}
function getEl(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; }
const radios = [
  { value: 'counted', checked: true, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } },
  { value: 'add', checked: false, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } },
];
let modalHtml = '';
let closed = false;
const toasts = [];
const calls = [];
let markImpl = async (id, p) => { calls.push(p); return { hold_pieces: 0 }; };

const sandbox = {
  document: {
    getElementById: getEl,
    querySelector: () => radios.find((x) => x.checked) || null,
    querySelectorAll: () => radios,
  },
  openModal: (html) => { modalHtml = html; },
  closeModal: () => { closed = true; },
  esc: (v) => String(v == null ? '' : v),
  defaultPieces: () => 24,
  showToast: (m, e) => { toasts.push({ m: m, e: e }); },
  load: async () => {},
  Api: { customerRequests: { searchProducts: async () => [], markArrived: (id, p) => markImpl(id, p) } },
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const order = { id: 'o1', item_name: 'Fanta', customer_name: 'Ann', product_id: null, branch_id: 'b1', order_pieces: 24, quantity: 2, unit_label: 'dozens' };

function reopen() {
  Object.keys(els).forEach((k) => { delete els[k]; });
  radios[0].checked = true;
  radios[1].checked = false;
  radios.forEach((x) => { x.listeners = {}; });
  calls.length = 0;
  toasts.length = 0;
  closed = false;
  sandbox.openArriveModal(order);
}
function chooseAdd() {
  radios[0].checked = false;
  radios[1].checked = true;
  radios[1].listeners.change();
}
async function clickGo() { await getEl('cr-modal-go').listeners.click(); }

(async () => {
  reopen();
  check('1 html: question', modalHtml.includes('Is this item already in your inventory stock?'));
  check('2 html: yes answer', modalHtml.includes('Yes, it is already counted. I only need to link it.'));
  check('3 html: no answer', modalHtml.includes('No, add it to the shelf now.'));
  check('4 html: pieces label', modalHtml.includes('How many pieces arrived?'));
  check('5 html: price label', modalHtml.includes('Selling price per piece (only needed if this item has no price yet)'));
  check('6 html: cost label', modalHtml.includes('Cost price per piece (optional)'));
  check('7 html: cost hint', modalHtml.includes('Leave empty if you do not know it yet. Correct it when the invoice is added.'));
  check('8 html: add to invoice hint', modalHtml.includes('Add to invoice'));
  check('9 html: pieces default is 24', modalHtml.includes('id="cr-add-pieces" min="1" value="24"'));
  check('22 html: old hold hint kept', modalHtml.includes('Held pieces cannot be sold at POS'));

  getEl('cr-hold').value = '3';
  await clickGo();
  check('10 counted: one call', calls.length === 1);
  check('11 counted: old payload', JSON.stringify(calls[0]) === '{"product_id":null,"hold_pieces":3}');
  check('12 counted: window closed', closed === true);

  reopen();
  chooseAdd();
  check('13 add mode hides the counted part', getEl('cr-counted-fields').style.display === 'none');
  check('14 add mode shows the add part', getEl('cr-add-fields').style.display === '');
  getEl('cr-add-pieces').value = '24';
  getEl('cr-add-price').value = '1500';
  getEl('cr-add-cost').value = '';
  await clickGo();
  check('15 add: payload', JSON.stringify(calls[0]) === '{"product_id":null,"add_to_shelf":true,"pieces_arrived":24,"selling_price_naira":1500}');

  reopen();
  chooseAdd();
  getEl('cr-add-pieces').value = '24';
  getEl('cr-add-price').value = '';
  getEl('cr-add-cost').value = '900';
  await clickGo();
  check('16 add: cost sent, price not sent', calls[0] && calls[0].cost_price_naira === 900 && calls[0].selling_price_naira === undefined);

  reopen();
  chooseAdd();
  getEl('cr-add-pieces').value = '0';
  await clickGo();
  check('17 pieces 0: nothing sent', calls.length === 0);
  check('18 pieces 0: error toast', toasts.length === 1 && toasts[0].m === 'Enter how many pieces arrived.' && toasts[0].e === true);

  markImpl = async () => { throw new Error('Type the selling price per piece. This item has no price yet.'); };
  reopen();
  chooseAdd();
  getEl('cr-add-pieces').value = '24';
  await clickGo();
  const last = toasts[toasts.length - 1];
  check('19 server refusal is shown', !!last && last.m === 'Type the selling price per piece. This item has no price yet.' && last.e === true);
  check('20 button enabled again after refusal', getEl('cr-modal-go').disabled === false);

  check('21 marker appears once', text.split('PATCH2B-MARK-ARRIVED-WINDOW').length - 1 === 1);

  console.log(passed + '/' + total + ' checks passed');
  process.exit(passed === total ? 0 : 1);
})();
