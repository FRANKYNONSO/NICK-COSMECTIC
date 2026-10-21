// Website coupon generator (POS page). Super admin only.
// Makes single-use discount codes for the customer WEBSITE (storefront),
// via POST /api/v1/storefront/coupons. These are NOT the 30-minute POS
// coupons made by the existing "Generate coupon" button; nothing here
// touches POS checkout, the cart, sales or stock. Codes made here are listed
// in Transactions > Website Orders > Generated coupons.
(function websiteCouponGenerator() {
  if (document.getElementById('website-coupon-btn')) return;

  function isSuperAdmin() {
    try {
      const u = (typeof Auth !== 'undefined' && Auth.getUser) ? Auth.getUser() : null;
      if (!u) return true; // cannot tell: show it; the API refuses non-super-admins anyway
      const raw = u.role !== undefined ? u.role : (u.role_name !== undefined ? u.role_name : undefined);
      const name = (raw && typeof raw === 'object') ? raw.name : raw;
      if (name === undefined || name === null) return true;
      return String(name) === 'super_admin';
    } catch (e) { return true; }
  }
  if (!isSuperAdmin()) return;

  const posCouponBtn = document.getElementById('generate-coupon-btn');
  const host = posCouponBtn ? posCouponBtn.parentElement : document.querySelector('.page-head');
  if (!host) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'website-coupon-btn';
  btn.className = 'btn btn-ghost btn-sm';
  btn.textContent = 'Website coupon';
  if (posCouponBtn && posCouponBtn.nextSibling) host.insertBefore(btn, posCouponBtn.nextSibling);
  else host.insertBefore(btn, host.firstChild);

  function call(payload) {
    if (typeof Api !== 'undefined' && typeof Api.generateStorefrontCoupons === 'function') {
      return Api.generateStorefrontCoupons(payload);
    }
    return apiRequest('/storefront/coupons', { method: 'POST', body: payload });
  }

  function naira(n) { return '\u20A6' + Number(n).toLocaleString('en-NG'); }

  function revealCall(ids) {
    if (typeof Api !== 'undefined' && typeof Api.revealStorefrontCoupons === 'function') {
      return Api.revealStorefrontCoupons(ids);
    }
    return apiRequest('/storefront/coupons/reveal', { method: 'POST', body: { ids } });
  }

  let overlay = null;

  function closeModal() {
    if (overlay) { overlay.remove(); overlay = null; }
  }

  function el(tag, props, children) {
    const e = document.createElement(tag);
    Object.keys(props || {}).forEach((k) => {
      if (k === 'text') e.textContent = props[k];
      else if (k === 'style') e.setAttribute('style', props[k]);
      else e[k] = props[k];
    });
    (children || []).forEach((c) => e.appendChild(c));
    return e;
  }

  function field(label, input) {
    return el('div', { className: 'field' }, [el('label', { text: label, htmlFor: input.id }), input]);
  }

  function openModal() {
    closeModal();
    overlay = el('div', { id: 'wcg-overlay', style: 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.55);padding:12px;' });
    const box = el('div', { className: 'modal', style: 'position:relative;display:block;opacity:1;visibility:visible;transform:none;width:100%;max-width:440px;max-height:92vh;overflow:auto;' });
    overlay.appendChild(box);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });

    const amount = el('input', { type: 'number', id: 'wcg-amount', min: '1', step: 'any', placeholder: 'e.g. 2000' });
    const hours = el('input', { type: 'number', id: 'wcg-hours', min: '1', max: '2160', step: '1', value: '24' });
    const count = el('input', { type: 'number', id: 'wcg-count', min: '1', max: '50', step: '1', value: '1' });
    const note = el('input', { type: 'text', id: 'wcg-note', maxLength: 200, placeholder: 'e.g. Instagram giveaway' });
    const err = el('div', { style: 'color:var(--danger,#d33);font-size:13px;margin:6px 0;display:none;' });
    const cancel = el('button', { type: 'button', className: 'btn btn-ghost', text: 'Cancel' });
    const submit = el('button', { type: 'submit', className: 'btn btn-primary', text: 'Generate' });
    const form = el('form', {}, [
      field('Discount amount (naira)', amount),
      field('Valid for (hours after the code is revealed)', hours),
      field('How many codes (1 to 50)', count),
      field('Note (optional)', note),
      err,
      el('div', { className: 'modal-actions' }, [cancel, submit]),
    ]);
    box.appendChild(el('h3', { text: 'Website coupon' }));
    box.appendChild(el('p', { className: 'modal-sub', text: 'Creates single-use discount codes for the customer website. Customers type the code at website checkout. This is separate from the POS coupon button.' }));
    box.appendChild(form);
    cancel.addEventListener('click', closeModal);

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      err.style.display = 'none';
      const a = Number(amount.value), h = Number(hours.value), c = Number(count.value);
      let msg = '';
      if (!(a > 0)) msg = 'Enter a discount amount in naira.';
      else if (!Number.isInteger(h) || h < 1 || h > 2160) msg = 'Valid-for hours must be a whole number from 1 to 2160.';
      else if (!Number.isInteger(c) || c < 1 || c > 50) msg = 'Number of codes must be a whole number from 1 to 50.';
      if (msg) { err.textContent = msg; err.style.display = ''; return; }
      const payload = { discount_amount_naira: a, expires_in_hours: h, count: c };
      if (note.value.trim()) payload.note = note.value.trim();
      submit.disabled = true;
      try {
        const made = await call(payload);
        showResult(box, Array.isArray(made) ? made : [], a, h);
      } catch (e) {
        err.textContent = (e && e.status === 403) ? 'Only the super admin can create website coupons.' : ((e && e.message) || 'Could not create the coupons.');
        err.style.display = '';
        submit.disabled = false;
      }
    });

    document.body.appendChild(overlay);
    amount.focus();
  }

  function showResult(box, list, amountNaira, hours) {
    box.innerHTML = '';
    const ids = list.map((c) => c.id).filter(Boolean);
    let realCodes = null; // filled only when the admin reveals the codes
    const hoursText = hours + ' hour' + (hours === 1 ? '' : 's');
    box.appendChild(el('h3', { text: list.length === 1 ? 'Website coupon created' : list.length + ' website coupons created' }));
    box.appendChild(el('p', { className: 'modal-sub', text: naira(amountNaira) + ' off each, single use. The code is hidden for now. Press Reveal or Copy to see it: that starts its ' + hoursText + ' countdown. They are also listed in Transactions > Website Orders > Generated coupons.' }));
    const display = el('div', { style: 'font-family:monospace;font-size:20px;font-weight:700;letter-spacing:1px;text-align:center;line-height:1.9;margin:12px 0;word-break:break-all;white-space:pre-line;', text: list.map((c) => c.code).join('\n') });
    box.appendChild(display);
    const reveal = el('button', { type: 'button', className: 'btn btn-ghost btn-sm', text: list.length === 1 ? 'Reveal code (starts countdown)' : 'Reveal codes (starts countdown)' });
    const copy = el('button', { type: 'button', className: 'btn btn-ghost btn-sm', text: list.length === 1 ? 'Copy code' : 'Copy all' });
    const done = el('button', { type: 'button', className: 'btn btn-primary', text: 'Done' });
    const info = el('div', { style: 'text-align:center;font-size:13px;margin:4px 0 8px;display:none;' });
    box.appendChild(el('div', { style: 'text-align:center;margin-bottom:8px;' }, [reveal, copy]));
    box.appendChild(info);
    box.appendChild(el('div', { className: 'modal-actions' }, [done]));
    function show(msg, isError) {
      info.textContent = msg;
      info.style.color = isError ? 'var(--danger,#d33)' : 'inherit';
      info.style.display = '';
    }
    async function ensureRevealed() {
      if (realCodes) return true;
      reveal.disabled = true; copy.disabled = true;
      try {
        const got = await revealCall(ids);
        const byId = {};
        (Array.isArray(got) ? got : []).forEach((c) => { byId[c.id] = c.code; });
        const codes = list.map((c) => byId[c.id]);
        if (codes.some((c) => !c || String(c).indexOf('*') >= 0)) throw new Error('the server did not return the code');
        realCodes = codes;
        display.textContent = codes.join('\n');
        reveal.style.display = 'none';
        show('Revealed. Countdown started: valid for ' + hoursText + ' from now.', false);
        return true;
      } catch (e) {
        show('Could not reveal the code (' + ((e && e.message) || 'unknown error') + '). It stays hidden and valid. Try again, or use Transactions > Website Orders > Generated coupons.', true);
        return false;
      } finally {
        reveal.disabled = false; copy.disabled = false;
      }
    }
    reveal.addEventListener('click', ensureRevealed);
    copy.addEventListener('click', async () => {
      if (!(await ensureRevealed())) return;
      const text = realCodes.join('\n');
      let ok = false;
      try { await navigator.clipboard.writeText(text); ok = true; }
      catch (e) {
        const ta = el('textarea', { value: text, style: 'position:fixed;opacity:0;' });
        document.body.appendChild(ta); ta.select();
        try { ok = !!document.execCommand('copy'); } catch (e2) { ok = false; }
        ta.remove();
      }
      if (ok) copy.textContent = 'Copied';
      else show('Revealed, but the copy failed. Select the code on screen and copy it by hand.', true);
    });
    done.addEventListener('click', closeModal);
  }

  btn.addEventListener('click', openModal);
})();
