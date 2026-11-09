// RBAC for the web app UI. This is a display/navigation convenience only --
// the server (nickcosmetics_core.domain.users.user + api/dependencies.py's
// require_permission/require_role) is the actual enforcement. Keep this
// table in sync with that file; if they drift, the UI might show a button
// the server will 403 on (harmless, just a bad look) or hide one the
// server would allow (annoying, but never a security hole either way,
// since the server never trusts the client).

const ROLE_PERMISSIONS = {
  super_admin: [
    'manage_users', 'manage_products', 'manage_inventory', 'manage_suppliers',
    'manage_coupons', 'view_all_reports', 'view_low_stock', 'view_financials',
    'configure_system', 'view_audit_logs', 'manage_sync', 'manage_backups',
    'process_sale', 'view_own_sales', 'remove_super_admin', 'transfer_ownership',
    'download_statements', 'view_purchase_invoices',
  ],
  branch_manager: [
    // "manage_users" deliberately excluded -- a branch manager can
    // view/list staff (any signed-in branch_manager reaches the Staff
    // page; the page itself scopes to their own branch), but editing,
    // deactivating, deleting, or resetting a password for an existing
    // account now requires "manage_users" specifically, which
    // branch_manager no longer carries by default. A super_admin can
    // grant it to one trusted branch manager via a custom role on the
    // Roles page.
    'manage_products', 'manage_inventory', 'manage_suppliers',
    'manage_coupons', 'view_all_reports', 'view_low_stock', 'view_financials',
    'view_audit_logs', 'manage_sync', 'process_sale', 'view_own_sales',
    'download_statements', 'view_purchase_invoices',
  ],
  staff: [
    // 'view_purchase_invoices' deliberately absent (platform update
    // spec section 5) -- staff have no Invoices access by default.
    'process_sale', 'view_own_sales', 'search_products', 'view_stock',
    'view_low_stock', 'register_customer', 'change_own_password',
  ],
  // PATCHW1B-MYPERMS: fallback list for store_assistant (same names as the
  // server matrix). Only used when the server's own list could not be
  // loaded; the real list from GET /auth/me/permissions wins whenever it
  // is available.
  store_assistant: [
    'change_own_password', 'search_products', 'view_stock', 'view_low_stock',
    'restock_shelf', 'set_shelf_limit', 'reduce_shelf_stock', 'view_received_goods',
    'manage_units', 'view_inventory_overview', 'view_stock_alerts', 'use_warehouse',
  ],
};

// Every permission a branch_manager can be individually restricted from
// (super_admin only, via PATCH /users/{id}/permissions) -- used to build
// the restriction checklist in staff.html. Deliberately excludes the base
// "process_sale"/"view_own_sales" pair: those are the minimum needed to
// actually run a till, so restricting them would leave a branch_manager
// unable to work at all rather than just scoped down. Also excludes
// "manage_users": that one isn't granted to branch_manager by default
// anymore, so there's nothing to restrict -- a super_admin grants it
// (rather than revokes it) to one trusted manager via a custom role.
const RESTRICTABLE_BRANCH_MANAGER_PERMISSIONS = [
  'manage_products', 'manage_inventory', 'manage_suppliers',
  'manage_coupons', 'view_all_reports', 'view_financials', 'view_audit_logs',
  'manage_sync',
];

// The other direction: extra abilities a super_admin can hand to one
// specific, trusted staff account on top of the base staff role --
// without upgrading them to branch_manager. Spec: "I have a particular
// staff I trust so much... things like we have the manage staff
// account, manage inventory and warehouse, issue and void coupons,
// view low stock, then configure system, manage device, synchronize,
// process sale". "view_low_stock" and "process_sale" are already part
// of the base staff role (see ROLE_PERMISSIONS.staff above) so aren't
// listed again here -- there'd be nothing to grant.
const GRANTABLE_STAFF_PERMISSIONS = [
  'manage_users', 'manage_inventory', 'manage_coupons',
  'configure_system', 'manage_sync',
];

const PERMISSION_LABELS = {
  manage_users: 'Manage staff accounts',
  manage_products: 'Manage products & categories',
  manage_inventory: 'Manage inventory & warehouse',
  manage_suppliers: 'Manage suppliers & purchasing',
  manage_coupons: 'Issue & void coupons',
  view_all_reports: 'View store-wide reports',
  view_financials: 'View financial totals',
  view_audit_logs: 'View audit logs',
  manage_sync: 'Manage device sync',
  download_statements: 'Download account statements',
  configure_system: 'Configure system settings',
  // PATCHR3A-ALPHA: names for the other permissions (so the Roles list reads well)
  view_low_stock: 'View low stock',
  manage_backups: 'Manage backups',
  process_sale: 'Process sales',
  view_own_sales: 'View own sales',
  search_products: 'Search products',
  view_stock: 'View stock',
  register_customer: 'Register customers',
  change_own_password: 'Change own password',
  remove_super_admin: 'Remove super admin',
  transfer_ownership: 'Transfer ownership',
  view_purchase_invoices: 'View purchase invoices',
  view_restock_list: 'View restock list',
  manage_restock_list: 'Manage restock list',
  restock_shelf: 'Restock shelf',
  set_shelf_limit: 'Set shelf limit',
  reduce_shelf_stock: 'Reduce shelf stock',
  view_received_goods: 'View received goods',
  view_catalog: 'View product catalog',
  manage_units: 'Manage packaging units',
  view_inventory_history: 'View inventory history',
  view_customer_orders: 'View customer orders',
  view_revenue_totals: 'View revenue totals',
  view_inventory_overview: 'View inventory overview',
  view_stock_alerts: 'View stock alerts',
  generate_pos_code: 'Generate POS code',
  view_website_code: 'View website code',
  view_transactions: 'View transactions',
  use_warehouse: 'Use warehouse',
};

