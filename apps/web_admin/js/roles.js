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
];

let rolesCache = [];
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
          ${r.name !== 'super_admin' ? `<button type="button" class="btn-ghost btn-sm" data-delete="${r.name}" ${r.user_count ? 'disabled title="Reassign accounts off this role first"' : ''}>Delete</button>` : ''}
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
  document.getElementById('role-permissions-list').innerHTML = ALL_PERMISSIONS.map((p) => `
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
      await Api.updateRole(editingRoleName, { display_name: displayName, permissions });
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

loadRoles();
