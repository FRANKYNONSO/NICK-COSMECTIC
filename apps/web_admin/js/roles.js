Auth.requireAuth();
// Section 15: Roles now shares a page with Staff/Branches/Shifts under
// one "Staff" nav item -- the page-level guard lives in staff.js
// (super_admin/branch_manager, the broadest of the four). A
// page-bouncing guardPage() here would wrongly redirect a
// branch_manager away from the WHOLE merged page just for lacking
// configure_system, when they should still see the Staff tab. Hiding
// just this tab BUTTON (not the content itself, which the rest of
// this file still safely renders into off-screen) is the equivalent
// of the old redirect, scoped correctly to only what this section
// actually gates -- nobody without configure_system can ever click
// their way into it.
if (!Perms.can('configure_system')) {
  document.getElementById('tab-btn-roles')?.remove();
}
Perms.applyNav();


document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });


function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

// PERMISSION_LABELS (from js/permissions.js) only covers the permissions
// that were ever individually restrictable on a branch_manager account --
// a role's editable set is broader than that (process_sale, view_own_sales,
// etc. are real permissions too, just never had a friendly label needed
// before now). Falls back to a title-cased version of the raw name for
// anything PERMISSION_LABELS doesn't cover.
function permissionLabel(name) {
  if (PERMISSION_LABELS[name]) return PERMISSION_LABELS[name];
  return name.split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');
}

// nickcosmetics_core.domain.users.user.ALL_PERMISSIONS, mirrored here so
// the checkbox list has a fixed, complete set to render regardless of
// which permissions any single existing role happens to use. Keep in
// sync with that file if a new permission is ever added there.
const ALL_PERMISSIONS = [
  'manage_users', 'manage_products', 'manage_inventory', 'manage_suppliers',
  'manage_coupons', 'view_all_reports', 'view_low_stock', 'view_financials',
  'configure_system', 'view_audit_logs', 'manage_sync', 'manage_backups',
  'process_sale', 'view_own_sales', 'search_products', 'view_stock',
  'register_customer', 'change_own_password', 'remove_super_admin', 'transfer_ownership',
  'download_statements',
  // PATCHW1C-ROLES-UI: permissions added after the first version of this list.
  'view_purchase_invoices', 'view_restock_list', 'manage_restock_list', 'restock_shelf',
  'set_shelf_limit', 'reduce_shelf_stock', 'view_received_goods', 'view_catalog',
  'manage_units', 'view_inventory_history', 'view_customer_orders', 'view_revenue_totals',
  'view_inventory_overview', 'view_stock_alerts', 'generate_pos_code', 'view_website_code',
  'view_transactions', 'use_warehouse',
];

// PATCHR2-ROLES-UI: the four system roles can be edited (display name on all
// four, permissions on all except super_admin) but can never be deleted. The
// server enforces the same rules (PATCH 403 for super_admin permissions,
// DELETE 400/403 for all four).
const SYSTEM_ROLE_NAMES = new Set(['super_admin', 'branch_manager', 'cashier', 'store_assistant']);
const FIXED_PERMISSIONS_ROLE = 'super_admin';

let rolesCache = [];
// PATCHP3O-ROLES: permission registry (groups and tabs) from GET /roles/registry.
// Empty means not loaded, or the call failed: the old flat list is used then.
let registryCache = [];
async function loadRegistry() {
  try {
    const data = await Api.getPermissionRegistry();
    registryCache = Array.isArray(data) ? data : [];
  } catch (err) {
    registryCache = [];
  }
}

// Draws the boxes under group and tab headings. Returns false (and draws
// nothing) when the registry has no usable permission, so the caller falls
// back to the flat list. A permission the role holds that the registry does
// not list goes in an Other section, so saving never strips it.
function renderGroupedPermissionCheckboxes(checkedPermissions, checked) {
  const known = new Set();
  const box = (key, label) => `
    <label class="checkbox" style="display:flex;align-items:center;gap:8px;font-size:13px;">
      <input type="checkbox" value="${escapeHtml(key)}" ${checked.has(key) ? 'checked' : ''} />
      ${escapeHtml(label)}
    </label>`;
  const groupStyle = 'grid-column:1 / -1;font-size:14px;font-weight:700;margin-top:10px;';
  const tabStyle = 'grid-column:1 / -1;font-size:12px;font-weight:600;color:var(--ink-faint);text-transform:uppercase;letter-spacing:.04em;margin-top:2px;';
  let html = '';
  registryCache.forEach((g) => {
    let groupHtml = '';
    (g.tabs || []).forEach((t) => {
      const items = [];
      (t.permissions || []).forEach((p) => {
        if (p && p.key && !known.has(p.key)) { known.add(p.key); items.push(p); }
      });
      if (!items.length) return;
      groupHtml += `<div style="${tabStyle}">${escapeHtml(t.tab)}</div>` + items.map((p) => box(p.key, p.label || p.key)).join('');
    });
    if (groupHtml) html += `<div style="${groupStyle}">${escapeHtml(g.group)}</div>` + groupHtml;
  });
  if (!known.size) return false;
  const other = checkedPermissions.filter((p) => !known.has(p))
    .sort((a, b) => permissionLabel(a).localeCompare(permissionLabel(b), 'en', { sensitivity: 'base' }));
  if (other.length) {
    html += `<div style="${groupStyle}">Other</div>` + other.map((p) => box(p, permissionLabel(p))).join('');
  }
  document.getElementById('role-permissions-list').innerHTML = html;
  return true;
}
let editingRoleName = null; // null = creating a new role

