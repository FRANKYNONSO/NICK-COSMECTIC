// Deliberately narrow scope: this makes the app installable and lets the
// shell (HTML/CSS/JS) load instantly on repeat visits, including with a
// flaky connection. It does NOT cache anything under /api/ and does NOT
// queue requests made while offline -- for a POS, showing stale stock
// levels or a stale cart total would be actively dangerous, and silently
// queuing a checkout that never actually reached the server would be
// worse than just failing loudly. True offline sale-taking (local queue +
// background sync once reconnected) is a separate, deliberately deferred
// project -- see the offline/outbox discussion earlier in this build.
// This service worker only ever makes "the app opens fast," never "the
// app works correctly without a connection."

// v2: categories.html + variant-types.html merged into
// category-variants.html (variant-category-system-spec.md) -- bumped so
// clients with the old shell cached don't keep serving the removed page.
const CACHE_NAME = 'nc-admin-shell-v14';
const SHELL_ASSETS = [
  'dashboard.html',
  'pos.html',
  'sales.html',
  'inventory.html',
  'low-stock.html',
  'warehouse.html',
  'category-variants.html',
  'reports.html',
  'staff.html',
  'branches.html',
  'css/style.css',
  'js/theme.js',
  'js/api.js',
  'js/permissions.js',
  'js/website-orders.js',
  'manifest.json',
  'icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {
      // Best-effort: a missing asset (e.g. a page not present in this
      // deployment) shouldn't block installation of the rest.
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never intercept API calls -- always go to the network, always get
  // live data. If the network is down, this fails the same way it would
  // without a service worker at all; no cached/fake response takes its
  // place.
  if (url.pathname.includes('/api/')) return;

  if (event.request.method !== 'GET') return;

// ---- network-first for code (cache bump)
  // JS, HTML, CSS and page navigations: ask the network first (bypassing
  // the browser's own HTTP cache), fall back to the cached copy only when
  // offline. API calls were already excluded above.
  const isCode =
    event.request.mode === 'navigate' ||
    /\.(?:js|html|css)$/i.test(url.pathname) ||
    url.pathname.endsWith('/');
  if (isCode) {
    event.respondWith(
      fetch(event.request, { cache: 'no-cache' })
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then((c) => c || Response.error()))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      // Cache-first for instant loads, but always refresh the cache
      // from the network in the background so the next load is current.
      return cached || network;
    })
  );
});
