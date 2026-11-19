/* fix76-phone-cart-panel */
(function () {
  "use strict";
  var QUERY = "(max-width: 760px)";
  var attempts = 0;

  function init() {
    var card = document.querySelector(".cart-card");
    var body = document.getElementById("cart-body");
    var bar = document.querySelector(".m-topbar");
    if (!card || !body) { return true; }
    if (!bar) { return false; }
    if (bar.querySelector(".m-cart-btn")) { return true; }

    var html = document.documentElement;
    var mq = window.matchMedia(QUERY);
    var savedY = 0;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "m-cart-btn";
    btn.setAttribute("aria-label", "Open cart");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z"/></svg><span class="m-cart-badge" hidden>0</span>';
    var actions = bar.querySelector(".m-actions");
    if (actions) { bar.insertBefore(btn, actions); } else { bar.appendChild(btn); }
    var badge = btn.querySelector(".m-cart-badge");

    var closeBar = document.createElement("div");
    closeBar.className = "m-cart-close-bar";
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "m-cart-close-btn";
    closeBtn.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg><span>Close</span>';
    closeBar.appendChild(closeBtn);
    card.insertBefore(closeBar, card.firstChild);

    function count() {
      var n = 0;
      var spans = body.querySelectorAll(".cart-row .qty span");
      for (var i = 0; i < spans.length; i++) {
        var v = parseInt(spans[i].textContent, 10);
        if (v > 0) { n += v; }
      }
      return n;
    }

    var last = -1;
    function refresh() {
      var n = count();
      badge.textContent = n > 99 ? "99+" : String(n);
      badge.hidden = (n === 0);
      btn.setAttribute("aria-label", n ? "Open cart, " + n + " pieces" : "Open cart");
      if (last >= 0 && n > last) {
        btn.classList.remove("m-bump");
        void btn.offsetWidth;
        btn.classList.add("m-bump");
      }
      last = n;
    }

    function setOpen(open) {
      if (open === card.classList.contains("m-cart-open")) { return; }
      if (open) { savedY = window.pageYOffset || 0; }
      card.classList.toggle("m-cart-open", open);
      html.classList.toggle("m-cart-lock", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { card.scrollTop = 0; } else { window.scrollTo(0, savedY); }
    }

    btn.addEventListener("click", function () { setOpen(true); });
    closeBtn.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !card.classList.contains("m-cart-open")) { return; }
      if (document.querySelector(".modal-backdrop.visible, .ud-overlay")) { return; }
      setOpen(false);
    });
    function onMq() { if (!mq.matches) { setOpen(false); } }
    if (mq.addEventListener) { mq.addEventListener("change", onMq); }
    else if (mq.addListener) { mq.addListener(onMq); }

    new MutationObserver(refresh).observe(body, { childList: true, subtree: true, characterData: true });
    refresh();
    html.classList.add("m-cart-ready");
    return true;
  }

  function attempt() {
    if (init()) { return; }
    attempts += 1;
    if (attempts < 40) { setTimeout(attempt, 50); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attempt);
  } else {
    attempt();
  }
})();
/* end fix76-phone-cart-panel */
