Auth.requireAuth();
// Section 15: Branches now shares a page with Staff/Roles/Shifts
// under one "Staff" nav item -- see roles.js for why a
// page-bouncing guardPage() here would be wrong now that this
// isn't its own page.
if (!Perms.can('configure_system')) {
  document.getElementById('tab-btn-branches')?.remove();
}
Perms.applyNav();
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });


async function loadBranches() {
  const tbody = document.getElementById('branches-tbody');
  const table = document.getElementById('branches-table');
  const emptyState = document.getElementById('branches-empty-state');
  emptyState.style.display = 'none';
  table.style.display = '';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="2">Loading branches…</td></tr>';
  try {
    const branches = await Api.listBranches();
    if (!branches.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
      return;
    }
    tbody.innerHTML = branches.map((b) => `
      <tr>
        <td><strong>${escapeHtml(b.name)}</strong></td>
        <td>${b.address ? escapeHtml(b.address) : '<span style="color:var(--ink-faint)">—</span>'}</td>
        <td id="shift-time-${b.id}">—</td>
        <td><button type="button" class="btn btn-ghost btn-sm" data-shift-settings="${b.id}" data-branch-name="${escapeHtml(b.name)}">Set closing time</button></td>
      </tr>`).join('');
    branches.forEach((b) => loadShiftTimeCell(b.id));
    tbody.querySelectorAll('[data-shift-settings]').forEach((btn) => {
      btn.addEventListener('click', () => openShiftSettings(btn.dataset.shiftSettings, btn.dataset.branchName));
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

const backdrop = document.getElementById('branch-modal-backdrop');
const branchModalError = document.getElementById('branches-modal-error');

// State -> dependent LGA dropdown, same pattern as register.html's
// "state/LGA of origin" fields.
const branchStateSelect = document.getElementById('branch-state');
const branchLgaSelect = document.getElementById('branch-lga');
Object.keys(NIGERIA_STATES_LGAS).sort().forEach((state) => {
  const opt = document.createElement('option');
  opt.value = state;
  opt.textContent = state;
  branchStateSelect.appendChild(opt);
});
branchStateSelect.addEventListener('change', () => {
  const lgas = NIGERIA_STATES_LGAS[branchStateSelect.value] || [];
  if (!lgas.length) {
    branchLgaSelect.innerHTML = '<option value="">Select a state first…</option>';
    branchLgaSelect.disabled = true;
    return;
  }
  branchLgaSelect.disabled = false;
  branchLgaSelect.innerHTML = '<option value="">Select an LGA…</option>' +
    lgas.map((l) => `<option value="${l}">${l}</option>`).join('');
});

document.getElementById('new-branch-btn').addEventListener('click', () => {
  document.getElementById('branch-form').reset();
  branchLgaSelect.innerHTML = '<option value="">Select a state first…</option>';
  branchLgaSelect.disabled = true;
  branchModalError.classList.remove('visible');
  backdrop.classList.add('visible');
});
document.getElementById('branch-modal-cancel').addEventListener('click', () => backdrop.classList.remove('visible'));

document.getElementById('branch-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  branchModalError.classList.remove('visible');
  // The backend still stores a single free-text address -- combine the
  // street address with the picked LGA/state so it's captured without
  // needing a schema change, while the picker keeps entry consistent
  // (no more guessing how a state/LGA name is spelled or abbreviated).
  const street = document.getElementById('address').value.trim();
  const lga = branchLgaSelect.value;
  const state = branchStateSelect.value;
  const fullAddress = [street, lga, state].filter(Boolean).join(', ');
  try {
    await Api.createBranch({
      address: fullAddress,
      name: document.getElementById('name').value.trim(),
    });
    backdrop.classList.remove('visible');
    showToast('Branch added.');
    loadBranches();
  } catch (err) {
    branchModalError.textContent = err.message;
    branchModalError.classList.add('visible');
  }
});

// -------------------------------------------------- Shift closing settings

async function loadShiftTimeCell(branchId) {
  const cell = document.getElementById(`shift-time-${branchId}`);
  if (!cell) return;
  try {
    const s = await Api.getShiftSettings(branchId);
    cell.textContent = s.expected_close_hour != null
      ? `${String(s.expected_close_hour).padStart(2, '0')}:${String(s.expected_close_minute || 0).padStart(2, '0')} (+${s.grace_minutes}m grace)`
      : 'Not set';
  } catch (err) {
    cell.textContent = '—';
  }
}

const shiftSettingsBackdrop = document.getElementById('shift-settings-modal-backdrop');
let currentShiftSettingsBranchId = null;

async function openShiftSettings(branchId, branchName) {
  currentShiftSettingsBranchId = branchId;
  document.getElementById('shift-settings-branch-name').textContent = `Shift closing time — ${branchName}`;
  document.getElementById('shift-settings-error').classList.remove('visible');
  document.getElementById('shift-settings-time').value = '';
  document.getElementById('shift-settings-grace').value = 10;
  try {
    const s = await Api.getShiftSettings(branchId);
    if (s.expected_close_hour != null) {
      document.getElementById('shift-settings-time').value =
        `${String(s.expected_close_hour).padStart(2, '0')}:${String(s.expected_close_minute || 0).padStart(2, '0')}`;
    }
    document.getElementById('shift-settings-grace').value = s.grace_minutes;
  } catch (err) { /* fall back to blank defaults */ }
  shiftSettingsBackdrop.classList.add('visible');
}
document.getElementById('shift-settings-cancel').addEventListener('click', () => {
  shiftSettingsBackdrop.classList.remove('visible');
});
document.getElementById('shift-settings-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errorEl = document.getElementById('shift-settings-error');
  const timeValue = document.getElementById('shift-settings-time').value; // "HH:MM" or ""
  const [hour, minute] = timeValue ? timeValue.split(':').map(Number) : [null, null];
  try {
    await Api.updateShiftSettings(currentShiftSettingsBranchId, {
      expected_close_hour: hour,
      expected_close_minute: minute,
      grace_minutes: Number(document.getElementById('shift-settings-grace').value || 0),
    });
    shiftSettingsBackdrop.classList.remove('visible');
    showToast('Shift closing time saved.');
    loadShiftTimeCell(currentShiftSettingsBranchId);
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add('visible');
  }
});

loadBranches();
