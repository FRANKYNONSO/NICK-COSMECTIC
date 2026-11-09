"use strict";
// PATCHP3P-PICKER: pure helpers for the per-staff Restrict and Grant dialogs
// (web_admin/js/staff.js). Nothing here touches the page, so Node can test it.
const StaffPicker = (function () {
  // Never offered as a NEW grant (decided by the owner, Nov 2026).
  const BLOCKED_GRANT_KEYS = [
    'view_financials', 'view_revenue_totals', 'view_all_reports',
    'view_audit_logs', 'download_statements', 'create_staff',
    'review_staff_applications', 'review_password_resets',
    'confirm_theft_losses', 'manage_backups',
  ];
  // Never offered in the Restrict list: denying these locks a person out.
  const NEVER_RESTRICT_KEYS = ['process_sale', 'view_own_sales', 'change_own_password'];
  // Owner-only keys: never offered in either list.
  const OWNER_ONLY_KEYS = ['remove_super_admin', 'transfer_ownership'];

  const GROUP_STYLE = 'font-size:14px;font-weight:700;margin-top:12px;margin-bottom:6px;';
  const TAB_STYLE = 'font-size:12px;font-weight:600;color:var(--ink-faint);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;';
  const BOX_STYLE = 'display:flex;align-items:center;gap:8px;margin-bottom:8px;';

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function toSet(list) {
    return new Set(Array.isArray(list) ? list : []);
  }

  // True when the registry holds at least one usable permission key.
  function isUsableRegistry(registry) {
    if (!Array.isArray(registry)) return false;
    return registry.some(function (g) {
      return g && Array.isArray(g.tabs) && g.tabs.some(function (t) {
        return t && Array.isArray(t.permissions) && t.permissions.some(function (p) { return p && p.key; });
      });
    });
  }

  // The permission keys a role holds, or null when the role is not in the list.
  function roleKeysFor(roles, roleName) {
    if (!Array.isArray(roles)) return null;
    const role = roles.find(function (r) { return r && r.name === roleName; });
    if (!role || !Array.isArray(role.permissions)) return null;
    return role.permissions.slice();
  }

  function isShownKey(mode, key, roleSet) {
    if (OWNER_ONLY_KEYS.indexOf(key) !== -1) return false;
    if (mode === 'restrict') {
      return roleSet.has(key) && NEVER_RESTRICT_KEYS.indexOf(key) === -1;
    }
    return !roleSet.has(key) && BLOCKED_GRANT_KEYS.indexOf(key) === -1;
  }

  function boxHtml(key, label, isOn) {
    return '<label class="checkbox" style="' + BOX_STYLE + '">' +
      '<input type="checkbox" data-perm="' + esc(key) + '" ' + (isOn ? 'checked' : '') + ' /> ' +
      esc(label) + '</label>';
  }

  // mode "restrict": a box is ticked when the key is NOT denied.
  // mode "grant": a box is ticked when the key IS granted.
  // activeKeys: the user's denied list (restrict) or granted list (grant).
  // Returns { html, shown } where shown lists every key that got a box.
  function renderBoxes(mode, registry, roleKeys, activeKeys) {
    const roleSet = toSet(roleKeys);
    const active = toSet(activeKeys);
    const known = new Set();
    const labels = {};
    const shown = [];
    let html = '';
    registry.forEach(function (g) {
      let groupHtml = '';
      ((g && g.tabs) || []).forEach(function (t) {
        let tabHtml = '';
        ((t && t.permissions) || []).forEach(function (p) {
          if (!p || !p.key || known.has(p.key)) return;
          known.add(p.key);
          labels[p.key] = p.label || p.key;
          if (!isShownKey(mode, p.key, roleSet)) return;
          shown.push(p.key);
          tabHtml += boxHtml(p.key, labels[p.key], mode === 'restrict' ? !active.has(p.key) : active.has(p.key));
        });
        if (tabHtml) {
          groupHtml += (t.tab ? '<div style="' + TAB_STYLE + '">' + esc(t.tab) + '</div>' : '') + tabHtml;
        }
      });
      if (groupHtml) html += '<div style="' + GROUP_STYLE + '">' + esc(g.group) + '</div>' + groupHtml;
    });
    if (mode === 'grant') {
      // A blocked key that is already granted stays visible so it can be removed.
      let extraHtml = '';
      BLOCKED_GRANT_KEYS.filter(function (k) { return active.has(k); }).sort().forEach(function (k) {
        shown.push(k);
        extraHtml += boxHtml(k, labels[k] || k, true);
      });
      if (extraHtml) {
        html += '<div style="' + GROUP_STYLE + '">Already granted (blocked from new grants)</div>' + extraHtml;
      }
    }
    if (!shown.length) {
      html = '<div style="font-size:13px;color:var(--ink-faint);">Nothing to show for this role.</div>';
    }
    return { html: html, shown: shown };
  }

  // Builds the list to save. Keys that were not shown stay as they were.
  // restrict: result = denied keys. A shown key that is NOT ticked is denied.
  // grant: result = granted keys. A shown key that IS ticked is granted.
  function mergeSelection(mode, shownKeys, tickedKeys, previousKeys) {
    const shown = toSet(shownKeys);
    const ticked = toSet(tickedKeys);
    const out = new Set();
    toSet(previousKeys).forEach(function (k) { if (!shown.has(k)) out.add(k); });
    shown.forEach(function (k) {
      if (mode === 'restrict') {
        if (!ticked.has(k)) out.add(k);
      } else if (ticked.has(k)) {
        out.add(k);
      }
    });
    return Array.from(out).sort();
  }

  return {
    BLOCKED_GRANT_KEYS: BLOCKED_GRANT_KEYS,
    NEVER_RESTRICT_KEYS: NEVER_RESTRICT_KEYS,
    OWNER_ONLY_KEYS: OWNER_ONLY_KEYS,
    isUsableRegistry: isUsableRegistry,
    roleKeysFor: roleKeysFor,
    renderBoxes: renderBoxes,
    mergeSelection: mergeSelection,
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = StaffPicker;
}
