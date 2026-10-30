// Shared list of Nigerian banks for any "select a bank" dropdown
// (spec section 8: "bank name (dropdown listing all banks in
// Nigeria)"). Kept as its own small file since it's needed by more
// than one page (Sales refunds, and eventually Credit/Booking refunds
// which reuse the same refund flow).
const NIGERIAN_BANKS = [
  'Access Bank', 'Citibank Nigeria', 'Ecobank Nigeria', 'Fidelity Bank',
  'First Bank of Nigeria', 'First City Monument Bank (FCMB)', 'Globus Bank',
  'Guaranty Trust Bank (GTBank)', 'Heritage Bank', 'Keystone Bank',
  'Polaris Bank', 'Providus Bank', 'Stanbic IBTC Bank',
  'Standard Chartered Bank', 'Sterling Bank', 'SunTrust Bank',
  'Titan Trust Bank', 'Union Bank of Nigeria', 'United Bank for Africa (UBA)',
  'Unity Bank', 'Wema Bank', 'Zenith Bank',
  'Jaiz Bank', 'Lotus Bank', 'Optimus Bank', 'Parallex Bank', 'Premium Trust Bank',
  'Signature Bank', 'Coronation Merchant Bank', 'FSDH Merchant Bank',
  'Rand Merchant Bank', 'Nova Merchant Bank', 'Greenwich Merchant Bank',
  'Kuda Bank', 'Opay', 'PalmPay', 'Moniepoint', 'VFD Microfinance Bank',
  'Carbon', 'Rubies Bank', 'Sparkle Bank',
];

function populateBankSelect(select) {
  if (!select) return;
  select.innerHTML = '<option value="">Select bank…</option>' +
    NIGERIAN_BANKS.map((b) => `<option value="${b}">${b}</option>`).join('');
}

