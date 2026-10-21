Auth.requireAuth();
Perms.guardRole('super_admin', 'branch_manager');
Perms.applyNav();

// Editing an existing staff account (edit fields, reset password,
// deactivate/reactivate, delete) requires "manage_users" specifically --
// branch_manager no longer carries it by default, so a manager without
// it can view the roster but the action buttons below are hidden for
// them. A super_admin can grant a trusted manager this permission via a
// custom role on the Roles page.
const canEditStaff = Perms.can('manage_users');

const roleLabels = {
  super_admin: 'Super admin',
  branch_manager: 'Branch manager',
  staff: 'Cashier',
};

const currentUser = Auth.getUser();
const isSuperAdmin = currentUser?.role === 'super_admin';
const isBranchManager = currentUser?.role === 'branch_manager';

document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';

document.getElementById('logout-link').addEventListener('click', (e) => {
  e.preventDefault();
  Auth.logout();
});

// Role picker/filter options are fetched from GET /roles (not just the
// three hardcoded ones) so a custom role a super_admin created shows up
// here too -- see loadRoleOptions() below, called once at init.
if (isBranchManager) {
  // Branch managers only ever create/manage staff -- trim the role
  // picker on the create form to match what the server will actually
  // allow, same as before, just no longer dependent on static <option>
  // elements already being in the markup.
  document.getElementById('role').innerHTML = '<option value="staff">Staff</option>';
}

// ------------------------------------------------------------ Branches

let branchCache = [];
let branchNameById = {};

async function loadBranches() {
  try {
    branchCache = await Api.listBranches();
    branchNameById = Object.fromEntries(branchCache.map((b) => [b.id, b.name]));
    const select = document.getElementById('branch_id');
    select.innerHTML =
      '<option value="">No branch assigned</option>' +
      branchCache.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('') +
      '<option value="__new__">+ Create new branch…</option>';
  } catch (err) {
    // Non-fatal — branch picker just stays empty if this fails.
  }
}

document.getElementById('branch_id').addEventListener('change', (e) => {
  const newBranchField = document.getElementById('new-branch-field');
  const newBranchInput = document.getElementById('new_branch_name');
  const isNew = e.target.value === '__new__';
  newBranchField.style.display = isNew ? '' : 'none';
  newBranchInput.required = isNew;
  if (!isNew) newBranchInput.value = '';
});

// ------------------------------------------------------------- Toast

