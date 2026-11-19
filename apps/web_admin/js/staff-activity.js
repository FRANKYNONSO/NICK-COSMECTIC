(function () {
  'use strict';

  var PAGE_SIZE = 50;
  var TZ = 'Africa/Lagos';
  var TYPES = {
    sale: ['Sale', '#2e9d6a'], booking: ['Booking', '#6c4bd6'], transfer: ['Transfer', '#3b82f6'],
    refund: ['Refund', '#e5484d'], loss: ['Loss', '#f59e0b'], credit: ['Credit', '#0ea5e9'],
    shift_open: ['Shift opened', '#8b8f9a'], shift_close: ['Shift closed', '#8b8f9a'],
    audit: ['Other action', '#8b8f9a']
  };
  var offset = 0;
  var total = 0;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function naira(k) {
    if (k == null) return '-';
    return '\u20A6' + (k / 100).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtTime(iso) {
    if (!iso) return '-';
    return new Date(iso).toLocaleString('en-NG', {
      timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  }
  function todayWat() { return new Date().toLocaleDateString('en-CA', { timeZone: TZ }); }
  function addDays(ymd, n) {
    var p = ymd.split('-');
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] + n)).toISOString().slice(0, 10);
  }
  function signOut() {
    ['nc_access_token', 'nc_refresh_token', 'nc_user'].forEach(function (k) { localStorage.removeItem(k); });
    location.href = 'index.html';
  }
  function showError(msg) {
    var el = $('sa-error');
    el.textContent = msg || '';
    el.style.display = msg ? '' : 'none';
  }

  function request(path, params) {
    var url = new URL(API_BASE + path);
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
    });
    return fetch(url.toString(), { headers: { Authorization: 'Bearer ' + Auth.getToken() } }).then(function (res) {
      if (res.status === 401) { signOut(); throw new Error('Session expired. Please sign in again.'); }
      if (res.status === 403) throw new Error('You do not have permission to view staff activity.');
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (b) {
          throw new Error(b.detail || ('Request failed (' + res.status + ')'));
        });
      }
      return res.json();
    });
  }

  function baseParams() {
    return {
      date_from: $('sa-from').value,
      date_to: $('sa-to').value,
      branch_id: (typeof BranchContext !== 'undefined') ? BranchContext.get() : undefined
    };
  }

  function markPreset(name) {
    document.querySelectorAll('#sa-presets .segmented-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-preset') === name);
    });
  }
  function setPreset(name) {
    var t = todayWat();
    if (name === 'today') { $('sa-from').value = t; $('sa-to').value = t; }
    else if (name === 'week') { $('sa-from').value = addDays(t, -6); $('sa-to').value = t; }
    else if (name === 'month') { $('sa-from').value = t.slice(0, 8) + '01'; $('sa-to').value = t; }
    markPreset(name);
  }

  function card(label, value) {
    return '<div class="sa-card"><small>' + esc(label) + '</small><b>' + esc(value) + '</b></div>';
  }
  function renderTotals(t) {
    $('sa-totals').innerHTML =
      card('Total actions', t.total_actions) +
      card('Sales', t.sales_count + ' (' + naira(t.sales_total_kobo) + ')') +
      card('Refunds', t.refunds_count + ' (' + naira(t.refunds_total_kobo) + ')') +
      card('Credit opened', t.credits_count + ' (' + naira(t.credits_total_kobo) + ')') +
      card('Losses', t.losses_count + ' (' + naira(t.losses_value_kobo) + ')') +
      card('Auto-closed shifts', t.shifts_auto_closed);
  }

  function renderSummary(rows) {
    var body = $('sa-summary-body');
    $('sa-summary-empty').style.display = rows.length ? 'none' : '';
    $('sa-summary-table').style.display = rows.length ? '' : 'none';
    body.innerHTML = rows.map(function (r) {
      return '<tr class="sa-row" data-staff="' + esc(r.user_id) + '" style="cursor:pointer">' +
        '<td><strong>' + esc(r.name) + '</strong><br><small class="sa-muted">' + esc((r.role || '').replace(/_/g, ' ')) +
          (r.is_active ? '' : ' \u00B7 inactive') + '</small></td>' +
        '<td>' + esc(r.branch_name || '-') + '</td>' +
        '<td>' + r.sales_count + ' \u00B7 ' + naira(r.sales_total_kobo) + '</td>' +
        '<td>' + r.refunds_count + (r.refunds_count ? ' \u00B7 ' + naira(r.refunds_total_kobo) : '') + '</td>' +
        '<td>' + r.credits_count + ' credit' + (r.credits_count ? ' (' + naira(r.credits_total_kobo) + ')' : '') +
          '<br><small class="sa-muted">' + r.bookings_count + ' booking</small></td>' +
        '<td>' + r.losses_count + (r.losses_count ? ' \u00B7 ' + naira(r.losses_value_kobo) : '') + '</td>' +
        '<td>' + r.shifts_opened + ' opened' + (r.shifts_auto_closed ? '<br><small class="sa-muted">' + r.shifts_auto_closed + ' auto-closed</small>' : '') + '</td>' +
        '<td>' + r.other_actions + '</td>' +
        '<td>' + esc(fmtTime(r.last_activity_at)) + '</td>' +
        '</tr>';
    }).join('');
  }

  function fillStaffSelect(rows) {
    var sel = $('sa-staff');
    var cur = sel.value;
    sel.innerHTML = '<option value="">All staff</option>' + rows.map(function (r) {
      return '<option value="' + esc(r.user_id) + '">' + esc(r.name) + '</option>';
    }).join('');
    sel.value = cur;
  }

  function renderTimeline(items) {
    var body = $('sa-timeline-body');
    $('sa-timeline-empty').style.display = items.length ? 'none' : '';
    $('sa-timeline-table').style.display = items.length ? '' : 'none';
    body.innerHTML = items.map(function (e) {
      var t = TYPES[e.type] || [e.type, '#8b8f9a'];
      return '<tr>' +
        '<td style="white-space:nowrap">' + esc(fmtTime(e.at)) + '</td>' +
        '<td>' + esc(e.staff_name) + '</td>' +
        '<td>' + esc(e.branch_name || '-') + '</td>' +
        '<td><span class="sa-badge" style="background:' + t[1] + '22;color:' + t[1] + '">' + esc(t[0]) + '</span></td>' +
        '<td>' + esc(e.summary) + '</td>' +
        '<td style="text-align:right;white-space:nowrap">' + (e.amount_kobo == null ? '-' : naira(e.amount_kobo)) + '</td>' +
        '</tr>';
    }).join('');
    var from = total ? offset + 1 : 0;
    var to = Math.min(offset + PAGE_SIZE, total);
    $('sa-page-info').textContent = total ? ('Showing ' + from + '-' + to + ' of ' + total) : '';
    $('sa-prev').disabled = offset === 0;
    $('sa-next').disabled = offset + PAGE_SIZE >= total;
  }

  function loadSummary() {
    return request('/staff-activity/summary', baseParams()).then(function (d) {
      renderTotals(d.totals);
      revealNav();
      renderSummary(d.rows);
      fillStaffSelect(d.rows);
      $('sa-truncated').style.display = d.truncated ? '' : 'none';
    });
  }
  function loadTimeline() {
    var p = baseParams();
    p.staff_id = $('sa-staff').value;
    p.action_type = $('sa-type').value;
    p.limit = PAGE_SIZE;
    p.offset = offset;
    return request('/staff-activity', p).then(function (d) {
      total = d.total;
      renderTimeline(d.items);
      if (d.truncated) $('sa-truncated').style.display = '';
    });
  }
  function reloadAll() {
    offset = 0;
    showError('');
    loadSummary().then(loadTimeline).catch(function (e) { showError(e.message || 'Could not load staff activity.'); });
  }
  function reloadTimeline() {
    offset = 0;
    showError('');
    loadTimeline().catch(function (e) { showError(e.message || 'Could not load staff activity.'); });
  }

  function revealNav() {
    document.querySelectorAll('.nav-gate-pending').forEach(function (n) { n.classList.remove('nav-gate-pending'); });
  }

  function init() {
    if (typeof Auth === 'undefined' || !Auth.getToken()) { location.href = 'index.html'; return; }
    if (typeof BranchContext !== 'undefined') {
      BranchContext.renderBadge('branch-badge');
      window.addEventListener('branch-context-changed', reloadAll);
    }
    document.querySelectorAll('[data-consolidated-tab]').forEach(function (b) {
      b.addEventListener('click', function () {
        location.href = 'staff.html?tab=' + b.getAttribute('data-consolidated-tab');
      });
    });
    var lo = $('logout-link');
    if (lo) lo.addEventListener('click', function (e) { e.preventDefault(); signOut(); });

    document.querySelectorAll('#sa-presets .segmented-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        var name = b.getAttribute('data-preset');
        if (name === 'custom') { markPreset('custom'); return; }
        setPreset(name);
        reloadAll();
      });
    });
    $('sa-from').addEventListener('change', function () { markPreset('custom'); });
    $('sa-to').addEventListener('change', function () { markPreset('custom'); });
    $('sa-apply').addEventListener('click', reloadAll);
    $('sa-staff').addEventListener('change', reloadTimeline);
    $('sa-type').addEventListener('change', reloadTimeline);
    $('sa-summary-body').addEventListener('click', function (e) {
      var tr = e.target.closest('tr[data-staff]');
      if (!tr) return;
      $('sa-staff').value = tr.getAttribute('data-staff');
      reloadTimeline();
      $('sa-timeline-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    $('sa-prev').addEventListener('click', function () {
      offset = Math.max(0, offset - PAGE_SIZE);
      loadTimeline().catch(function (e) { showError(e.message); });
    });
    $('sa-next').addEventListener('click', function () {
      offset += PAGE_SIZE;
      loadTimeline().catch(function (e) { showError(e.message); });
    });

    setPreset('today');
    reloadAll();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();