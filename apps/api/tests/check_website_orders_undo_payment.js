'use strict';
// Node checks for patch 19d: "Undo payment" button and box in website-orders.js.
// PATCH19D-UNDO-PAYMENT
// Uses a hand-made fake page (jsdom is not installed). Run: node check_website_orders_undo_payment.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', '..', 'web_admin', 'js');
const ORDERS_PATH = path.join(JS_DIR, 'website-orders.js');
const API_PATH = path.join(JS_DIR, 'api.js');
const MARK = 'PATCH19D-UNDO-PAYMENT';

let passed = 0;
let failed = 0;

async function t(name, fn) {
  try {
    const r = await fn();
    if (r === true) {
      passed += 1;
      console.log('PASS ' + name);
    } else {
      failed += 1;
      console.log('FAIL ' + name + ' :: ' + String(r));
    }
  } catch (e) {
    failed += 1;
    console.log('FAIL ' + name + ' :: exception: ' + (e && e.message));
  }
}

class FakeEl {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.parentNode = null;
    this.style = {};
    this.attrs = {};
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.id = '';
  }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  querySelector() { return null; }
  focus() {}
}

function allText(el) {
  return String(el.textContent || '') + ' ' + el.children.map(allText).join(' ');
}
function findById(el, id) {
  if (el.id === id) return el;
  for (const c of el.children) {
    const f = findById(c, id);
    if (f) return f;
  }
  return null;
}
function findTag(el, tag) {
  if (el.tag === tag) return el;
  for (const c of el.children) {
    const f = findTag(c, tag);
    if (f) return f;
  }
  return null;
}
function findButton(el, text) {
  if (el.tag === 'button' && el.textContent === text) return el;
  for (const c of el.children) {
    const f = findButton(c, text);
    if (f) return f;
  }
  return null;
}