let toastTimer = null;
function showToast(message, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

// -------------------------------------------------------------- Load

let staffCache = [];

async function loadStaff() {
  const role = document.getElementById('role-filter').value;
  const includeInactive = document.getElementById('include-inactive').checked;
  // Scope the roster to whichever branch is active on the Dashboard, same
  // as every other page -- otherwise a brand-new branch with no staff of
  // its own would still show every other branch's people. A branch
  // manager is always hard-scoped to their own branch server-side
  // regardless of this value; this only matters for super_admin.
  const branchId = BranchContext.get();
  const tbody = document.getElementById('staff-tbody');
  document.getElementById('empty-state').style.display = 'none';
  document.getElementById('staff-table').style.display = '';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading staff…</td></tr>';
  try {
    const [users, applications] = await Promise.all([
      Api.listUsers({ branchId, role: role || undefined, includeInactive }),
      Api.listApplications(),
    ]);
    // Attach each user's application status (if any) so the row can show
    // "Under review" / "Rejected" instead of a misleading "Active" for
    // someone who was never actually granted access.
    applicationByUserId = {};
    applications.forEach((a) => { applicationByUserId[a.user_id] = a; });
    staffCache = users;
    renderStaff(staffCache);
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

let applicationByUserId = {};

function renderStaff(users) {
  const tbody = document.getElementById('staff-tbody');
  const emptyState = document.getElementById('empty-state');
  const table = document.getElementById('staff-table');

  if (!users.length) {
    tbody.innerHTML = '';
    table.style.display = 'none';
    emptyState.style.display = 'block';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';

  tbody.innerHTML = users.map((u) => rowHtml(u)).join('');

  // Wire up per-row actions after the HTML lands.
  users.forEach((u) => {
    document.querySelector(`[data-edit="${u.id}"]`)?.addEventListener('click', () => openEditModal(u));
    document.querySelector(`[data-reset="${u.id}"]`)?.addEventListener('click', () => openResetModal(u));
    document.querySelector(`[data-toggle="${u.id}"]`)?.addEventListener('click', () => toggleActive(u));
    document.querySelector(`[data-delete-user="${u.id}"]`)?.addEventListener('click', () => deleteUser(u));
    const application = applicationByUserId[u.id];
    if (application) {
      document.querySelector(`[data-review="${application.id}"]`)?.addEventListener('click', () => openReviewModal(application.id));
    }
    const roleSelect = document.querySelector(`[data-role-select="${u.id}"]`);
    if (roleSelect) {
      roleSelect.addEventListener('change', () => changeRole(u, roleSelect.value));
    }
    document.querySelector(`[data-restrict="${u.id}"]`)?.addEventListener('click', () => openRestrictModal(u));
    document.querySelector(`[data-grant="${u.id}"]`)?.addEventListener('click', () => openGrantModal(u));
  });
}

function rowHtml(u) {
  const contactBits = [u.email, u.phone].filter(Boolean).join(' · ');
  const roleCell = isSuperAdmin
    ? `<select class="mono" data-role-select="${u.id}" style="padding:5px 8px;border-radius:6px;border:1px solid var(--border);font-size:12.5px;">
         ${Object.entries(roleLabels).map(([val, label]) =>
           `<option value="${val}" ${u.role === val ? 'selected' : ''}>${label}</option>`
         ).join('')}
       </select>`
    : `<span class="role-badge role-${u.role}"><span class="dot"></span>${roleLabels[u.role] || u.role}</span>`;

  const toggleLabel = u.is_active ? 'Deactivate' : 'Activate';
  const isSelf = u.id === currentUser?.id;

  // A linked application overrides the plain is_active-based status --
  // "Active" is misleading for someone still under_review/rejected, who
  // never actually had access regardless of their is_active flag.
  const application = applicationByUserId[u.id];
  let statusPill = `<span class="status-pill ${u.is_active ? 'active' : 'inactive'}">${u.is_active ? 'Active' : 'Deactivated'}</span>`;
  if (application && application.status !== 'accepted') {
    statusPill = `<span class="status-pill ${application.status}">${statusLabels[application.status] || application.status}</span>`;
  }

  return `
    <tr class="${u.is_active ? '' : 'inactive-row'}">
      <td class="name-cell">
        <div class="full-name">${escapeHtml(u.full_name)}</div>
        ${contactBits ? `<div class="contact">${escapeHtml(contactBits)}</div>` : ''}
      </td>
      <td>${roleCell}</td>
      <td>${u.branch_id ? escapeHtml(branchNameById[u.branch_id] || `${u.branch_id.slice(0, 8)}…`) : '<span style="color:var(--ink-faint)">—</span>'}</td>
      <td>${statusPill}</td>
      <td>
        <div class="row-actions">
          ${application ? (
            application.status !== 'accepted'
              ? `<button class="btn btn-ghost btn-sm" data-review="${application.id}">Review</button>`
              // Once accepted, the applicant becomes ordinary "active
              // staff" -- but everything they uploaded when applying
              // (DOB, national ID, next-of-kin, ID document, etc.)
              // should still be reachable, not just during the brief
              // review window. Super admin only, per spec ("it should
              // only be accessible to the super admin"). Widened: branch managers can now
              // open it too, for staff in their own branch (the API enforces that).
              : ((isSuperAdmin || (currentUser?.role === 'branch_manager' && (isSelf || !['super_admin', 'branch_manager'].includes(u.role)))) ? `<button class="btn btn-ghost btn-sm" data-review="${application.id}">View details</button>` : '')
          ) : ''}
          ${canEditStaff ? `<button class="btn btn-ghost btn-sm" data-edit="${u.id}">Edit</button>` : ''}
          ${canEditStaff ? `<button class="btn btn-ghost btn-sm" data-reset="${u.id}">Reset password</button>` : ''}
          ${isSuperAdmin && u.role === 'branch_manager' ? `<button class="btn btn-ghost btn-sm" data-restrict="${u.id}">Restrict access</button>` : ''}
          ${isSuperAdmin && u.role === 'staff' ? `<button class="btn btn-ghost btn-sm" data-grant="${u.id}">Grant extra access</button>` : ''}
          ${canEditStaff && !isSelf ? `<button class="btn btn-ghost btn-sm" data-toggle="${u.id}">${toggleLabel}</button>` : ''}
          ${canEditStaff && !isSelf ? `<button class="btn btn-danger-text btn-sm" data-delete-user="${u.id}">Delete</button>` : ''}
        </div>
      </td>
    </tr>`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Populates the create-form role picker and the list filter from GET
// /roles, and merges each role's display_name into roleLabels so the
// per-row role-change <select> (built from Object.entries(roleLabels)
// in rowHtml above) and the role-badge text both pick up custom roles
// too. Falls back to the static three-role default already in
// roleLabels if the fetch fails, so the page still functions.
async function loadRoleOptions() {
  let roles = [];
  try {
    roles = await Api.listRoles();
  } catch (err) {
    // Non-fatal -- proceed with the static default roleLabels above.
  }
  roles.forEach((r) => { roleLabels[r.name] = r.display_name; });

  const options = roles.length
    ? roles.map((r) => ({ value: r.name, label: r.display_name }))
    : Object.entries(roleLabels).map(([value, label]) => ({ value, label }));

  if (!isBranchManager) {
    document.getElementById('role').innerHTML = options
      .map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join('');
  }
  document.getElementById('role-filter').innerHTML = '<option value="">All roles</option>' + options
    .map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join('');
}

document.getElementById('role-filter').addEventListener('change', loadStaff);
document.getElementById('include-inactive').addEventListener('change', loadStaff);

// ----------------------------------------------------- Create / Edit modal

const userModalBackdrop = document.getElementById('user-modal-backdrop');
const userForm = document.getElementById('user-form');
const modalError = document.getElementById('modal-error');

function openCreateModal() {
  userForm.reset();
  document.getElementById('user-id').value = '';
  document.getElementById('user-modal-title').textContent = 'New staff';
  document.getElementById('user-modal-sub').textContent = 'Create a staff account.';
  document.getElementById('role-field').style.display = '';
  document.getElementById('password-field').style.display = '';
  document.getElementById('branch-field').style.display = isBranchManager ? 'none' : '';
  document.getElementById('new-branch-field').style.display = 'none';
  document.getElementById('new_branch_name').required = false;
  document.getElementById('password').required = true;
  modalError.classList.remove('visible');
  userModalBackdrop.classList.add('visible');
}

function openEditModal(u) {
  userForm.reset();
  document.getElementById('user-id').value = u.id;
  document.getElementById('user-modal-title').textContent = 'Edit staff';
  document.getElementById('user-modal-sub').textContent = 'Update contact details.';
  document.getElementById('full_name').value = u.full_name;
  document.getElementById('email').value = u.email || '';
  document.getElementById('phone').value = u.phone || '';
  document.getElementById('branch_id').value = u.branch_id || '';
  // Role and password aren't editable here — role has its own dedicated
  // control (super_admin only), and password has its own reset flow.
  document.getElementById('role-field').style.display = 'none';
  document.getElementById('password-field').style.display = 'none';
  document.getElementById('branch-field').style.display = isBranchManager ? 'none' : '';
  document.getElementById('new-branch-field').style.display = 'none';
  document.getElementById('new_branch_name').required = false;
  document.getElementById('password').required = false;
  modalError.classList.remove('visible');
  userModalBackdrop.classList.add('visible');
}

function closeUserModal() {
  userModalBackdrop.classList.remove('visible');
}

document.getElementById('new-staff-btn').addEventListener('click', openCreateModal);
document.getElementById('user-modal-cancel').addEventListener('click', closeUserModal);

userForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = document.getElementById('user-id').value;
  const saveBtn = document.getElementById('user-modal-save');
  saveBtn.disabled = true;
  modalError.classList.remove('visible');

  try {
    let branchId = document.getElementById('branch_id').value || null;

    // "+ Create new branch…" was picked — create it first, then use the
    // real id it comes back with for the user we're about to save.
    if (branchId === '__new__') {
      const name = document.getElementById('new_branch_name').value.trim();
      const newBranch = await Api.createBranch({ name, address: null, phone: null, is_warehouse: false });
      branchId = newBranch.id;
      await loadBranches();
      showToast(`Branch "${name}" created.`);
    }

    if (id) {
      await Api.updateUser(id, {
        full_name: document.getElementById('full_name').value.trim(),
        email: document.getElementById('email').value.trim() || null,
        phone: document.getElementById('phone').value.trim() || null,
        branch_id: branchId,
      });
      showToast('Staff details updated.');
    } else {
      await Api.createUser({
        full_name: document.getElementById('full_name').value.trim(),
        role: document.getElementById('role').value,
        email: document.getElementById('email').value.trim() || null,
        phone: document.getElementById('phone').value.trim() || null,
        password: document.getElementById('password').value,
        branch_id: branchId,
      });
      showToast('Staff account created.');
    }
    closeUserModal();
    loadStaff();
  } catch (err) {
    modalError.textContent = err.message;
    modalError.classList.add('visible');
  } finally {
    saveBtn.disabled = false;
  }
});

// ------------------------------------------------------------- Role change

async function changeRole(user, newRole) {
  if (newRole === user.role) return;
  const label = roleLabels[newRole] || newRole;
  if (!(await showConfirmModal(`Change ${user.full_name}'s role to ${label}?`, { title: 'Change role', confirmLabel: 'Change' }))) {
    loadStaff(); // revert the select back to the actual role
    return;
  }
  try {
    await Api.updateUserRole(user.id, newRole);
    showToast(`${user.full_name} is now ${label}.`);
    loadStaff();
  } catch (err) {
    showToast(err.message, true);
    loadStaff();
  }
}

// --------------------------------------------------------- Activate/deactivate

async function toggleActive(user) {
  const verb = user.is_active ? 'deactivate' : 'activate';
  if (!(await showConfirmModal(`${verb === 'deactivate' ? 'Deactivate' : 'Activate'} ${user.full_name}?`, { title: verb === 'deactivate' ? 'Deactivate staff member' : 'Activate staff member', confirmLabel: verb === 'deactivate' ? 'Deactivate' : 'Activate' }))) return;
  try {
    if (user.is_active) await Api.deactivateUser(user.id);
    else await Api.activateUser(user.id);
    showToast(`${user.full_name} ${verb}d.`);
    loadStaff();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteUser(user) {
  if (!(await showConfirmModal(`Permanently delete ${user.full_name}? This cannot be undone.`, { title: 'Delete staff account', confirmLabel: 'Delete' }))) return;
  try {
    await Api.deleteUser(user.id);
    showToast(`${user.full_name} deleted.`);
    loadStaff();
  } catch (err) {
    // A 409 here means they have real business history on record (a
    // sale, a shift, etc.) -- Api surfaces the server's explanation of
    // exactly what's blocking it, so just show that rather than a
    // generic error.
    showToast(err.message, true);
  }
}

// ------------------------------------------------------------- Reset password

const resetModalBackdrop = document.getElementById('reset-modal-backdrop');
const resetForm = document.getElementById('reset-form');
const resetModalError = document.getElementById('reset-modal-error');
let resetTargetId = null;

function openResetModal(u) {
  resetTargetId = u.id;
  resetForm.reset();
  document.getElementById('reset-modal-sub').textContent = `Set a new password for ${u.full_name}.`;
  resetModalError.classList.remove('visible');
  resetModalBackdrop.classList.add('visible');
}

document.getElementById('reset-modal-cancel').addEventListener('click', () => {
  resetModalBackdrop.classList.remove('visible');
});

resetForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await Api.resetPassword(resetTargetId, document.getElementById('new_password').value);
    resetModalBackdrop.classList.remove('visible');
    showToast('Password reset.');
  } catch (err) {
    resetModalError.textContent = err.message;
    resetModalError.classList.add('visible');
  }
});

// ------------------------------------------------------------- Restrict access
// Super-admin-only control to revoke specific permissions from an
// individual branch manager, on top of the role's normal grants. The
// checklist starts checked = "still allowed"; unchecking an item adds it
// to denied_permissions when saved. Actual enforcement is server-side
// (PATCH /users/{id}/permissions) -- this is just the UI for it.

const restrictModalBackdrop = document.getElementById('restrict-modal-backdrop');
const restrictForm = document.getElementById('restrict-form');
const restrictModalError = document.getElementById('restrict-modal-error');
const restrictChecklist = document.getElementById('restrict-checklist');
let restrictTargetId = null;
let restrictTargetGranted = [];

function openRestrictModal(u) {
  restrictTargetId = u.id;
  restrictTargetGranted = u.granted_permissions || [];
  const denied = new Set(u.denied_permissions || []);
  document.getElementById('restrict-modal-sub').textContent =
    `Turn off specific abilities for ${u.full_name}. Everything left checked stays available to them.`;
  restrictChecklist.innerHTML = RESTRICTABLE_BRANCH_MANAGER_PERMISSIONS.map((perm) => `
    <label class="checkbox" style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <input type="checkbox" data-perm="${perm}" ${denied.has(perm) ? '' : 'checked'} />
      ${PERMISSION_LABELS[perm] || perm}
    </label>`).join('');
  restrictModalError.classList.remove('visible');
  restrictModalBackdrop.classList.add('visible');
}

document.getElementById('restrict-modal-cancel').addEventListener('click', () => {
  restrictModalBackdrop.classList.remove('visible');
});

restrictForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const deniedPermissions = [...restrictChecklist.querySelectorAll('[data-perm]')]
    .filter((el) => !el.checked)
    .map((el) => el.dataset.perm);
  try {
    await Api.updateUserPermissions(restrictTargetId, deniedPermissions, restrictTargetGranted);
    restrictModalBackdrop.classList.remove('visible');
    showToast('Restrictions saved.');
    loadStaff();
  } catch (err) {
    restrictModalError.textContent = err.message;
    restrictModalError.classList.add('visible');
  }
});

