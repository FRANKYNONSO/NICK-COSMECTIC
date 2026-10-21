'use strict';
// Node checks for patch 19c: payment pill, "Mark as paid" button and the
// "Record payment" box in website-orders.js. PATCH19C-PAYMENT-SCREEN
// Uses a hand-made fake page (jsdom is not installed). Run: node check_website_orders_payment.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', '..', 'web_admin', 'js');
const ORDERS_PATH = path.join(JS_DIR, 'website-orders.js');
const API_PATH = path.join(JS_DIR, 'api.js');
const MARK = 'PATCH19C-PAYMENT-SCREEN';

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

async function makeContext() {
  const body = new FakeEl('body');
  const head = new FakeEl('head');
  const tbody = new FakeEl('tbody');
  const toasts = [];
  const confirms = [];
  const calls = { record: [], list: 0, update: [] };
  const state = { listResult: [], failWith: null, confirmAnswer: true };
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
    confirmAction: async (o) => { confirms.push(o); return state.confirmAnswer; },
    openReceipt() {},
    activateTab() {},
    Api: {
      listStorefrontOrders: async () => { calls.list += 1; return state.listResult; },
      updateStorefrontOrder: async (id, p) => { calls.update.push([id, p]); return {}; },
      recordStorefrontPayment: async (id, p) => {
        calls.record.push([id, p]);
        if (state.failWith) throw state.failWith;
        return {};
      },
    },
  };
  vm.createContext(sandbox);
  const src = fs.readFileSync(ORDERS_PATH, 'utf8') + '\n;this.__setOrders = (x) => { woOrders = x; };\n';
  vm.runInContext(src, sandbox, { filename: 'website-orders.js' });
  await new Promise((r) => setImmediate(r));
  return { ctx: sandbox, body: body, tbody: tbody, toasts: toasts, confirms: confirms, calls: calls, state: state };
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
  h.ctx.woOpenPayBox(o.id);
  return { h: h, overlay: h.body.children[0] };
}

async function submitBox(overlay) {
  const form = findTag(overlay, 'form');
  await form.listeners.submit[0]({ preventDefault() {} });
}

function cancelRow() {
  const map = {
    '[data-wo-status]': { value: 'cancelled' },
    '[data-wo-waybill]': { value: '' },
    '[data-wo-note]': { value: '' },
    '[data-wo-save]': { disabled: false },
  };
  return { querySelector: (sel) => map[sel] || null };
}