async function makeContext() {
  const body = new FakeEl('body');
  const head = new FakeEl('head');
  const tbody = new FakeEl('tbody');
  const toasts = [];
  const calls = { undo: [], list: 0 };
  const state = { listResult: [], failWith: null };
  const byId = { 'wo-tbody': tbody };
  const document = {
    body: body,
    head: head,
    createElement: (tag) => new FakeEl(tag),
    getElementById: (id) => byId[id] || null,
    querySelector: () => null,
  };
  const sandbox = {
    console: console,
    document: document,
    window: { location: { hash: '' }, addEventListener() {} },
    escapeHtml: (s) => String(s === null || s === undefined ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    formatNaira: (n) => 'NGN ' + Number(n).toFixed(2),
    formatDateTime: (s) => String(s),
    showToast: (m, isErr) => { toasts.push({ m: m, isErr: !!isErr }); },
    confirmAction: async () => true,
    openReceipt() {},
    activateTab() {},
    Api: {
      listStorefrontOrders: async () => { calls.list += 1; return state.listResult; },
      updateStorefrontOrder: async () => ({}),
      recordStorefrontPayment: async () => ({}),
      undoStorefrontPayment: async (id, p) => {
        calls.undo.push([id, p]);
        if (state.failWith) throw state.failWith;
        return {};
      },
    },
  };
  vm.createContext(sandbox);
  const src = fs.readFileSync(ORDERS_PATH, 'utf8') + '\n;this.__setOrders = (x) => { woOrders = x; };\n';
  vm.runInContext(src, sandbox, { filename: 'website-orders.js' });
  await new Promise((r) => setImmediate(r));
  return { ctx: sandbox, body: body, tbody: tbody, toasts: toasts, calls: calls, state: state };
}

function order(over) {
  return Object.assign({
    id: 'order-1111-2222', reference: 'R1', status: 'new', created_at: '2026-10-18T10:00:00+00:00',
    customer_name: 'Ada', customer_phone: '0801', customer_email: null,
    delivery_address: '1 Road', delivery_city: null, delivery_lga: null, delivery_state: 'Taraba',
    shipping_fee_naira: 0, shipping_note: null, subtotal_naira: 12500, discount_naira: 0,
    total_naira: 12500, payable_naira: 12500, coupon_code: null, waybill_ref: null, admin_note: null,
    payment_status: 'unpaid', payment_method: null, amount_received_naira: null, paid_at: null,
    paid_recorded_by: null, payment_note: null, sales: [], pick_list: [],
  }, over || {});
}

function paidOrder(over) {
  return order(Object.assign({
    payment_status: 'paid', payment_method: 'bank_transfer', amount_received_naira: 12500,
    paid_at: '2026-10-18T10:00:00+00:00',
  }, over || {}));
}

async function boxFor(o) {
  const h = await makeContext();
  h.ctx.__setOrders([o]);
  h.ctx.woOpenUnpayBox(o.id);
  return { h: h, overlay: h.body.children[0] };
}

async function submitBox(overlay) {
  const form = findTag(overlay, 'form');
  await form.listeners.submit[0]({ preventDefault() {} });
}

async function main() {
  const ordersSrc = fs.readFileSync(ORDERS_PATH, 'utf8');
  const apiSrc = fs.readFileSync(API_PATH, 'utf8');

  await t('01 api.js has undoStorefrontPayment (POST to the undo path)', async () => {
    const re = /undoStorefrontPayment\(orderId, payload\) \{\r?\n\s+return apiRequest\(`\/storefront\/orders\/\$\{orderId\}\/payment\/undo`, \{ method: 'POST', body: payload \}\);/;
    return re.test(apiSrc) || 'function not found';
  });

  await t('02 api.js still has only CRLF line endings', async () => {
    const crlf = (apiSrc.match(/\r\n/g) || []).length;
    const lf = (apiSrc.match(/\n/g) || []).length;
    const cr = (apiSrc.match(/\r/g) || []).length;
    return (crlf > 0 && crlf === lf && cr === crlf) || ('crlf=' + crlf + ' lf=' + lf + ' cr=' + cr);
  });

  await t('03 website-orders.js has the marker exactly once', async () => {
    const n = ordersSrc.split(MARK).length - 1;
    return n === 1 || ('count=' + n);
  });

  await t('04 website-orders.js has LF only and no BOM', async () => {
    return (ordersSrc.indexOf('\r') === -1 && ordersSrc.charCodeAt(0) !== 0xFEFF) || 'CR or BOM found';
  });

  await t('05 row of a paid order has the Undo payment button', async () => {
    const h = await makeContext();
    const html = h.ctx.woRowHtml(paidOrder());
    return (html.includes('data-wo-unpay="order-1111-2222"') && html.includes('Undo payment')) || 'button missing';
  });

  await t('06 row of an unpaid order has no Undo payment button', async () => {
    const h = await makeContext();
    const html = h.ctx.woRowHtml(order());
    return !html.includes('data-wo-unpay') || 'button present';
  });

  await t('07 row of a cancelled paid order has no Undo payment button', async () => {
    const h = await makeContext();
    const html = h.ctx.woRowHtml(paidOrder({ status: 'cancelled' }));
    return !html.includes('data-wo-unpay') || 'button present';
  });

  await t('08 the undo box opens on a paid order', async () => {
    const r = await boxFor(paidOrder());
    return r.h.body.children.length === 1 || ('overlays=' + r.h.body.children.length);
  });

  await t('09 the box shows the title and the recorded line', async () => {
    const r = await boxFor(paidOrder());
    const text = allText(r.overlay);
    return (text.includes('Undo payment') && text.includes('Recorded: NGN 12500.00 by Bank transfer, 18 Oct 2026.')) || text;
  });

  await t('10 no box for an unpaid order (an error message is shown)', async () => {
    const r = await boxFor(order());
    return (r.h.body.children.length === 0 && r.h.toasts.some((x) => x.isErr)) || 'box opened or no message';
  });

  await t('11 an empty reason shows a message and calls nothing', async () => {
    const r = await boxFor(paidOrder());
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-unpay-err');
    return (err.textContent === 'Write why the payment is being undone.' && err.style.display === '' && r.h.calls.undo.length === 0) || err.textContent;
  });

  await t('12 a blank reason (spaces only) shows the same message', async () => {
    const r = await boxFor(paidOrder());
    findById(r.overlay, 'wo-unpay-reason').value = '    ';
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-unpay-err');
    return (err.textContent === 'Write why the payment is being undone.' && r.h.calls.undo.length === 0) || err.textContent;
  });

  await t('13 a valid reason sends the trimmed payload', async () => {
    const r = await boxFor(paidOrder());
    findById(r.overlay, 'wo-unpay-reason').value = '  Wrong amount typed  ';
    await submitBox(r.overlay);
    const want = JSON.stringify(['order-1111-2222', { reason: 'Wrong amount typed' }]);
    return (r.h.calls.undo.length === 1 && JSON.stringify(r.h.calls.undo[0]) === want) || JSON.stringify(r.h.calls.undo);
  });

  await t('14 after a good undo the box closes, Payment undone is shown and the list reloads', async () => {
    const r = await boxFor(paidOrder());
    findById(r.overlay, 'wo-unpay-reason').value = 'Wrong amount typed';
    await submitBox(r.overlay);
    return (r.h.body.children.length === 0 && r.h.toasts.some((x) => x.m === 'Payment undone') && r.h.calls.list === 2) || ('children=' + r.h.body.children.length + ' list=' + r.h.calls.list);
  });

  await t('15 a 409 shows the API sentence, keeps the box open and reloads the list', async () => {
    const r = await boxFor(paidOrder());
    r.h.state.failWith = { status: 409, message: 'This order is not marked as paid.' };
    findById(r.overlay, 'wo-unpay-reason').value = 'Wrong amount typed';
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-unpay-err');
    return (err.textContent === 'This order is not marked as paid.' && err.style.display === '' && r.h.body.children.length === 1 && r.h.calls.list === 2) || err.textContent;
  });

  await t('16 a 403 shows the role message', async () => {
    const r = await boxFor(paidOrder());
    r.h.state.failWith = { status: 403, message: 'Role is not permitted' };
    findById(r.overlay, 'wo-unpay-reason').value = 'Wrong amount typed';
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-unpay-err');
    return err.textContent === 'Only a super admin or a branch manager can undo a payment.' || err.textContent;
  });

  await t('17 Keep payment closes the box and calls nothing', async () => {
    const r = await boxFor(paidOrder());
    const keep = findButton(r.overlay, 'Keep payment');
    if (!keep) return 'Keep payment button not found';
    keep.listeners.click[0]();
    return (r.h.body.children.length === 0 && r.h.calls.undo.length === 0) || 'box still open or a call was made';
  });

  await t('18 clicking Undo payment in the table opens the box', async () => {
    const h = await makeContext();
    const o = paidOrder();
    h.ctx.__setOrders([o]);
    const click = h.tbody.listeners.click[0];
    click({ target: { closest: (sel) => (sel === '[data-wo-unpay]' ? { dataset: { woUnpay: o.id } } : null) } });
    return h.body.children.length === 1 || ('overlays=' + h.body.children.length);
  });

  console.log('');
  console.log('Checks: ' + (passed + failed) + ', passed: ' + passed + ', failed: ' + failed);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => {
  console.log('FATAL ' + (e && e.stack));
  process.exit(1);
});