// ACCOUNT-LOOKUP: fill the account name from the bank + 10-digit account
// number (server calls Paystack; the secret key never reaches the browser).
// If anything fails the cashier simply types the name by hand.
(function () {
  var seq = 0;

  function groupFromNumber(el) {
    var id = el.id || '';
    if (/account-number$/.test(id)) {
      var p = id.slice(0, id.length - 'account-number'.length);
      return { num: el, bank: document.getElementById(p + 'bank-name'), name: document.getElementById(p + 'account-name') };
    }
    if (el.classList && el.classList.contains('owed-row-account-number')) {
      var row = el.closest(':has(.owed-row-bank-name)');
      if (row) return { num: el, bank: row.querySelector('.owed-row-bank-name'), name: row.querySelector('.owed-row-account-name') };
    }
    return null;
  }

  function numberFromBank(el) {
    var id = el.id || '';
    if (/bank-name$/.test(id)) {
      return document.getElementById(id.slice(0, id.length - 'bank-name'.length) + 'account-number');
    }
    if (el.classList && el.classList.contains('owed-row-bank-name')) {
      var row = el.closest(':has(.owed-row-account-number)');
      if (row) return row.querySelector('.owed-row-account-number');
    }
    return null;
  }

  function setNote(nameEl, msg, ok) {
    var note = nameEl.parentNode.querySelector('.account-lookup-note');
    if (!msg) { if (note) note.remove(); return; }
    if (!note) {
      note = document.createElement('div');
      note.className = 'account-lookup-note';
      note.style.fontSize = '0.8em';
      note.style.marginTop = '4px';
      nameEl.insertAdjacentElement('afterend', note);
    }
    note.textContent = msg;
    note.style.opacity = ok ? '1' : '0.75';
  }

  function clearAuto(nameEl) {
    if (nameEl.dataset.autofilled === '1') {
      nameEl.value = '';
      delete nameEl.dataset.autofilled;
    }
    setNote(nameEl, '', false);
  }

  async function runLookup(g) {
    if (!g || !g.bank || !g.name) return;
    var acct = (g.num.value || '').trim();
    var bankName = g.bank.value;
    if (!/^\d{10}$/.test(acct) || !bankName) { g.name._lk = ++seq; clearAuto(g.name); return; }
    if (typeof apiRequest !== 'function') return;
    var mine = ++seq;
    g.name._lk = mine;
    setNote(g.name, 'Checking account name...', false);
    try {
      var res = await apiRequest('/bank-lookup/resolve?bank_name=' + encodeURIComponent(bankName) + '&account_number=' + encodeURIComponent(acct));
      if (g.name._lk !== mine) return;
      if (res && res.account_name) {
        if (!g.name.value.trim() || g.name.dataset.autofilled === '1') {
          g.name.value = res.account_name;
          g.name.dataset.autofilled = '1';
          setNote(g.name, 'Name filled from the bank. Confirm it matches the customer.', true);
        } else {
          setNote(g.name, 'Bank says: ' + res.account_name, true);
        }
      } else {
        setNote(g.name, 'Could not check the name. Type it manually.', false);
      }
    } catch (e) {
      if (g.name._lk !== mine) return;
      setNote(g.name, (e && e.message) ? e.message : 'Could not check the name. Type it manually.', false);
    }
  }

  document.addEventListener('input', function (ev) {
    var t = ev.target;
    if (!t || !t.dataset) return;
    if (t.dataset.autofilled === '1') delete t.dataset.autofilled;
    var g = groupFromNumber(t);
    if (g) runLookup(g);
  });

  // ACCOUNT-SUGGEST: account number first; bank suggestions appear under it.
  var DIGITAL_BANKS = ["Opay", "PalmPay", "Moniepoint", "Kuda Bank"];
  var sugSeq = 0;

  function sugShow(g, names, note) {
    var box = g.num.parentNode.querySelector(".bank-suggest");
    if (!box) {
      box = document.createElement("div");
      box.className = "bank-suggest";
      box.style.display = "flex";
      box.style.flexWrap = "wrap";
      box.style.gap = "6px";
      box.style.marginTop = "6px";
      g.num.insertAdjacentElement("afterend", box);
    }
    box.innerHTML = "";
    if (!names.length) return;
    var lab = document.createElement("div");
    lab.style.width = "100%"; lab.style.fontSize = "0.8em"; lab.style.opacity = "0.75";
    lab.textContent = note;
    box.appendChild(lab);
    names.forEach(function (n) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "btn btn-ghost btn-sm"; b.textContent = n;
      b.addEventListener("click", function () {
        g.bank.value = n;
        g.bank.dispatchEvent(new Event("change", { bubbles: true }));
        box.innerHTML = "";
      });
      box.appendChild(b);
    });
  }

  document.addEventListener("input", async function (ev) {
    var t = ev.target;
    if (!t || !t.classList) return;
    var g = groupFromNumber(t);
    if (!g || !g.bank) return;
    var acct = (t.value || "").trim();
    var mine = ++sugSeq;
    if (!/^\d{10}$/.test(acct) || g.bank.value) { sugShow(g, [], ""); return; }
    var all = Array.prototype.map.call(g.bank.options, function (o) { return o.value; }).filter(Boolean);
    var res = null;
    try {
      if (typeof apiRequest === "function") {
        var url = "/bank-lookup/find?account_number=" + encodeURIComponent(acct) + all.map(function (n) { return "&bank_names=" + encodeURIComponent(n); }).join("");
        res = await apiRequest(url);
      }
    } catch (e) { res = null; }
    if (sugSeq !== mine || !res) return;
    var hits = res.matches || [];
    if (hits.length === 1) {
      sugShow(g, [], "");
      g.bank.value = hits[0].bank_name;
      g.bank.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    if (hits.length > 1) {
      sugShow(g, hits.map(function (h) { return h.bank_name; }), "More than one bank matched. Pick the right one:");
      return;
    }
    sugShow(g, res.suggestions || [], res.limited ? "Lookup is paused for a few minutes. Possible banks:" : "Could not find this account. Possible banks:");
  });

  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (!t || t.tagName !== "SELECT") return;
    var num = numberFromBank(t);
    var box = num && num.parentNode.querySelector(".bank-suggest");
    if (box) box.innerHTML = "";
  });

  document.addEventListener('change', function (ev) {
    var t = ev.target;
    if (!t || t.tagName !== 'SELECT') return;
    var num = numberFromBank(t);
    if (num) runLookup(groupFromNumber(num));
  });
})();