// Which sidebar nav item requires which permission. A nav item with no
// entry here is open to any logged-in account (its page scopes its own
// content, e.g. dashboard/reports show "your data" vs "store data").
// 'warehouse' is intentionally *not* listed here: staff get read-only
// access via 'view_stock' (checked in warehouse.js itself), while
// branch_manager/super_admin get the full edit UI via 'manage_inventory'.
// Either grant is enough to see the link at all.
const NAV_REQUIREMENTS = {
  // Merged Categories + Variant Types page (variant-category-system-spec.md,
  // Feature Spec 1, section 1) -- was two separate nav entries
  // ('categories', 'variant-types'), now one.
  'category-variants': 'manage_products',
  // 'cashier' deliberately NOT listed here -- viewing the roster is a
  // role-based grant (any super_admin or branch_manager), not a
  // permission one, since branch_manager no longer carries
  // 'manage_users' by default but must still see the page. See
  // NAV_ROLE_REQUIREMENTS below.
  branches: 'configure_system',
  // Role management is deliberately gated on 'configure_system' rather
  // than 'manage_users' -- same super_admin-only permission 'branches'
  // above uses, so a branch_manager still can't reach it. Matches the
  // server-side check in api/v1/routers/roles.py (create/edit/delete
  // are super_admin-only).
  roles: 'configure_system',
  // Store branding (logo/name) -- same super_admin-only tier as
  // branches/roles above.
  settings: 'configure_system',
  // Shift open/close history -- staff can open/close their own shift
  // from the POS itself; this admin listing of everyone's shifts
  // matches the server-side view_all_reports check in shifts.py.
  shifts: 'view_all_reports',
  // Losses History (update spec 3, section 18.4) -- same tier as the
  // dashboard's Losses card and the /inventory/losses/history endpoint.
  losses: 'view_all_reports',
  // Dashboard redesign implementation prompt, step 4 -- Performance
  // Records replaces the old resettable financial-period tracking,
  // which only ever had a super-admin-triggered reset button but a
  // read-only history log gated the same as every other reports view.
  // Matches GET /reports/performance-records's own view_all_reports
  // check server-side.
  records: 'view_all_reports',
  // Platform update spec section 5: "Staff should not have access to
  // Invoices at all... invoices should be visible only to the super
  // admin and branch managers." Mirrors the server's
  // "view_purchase_invoices" gate (now revoked from the staff role by
  // migration dd11ee22ff33) on GET /purchase-invoices.
  invoice: 'view_purchase_invoices',
  // PATCHP3R-NAV: sidebar links for POS, Transactions and Reports use the
  // same key as the page. super_admin always passes Perms.can().
  pos: 'process_sale',
  sales: 'view_transactions',
  reports: 'view_all_reports',
};

// Nav items gated on role membership rather than a permission grant --
// see the 'cashier' comment above for why viewing the roster needs this
// instead of NAV_REQUIREMENTS.
const NAV_ROLE_REQUIREMENTS = {
  staff: ['super_admin', 'branch_manager'],
};

// PATCHP3A-WAREHOUSE-ACCESS
// Nav items shown when the person has ANY of the listed permissions.
// The Warehouse link needs use_warehouse or manage_inventory.
const NAV_ANY_REQUIREMENTS = {
  warehouse: ['use_warehouse', 'manage_inventory'],
};