async function loadRoles() {
  const tbody = document.getElementById('roles-tbody');
  const emptyState = document.getElementById('roles-empty-state');
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading…</td></tr>';
  try {
    rolesCache = await Api.listRoles();
    if (!rolesCache.length) {
      tbody.innerHTML = '';
      emptyState.style.display = 'block';
      return;
    }
    emptyState.style.display = 'none';
    tbody.innerHTML = rolesCache.map((r) => `
      <tr>
        <td><strong>${escapeHtml(r.display_name)}</strong><div style="font-size:11.5px;color:var(--ink-faint);">${escapeHtml(r.name)}</div></td>
        <td>${r.is_system ? '<span class="category-badge">System</span>' : '<span class="category-badge">Custom</span>'}</td>
        <td>${r.permissions.length} permission${r.permissions.length === 1 ? '' : 's'}</td>
        <td>${r.user_count} account${r.user_count === 1 ? '' : 's'}</td>
        <td style="text-align:right;">
          <button type="button" class="btn-ghost btn-sm" data-edit="${r.name}">Edit</button>
          ${SYSTEM_ROLE_NAMES.has(r.name) ? '' : `<button type="button" class="btn-ghost btn-sm" data-delete="${r.name}" ${r.user_count ? 'disabled title="Reassign accounts off this role first"' : ''}>Delete</button>`}
        </td>
      </tr>`).join('');

    tbody.querySelectorAll('[data-edit]').forEach((btn) => {
      btn.addEventListener('click', () => openRoleModal(btn.dataset.edit));
    });
    tbody.querySelectorAll('[data-delete]').forEach((btn) => {
      btn.addEventListener('click', () => deleteRole(btn.dataset.delete));
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

const roleBackdrop = document.getElementById('role-modal-backdrop');

function renderPermissionCheckboxes(checkedPermissions) {
  const checked = new Set(checkedPermissions);
  // PATCHP3O-ROLES: grouped list when the registry is loaded. Otherwise the
  // old flat list below runs unchanged.
  if (registryCache.length && renderGroupedPermissionCheckboxes(checkedPermissions, checked)) return;
  // Show every known permission plus any permission this role already
  // holds that is not in the fixed list, so saving never strips it.
  // PATCHR3A-ALPHA: A-Z order of the name the admin reads.
  const shown = ALL_PERMISSIONS.concat(checkedPermissions.filter((p) => !ALL_PERMISSIONS.includes(p)))
    .sort((a, b) => permissionLabel(a).localeCompare(permissionLabel(b), 'en', { sensitivity: 'base' }));
  document.getElementById('role-permissions-list').innerHTML = shown.map((p) => `
    <label class="checkbox" style="display:flex;align-items:center;gap:8px;font-size:13px;">
      <input type="checkbox" value="${p}" ${checked.has(p) ? 'checked' : ''} />
      ${escapeHtml(permissionLabel(p))}
    </label>`).join('');
}

function openRoleModal(name) {
  document.getElementById('role-modal-error').classList.remove('visible');
  const role = name ? rolesCache.find((r) => r.name === name) : null;
  editingRoleName = name || null;
  roleIdTouched = false;

  document.getElementById('role-modal-title').textContent = role ? `Edit ${role.display_name}` : 'New role';
  document.getElementById('role-modal-hint').textContent = role
    ? 'Changes apply to every account currently assigned this role.'
    : 'A brand-new role starts with no permissions — check off exactly what it should be able to do.';
  document.getElementById('role-display-name').value = role ? role.display_name : '';
  document.getElementById('role-name').value = role ? role.name : '';
  // Role ID is immutable once created (it's what users.role points to) --
  // only editable while creating a brand-new role.
  document.getElementById('role-name').disabled = !!role;
  document.getElementById('role-name-field').style.display = role ? 'none' : '';

  renderPermissionCheckboxes(role ? role.permissions : []);
  // PATCHR2-ROLES-UI: the Super Admin permissions are fixed -- show them read-only.
  const permissionsFixed = !!role && role.name === FIXED_PERMISSIONS_ROLE;
  document.querySelectorAll('#role-permissions-list input').forEach((cb) => { cb.disabled = permissionsFixed; });
  if (permissionsFixed) {
    document.getElementById('role-modal-hint').textContent = 'The Super Admin permissions are fixed. You can only change the display name.';
  }
  roleBackdrop.classList.add('visible');
}

document.getElementById('new-role-btn').addEventListener('click', () => openRoleModal(null));
// Role ID auto-fills from the display name (still editable) until the admin types their own.
function slugifyRoleId(text) {
  let s = String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (s && !/^[a-z]/.test(s)) s = 'role_' + s;
  return s;
}
let roleIdTouched = false;
document.getElementById('role-name').addEventListener('input', () => { roleIdTouched = true; });
document.getElementById('role-display-name').addEventListener('input', (e) => {
  if (editingRoleName || roleIdTouched) return;
  document.getElementById('role-name').value = slugifyRoleId(e.target.value);
});
document.getElementById('role-modal-cancel').addEventListener('click', () => roleBackdrop.classList.remove('visible'));

document.getElementById('role-modal-save').addEventListener('click', async () => {
  const errorEl = document.getElementById('role-modal-error');
  errorEl.classList.remove('visible');

  const displayName = document.getElementById('role-display-name').value.trim();
  if (!displayName) {
    errorEl.textContent = 'Enter a display name.';
    errorEl.classList.add('visible');
    return;
  }
  const permissions = [...document.querySelectorAll('#role-permissions-list input:checked')].map((cb) => cb.value);

  const btn = document.getElementById('role-modal-save');
  btn.disabled = true;
  try {
    if (editingRoleName) {
      const updateBody = { display_name: displayName };
      if (editingRoleName !== FIXED_PERMISSIONS_ROLE) updateBody.permissions = permissions;
      await Api.updateRole(editingRoleName, updateBody);
      showToast('Role updated.');
    } else {
      const name = document.getElementById('role-name').value.trim() || slugifyRoleId(displayName);
      if (!/^[a-z][a-z0-9_]*$/.test(name)) {
        errorEl.textContent = 'Role ID must be lowercase letters, numbers, and underscores, starting with a letter.';
        errorEl.classList.add('visible');
        btn.disabled = false;
        return;
      }
      await Api.createRole({ name, display_name: displayName, permissions });
      showToast('Role created.');
    }
    // The nav/permission cache (permissions.js) refreshes in the
    // background on the next page load -- edits here take effect for
    // everyone holding the role on their *next* navigation, same as
    // documented in Perms.refreshRolePermissionsCache().
    roleBackdrop.classList.remove('visible');
    loadRoles();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  } finally {
    btn.disabled = false;
  }
});

async function deleteRole(name) {
  if (SYSTEM_ROLE_NAMES.has(name)) return; // PATCHR2-ROLES-UI: system roles can never be deleted
  const role = rolesCache.find((r) => r.name === name);
  if (!(await showConfirmModal(`This can't be undone.`, { title: `Delete the "${role?.display_name || name}" role?`, confirmLabel: 'Delete' }))) return;
  try {
    await Api.deleteRole(name);
    showToast('Role deleted.');
    loadRoles();
  } catch (err) {
    showToast(err.message, true);
  }
}

// PATCHR2-ROLES-UI: short note under the roles table. Some pages and actions
// check the role NAME, so ticking a box here does not change them.
function addRoleNameNote() {
  const tbody = document.getElementById('roles-tbody');
  const table = tbody && tbody.closest('table');
  if (!table || !table.parentNode || document.getElementById('roles-name-note')) return;
  const note = document.createElement('p');
  note.id = 'roles-name-note';
  note.style.cssText = 'font-size:12.5px;color:var(--ink-faint);margin:10px 0 0;';
  note.textContent = 'Note: staff applications, website orders, user accounts, password reset requests, some sales actions and the Staff page tabs check the role itself, so the tick boxes here do not change them.';
  table.parentNode.insertBefore(note, table.nextSibling);
}
addRoleNameNote();
loadRoles();
// PATCHP3O-ROLES: the registry route is super_admin only, so ask only when
// this account can configure the system. Any failure keeps the flat list.
if (Perms.can('configure_system')) loadRegistry();