// ------------------------------------------------------------- Grant extra access
// The opposite of the restrict modal above: hand ONE trusted staff
// member an extra ability their role wouldn't normally have, without
// upgrading their role (spec: "I have a particular staff I trust so
// much... it shouldn't be that they upgrade his role, I just come and
// edit it"). Checklist starts unchecked = "not granted"; checking an
// item adds it to granted_permissions when saved.

const grantModalBackdrop = document.getElementById('grant-modal-backdrop');
const grantForm = document.getElementById('grant-form');
const grantModalError = document.getElementById('grant-modal-error');
const grantChecklist = document.getElementById('grant-checklist');
let grantTargetId = null;
let grantTargetDenied = [];

function openGrantModal(u) {
  grantTargetId = u.id;
  grantTargetDenied = u.denied_permissions || [];
  const granted = new Set(u.granted_permissions || []);
  document.getElementById('grant-modal-sub').textContent =
    `Give ${u.full_name} one or two extra abilities beyond the normal Staff role -- without upgrading their role.`;
  grantChecklist.innerHTML = GRANTABLE_STAFF_PERMISSIONS.map((perm) => `
    <label class="checkbox" style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <input type="checkbox" data-perm="${perm}" ${granted.has(perm) ? 'checked' : ''} />
      ${PERMISSION_LABELS[perm] || perm}
    </label>`).join('');
  grantModalError.classList.remove('visible');
  grantModalBackdrop.classList.add('visible');
}