const Perms = {
  user() {
    return Auth.getUser();
  },
  role() {
    return this.user()?.role;
  },
  isSuperAdmin() {
    return this.role() === 'super_admin';
  },
  isBranchManager() {
    return this.role() === 'branch_manager';
  },
  isStaff() {
    return this.role() === 'cashier';
  },
  can(action) {
    const user = this.user();
    if (!user) return false;
    // PATCHW1B-MYPERMS: super_admin is never restricted in the UI. Anyone
    // else uses the server's own list (GET /auth/me/permissions: role
    // list plus individual grants minus individual denials) whenever it
    // has been loaded. Only if it is missing (first page after an old
    // login, or the server could not be reached) does the old logic
    // below run, unchanged.
    if (user.role === 'super_admin') return true;
    const mine = Auth.getMyPermissions();
    if (mine) return mine.includes(action);
    // The cache (populated below from GET /roles) wins whenever it has
    // an entry for this role -- that's what lets a custom role, or a
    // super-admin edit to any role's permissions (including the three
    // system roles), actually show up here instead of only in the
    // hardcoded ROLE_PERMISSIONS table above, which never changes at
    // runtime. Falls back to that table only when the cache hasn't
    // populated yet (e.g. the very first page load after login).
    const cached = Auth.getRolePermissionsCache();
    const roleGrants = (cached && cached[user.role]) || ROLE_PERMISSIONS[user.role] || [];
    // Per-account grants (User.granted_permissions server-side) extend
    // the role's own list -- e.g. one trusted staff member with
    // "manage_inventory" granted individually, without their role
    // changing. Mirrors api/dependencies.py's require_permission().
    const granted = user.granted_permissions || [];
    if (!roleGrants.includes(action) && !granted.includes(action)) return false;
    const denied = user.denied_permissions || [];
    // super_admin can never be restricted -- mirrors User.can() server-side.
    if (user.role !== 'super_admin' && denied.includes(action)) return false;
    return true;
  },

  // Fire-and-forget refresh of the role-permissions cache, called once
  // per page load below. Deliberately not awaited by applyNav()/can() --
  // this page's nav still renders from whatever was cached last time
  // (or the static fallback table), and the *next* page navigation
  // picks up anything that changed. Good enough for an admin panel
  // where role edits are rare and not a security boundary (the server
  // is still the real enforcement either way).
  async refreshRolePermissionsCache() {
    if (!Auth.getToken()) return;
    try {
      const roles = await Api.listRoles();
      const map = {};
      roles.forEach((r) => { map[r.name] = r.permissions; });
      Auth.setRolePermissionsCache(map);
    } catch (err) {
      // Not fatal -- can() just keeps using whatever was cached before
      // (or the static fallback), same as if this call never ran.
    }
  },

  // PATCHW1B-MYPERMS: refresh the saved copy of this user's real
  // permissions (GET /auth/me/permissions). Fire-and-forget like the
  // role cache above; on any failure the previously saved copy stays.
  async refreshMyPermissions() {
    if (!Auth.getToken()) return;
    try {
      const mine = await Api.myPermissions();
      if (mine && Array.isArray(mine.permissions)) Auth.setMyPermissions(mine.permissions);
    } catch (err) {
      // Not fatal -- can() keeps using what was saved before, or the old fallback.
    }
  },

  // Hides sidebar links the current user shouldn't see. Call once per
  // page, right after Auth.requireAuth().
  applyNav() {
    document.querySelectorAll('[data-nav]').forEach((el) => {
      const required = NAV_REQUIREMENTS[el.dataset.nav];
      if (required && !this.can(required)) el.remove();
      const roleRequired = NAV_ROLE_REQUIREMENTS[el.dataset.nav];
      if (roleRequired && !roleRequired.includes(this.role())) el.remove();
      const anyRequired = NAV_ANY_REQUIREMENTS[el.dataset.nav]; // PATCHP3A-WAREHOUSE-ACCESS
      if (anyRequired && !anyRequired.some((p) => this.can(p))) el.remove();
    });
    // Reveal the nav now that the links that shouldn't be there are
    // gone -- see the .nav-gate-pending CSS rule in style.css for why
    // it started hidden (prevents a flash of not-allowed nav items
    // before this function has had a chance to run).
    document.querySelectorAll('nav.nav-gate-pending').forEach((n) => n.classList.remove('nav-gate-pending'));
  },

  // Belt-and-braces redirect for pages that are entirely gated behind one
  // permission (categories, import-export, branches) -- covers a
  // typed URL or stale bookmark, on top of the link already being hidden
  // and the server's own 403 on the underlying API calls.
  guardPage(requiredPermission) {
    if (requiredPermission && !this.can(requiredPermission)) {
      window.location.href = 'dashboard.html';
    }
  },

  // Like guardPage, but redirects based on role membership rather than a
  // permission grant -- for pages like staff.html that any super_admin
  // or branch_manager can view (branch-scoped for the latter), even
  // though editing within that page is gated on a finer-grained
  // permission check done separately, in the page itself.
  guardRole(...allowedRoles) {
    if (allowedRoles.length && !allowedRoles.includes(this.role())) {
      window.location.href = 'dashboard.html';
    }
  },

  // Like guardPage, but accepts *any* of several permissions -- for pages
  // like warehouse.html that are viewable by more than one grant (staff
  // via 'view_stock' read-only, manager/admin via 'manage_inventory' full
  // access) and should only bounce someone who has neither.
  guardPageAny(requiredPermissions) {
    if (requiredPermissions.length && !requiredPermissions.some((p) => this.can(p))) {
      window.location.href = 'dashboard.html';
    }
  },
};

// Kick off a background cache refresh on every page that includes this
// file (all of them) -- see refreshRolePermissionsCache() above for why
// this is fire-and-forget rather than blocking page render.
Perms.refreshRolePermissionsCache();
// PATCHW1B-MYPERMS: same for the user's own real permission list.
Perms.refreshMyPermissions();

// Chat was removed from the sidebar (no unread-count badge any more).
