/* Beauty Advisor chat. Plain JS, no build step. Text from the server is only
   ever inserted with textContent (never innerHTML). */
(function () {
  'use strict';

  var NAIRA = '\u20A6';

  function formatNaira(n) {
    var v = Math.round(Number(n) || 0);
    return NAIRA + String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  var root = document.getElementById('bc-root');
  if (!root) {
    if (typeof module !== 'undefined') { module.exports = { formatNaira: formatNaira }; }
    return;
  }

  var log = document.getElementById('bc-log');
  var chipsBox = document.getElementById('bc-chips');
  var form = document.getElementById('bc-form');
  var input = document.getElementById('bc-input');
  var sendBtn = document.getElementById('bc-send');
  var busy = false;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }

  // The page itself scrolls (no inner scroll box, which is awkward on phones).
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function scrollTo(node, block) {
    if (node && node.scrollIntoView) {
      node.scrollIntoView({ block: block, behavior: reduceMotion ? 'auto' : 'smooth' });
    }
  }
  function toBottom() { /* handled per response in render() */ }

  function addMessage(role, text) {
    var row = el('div', 'bc-msg bc-' + role);
    if (role === 'bot') { row.appendChild(el('span', 'bc-mark')); }
    row.appendChild(el('p', 'bc-bubble', text));
    log.appendChild(row);
    toBottom();
  }

  function addNote(text) {
    log.appendChild(el('p', 'bc-inline-note', text));
    toBottom();
  }

  var typingEl = null;
  function showTyping(on) {
    if (on && !typingEl) {
      typingEl = el('div', 'bc-msg bc-bot bc-typing');
      typingEl.appendChild(el('span', 'bc-mark'));
      var dots = el('p', 'bc-bubble');
      dots.setAttribute('aria-label', 'The advisor is typing');
      dots.appendChild(el('i')); dots.appendChild(el('i')); dots.appendChild(el('i'));
      typingEl.appendChild(dots);
      log.appendChild(typingEl);
      toBottom();
    } else if (!on && typingEl) {
      typingEl.parentNode.removeChild(typingEl);
      typingEl = null;
    }
  }

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    Array.prototype.forEach.call(chipsBox.querySelectorAll('button'), function (b) { b.disabled = on; });
  }

  // ------------------------------------------------------------ cart
  function bumpCartCount() {
    var link = document.querySelector('.cart-link');
    if (!link) { return; }
    var c = link.querySelector('.cart-count');
    if (c) { c.textContent = String((parseInt(c.textContent, 10) || 0) + 1); return; }
    c = el('span', 'cart-count', '1');
    link.appendChild(c);
  }

  function addToCart(item, btn, status) {
    btn.disabled = true;
    btn.textContent = 'Adding...';
    var body = new URLSearchParams();
    body.append('product_id', item.product_id);
    body.append('quantity', '1');
    fetch('/cart/add', { method: 'POST', body: body, credentials: 'same-origin' })
      .then(function (r) {
        // The cart route redirects to /cart on success and back to the product
        // page when the item has just gone out of stock.
        var ok = /\/cart\/?$/.test(r.url.split('?')[0]);
        if (ok) {
          btn.textContent = 'Added';
          status.textContent = '';
          var a = el('a', 'bc-link', 'View cart');
          a.href = '/cart';
          status.appendChild(a);
          bumpCartCount();
        } else {
          btn.textContent = 'Out of stock';
          status.textContent = 'Sorry, someone just bought the last one.';
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = 'Add to cart';
        status.textContent = 'Could not add it. Please try again.';
      });
  }

  // ------------------------------------------------------------ cards
  function addCards(items) {
    var wrap = el('div', 'bc-cards');
    items.forEach(function (it) {
      var card = el('article', 'bc-card');
      if (it.image_url) {
        var img = el('img');
        img.src = it.image_url;
        img.alt = it.name;
        img.loading = 'lazy';
        card.appendChild(img);
      } else {
        card.appendChild(el('div', 'bc-card-ph'));
      }
      var body = el('div', 'bc-card-body');
      body.appendChild(el('h3', null, it.name + (it.size ? ' (' + it.size + ')' : '')));
      var meta = el('p', 'bc-card-meta');
      meta.appendChild(el('span', 'price', formatNaira(it.price_naira)));
      meta.appendChild(el('span', 'stock in', 'In stock'));
      body.appendChild(meta);
      var ul = el('ul', 'bc-reasons');
      (it.reasons || []).forEach(function (r) { ul.appendChild(el('li', null, r)); });
      body.appendChild(ul);
      var actions = el('div', 'bc-card-actions');
      var view = el('a', 'bc-view', 'View product');
      view.href = it.url;
      var add = el('button', 'btn-primary bc-add', 'Add to cart');
      add.type = 'button';
      var status = el('span', 'bc-add-status');
      status.setAttribute('aria-live', 'polite');
      add.addEventListener('click', function () { addToCart(it, add, status); });
      actions.appendChild(view);
      actions.appendChild(add);
      body.appendChild(actions);
      body.appendChild(status);
      card.appendChild(body);
      wrap.appendChild(card);
    });
    log.appendChild(wrap);
    toBottom();
  }

  // ------------------------------------------------------------ chips
  function clearChips() {
    chipsBox.textContent = '';
    chipsBox.hidden = true;
  }

  function renderChips(spec) {
    clearChips();
    if (!spec || !spec.options || !spec.options.length) { return; }
    chipsBox.hidden = false;
    var picked = [];

    function choose(values, shownLabels) {
      if (busy) { return; }
      clearChips();
      addMessage('user', shownLabels.length ? shownLabels.join(', ') : 'Skip');
      send({ action: 'choose', field: spec.field, values: values });
    }

    spec.options.forEach(function (o) {
      var b = el('button', 'bc-chip', o.label);
      b.type = 'button';
      if (spec.multi) {
        b.setAttribute('aria-pressed', 'false');
        b.addEventListener('click', function () {
          var i = picked.indexOf(o.value);
          if (i === -1) { picked.push(o.value); } else { picked.splice(i, 1); }
          b.classList.toggle('is-on', i === -1);
          b.setAttribute('aria-pressed', i === -1 ? 'true' : 'false');
          done.disabled = picked.length === 0;
        });
      } else {
        b.addEventListener('click', function () { choose([o.value], [o.label]); });
      }
      chipsBox.appendChild(b);
    });

    var done = null;
    if (spec.multi) {
      done = el('button', 'bc-chip bc-chip-go', 'Done');
      done.type = 'button';
      done.disabled = true;
      done.addEventListener('click', function () {
        var labels = [];
        Array.prototype.forEach.call(chipsBox.querySelectorAll('.bc-chip.is-on'), function (x) { labels.push(x.textContent); });
        choose(picked.slice(), labels);
      });
      chipsBox.appendChild(done);
    }
    if (spec.skippable) {
      var skip = el('button', 'bc-chip bc-chip-quiet', 'Skip');
      skip.type = 'button';
      skip.addEventListener('click', function () { choose(['skip'], []); });
      chipsBox.appendChild(skip);
    }
  }

  // ------------------------------------------------------------ talking to the server
  function render(data) {
    var before = log.lastElementChild;
    (data.messages || []).forEach(function (m) { addMessage('bot', m); });
    if (data.items && data.items.length) { addCards(data.items); }
    if (data.disclaimer) {
      addNote('Cosmetic guidance only, not a medical diagnosis. See a doctor or pharmacist for a skin problem that is painful, severe or does not go away.');
    }
    renderChips(data.chips);
    var first = before ? before.nextElementSibling : log.firstElementChild;
    if (data.items && data.items.length) {
      scrollTo(first, 'start');          // show the start of the picks
    } else if (!chipsBox.hidden) {
      scrollTo(chipsBox, 'nearest');     // keep the options in view
    } else {
      scrollTo(form, 'nearest');
    }
  }

  function send(body) {
    setBusy(true);
    showTyping(true);
    fetch('/advisor/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (d) { return { status: r.status, data: d }; });
    }).then(function (res) {
      showTyping(false);
      setBusy(false);
      if (res.data && res.data.ok) { render(res.data); return; }
      addMessage('bot', (res.data && res.data.message) || 'Something went wrong. Please try again.');
    }).catch(function () {
      showTyping(false);
      setBusy(false);
      addMessage('bot', 'We could not reach the shop right now. Please try again shortly, or browse the shop.');
    });
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text || busy) { return; }
    input.value = '';
    clearChips();
    addMessage('user', text);
    send({ action: 'text', text: text });
  });

  document.getElementById('bc-restart').addEventListener('click', function () {
    if (busy) { return; }
    log.textContent = '';
    clearChips();
    send({ action: 'start' });
  });

  send({ action: 'start' });
})();