document.getElementById('grant-modal-cancel')?.addEventListener('click', () => {
  grantModalBackdrop.classList.remove('visible');
});

grantForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const grantedPermissions = [...grantChecklist.querySelectorAll('[data-perm]')]
    .filter((el) => el.checked)
    .map((el) => el.dataset.perm);
  try {
    await Api.updateUserPermissions(grantTargetId, grantTargetDenied, grantedPermissions);
    grantModalBackdrop.classList.remove('visible');
    showToast('Extra access saved.');
    loadStaff();
  } catch (err) {
    grantModalError.textContent = err.message;
    grantModalError.classList.add('visible');
  }
});

// ------------------------------------------------------------------ Init

loadBranches();
loadRoleOptions().then(loadStaff);
BranchContext.renderBadge('branch-badge');
// Reload the roster whenever the active branch changes on the Dashboard,
// same pattern used by sales.js / low-stock.js.
window.addEventListener('branch-context-changed', loadStaff);

// -------------------------------------------------------- Applications tab
// "Staff Applications / Active Staff / Suspended / Rejected" all live
// as one tabbed view here rather than a separate page, per the spec's
// instruction to extend the existing Staff section instead of bolting
// on a new dashboard area.

const statusLabels = {
  under_review: 'Under review',
  accepted: 'Accepted',
  rejected: 'Rejected',
  suspended: 'Suspended',
};

document.getElementById('staff-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-tab]');
  if (!btn) return;
  document.querySelectorAll('#staff-tabs .segmented-btn').forEach((b) => b.classList.remove('active'));
  btn.classList.add('active');
  const tab = btn.dataset.tab;
  document.getElementById('active-panel').style.display = tab === 'active' ? '' : 'none';
  document.getElementById('applications-panel').style.display = tab === 'applications' ? '' : 'none';
  document.getElementById('resets-panel').style.display = tab === 'resets' ? '' : 'none';
  if (tab === 'applications') loadApplications();
  if (tab === 'resets') loadResetRequests();
});

