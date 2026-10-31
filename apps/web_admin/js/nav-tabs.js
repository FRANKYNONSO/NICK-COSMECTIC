// Shared section tab strip for grouped pages.
//   Inventory : Products | Catalog | Alerts
//   Reports   : Reports | Performance
// (Invoice and Warehouse are standalone sidebar items.)
// Tabs the signed-in person is not allowed to open are left out (same
// permissions as the old sidebar links). The strip is hidden if fewer than
// two tabs are visible. Transactions and Team already have their own tabs.
(function () {
  'use strict';

  var GROUPS = [
    { tabs: [
      { file: 'inventory.html', label: 'Products' },
      { file: 'category-variants.html', label: 'Catalog', perm: 'manage_products' },
      { file: 'low-stock.html', label: 'Alerts' },
      { file: 'restock-list.html', label: 'Restock List', perm: 'view_restock_list' },
      { file: 'customer-requests.html', label: 'Customer Orders', perm: 'view_restock_list' },
      { file: 'receive-goods.html', label: 'Receive Goods', perm: 'manage_inventory' },
      { file: 'manage-units.html', label: 'Manage Units', perm: 'manage_products' }
    ] },
    { tabs: [
      { file: 'reports.html', label: 'Reports' },
      { file: 'records.html', label: 'Performance', perm: 'view_all_reports' }
    ] }
  ];

  function canSee(tab) {
    if (!tab.perm) return true;
    try {
      return typeof Perms !== 'undefined' && Perms.can(tab.perm);
    } catch (e) {
      return false;
    }
  }

  function build() {
    var current = (location.pathname.split('/').pop() || '').toLowerCase();
    var group = null;
    for (var i = 0; i < GROUPS.length; i++) {
      for (var j = 0; j < GROUPS[i].tabs.length; j++) {
        if (GROUPS[i].tabs[j].file === current) { group = GROUPS[i]; }
      }
    }
    if (!group) return;
    var tabs = group.tabs.filter(canSee);
    if (tabs.length < 2) return;
    var main = document.querySelector('main.main');
    if (!main || main.querySelector('.group-tabs')) return;

    if (!document.getElementById('group-tabs-style')) {
      var st = document.createElement('style');
      st.id = 'group-tabs-style';
      st.textContent =
        '.group-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px;}' +
        '.group-tabs a{padding:6px 16px;border-radius:999px;font-size:12.5px;' +
        'text-decoration:none;color:var(--ink-muted);border:1px solid var(--border);' +
        'background:var(--surface-raised,var(--surface));}' +
        '.group-tabs a:hover:not(.active){color:var(--ink);background:var(--bg-muted);}' +
        '.group-tabs a.active{background:var(--accent,#6c4bd6);color:#fff;' +
        'border-color:transparent;font-weight:500;}' +
        '.inventory-card>.group-tabs,.io-card>.group-tabs,.tabs-card>.group-tabs{margin:0;padding:18px 24px 0;}' +
        '.tabs-card{background:var(--surface);border:1px solid var(--border);border-radius:18px;margin-bottom:20px;overflow:hidden;}' +
        '.tabs-card>.page-head{margin:0;padding:16px 24px 22px;}';
      document.head.appendChild(st);
    }

    var bar = document.createElement('div');
    bar.className = 'group-tabs';
    bar.setAttribute('role', 'navigation');
    bar.setAttribute('aria-label', 'Section');
    tabs.forEach(function (t) {
      var a = document.createElement('a');
      a.href = t.file;
      a.textContent = t.label;
      if (t.file === current) { a.className = 'active'; a.setAttribute('aria-current', 'page'); }
      bar.appendChild(a);
    });
    var first = main.firstElementChild;
    var inInventoryGroup = group.tabs[0].file === 'inventory.html';
    if (inInventoryGroup && first && (first.classList.contains('inventory-card') || first.classList.contains('io-card'))) {
      first.insertBefore(bar, first.firstChild);
    } else if (inInventoryGroup && first && first.classList.contains('page-head')) {
      var wrap = document.createElement('div');
      wrap.className = 'tabs-card';
      main.insertBefore(wrap, first);
      wrap.appendChild(bar);
      wrap.appendChild(first);
    } else {
      main.insertBefore(bar, main.firstChild);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
