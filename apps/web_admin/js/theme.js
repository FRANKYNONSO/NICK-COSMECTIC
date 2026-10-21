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