async function loadApplications() {
  const statusFilter = document.getElementById('application-status-filter').value;
  const tbody = document.getElementById('applications-tbody');
  document.getElementById('applications-empty-state').style.display = 'none';
  document.getElementById('applications-table').style.display = '';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="5">Loading applications…</td></tr>';
  try {
    const applications = await Api.listApplications(statusFilter || undefined);
    renderApplications(applications);
    refreshApplicationsBadge();
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

async function refreshApplicationsBadge() {
  try {
    const pending = await Api.listApplications('under_review');
    const badge = document.getElementById('applications-badge');
    if (pending.length) {
      badge.textContent = pending.length;
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  } catch (err) {
    // Non-fatal — badge just doesn't show if this fails.
  }
}

function renderApplications(applications) {
  const tbody = document.getElementById('applications-tbody');
  const emptyState = document.getElementById('applications-empty-state');
  const table = document.getElementById('applications-table');

  if (!applications.length) {
    tbody.innerHTML = '';
    table.style.display = 'none';
    emptyState.style.display = 'block';
    return;
  }
  table.style.display = '';
  emptyState.style.display = 'none';

  tbody.innerHTML = applications.map((a) => `
    <tr>
      <td class="name-cell"><div class="full-name">${escapeHtml(a.full_legal_name)}</div><div class="contact">${escapeHtml(a.preferred_name)}</div></td>
      <td>${escapeHtml(a.email)}</td>
      <td>${new Date(a.submitted_at).toLocaleDateString('en-NG', { timeZone: 'Africa/Lagos' })}</td>
      <td><span class="status-pill ${a.status}">${statusLabels[a.status] || a.status}</span></td>
      <td><div class="row-actions"><button class="btn btn-ghost btn-sm" data-review="${a.id}">Review</button></div></td>
    </tr>`).join('');

  applications.forEach((a) => {
    document.querySelector(`[data-review="${a.id}"]`)?.addEventListener('click', () => openReviewModal(a.id));
  });
}

document.getElementById('application-status-filter').addEventListener('change', loadApplications);

// ------------------------------------------------------------- Review modal

const reviewModalBackdrop = document.getElementById('review-modal-backdrop');
const reviewModalError = document.getElementById('review-modal-error');
let reviewTarget = null;

function detailRow(label, value) {
  if (!value) return '';
  return `<div style="display:flex;gap:8px;"><div style="width:170px;color:var(--ink-muted);flex-shrink:0;">${label}</div><div>${escapeHtml(String(value))}</div></div>`;
}

const idTypeLabels = { national_id: 'National ID', passport: 'International Passport', drivers_license: "Driver's License" };
const roleLabelsForApplications = { staff: 'Cashier', branch_manager: 'Branch Manager' };

// ---- Credential photos (profile photo + ID document) in the review modal ----
function openPhotoLightbox(src, caption) {
  let box = document.getElementById('photo-lightbox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'photo-lightbox';
    box.style.cssText = 'position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.82);display:none;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:24px;cursor:zoom-out;';
    box.innerHTML = '<img id="photo-lightbox-img" alt="" style="max-width:92vw;max-height:80vh;border-radius:10px;object-fit:contain;background:#fff;" /><div id="photo-lightbox-caption" style="color:#fff;font-size:13px;"></div>';
    box.addEventListener('click', () => { box.style.display = 'none'; });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') box.style.display = 'none'; });
    document.body.appendChild(box);
  }
  box.querySelector('#photo-lightbox-img').src = src;
  box.querySelector('#photo-lightbox-caption').textContent = caption || '';
  box.style.display = 'flex';
}

function credentialPhotosHtml() {
  const tile = (id, label) =>
    `<div style="text-align:center;">` +
    `<div id="${id}" style="width:140px;height:140px;border:1px solid var(--border);border-radius:10px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--ink-faint);font-size:12px;padding:6px;">...</div>` +
    `<div style="margin-top:4px;font-size:12px;color:var(--ink-muted);">${label}</div></div>`;
  return `<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;">${tile('cred-photo-tile', 'Profile photo')}${tile('cred-id-tile', 'ID document')}</div>`;
}

let credentialBlobUrls = [];

async function loadCredentialPhotos(app) {
  credentialBlobUrls.forEach((u) => URL.revokeObjectURL(u));
  credentialBlobUrls = [];
  const photoTile = document.getElementById('cred-photo-tile');
  const idTile = document.getElementById('cred-id-tile');
  if (!photoTile || !idTile) return;

  const showImage = (tile, src, caption) => {
    tile.innerHTML = '';
    const img = document.createElement('img');
    img.alt = caption;
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;cursor:zoom-in;';
    img.addEventListener('click', () => openPhotoLightbox(src, caption));
    img.addEventListener('error', () => { tile.textContent = 'Could not load'; });
    img.src = src;
    tile.appendChild(img);
  };

  // Profile photo: a normal public URL.
  if (app.profile_photo_url) {
    showImage(photoTile, app.profile_photo_url, `${app.full_legal_name} - profile photo`);
  } else {
    photoTile.textContent = 'No photo uploaded';
  }

  // ID document: private, so it is fetched with the auth header as a blob.
  if (!app.has_id_document) {
    idTile.textContent = 'No ID uploaded';
    return;
  }
  try {
    const { url, type } = await Api.fetchIdDocumentBlob(app.id);
    if (reviewTarget && reviewTarget.id !== app.id) { URL.revokeObjectURL(url); return; }
    credentialBlobUrls.push(url);
    if (type === 'application/pdf') {
      idTile.innerHTML = '';
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Open ID (PDF)';
      idTile.appendChild(link);
    } else {
      showImage(idTile, url, `${app.full_legal_name} - ID document`);
    }
  } catch (err) {
    idTile.textContent = err.message || 'Could not load ID';
  }
}

async function openReviewModal(id) {
  reviewModalError.classList.remove('visible');
  document.getElementById('review-details').innerHTML = 'Loading…';
  reviewModalBackdrop.classList.add('visible');

  try {
    reviewTarget = await Api.getApplication(id);
    document.getElementById('review-modal-sub').textContent =
      `${reviewTarget.full_legal_name} · ${statusLabels[reviewTarget.status] || reviewTarget.status}`;

    document.getElementById('review-details').innerHTML = [
      credentialPhotosHtml(),
      detailRow('Applying for', roleLabelsForApplications[reviewTarget.role_applied_for] || reviewTarget.role_applied_for),
      detailRow('Branch applied to', reviewTarget.branch_id ? (branchNameById[reviewTarget.branch_id] || `${reviewTarget.branch_id.slice(0, 8)}…`) : '—'),
      detailRow('Preferred name', reviewTarget.preferred_name),
      detailRow('Date of birth', reviewTarget.date_of_birth),
      detailRow('Gender', reviewTarget.gender === 'male' ? 'Male' : reviewTarget.gender === 'female' ? 'Female' : reviewTarget.gender),
      detailRow('Phone', reviewTarget.phone_number),
      detailRow('Email', reviewTarget.email),
      detailRow('Current address', reviewTarget.residential_address),
      detailRow('State of origin', reviewTarget.state_of_origin),
      detailRow('LGA of origin', reviewTarget.lga_of_origin),
      detailRow('Hometown', reviewTarget.hometown),
      detailRow('Home address of origin', reviewTarget.home_address_of_origin),
      detailRow('Emergency contact', reviewTarget.emergency_contact_name),
      detailRow('Emergency phone', reviewTarget.emergency_contact_phone),
      detailRow('Relationship', reviewTarget.emergency_contact_relationship),
      detailRow('ID type', idTypeLabels[reviewTarget.id_type] || reviewTarget.id_type),
      detailRow('ID number', reviewTarget.id_number),
      reviewTarget.has_id_document
        ? `<div style="margin-top:8px;"><button type="button" class="btn btn-ghost btn-sm" id="view-id-doc-btn">View ID document</button></div>`
        : '',
      reviewTarget.rejection_reason ? detailRow('Rejection reason', reviewTarget.rejection_reason) : '',
    ].join('');

    loadCredentialPhotos(reviewTarget);

    document.getElementById('view-id-doc-btn')?.addEventListener('click', () => {
      Api.openIdDocument(reviewTarget.id).catch((err) => showToast(err.message, true));
    });

    if (branchCache.length) {
      document.getElementById('review-branch').innerHTML = branchCache.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
    }
    if (isBranchManager) {
      document.getElementById('review-role').value = 'staff';
      document.getElementById('review-role-field').style.display = 'none';
      document.getElementById('review-branch').value = currentUser.branch_id || '';
      document.getElementById('review-branch').disabled = true;
    } else {
      document.getElementById('review-role-field').style.display = '';
      document.getElementById('review-branch').disabled = false;
      // Default the branch picker to whichever branch they applied
      // to (see StaffRegisterRequest.branch_id) -- the admin can
      // still change it before accepting.
      if (reviewTarget.branch_id) {
        document.getElementById('review-branch').value = reviewTarget.branch_id;
      }
      // Default the role picker to what they applied for -- the admin
      // can still change it before accepting.
      if (reviewTarget.role_applied_for) {
        document.getElementById('review-role').value = reviewTarget.role_applied_for;
      }
    }

    const canReview = reviewTarget.status === 'under_review';
    document.getElementById('review-accept-fields').style.display = canReview ? '' : 'none';
    document.getElementById('review-accept-btn').style.display = canReview ? '' : 'none';
    document.getElementById('review-reject-btn').style.display = canReview ? '' : 'none';

    document.getElementById('review-reject-btn').textContent = 'Reject';
    document.getElementById('review-accept-btn').textContent = 'Accept';
    if (reviewTarget.status === 'accepted') {
      document.getElementById('review-reject-btn').style.display = '';
      document.getElementById('review-reject-btn').textContent = 'Suspend';
    } else if (reviewTarget.status === 'suspended') {
      document.getElementById('review-accept-btn').style.display = '';
      document.getElementById('review-accept-btn').textContent = 'Reactivate';
    }
  } catch (err) {
    document.getElementById('review-details').innerHTML = '';
    reviewModalError.textContent = err.message;
    reviewModalError.classList.add('visible');
  }
}

document.getElementById('review-modal-cancel').addEventListener('click', () => {
  reviewModalBackdrop.classList.remove('visible');
});

document.getElementById('review-delete-btn').addEventListener('click', async () => {
  if (!reviewTarget) return;
  if (!(await showConfirmModal(`Permanently delete ${reviewTarget.full_legal_name}'s account? This cannot be undone.`, { title: 'Delete application', confirmLabel: 'Delete' }))) return;
  try {
    await Api.deleteApplication(reviewTarget.id);
    reviewModalBackdrop.classList.remove('visible');
    showToast(`${reviewTarget.full_legal_name} deleted.`);
    loadApplications();
  } catch (err) {
    reviewModalError.textContent = err.message;
    reviewModalError.classList.add('visible');
  }
});

document.getElementById('review-accept-btn').addEventListener('click', async () => {
  if (!reviewTarget) return;

  if (reviewTarget.status === 'suspended') {
    try {
      await Api.reactivateApplication(reviewTarget.id);
      reviewModalBackdrop.classList.remove('visible');
      showToast(`${reviewTarget.full_legal_name} reactivated.`);
      loadApplications();
    } catch (err) {
      reviewModalError.textContent = err.message;
      reviewModalError.classList.add('visible');
    }
    return;
  }

  const branchId = document.getElementById('review-branch').value;
  const role = document.getElementById('review-role').value;
  if (!branchId) {
    reviewModalError.textContent = 'Choose a branch before accepting.';
    reviewModalError.classList.add('visible');
    return;
  }
  try {
    await Api.acceptApplication(reviewTarget.id, branchId, role);
    reviewModalBackdrop.classList.remove('visible');
    showToast(`${reviewTarget.full_legal_name} is now active staff.`);
    loadApplications();
  } catch (err) {
    reviewModalError.textContent = err.message;
    reviewModalError.classList.add('visible');
  }
});

document.getElementById('review-reject-btn').addEventListener('click', async () => {
  if (!reviewTarget) return;

  if (reviewTarget.status === 'accepted') {
    if (!(await showConfirmModal(`Suspend ${reviewTarget.full_legal_name}? They'll lose access until reactivated.`, { title: 'Suspend staff member', confirmLabel: 'Suspend' }))) return;
    try {
      await Api.suspendApplication(reviewTarget.id);
      reviewModalBackdrop.classList.remove('visible');
      showToast(`${reviewTarget.full_legal_name} suspended.`);
      loadApplications();
    } catch (err) {
      reviewModalError.textContent = err.message;
      reviewModalError.classList.add('visible');
    }
    return;
  }

  const reason = (await showPromptModal('', { title: 'Reason for rejecting (optional)', confirmLabel: 'Reject' })) || '';
  try {
    await Api.rejectApplication(reviewTarget.id, reason.trim() || null);
    reviewModalBackdrop.classList.remove('visible');
    showToast(`${reviewTarget.full_legal_name}'s application was rejected.`);
    loadApplications();
  } catch (err) {
    reviewModalError.textContent = err.message;
    reviewModalError.classList.add('visible');
  }
});

refreshApplicationsBadge();


// ------------------------------------------------------ Password reset requests
// Users who tap "Forgot Password" land here; approving lets that user set a
// new password from the Forgot Password page (see js/api.js + forgot-password.html).

const resetStatusLabels = {
  pending: 'Waiting',
  approved: 'Approved',
  used: 'Changed',
  rejected: 'Rejected',
  expired: 'Expired',
  cancelled: 'Replaced',
};
const resetStatusClass = {
  pending: 'under_review',
  approved: 'accepted',
  used: 'active',
  rejected: 'rejected',
  expired: 'inactive',
  cancelled: 'inactive',
};

async function loadResetRequests(mode) {
  const filter = document.getElementById('reset-status-filter').value;
  const tbody = document.getElementById('resets-tbody');
  document.getElementById('resets-empty-state').style.display = 'none';
  document.getElementById('resets-table').style.display = '';
  if (mode !== 'silent') tbody.innerHTML = '<tr class="loading-row"><td colspan="6">Loading requests…</td></tr>';
  try {
    const rows = await Api.listPasswordResetRequests(filter || undefined);
    renderResetRequests(rows);
    tickResetCountdowns();
    refreshResetsBadge();
    loadResetHistory();
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

async function refreshResetsBadge() {
  try {
    const pending = await Api.listPasswordResetRequests('pending');
    const badge = document.getElementById('resets-badge');
    if (pending.length) {
      badge.textContent = pending.length;
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  } catch (err) {
    // Non-fatal -- badge just doesn't show.
  }
}

function renderResetRequests(rows) {
  const tbody = document.getElementById('resets-tbody');
  const table = document.getElementById('resets-table');
  const empty = document.getElementById('resets-empty-state');
  if (!rows.length) {
    tbody.innerHTML = '';
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }
  table.style.display = '';
  empty.style.display = 'none';
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td class="name-cell"><div class="full-name">${escapeHtml(r.full_name)}</div></td>
      <td>${escapeHtml(r.email || '')}</td>
      <td>${r.branch_id ? escapeHtml(branchNameById[r.branch_id] || `${r.branch_id.slice(0, 8)}…`) : '<span style="color:var(--ink-faint)">—</span>'}</td>
      <td>${new Date(r.requested_at).toLocaleString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium', timeStyle: 'short' })}</td>
      <td><span class="status-pill ${resetStatusClass[r.status] || 'inactive'}">${resetStatusLabels[r.status] || r.status}</span>${r.status === 'approved' && r.approval_expires_at ? ` <span class="reset-countdown" data-expires="${r.approval_expires_at}" style="font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--ink-muted);"></span>` : ''}<div style="font-size:12px;color:var(--ink-muted);margin-top:4px;">${resetNote(r)}</div></td>
      <td>${r.status === 'pending' ? `<div class="row-actions">
        <button class="btn btn-primary btn-sm" style="width:auto;" data-reset-approve="${r.id}" data-name="${escapeHtml(r.full_name)}">Approve</button>
        <button class="btn btn-ghost btn-sm" data-reset-reject="${r.id}" data-name="${escapeHtml(r.full_name)}">Reject</button>
      </div>` : ''}</td>
    </tr>`).join('');
}

document.getElementById('resets-tbody').addEventListener('click', async (e) => {
  const approveBtn = e.target.closest('[data-reset-approve]');
  const rejectBtn = e.target.closest('[data-reset-reject]');
  const btn = approveBtn || rejectBtn;
  if (!btn) return;
  const id = btn.dataset.resetApprove || btn.dataset.resetReject;
  const name = btn.dataset.name;
  const approving = !!approveBtn;
  const ok = await showConfirmModal(
    approving
      ? `Approve ${name}'s password reset? They will have 30 minutes to set a new password.`
      : `Reject ${name}'s password reset request?`,
    { title: approving ? 'Approve reset' : 'Reject reset', confirmLabel: approving ? 'Approve' : 'Reject' }
  );
  if (!ok) return;
  try {
    if (approving) await Api.approvePasswordReset(id);
    else await Api.rejectPasswordReset(id);
    showToast(approving ? 'Approved. They can now set a new password.' : 'Request rejected.');
    loadResetRequests();
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('reset-status-filter').addEventListener('change', loadResetRequests);
refreshResetsBadge();


// Live countdown beside each approved request (30-minute window).
function tickResetCountdowns() {
  let anyExpired = false;
  document.querySelectorAll('.reset-countdown').forEach((el) => {
    if (el.dataset.done) return;
    const ms = new Date(el.dataset.expires).getTime() - Date.now();
    if (ms <= 0) {
      el.textContent = 'time is up';
      el.dataset.done = '1';
      anyExpired = true;
      return;
    }
    const total = Math.floor(ms / 1000);
    el.textContent = `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')} left`;
  });
  if (anyExpired) loadResetRequests(); // server now reports these as "Expired"
}
setInterval(tickResetCountdowns, 1000);


// ---------------------------------------------- Reset history + "changed?" messages

function fmtResetTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('en-NG', { timeZone: 'Africa/Lagos', dateStyle: 'medium', timeStyle: 'short' });
}

// Plain-language line under each status: has the staff member changed their password yet?
function resetNote(r) {
  switch (r.status) {
    case 'pending': return 'Waiting for you to approve';
    case 'approved': return 'Approved. Password not changed yet';
    case 'used': return `✓ Password changed ${fmtResetTime(r.used_at)}`;
    case 'expired': return 'Time ran out. Password not changed';
    case 'rejected': return 'Rejected';
    case 'cancelled': return 'Replaced by a newer request';
    default: return '';
  }
}

let lastResetStatus = null; // id -> status from the previous fetch; null until first load

async function loadResetHistory() {
  try {
    const rows = await Api.listPasswordResetRequests('all');

    // Tell the admin the moment a staff member finishes changing their password.
    if (lastResetStatus) {
      rows.forEach((r) => {
        if (r.status === 'used' && lastResetStatus[r.id] && lastResetStatus[r.id] !== 'used') {
          showToast(`${r.full_name} has changed their password.`);
        }
      });
    }
    lastResetStatus = Object.fromEntries(rows.map((r) => [r.id, r.status]));

    const tbody = document.getElementById('reset-history-tbody');
    const table = document.getElementById('reset-history-table');
    const empty = document.getElementById('reset-history-empty');
    if (!rows.length) {
      tbody.innerHTML = '';
      table.style.display = 'none';
      empty.style.display = 'block';
      return;
    }
    table.style.display = '';
    empty.style.display = 'none';

    const decision = (r) => {
      const who = escapeHtml(r.decided_by_name || 'an admin');
      if (r.status === 'pending') return 'Waiting';
      if (r.status === 'cancelled') return '<span style="color:var(--ink-faint)">—</span>';
      if (r.status === 'rejected') return `Rejected by ${who}<div style="font-size:12px;color:var(--ink-muted);">${fmtResetTime(r.decided_at)}</div>`;
      return `Approved by ${who}<div style="font-size:12px;color:var(--ink-muted);">${fmtResetTime(r.decided_at)}</div>`;
    };
    const changed = (r) => {
      if (r.status === 'used') return `<strong style="color:var(--success);">✓ Changed</strong><div style="font-size:12px;color:var(--ink-muted);">${fmtResetTime(r.used_at)}</div>`;
      if (r.status === 'approved') return 'Not yet';
      if (r.status === 'expired') return 'No (time ran out)';
      return '<span style="color:var(--ink-faint)">—</span>';
    };

    tbody.innerHTML = rows.map((r) => `
      <tr>
        <td class="name-cell"><div class="full-name">${escapeHtml(r.full_name)}</div><div class="contact">${escapeHtml(r.email || '')}</div></td>
        <td>${r.branch_id ? escapeHtml(branchNameById[r.branch_id] || `${r.branch_id.slice(0, 8)}…`) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td>${fmtResetTime(r.requested_at)}</td>
        <td>${decision(r)}</td>
        <td>${changed(r)}</td>
        <td><span class="status-pill ${resetStatusClass[r.status] || 'inactive'}">${resetStatusLabels[r.status] || r.status}</span></td>
      </tr>`).join('');
  } catch (err) {
    // Non-fatal: history just doesn't refresh this time.
  }
}

// Keep things fresh without reloading the page. When the Password Resets tab is open the
// whole list refreshes quietly; otherwise only the history is checked (for the "changed" message).
setInterval(() => {
  if (document.hidden) return;
  const panelOpen = document.getElementById('resets-panel').style.display !== 'none';
  if (panelOpen) loadResetRequests('silent');
  else loadResetHistory();
}, 20000);

loadResetHistory();