async function main() {
  const ordersSrc = fs.readFileSync(ORDERS_PATH, 'utf8');
  const apiSrc = fs.readFileSync(API_PATH, 'utf8');

  await t('01 api.js has recordStorefrontPayment (POST to the payment path)', async () => {
    const re = /recordStorefrontPayment\(orderId, payload\) \{\r?\n\s+return apiRequest\(`\/storefront\/orders\/\$\{orderId\}\/payment`, \{ method: 'POST', body: payload \}\);/;
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

  await t('05 unpaid pill says Not paid', async () => {
    const h = await makeContext();
    const html = h.ctx.woPaymentPillHtml(order());
    return (html.includes('Not paid') && html.includes('wo-pill unpaid')) || html;
  });

  await t('06 paid pill shows amount, Bank transfer and the date', async () => {
    const h = await makeContext();
    const html = h.ctx.woPaymentPillHtml(paidOrder());
    return html.includes('Paid NGN 12500.00 by Bank transfer, 18 Oct 2026') || html;
  });

  await t('07 paid pill shows Cash on delivery', async () => {
    const h = await makeContext();
    const html = h.ctx.woPaymentPillHtml(paidOrder({ payment_method: 'cash_on_delivery' }));
    return html.includes('by Cash on delivery') || html;
  });

  await t('08 pill date uses Nigeria time (23:30 UTC is the next day)', async () => {
    const h = await makeContext();
    const html = h.ctx.woPaymentPillHtml(paidOrder({ paid_at: '2026-10-18T23:30:00+00:00' }));
    return html.includes('19 Oct 2026') || html;
  });

  await t('09 row of an unpaid new order has the pill and the Mark as paid button', async () => {
    const h = await makeContext();
    const html = h.ctx.woRowHtml(order());
    return (html.includes('Not paid') && html.includes('data-wo-pay="order-1111-2222"') && html.includes('Mark as paid')) || 'missing pill or button';
  });

  await t('10 row of a paid order shows Paid and has no Mark as paid button', async () => {
    const h = await makeContext();
    const html = h.ctx.woRowHtml(paidOrder());
    return (html.includes('Paid NGN 12500.00') && !html.includes('data-wo-pay')) || 'wrong pill or button present';
  });

  await t('11 row of a cancelled unpaid order shows Not paid and no button', async () => {
    const h = await makeContext();
    const html = h.ctx.woRowHtml(order({ status: 'cancelled' }));
    return (html.includes('Not paid') && !html.includes('data-wo-pay')) || 'wrong pill or button present';
  });

  await t('12 cancel reminder for a paid order', async () => {
    const h = await makeContext();
    const html = h.ctx.woPaidReminderHtml(paidOrder());
    return html.includes('This order was paid NGN 12500.00. Remember to return the money to the customer.') || html;
  });

  await t('13 no cancel reminder for an unpaid order', async () => {
    const h = await makeContext();
    return h.ctx.woPaidReminderHtml(order()) === '' || 'not empty';
  });

  await t('14 amount equal to the total does not differ', async () => {
    const h = await makeContext();
    return h.ctx.woAmountDiffers('12500', 12500) === false || 'wrong';
  });

  await t('15 a different amount differs', async () => {
    const h = await makeContext();
    return h.ctx.woAmountDiffers('12000', 12500) === true || 'wrong';
  });

  await t('16 an empty amount does not show the warning', async () => {
    const h = await makeContext();
    return h.ctx.woAmountDiffers('', 12500) === false || 'wrong';
  });

  await t('17 the box opens on an unpaid order', async () => {
    const r = await boxFor(order());
    return r.h.body.children.length === 1 || ('overlays=' + r.h.body.children.length);
  });

  await t('18 the box shows the order total line', async () => {
    const r = await boxFor(order());
    const text = allText(r.overlay);
    return (text.includes('Record payment') && text.includes('Order total: NGN 12500.00')) || text;
  });

  await t('19 the amount starts at the payable amount', async () => {
    const r = await boxFor(order({ payable_naira: 14500 }));
    const v = findById(r.overlay, 'wo-pay-amount').value;
    return v === '14500' || v;
  });

  await t('20 no payment method is preselected', async () => {
    const r = await boxFor(order());
    return (findById(r.overlay, 'wo-pay-bank').checked === false && findById(r.overlay, 'wo-pay-cash').checked === false) || 'a method is preselected';
  });

  await t('21 the yellow warning is hidden at the start', async () => {
    const r = await boxFor(order());
    return findById(r.overlay, 'wo-pay-warn').style.display === 'none' || 'warning visible';
  });

  await t('22 the yellow warning shows when the amount differs', async () => {
    const r = await boxFor(order());
    const amount = findById(r.overlay, 'wo-pay-amount');
    amount.value = '12000';
    amount.listeners.input[0]({});
    return findById(r.overlay, 'wo-pay-warn').style.display === '' || 'warning not visible';
  });

  await t('23 saving without a method shows a message and calls nothing', async () => {
    const r = await boxFor(order());
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-pay-err');
    return (err.textContent === 'Choose how the customer paid.' && err.style.display === '' && r.h.calls.record.length === 0) || err.textContent;
  });

  await t('24 an amount of 0 shows a message and calls nothing', async () => {
    const r = await boxFor(order());
    findById(r.overlay, 'wo-pay-bank').checked = true;
    findById(r.overlay, 'wo-pay-amount').value = '0';
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-pay-err');
    return (err.textContent === 'The amount must be more than 0.' && r.h.calls.record.length === 0) || err.textContent;
  });

  await t('25 a valid save sends the right payload', async () => {
    const r = await boxFor(order());
    findById(r.overlay, 'wo-pay-bank').checked = true;
    findById(r.overlay, 'wo-pay-note').value = '  Paid at GTB  ';
    await submitBox(r.overlay);
    const want = JSON.stringify(['order-1111-2222', { method: 'bank_transfer', amount_naira: 12500, note: 'Paid at GTB' }]);
    return (r.h.calls.record.length === 1 && JSON.stringify(r.h.calls.record[0]) === want) || JSON.stringify(r.h.calls.record);
  });

  await t('26 after a good save the box closes and Payment recorded is shown', async () => {
    const r = await boxFor(order());
    findById(r.overlay, 'wo-pay-bank').checked = true;
    await submitBox(r.overlay);
    return (r.h.body.children.length === 0 && r.h.toasts.some((x) => x.m === 'Payment recorded')) || 'box still open or no toast';
  });

  await t('27 a different amount still saves (cash on delivery, no note)', async () => {
    const r = await boxFor(order());
    findById(r.overlay, 'wo-pay-cash').checked = true;
    const amount = findById(r.overlay, 'wo-pay-amount');
    amount.value = '12000';
    amount.listeners.input[0]({});
    const warnShown = findById(r.overlay, 'wo-pay-warn').style.display === '';
    await submitBox(r.overlay);
    const call = r.h.calls.record[0];
    return (warnShown && call && call[1].method === 'cash_on_delivery' && call[1].amount_naira === 12000 && !('note' in call[1])) || JSON.stringify(call);
  });

  await t('28 a 409 shows the API sentence and keeps the box open', async () => {
    const r = await boxFor(order());
    r.h.state.failWith = { status: 409, message: 'This order is already marked as paid.' };
    findById(r.overlay, 'wo-pay-bank').checked = true;
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-pay-err');
    return (err.textContent === 'This order is already marked as paid.' && err.style.display === '' && r.h.body.children.length === 1) || err.textContent;
  });

  await t('29 a 409 reloads the order list', async () => {
    const r = await boxFor(order());
    r.h.state.failWith = { status: 409, message: 'This order is already marked as paid.' };
    findById(r.overlay, 'wo-pay-bank').checked = true;
    await submitBox(r.overlay);
    return r.h.calls.list === 2 || ('list calls=' + r.h.calls.list);
  });

  await t('30 a 403 shows the role message', async () => {
    const r = await boxFor(order());
    r.h.state.failWith = { status: 403, message: 'Role is not permitted' };
    findById(r.overlay, 'wo-pay-bank').checked = true;
    await submitBox(r.overlay);
    const err = findById(r.overlay, 'wo-pay-err');
    return err.textContent === 'Only a super admin or a branch manager can record a payment.' || err.textContent;
  });

  await t('31 no box for a cancelled order (an error message is shown)', async () => {
    const r = await boxFor(order({ status: 'cancelled' }));
    return (r.h.body.children.length === 0 && r.h.toasts.some((x) => x.isErr)) || 'box opened or no message';
  });

  await t('32 no box for an order that is already paid', async () => {
    const r = await boxFor(paidOrder());
    return r.h.body.children.length === 0 || 'box opened';
  });

  await t('33 cancelling a paid order adds the reminder to the confirm box', async () => {
    const h = await makeContext();
    const o = paidOrder();
    h.ctx.__setOrders([o]);
    await h.ctx.woSave(o.id, cancelRow());
    const body = h.confirms[0] && h.confirms[0].bodyHtml;
    return (body && body.includes('This order was paid NGN 12500.00. Remember to return the money to the customer.')) || String(body);
  });

  await t('34 the old cancel text stays and the reminder comes after it', async () => {
    const h = await makeContext();
    const o = paidOrder();
    h.ctx.__setOrders([o]);
    await h.ctx.woSave(o.id, cancelRow());
    const body = String(h.confirms[0] && h.confirms[0].bodyHtml);
    const a = body.indexOf('This cannot be undone.');
    const b = body.indexOf('This order was paid');
    return (a >= 0 && b > a) || body;
  });

  await t('35 cancelling an unpaid order has no reminder', async () => {
    const h = await makeContext();
    const o = order();
    h.ctx.__setOrders([o]);
    await h.ctx.woSave(o.id, cancelRow());
    const body = String(h.confirms[0] && h.confirms[0].bodyHtml);
    return (h.confirms.length === 1 && !body.includes('Remember to return')) || body;
  });

  await t('36 clicking Mark as paid in the table opens the box', async () => {
    const h = await makeContext();
    const o = order();
    h.ctx.__setOrders([o]);
    const click = h.tbody.listeners.click[0];
    click({ target: { closest: (sel) => (sel === '[data-wo-pay]' ? { dataset: { woPay: o.id } } : null) } });
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
