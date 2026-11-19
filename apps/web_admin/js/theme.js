// Applied immediately (before body paints) so switching pages never
// flashes the wrong theme. Persisted choice lives in localStorage;
// default is dark, matching the app's original look.
(function () {
  var STORAGE_KEY = 'nc-theme';
  var saved = localStorage.getItem(STORAGE_KEY);
  var theme = saved === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', theme);

  function wireToggle() {
    var btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    var label = document.getElementById('theme-toggle-text');
    function render() {
      if (label) label.textContent = theme === 'dark' ? 'Dark mode' : 'Light mode';
    }
    render();
    btn.addEventListener('click', function () {
      theme = theme === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem(STORAGE_KEY, theme);
      render();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireToggle);
  } else {
    wireToggle();
  }

  // PWA install support -- registers the shell-caching service worker
  // (see service-worker.js for exactly what it does and, just as
  // importantly, what it deliberately doesn't do). Skipped on file://
  // or non-secure contexts where the browser wouldn't allow it anyway.
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('service-worker.js').catch(function () {
        // Non-fatal -- the app works fully without it, just without
        // the installability/instant-repeat-load benefit.
      });
    });
  }
})();

/* fix60-phone-menu-drawer */
(function () {
  "use strict";
  function init() {
    var shell = document.querySelector(".app-shell");
    var sidebar = shell ? shell.querySelector(".sidebar") : null;
    if (!shell || !sidebar || document.querySelector(".m-topbar")) { return; }
    if (!sidebar.id) { sidebar.id = "app-sidebar"; }

    var bar = document.createElement("div");
    bar.className = "m-topbar";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "m-menu-btn";
    btn.setAttribute("aria-label", "Open menu");
    btn.setAttribute("aria-controls", sidebar.id);
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
    bar.appendChild(btn);

    var brandSrc = sidebar.querySelector(".sb-text");
    if (brandSrc) {
      var brand = document.createElement("span");
      brand.className = "m-brand";
      brand.appendChild(brandSrc.cloneNode(true));
      bar.appendChild(brand);
    }

    var backdrop = document.createElement("div");
    backdrop.className = "m-backdrop";

    shell.insertBefore(bar, shell.firstChild);
    shell.appendChild(backdrop);

    function setOpen(open) {
      shell.classList.toggle("m-open", open);
      document.documentElement.classList.toggle("m-menu-lock", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }

    btn.addEventListener("click", function () {
      setOpen(!shell.classList.contains("m-open"));
    });
    backdrop.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { setOpen(false); }
    });
    sidebar.addEventListener("click", function (e) {
      var a = e.target && e.target.closest ? e.target.closest("a") : null;
      if (a) { setOpen(false); }
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 760) { setOpen(false); }
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
/* end fix60-phone-menu-drawer */

/* fix61-phone-topbar-actions */
(function () {
  'use strict';
  var QUERY = '(max-width: 760px)';
  var attempts = 0;

  function init() {
    var bell = document.querySelector('.bell-wrap');
    var avatar = document.querySelector('.avatar-wrap');
    if (!bell && !avatar) { return true; }
    var bar = document.querySelector('.m-topbar');
    if (!bar) { return false; }
    if (bar.querySelector('.m-actions')) { return true; }
    var box = document.createElement('div');
    box.className = 'm-actions';
    bar.appendChild(box);
    var items = [];
    [bell, avatar].forEach(function (el) {
      if (!el || !el.parentNode) { return; }
      var ph = document.createComment('fix61-placeholder');
      el.parentNode.insertBefore(ph, el);
      items.push({ el: el, ph: ph });
    });
    var mq = window.matchMedia(QUERY);
    function place() {
      var moved = 0;
      items.forEach(function (it) {
        if (mq.matches) {
          if (it.el.parentNode !== box) { box.appendChild(it.el); }
          moved += 1;
        } else if (it.ph.nextSibling !== it.el) {
          it.ph.parentNode.insertBefore(it.el, it.ph.nextSibling);
        }
      });
      document.documentElement.setAttribute('data-fix61', 'moved=' + moved);
    }
    place();
    if (mq.addEventListener) { mq.addEventListener('change', place); }
    else if (mq.addListener) { mq.addListener(place); }
    return true;
  }

  function attempt() {
    if (init()) { return; }
    attempts += 1;
    if (attempts < 40) { setTimeout(attempt, 50); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attempt);
  } else {
    attempt();
  }
})();
/* end fix61-phone-topbar-actions */

/* fix62-phone-brand-sound */
(function () {
  'use strict';

  function fixSound() {
    var btn = document.getElementById('wo-alarm-btn');
    if (!btn) { return; }
    var actions = document.querySelector('.top-actions');
    if (!actions) { return; }
    if (btn.parentNode === actions) { return; }
    var sync = document.getElementById('sync-btn');
    if (sync && sync.parentNode === actions) {
      actions.insertBefore(btn, sync.nextSibling);
    } else {
      actions.appendChild(btn);
    }
    btn.style.position = '';
    btn.style.top = '';
    btn.style.right = '';
    btn.style.zIndex = '';
    document.documentElement.setAttribute('data-fix62', 'sound-moved');
  }

  function start() {
    if (!document.querySelector('.top-actions')) { return; }
    fixSound();
    if (window.MutationObserver && document.body) {
      var mo = new MutationObserver(fixSound);
      mo.observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
/* end fix62-phone-brand-sound */
