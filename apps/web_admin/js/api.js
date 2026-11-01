

const API_BASE = `http://${window.location.hostname}:8000/api/v1`;

// Global branch context: the branch picked on the Dashboard now scopes
// every other page (POS, sales, invoices, credit, inventory, low stock,
// warehouse, reports, staff, shifts, etc). Persisted in localStorage so
// it survives navigation and reloads, and stays selected until the user
// deliberately changes it (or switches to "All branches", which clears
// it). Every other page's own branch dropdown was removed in favor of
// reading this single value -- see BranchContext.badge() for the
// read-only indicator that replaced them.
const BRANCH_STORAGE_KEY = 'nc_active_branch_id';
const BRANCH_NAME_STORAGE_KEY = 'nc_active_branch_name';

const BranchContext = {
  // undefined means "All branches" -- matches how Api.* functions already
  // treat a falsy branchId (they omit branch_id and the server returns
  // data across every branch).
  get() {
    return localStorage.getItem(BRANCH_STORAGE_KEY) || undefined;
  },
  getName() {
    return localStorage.getItem(BRANCH_NAME_STORAGE_KEY) || 'All branches';
  },
  set(id, name) {
    if (id) {
      localStorage.setItem(BRANCH_STORAGE_KEY, id);
      localStorage.setItem(BRANCH_NAME_STORAGE_KEY, name || '');
    } else {
      localStorage.removeItem(BRANCH_STORAGE_KEY);
      localStorage.removeItem(BRANCH_NAME_STORAGE_KEY);
    }
    window.dispatchEvent(new CustomEvent('branch-context-changed', { detail: { branchId: id || null, name: name || null } }));
  },
  // Renders the read-only "Branch: X" indicator used on every page except
  // the Dashboard (which keeps the real selector). Call once on page load
  // with the id of an empty container element.
  renderBadge(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const paint = () => {
      const name = BranchContext.get() ? BranchContext.getName() : 'All branches';
      el.innerHTML = `<span class="branch-context-badge" title="Change the active branch from the Dashboard">Branch: <strong>${name}</strong></span>`;
    };
    paint();
    window.addEventListener('branch-context-changed', paint);
    window.addEventListener('storage', (e) => { if (e.key === BRANCH_STORAGE_KEY || e.key === BRANCH_NAME_STORAGE_KEY) paint(); });
  },
};

const Auth = {
  getToken() { return localStorage.getItem('nc_access_token'); },
  getRefreshToken() { return localStorage.getItem('nc_refresh_token'); },
  getUser() {
    const raw = localStorage.getItem('nc_user');
    return raw ? JSON.parse(raw) : null;
  },
  // 'accepted' | 'under_review' | 'rejected' | 'suspended' | null (null =
  // this account has no staff application at all, e.g. admin-created --
  // always treated as fully active).
  getAppStatus() { return localStorage.getItem('nc_app_status') || null; },
  // Cache of { role_name: [permission, ...] } fetched from GET /roles --
  // lets Perms.can() (permissions.js) reflect custom roles and any live
  // edits to a role's permissions (including the three system roles),
  // not just the hardcoded three-role table baked into that file.
  // Refreshed in the background on every page load (see permissions.js);
  // this is what's read synchronously in between refreshes.
  getRolePermissionsCache() {
    try {
      return JSON.parse(localStorage.getItem('nc_role_permissions_cache') || 'null');
    } catch (err) {
      return null;
    }
  },
  setRolePermissionsCache(map) {
    localStorage.setItem('nc_role_permissions_cache', JSON.stringify(map));
  },
  setSession(token, user, appStatus = null, refreshToken = null) {
    localStorage.setItem('nc_access_token', token);
    localStorage.setItem('nc_user', JSON.stringify(user));
    if (appStatus) localStorage.setItem('nc_app_status', appStatus);
    else localStorage.removeItem('nc_app_status');
    if (refreshToken) localStorage.setItem('nc_refresh_token', refreshToken);
  },
  // Access tokens are short-lived. Called on a 401 before we give up and
  // boot the user to the login page -- this is what lets someone stay on
  // a page for a long time without being signed out from under them,
  // while a stolen/expired refresh token still eventually dead-ends at a
  // real login. Returns 'ok', 'invalid' (refresh token itself rejected --
  // this is the only case that should end the session), or 'network'
  // (couldn't reach the server -- session must NOT be cleared for this,
  // or a brief connectivity blip would wrongly log the user out).
  //
  // _refreshInFlight coalesces concurrent callers (e.g. several requests
  // that all got a 401 at once) onto a single network call instead of
  // racing multiple refreshes against the same refresh token.
  _refreshInFlight: null,
  async trySilentRefresh() {
    if (this._refreshInFlight) return this._refreshInFlight;
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return 'invalid';
    this._refreshInFlight = (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (res.status === 401) return 'invalid';
        if (!res.ok) return 'network';
        const tokens = await res.json();
        localStorage.setItem('nc_access_token', tokens.access_token);
        if (tokens.refresh_token) localStorage.setItem('nc_refresh_token', tokens.refresh_token);
        return 'ok';
      } catch (err) {
        // fetch itself threw -- offline / server unreachable, not an
        // invalid session. Leave tokens in place so the user isn't
        // logged out just because their connection blipped.
        return 'network';
      } finally {
        this._refreshInFlight = null;
      }
    })();
    return this._refreshInFlight;
  },
  clearSession() {
    localStorage.removeItem('nc_access_token');
    localStorage.removeItem('nc_refresh_token');
    localStorage.removeItem('nc_user');
    localStorage.removeItem('nc_app_status');
    localStorage.removeItem('nc_role_permissions_cache');
  },
  requireAuth() {
    if (!this.getToken()) {
      window.location.href = 'index.html';
      return;
    }
    // Belt-and-braces redirect, same reasoning as Perms.guardPage below --
    // the real enforcement is server-side (api/dependencies.py), this just
    // keeps a pending/rejected/suspended applicant off pages that would
    // otherwise 403 on every API call.
    const status = this.getAppStatus();
    const onStatusPage = window.location.pathname.endsWith('application-status.html');
    if (status && status !== 'accepted' && !onStatusPage) {
      window.location.href = 'application-status.html';
    }
  },
  logout() {
    this.clearSession();
    window.location.href = 'index.html';
  },
};

class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function apiRequest(path, { method = 'GET', body = null } = {}, _isRetry = false) {
  const headers = { Accept: 'application/json' };
  const token = Auth.getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== null) headers['Content-Type'] = 'application/json';

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== null ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(
      'Could not reach the server. Check that the API is running on 127.0.0.1:8000.',
      0,
      null
    );
  }

  if (res.status === 401) {
    // Don't sign the user out just because the (short-lived) access
    // token expired -- try once to refresh it silently and replay the
    // request. Only fall back to a real logout if there's no refresh
    // token, the refresh itself was rejected, or this is already a
    // retry (so we never loop).
    if (!_isRetry) {
      const outcome = await Auth.trySilentRefresh();
      if (outcome === 'ok') return apiRequest(path, { method, body }, true);
      if (outcome === 'network') {
        // Couldn't reach the server to refresh -- don't wipe the
        // session over a connectivity blip. Surface a normal, retryable
        // error instead of forcing the user back to the login page.
        throw new ApiError(
          'Could not reach the server. Check that the API is running on 127.0.0.1:8000.',
          0,
          null
        );
      }
    }
    // Only an explicitly invalid/expired refresh token ends the session.
    Auth.clearSession();
    window.location.href = 'index.html';
    return null;
  }

  if (res.status === 204) return null;

  const data = await res.json().catch(() => null);

  if (!res.ok) {
    const detail = data && data.detail;
    let message = 'Something went wrong. Please try again.';
    if (typeof detail === 'string') message = detail;
    else if (Array.isArray(detail) && detail[0]) message = detail[0].msg || message;
    throw new ApiError(message, res.status, detail);
  }

  return data;
}

// Multipart upload (product/sales imports) — no JSON content-type, body is FormData.
async function apiUpload(path, formData, _isRetry = false) {
  const headers = { Accept: 'application/json' };
  const token = Auth.getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method: 'POST', headers, body: formData });
  } catch (err) {
    throw new ApiError('Could not reach the server. Check that the API is running on 127.0.0.1:8000.', 0, null);
  }

  if (res.status === 401) {
    if (!_isRetry) {
      const outcome = await Auth.trySilentRefresh();
      if (outcome === 'ok') return apiUpload(path, formData, true);
      if (outcome === 'network') {
        throw new ApiError('Could not reach the server. Check that the API is running on 127.0.0.1:8000.', 0, null);
      }
    }
    Auth.clearSession();
    window.location.href = 'index.html';
    return null;
  }

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail;
    let message = 'Import failed. Please check the file and try again.';
    if (typeof detail === 'string') message = detail;
    else if (Array.isArray(detail) && detail[0]) message = detail[0].msg || message;
    throw new ApiError(message, res.status, detail);
  }
  return data;
}

// File download (exports) — triggers a browser save of whatever the server returns.
async function apiDownload(path, suggestedName, _isRetry = false) {
  const headers = {};
  const token = Auth.getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers });
  } catch (err) {
    throw new ApiError('Could not reach the server. Check that the API is running on 127.0.0.1:8000.', 0, null);
  }

  if (res.status === 401) {
    if (!_isRetry) {
      const outcome = await Auth.trySilentRefresh();
      if (outcome === 'ok') return apiDownload(path, suggestedName, true);
      if (outcome === 'network') {
        throw new ApiError('Could not reach the server. Check that the API is running on 127.0.0.1:8000.', 0, null);
      }
    }
    Auth.clearSession();
    window.location.href = 'index.html';
    return;
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError((data && data.detail) || 'Download failed.', res.status, null);
  }

  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = (match && match[1]) || suggestedName;

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const Api = {
  login(email, password) {
    return apiRequest('/auth/login', { method: 'POST', body: { email, password } });
  },
  me() {
    return apiRequest('/auth/me');
  },
  listUsers({ branchId, role, includeInactive } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (role) params.set('role', role);
    if (includeInactive) params.set('include_inactive', 'true');
    const qs = params.toString();
    return apiRequest(`/users${qs ? `?${qs}` : ''}`);
  },
  createUser(payload) {
    return apiRequest('/users', { method: 'POST', body: payload });
  },
  updateUser(id, payload) {
    return apiRequest(`/users/${id}`, { method: 'PATCH', body: payload });
  },
  updateUserRole(id, role) {
    return apiRequest(`/users/${id}/role`, { method: 'PATCH', body: { role } });
  },
  updateUserPermissions(id, deniedPermissions, grantedPermissions = []) {
    return apiRequest(`/users/${id}/permissions`, {
      method: 'PATCH',
      body: { denied_permissions: deniedPermissions, granted_permissions: grantedPermissions },
    });
  },
  deactivateUser(id) {
    return apiRequest(`/users/${id}/deactivate`, { method: 'PATCH' });
  },
  activateUser(id) {
    return apiRequest(`/users/${id}/activate`, { method: 'PATCH' });
  },
  deleteUser(id) {
    return apiRequest(`/users/${id}`, { method: 'DELETE' });
  },
  resetPassword(id, newPassword) {
    return apiRequest(`/users/${id}/reset-password`, {
      method: 'POST',
      body: { new_password: newPassword },
    });
  },

  // ---------------------------------------------------- Staff applications
  staffApplicationOptions() {
    return apiRequest('/staff-applications/application-options');
  },
  registerStaffApplication(formData) {
    return apiUpload('/staff-applications/register', formData);
  },
  myApplicationStatus() {
    return apiRequest('/staff-applications/me');
  },
  // PATCH26A-API
  // Blank printable staff form (PDF). The endpoint needs the auth header, so a
  // plain <a href> can't reach it -- fetch as a blob and save from here.
  async downloadStaffFormPdf() {
    const headers = {};
    const token = Auth.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/staff-applications/form.pdf`, { headers });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError((data && data.detail) || 'Could not download the form.', res.status, null);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'nick-cosmetics-staff-form.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  },

  // ------------------------------------------------------------- Profile
  forgotPassword(email) {
    return apiRequest('/auth/forgot-password', { method: 'POST', body: { email } });
  },
  resetPasswordWithToken(token, newPassword) {
    return apiRequest('/auth/reset-password', { method: 'POST', body: { token, new_password: newPassword } });
  },
  // --- Admin-approved password reset (replaces the emailed-link flow on the page) ---
  requestPasswordReset(email) {
    return apiRequest('/auth/password-reset-requests', { method: 'POST', body: { email } });
  },
  passwordResetStatus(requestId, secret) {
    return apiRequest('/auth/password-reset-requests/status', { method: 'POST', body: { request_id: requestId, secret } });
  },
  completePasswordReset(requestId, secret, newPassword) {
    return apiRequest('/auth/password-reset-requests/complete', {
      method: 'POST',
      body: { request_id: requestId, secret, new_password: newPassword },
    });
  },
  listPasswordResetRequests(status) {
    const q = status ? `?status=${encodeURIComponent(status)}` : '';
    return apiRequest(`/password-reset-requests${q}`);
  },
  approvePasswordReset(id) {
    return apiRequest(`/password-reset-requests/${id}/approve`, { method: 'POST' });
  },
  rejectPasswordReset(id) {
    return apiRequest(`/password-reset-requests/${id}/reject`, { method: 'POST' });
  },
  getMyProfile() {
    return apiRequest('/me/profile');
  },
  updateMyProfile(fields) {
    return apiRequest('/me/profile', { method: 'PATCH', body: fields });
  },
  updateMyPhoto(formData) {
    return apiUpload('/me/photo', formData);
  },
  changeMyPassword(currentPassword, newPassword) {
    return apiRequest('/me/change-password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },
  requestEmailChange(newEmail) {
    return apiRequest('/me/email/request-change', { method: 'POST', body: { new_email: newEmail } });
  },
  verifyEmailChange(token) {
    return apiRequest('/me/email/verify', { method: 'POST', body: { token } });
  },
  listApplications(status) {
    const qs = status ? `?status_filter=${status}` : '';
    return apiRequest(`/staff-applications${qs}`);
  },
  getApplication(id) {
    return apiRequest(`/staff-applications/${id}`);
  },
  acceptApplication(id, branchId, role) {
    return apiRequest(`/staff-applications/${id}/accept`, {
      method: 'POST',
      body: { branch_id: branchId, role },
    });
  },
  rejectApplication(id, reason) {
    return apiRequest(`/staff-applications/${id}/reject`, {
      method: 'POST',
      body: { reason: reason || null },
    });
  },
  suspendApplication(id) {
    return apiRequest(`/staff-applications/${id}/suspend`, { method: 'POST' });
  },
  reactivateApplication(id) {
    return apiRequest(`/staff-applications/${id}/reactivate`, { method: 'POST' });
  },
  deleteApplication(id) {
    return apiRequest(`/staff-applications/${id}`, { method: 'DELETE' });
  },
  // The id-document endpoint requires an auth header, so a plain <a href>
  // or <img src> can't reach it directly -- fetch it as an authenticated
  // blob and open that instead. See staff.html's "View ID document" action.
  async fetchIdDocumentBlob(id) {
    const headers = {};
    const token = Auth.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/staff-applications/${id}/id-document`, { headers });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError((data && data.detail) || 'Could not load document.', res.status, null);
    }
    const blob = await res.blob();
    return { url: URL.createObjectURL(blob), type: blob.type };
  },
  // Same thing for the signed-in person's OWN ID document (profile page).
  async fetchMyIdDocumentBlob() {
    const headers = {};
    const token = Auth.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/me/id-document`, { headers });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError((data && data.detail) || 'Could not load document.', res.status, null);
    }
    const blob = await res.blob();
    return { url: URL.createObjectURL(blob), type: blob.type };
  },
  async openIdDocument(id) {
    const headers = {};
    const token = Auth.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/staff-applications/${id}/id-document`, { headers });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError((data && data.detail) || 'Could not load document.', res.status, null);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
  },
  listBranches() {
    return apiRequest('/branches');
  },
  createBranch(payload) {
    return apiRequest('/branches', { method: 'POST', body: payload });
  },
  setBranchSuspended(id, suspended) {
    return apiRequest(`/branches/${id}/suspension`, { method: "POST", body: { suspended } });
  },
  updateBranchSupportLine(id, supportLine) {
    return apiRequest(`/branches/${id}/support-line`, { method: "PATCH", body: { support_line: supportLine || null } });
  },
  deleteBranch(id) {
    return apiRequest(`/branches/${id}`, { method: "DELETE" });
  },
  salesSummary({ branchId, dateFrom, dateTo, groupBy } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (groupBy) params.set('group_by', groupBy);
    const qs = params.toString();
    return apiRequest(`/reports/sales-summary${qs ? `?${qs}` : ''}`);
  },
  lowStock(branchId) {
    // branchId is optional -- omit it to get stock aggregated across
    // every branch (needed for a super_admin, who has no single branch
    // of their own and would otherwise never see this at all).
    return apiRequest(`/reports/low-stock${branchId ? `?branch_id=${branchId}` : ''}`);
  },
  expiryAlerts(branchId, withinDays) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (withinDays) params.set('within_days', withinDays);
    const qs = params.toString();
    return apiRequest(`/reports/expiry${qs ? `?${qs}` : ''}`);
  },
  // September 2026 change spec section 6 -- the push-notification
  // version of the above: pre-scoped to the current user (their own
  // branch, or every branch for a super admin) and excludes anything
  // they've already dismissed.
  myPendingExpiryAlerts() {
    return apiRequest('/reports/expiry-alerts/my-pending');
  },
  dismissExpiryAlert(stockBatchId) {
    return apiRequest(`/reports/expiry-alerts/${stockBatchId}/dismiss`, { method: 'POST' });
  },
  lossesSummary(branchId, startDate, endDate) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    const qs = params.toString();
    return apiRequest(`/inventory/losses/summary${qs ? `?${qs}` : ''}`);
  },
  lossesHistory({ branchId, category, year, month } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (category) params.set('category', category);
    if (year) params.set('year', year);
    if (month) params.set('month', month);
    const qs = params.toString();
    return apiRequest(`/inventory/losses/history${qs ? `?${qs}` : ''}`);
  },
  totalInvested(branchId, dateFrom) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (dateFrom) params.set('date_from', dateFrom);
    const qs = params.toString();
    return apiRequest(`/purchase-invoices/total-invested${qs ? `?${qs}` : ''}`);
  },
  // Dashboard redesign implementation prompt, step 4 -- permanent
  // per-day Performance Records, replacing the old resettable
  // financial-period tracking (see step 1: "nothing should ever
  // reset again").
  performanceRecords({ metric, period, branchId, limit } = {}) {
    const params = new URLSearchParams();
    params.set('metric', metric);
    if (period) params.set('period', period);
    if (branchId) params.set('branch_id', branchId);
    if (limit) params.set('limit', limit);
    return apiRequest(`/reports/performance-records?${params.toString()}`);
  },
  // Step 5 -- notification bell replacing the five removed dashboard
  // alert cards.
  priceChangeHistory(branchId) {
    return apiRequest(`/reports/price-change-history${branchId ? `?branch_id=${branchId}` : ''}`);
  },
  notificationsSummary(branchId) {
    return apiRequest(`/notifications/summary${branchId ? `?branch_id=${branchId}` : ''}`);
  },
  listStockAdjustments({ branchId, reason, startDate, endDate } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (reason) params.set('reason', reason);
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    const qs = params.toString();
    return apiRequest(`/inventory/stock-adjustments${qs ? `?${qs}` : ''}`);
  },
  createStockAdjustment(payload) {
    return apiRequest('/inventory/stock-adjustments', { method: 'POST', body: payload });
  },
  // September 2026 change spec section 4 -- structured Damaged/
  // Stolen/Expired loss reporting.
  uploadLossEvidence(file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload('/inventory/stock-adjustments/evidence', formData);
  },
  confirmTheftAdjustment(adjustmentId) {
    return apiRequest(`/inventory/stock-adjustments/${adjustmentId}/confirm-theft`, { method: 'POST' });
  },
  markAdjustmentReviewed(adjustmentId) {
    return apiRequest(`/inventory/stock-adjustments/${adjustmentId}/mark-reviewed`, { method: 'POST' });
  },
  approveAdjustment(adjustmentId, valueNaira) {
    return apiRequest(`/inventory/stock-adjustments/${adjustmentId}/approve?value_naira=${encodeURIComponent(valueNaira ?? '')}`, { method: 'POST' });
  },
  rejectAdjustment(adjustmentId, reason) {
    return apiRequest(`/inventory/stock-adjustments/${adjustmentId}/reject?reason=${encodeURIComponent(reason ?? '')}`, { method: 'POST' });
  },
  lossRefundDetails(adjustmentId) {
    return apiRequest(`/inventory/stock-adjustments/${adjustmentId}/refund-details`);
  },
  pendingReviewAdjustments() {
    return apiRequest('/inventory/stock-adjustments/pending-review');
  },
  // ----- Credit list -----
  listCredit({ branchId, status } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (status) params.set('status_filter', status);
    const qs = params.toString();
    return apiRequest(`/credit${qs ? `?${qs}` : ''}`);
  },
  createCredit(payload) {
    return apiRequest('/credit', { method: 'POST', body: payload });
  },
  addCreditPayment(creditId, payload) {
    return apiRequest(`/credit/${creditId}/payments`, { method: 'POST', body: payload });
  },
  muteOutOfStockAlert(productId) {
    return apiRequest(`/catalog/products/${productId}/mute-out-of-stock-alert`, { method: 'POST' });
  },
  unmuteOutOfStockAlert(productId) {
    return apiRequest(`/catalog/products/${productId}/unmute-out-of-stock-alert`, { method: 'POST' });
  },
  productPerformance({ branchId, dateFrom, dateTo, limit } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (limit) params.set('limit', limit);
    const qs = params.toString();
    return apiRequest(`/reports/product-performance${qs ? `?${qs}` : ''}`);
  },
  profitSummary({ branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    const qs = params.toString();
    return apiRequest(`/reports/profit-summary${qs ? `?${qs}` : ''}`);
  },
  bookingsReport(branchId) {
    const qs = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : '';
    return apiRequest(`/reports/bookings-report${qs}`);
  },
  salesBreakdown({ branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
    return apiRequest(`/reports/sales-breakdown?${params.toString()}`);
  },
  returnsReport({ branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
    return apiRequest(`/reports/returns-report?${params.toString()}`);
  },
  purchasesReport({ branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
    return apiRequest(`/reports/purchases-report?${params.toString()}`);
  },
  stockMovements({ productId, branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    params.set('product_id', productId);
    if (branchId) params.set('branch_id', branchId);
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
    return apiRequest(`/reports/stock-movements?${params.toString()}`);
  },
  transfersReport({ branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
    return apiRequest(`/reports/transfers-report?${params.toString()}`);
  },
  countVariance({ branchId, dateFrom, dateTo } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
    return apiRequest(`/reports/stock-count-variance?${params.toString()}`);
  },

  // ------------------------------------------------------------ Categories
  listBrands() {
    return apiRequest('/catalog/brands');
  },
  createBrand(payload) {
    return apiRequest('/catalog/brands', { method: 'POST', body: payload });
  },
  listCategories() {
    return apiRequest('/catalog/categories');
  },
  createCategory(payload) {
    return apiRequest('/catalog/categories', { method: 'POST', body: payload });
  },
  renameCategory(id, payload) {
    return apiRequest(`/catalog/categories/${id}`, { method: 'PATCH', body: payload });
  },
  deleteCategory(id) {
    return apiRequest(`/catalog/categories/${id}`, { method: 'DELETE' });
  },
  listUnits() {
    return apiRequest('/catalog/units');
  },
  createUnit(payload) {
    return apiRequest('/catalog/units', { method: 'POST', body: payload });
  },
  deleteUnit(id, force = false) {
    return apiRequest(`/catalog/units/${id}${force ? '?force=true' : ''}`, { method: 'DELETE' });
  },
  listCategorySizes(categoryId) {
    return apiRequest(`/catalog/categories/${categoryId}/sizes`);
  },

  // ------------------------------------------------------- Product templates
  // Item 8 -- see /mnt/skills or product_templates.py for the concept.
  listProductTemplates() {
    return apiRequest('/product-templates');
  },
  suggestProductTemplates(q) {
    return apiRequest(`/product-templates/suggest?q=${encodeURIComponent(q)}`);
  },
  createProductTemplate(payload) {
    return apiRequest('/product-templates', { method: 'POST', body: payload });
  },
  getProductTemplate(id) {
    return apiRequest(`/product-templates/${id}`);
  },
  setProductTemplateCategories(id, payload) {
    return apiRequest(`/product-templates/${id}/categories`, { method: 'PUT', body: payload });
  },
  deleteProductTemplate(id) {
    return apiRequest(`/product-templates/${id}`, { method: 'DELETE' });
  },

  // -------------------------------------------------------------- Products
  listProducts({ branchId, categoryId, search, lowStockOnly, outOfStockOnly, needsInvoiceOnly } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (categoryId) params.set('category_id', categoryId);
    if (search) params.set('search', search);
    if (lowStockOnly) params.set('low_stock', 'true');
    if (outOfStockOnly) params.set('out_of_stock', 'true');
    if (needsInvoiceOnly) params.set('needs_invoice', 'true');
    const qs = params.toString();
    return apiRequest(`/catalog/products${qs ? `?${qs}` : ''}`);
  },
  // September 2026 change spec section 9 (POS live indicator): how many
  // units of each product are tied up in open bookings at this branch,
  // right now. Read-only -- checkout/booking creation still hard-block
  // an oversell server-side regardless of what the POS shows.
  getBookingHolds(branchId, productIds) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    (productIds || []).forEach((id) => params.append('product_id', id));
    const qs = params.toString();
    return apiRequest(`/sales/bookings/holds${qs ? `?${qs}` : ''}`);
  },
  uploadProductImage(productId, file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload(`/catalog/products/${productId}/images`, formData);
  },
  receiveStock(payload) {
    return apiRequest('/inventory/stock-batches', { method: 'POST', body: payload });
  },
  listStockBatches(productId, branchId) {
    const params = new URLSearchParams();
    if (productId) params.set('product_id', productId);
    if (branchId) params.set('branch_id', branchId);
    const qs = params.toString();
    return apiRequest(`/inventory/stock-batches${qs ? `?${qs}` : ''}`);
  },
  // PATCH8A-PRODUCT-RETURNS-CREDIT: refunds and goods taken on credit for one product
  productReturnsAndCredit(productId, branchId) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    const qs = params.toString();
    return apiRequest(`/inventory/products/${productId}/returns-and-credit${qs ? `?${qs}` : ''}`);
  },
  stockLevels(branchId) {
    const params = new URLSearchParams();
    params.set('branch_id', branchId);
    return apiRequest(`/inventory/stock-levels?${params.toString()}`);
  },
  createStockTransfer(payload) {
    return apiRequest('/warehouse/transfers', { method: 'POST', body: payload });
  },
  // September 2026 change spec section 19 -- dry-run cost preview for
  // the confirmation-receipt step before an actual transfer.
  previewTransferCost({ productId, fromBranchId, quantityPieces }) {
    const params = new URLSearchParams({
      product_id: productId, from_branch_id: fromBranchId, quantity_pieces: quantityPieces,
    });
    return apiRequest(`/warehouse/transfer-cost-preview?${params.toString()}`);
  },
  // XFER2-SHELF-OR-WAREHOUSE -- shelf/warehouse figures and the split of a transfer.
  previewTransferSplit({ productId, fromBranchId, toBranchId, quantityPieces, takeFrom }) {
    const params = new URLSearchParams({
      product_id: productId,
      from_branch_id: fromBranchId,
      to_branch_id: toBranchId,
      quantity_pieces: quantityPieces,
      take_from: takeFrom || 'shelf',
    });
    return apiRequest(`/warehouse/transfer-split-preview?${params.toString()}`);
  },
  // XFER1-TRANSFER-AS-SALE -- default price per piece for a transfer.
  previewTransferPrice({ productId, quantityPieces, fromBranchId }) {
    const params = new URLSearchParams({ product_id: productId, quantity_pieces: quantityPieces });
    if (fromBranchId) params.set('from_branch_id', fromBranchId);
    return apiRequest(`/warehouse/transfer-price-preview?${params.toString()}`);
  },
  listStockTransfers({ branchId, transferType, productId } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (transferType) params.set('transfer_type', transferType);
    if (productId) params.set('product_id', productId);
    const qs = params.toString();
    return apiRequest(`/warehouse/transfers${qs ? `?${qs}` : ''}`);
  },
  // RET3 -- branch transfer returns (the routes are already built and tested).
  listTransferReturns({ branchId, transferId } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (transferId) params.set('transfer_id', transferId);
    const qs = params.toString();
    return apiRequest(`/warehouse/transfer-returns${qs ? `?${qs}` : ''}`);
  },
  createTransferReturn(payload) {
    return apiRequest('/warehouse/transfer-returns', { method: 'POST', body: payload });
  },
  acceptTransferReturn(returnId) {
    return apiRequest(`/warehouse/transfer-returns/${encodeURIComponent(returnId)}/accept`, { method: 'POST' });
  },
  declineTransferReturn(returnId) {
    return apiRequest(`/warehouse/transfer-returns/${encodeURIComponent(returnId)}/decline`, { method: 'POST' });
  },
  cancelTransferReturn(returnId) {
    return apiRequest(`/warehouse/transfer-returns/${encodeURIComponent(returnId)}/cancel`, { method: 'POST' });
  },
  approveTransferReturn(returnId, paymentMethod) {
    return apiRequest(`/warehouse/transfer-returns/${encodeURIComponent(returnId)}/approve`, { method: 'POST', body: { payment_method: paymentMethod } });
  },
  getTransferReturnHistory(returnId) {
    return apiRequest(`/warehouse/transfer-returns/${encodeURIComponent(returnId)}/history`);
  },
  // Distinct from createStockTransfer above, which moves sellable shop
  // stock directly between two branches with no warehouse involved.
  pushToWarehouse(branchId, payload) {
    return apiRequest(`/warehouse/push-to-warehouse?branch_id=${encodeURIComponent(branchId)}`, { method: 'POST', body: payload });
  },
  pullToStore(branchId, payload) {
    return apiRequest(`/warehouse/pull-to-store?branch_id=${encodeURIComponent(branchId)}`, { method: 'POST', body: payload });
  },
  transferWarehouseToBranch(branchId, payload) {
    return apiRequest(`/warehouse/transfer-to-branch?branch_id=${encodeURIComponent(branchId)}`, { method: 'POST', body: payload });
  },
  // Items eligible to be pushed to this branch's warehouse -- i.e. on
  // an invoice on file and not already fully pushed. Backs the
  // "push to warehouse" picker, which no longer lets you pick an
  // arbitrary catalog product/variant.
  listPushableInvoiceItems(branchId) {
    return apiRequest(`/warehouse/pushable-invoice-items?branch_id=${encodeURIComponent(branchId)}`);
  },
  linkInvoiceLineItemProduct(lineItemId, productId) {
    return apiRequest(`/warehouse/invoice-line-items/${lineItemId}/link-product`, {
      method: 'PATCH',
      body: { product_id: productId },
    });
  },
  listWarehouseStock(branchId) {
    const qs = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : '';
    return apiRequest(`/warehouse/stock${qs}`);
  },
  // PATCH9A-UNITS -- shared list of packaging units (carton / bag / roll / dozen / ...).
  listPackagingUnits({ includeInactive = false } = {}) {
    return apiRequest(`/catalog/packaging-units${includeInactive ? '?include_inactive=true' : ''}`);
  },
  createPackagingUnit(body) {
    return apiRequest('/catalog/packaging-units', { method: 'POST', body });
  },
  updatePackagingUnit(unitId, body) {
    return apiRequest(`/catalog/packaging-units/${unitId}`, { method: 'PATCH', body });
  },
  // PATCH10B-UNITSIZES -- per-product size for each packaging unit.
  listProductUnitSizes() {
    return apiRequest('/catalog/product-unit-sizes');
  },
  setProductUnitSizes(productId, sizes) {
    return apiRequest(`/catalog/products/${productId}/unit-sizes`, { method: 'PUT', body: { sizes } });
  },
  listIntakeReceipts(branchId) {
    const qs = branchId ? `?branch_id=${branchId}` : '';
    return apiRequest(`/warehouse/receipts${qs}`);
  },
  uploadIntakeReceipt(formData) {
    return apiUpload('/warehouse/receipts', formData);
  },
  // ---- Goods receiving (fix38): /goods-receiving/* ----
  goodsStockSummary({ branchId, productId, alertsOnly } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (productId) params.set('product_id', productId);
    if (alertsOnly) params.set('alerts_only', 'true');
    return apiRequest(`/goods-receiving/stock-summary?${params.toString()}`);
  },
  listShelfSettings({ branchId, productId } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (productId) params.set('product_id', productId);
    return apiRequest(`/goods-receiving/shelf-settings?${params.toString()}`);
  },
  saveShelfSetting(payload) {
    return apiRequest('/goods-receiving/shelf-settings', { method: 'PUT', body: payload });
  },
  // fix52: Reduce stock -- sends pieces above the shelf default back to the warehouse
  reduceShelfStock(payload) {
    return apiRequest('/goods-receiving/shelf-reduce', { method: 'POST', body: payload });
  },
  goodsCostSummary(branchId) {
    const qs = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : '';
    return apiRequest(`/goods-receiving/cost-summary${qs}`);
  },
  listGoodsReceipts(filters = {}) {
    const params = new URLSearchParams();
    Object.keys(filters).forEach((k) => {
      if (filters[k] !== undefined && filters[k] !== null && filters[k] !== '') params.set(k, filters[k]);
    });
    const qs = params.toString();
    return apiRequest(`/goods-receiving/receipts${qs ? `?${qs}` : ''}`);
  },
  outstandingReceiptQuantities(invoiceId) {
    return apiRequest(`/goods-receiving/receipts/outstanding?invoice_id=${encodeURIComponent(invoiceId)}`);
  },
  getGoodsReceipt(id) {
    return apiRequest(`/goods-receiving/receipts/${id}`);
  },
  createGoodsReceipt(payload) {
    return apiRequest('/goods-receiving/receipts', { method: 'POST', body: payload });
  },
  updateGoodsReceipt(id, payload) {
    return apiRequest(`/goods-receiving/receipts/${id}`, { method: 'PUT', body: payload });
  },
  postGoodsReceipt(id, options) {
    return apiRequest(`/goods-receiving/receipts/${id}/post`, { method: 'POST', body: options || {} });
  },
  linkGoodsReceiptInvoice(id, payload) {
    return apiRequest(`/goods-receiving/receipts/${id}/link-invoice`, { method: 'PATCH', body: payload });
  },
  getProduct(id) {
    return apiRequest(`/catalog/products/${id}`);
  },
  createProduct(payload) {
    return apiRequest('/catalog/products', { method: 'POST', body: payload });
  },
  updateProduct(id, payload) {
    return apiRequest(`/catalog/products/${id}`, { method: 'PATCH', body: payload });
  },
  deleteProduct(id, { reason, reason_note } = {}) {
    return apiRequest(`/catalog/products/${id}`, { method: 'DELETE', body: { reason, reason_note } });
  },
  // September 2026 change spec section 8: undo a soft delete --
  // identity fields only come back, never quantity-in-stock or price
  // (the server resets those; see the endpoint's own docstring).
  restoreProduct(id) {
    return apiRequest(`/catalog/products/${id}/restore`, { method: 'POST' });
  },
  hardDeleteProduct(id) {
    return apiRequest(`/catalog/products/${id}/hard-delete`, { method: 'POST' });
  },
  listInventoryDeletions({ branchId, isLoss } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (isLoss !== undefined && isLoss !== null) params.set('is_loss', isLoss);
    const qs = params.toString();
    return apiRequest(`/catalog/inventory-deletions${qs ? `?${qs}` : ''}`);
  },
  reclassifyInventoryDeletion(id, payload) {
    return apiRequest(`/catalog/inventory-deletions/${id}`, { method: 'PATCH', body: payload });
  },
  exportProducts() {
    return apiDownload('/catalog/products/export', 'products.xlsx');
  },
  downloadProductTemplate() {
    return apiDownload('/catalog/products/import-template', 'product-import-template.xlsx');
  },
  importProducts(file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload('/catalog/products/import', formData);
  },

  // ----------------------------------------------------------------- Sales
  listSales({ branchId, dateFrom, dateTo, cashierId } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (cashierId) params.set('cashier_id', cashierId);
    const qs = params.toString();
    return apiRequest(`/sales${qs ? `?${qs}` : ''}`);
  },
  getSale(id) {
    return apiRequest(`/sales/${id}`);
  },
  checkout(payload) {
    return apiRequest('/sales/checkout', { method: 'POST', body: payload });
  },
  createBooking(payload) {
    return apiRequest('/sales/bookings', { method: 'POST', body: payload });
  },
  listBookings(branchId) {
    const qs = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : '';
    return apiRequest(`/sales/bookings${qs}`);
  },
  completeBooking(id, payload) {
    return apiRequest(`/sales/bookings/${id}/add-payment`, { method: 'POST', body: payload });
  },
  addBookingPayment(id, payload) {
    return apiRequest(`/sales/bookings/${id}/add-payment`, { method: 'POST', body: payload });
  },
  cancelBooking(id, payload) {
    return apiRequest(`/sales/bookings/${id}/cancel`, { method: 'POST', body: payload });
  },
  changeBookingItem(id, payload) {
    return apiRequest(`/sales/bookings/${id}/change-item`, { method: 'POST', body: payload });
  },
  // Platform update spec section 2 -- "Customer is owed change"
  // tracking, tied to the sale/receipt itself, with cash/transfer/
  // airtime settlement methods routed through the same
  // approval-queue shape as refunds below.
  createPendingChange(payload) {
    return apiRequest('/sales/pending-changes', { method: 'POST', body: payload });
  },
  listPendingChanges({ status, branchId } = {}) {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (branchId) params.set('branch_id', branchId);
    const qs = params.toString();
    return apiRequest(`/sales/pending-changes${qs ? `?${qs}` : ''}`);
  },
  settlePendingChange(id, payload) {
    return apiRequest(`/sales/pending-changes/${id}/settle`, { method: 'POST', body: payload });
  },
  pendingChangeCount(branchId) {
    const qs = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : '';
    return apiRequest(`/sales/pending-changes/pending-count${qs}`);
  },
  markPendingChangeProcessed(id, payload = {}) {
    return apiRequest(`/sales/pending-changes/${id}/mark-processed`, { method: 'POST', body: payload });
  },
  openShift(payload) {
    return apiRequest('/shifts', { method: 'POST', body: payload });
  },
  getCurrentShift() {
    return apiRequest('/shifts/current');
  },
  getPendingShiftClosure() {
    return apiRequest('/shifts/pending-closure');
  },
  closeShift(id, closingCountedNaira) {
    return apiRequest(`/shifts/${id}/close`, {
      method: 'PATCH',
      body: { closing_counted_naira: closingCountedNaira },
    });
  },
  shiftCashSummary(id) {
    return apiRequest(`/shifts/${id}/cash-summary`);
  },
  refundSale(saleId, payload) {
    return apiRequest(`/sales/${saleId}/refund`, { method: 'POST', body: payload });
  },
  listRefunds({ branchId, status } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (status) params.set('status', status);
    const qs = params.toString();
    return apiRequest(`/sales/refunds${qs ? `?${qs}` : ''}`);
  },
  // Website (storefront) orders -- see js/website-orders.js
  listStorefrontOrders() {
    return apiRequest('/storefront/orders');
  },
  updateStorefrontOrder(orderId, payload) {
    return apiRequest(`/storefront/orders/${orderId}`, { method: 'PATCH', body: payload });
  },
  // PATCH19C-PAYMENT-SCREEN
  recordStorefrontPayment(orderId, payload) {
    return apiRequest(`/storefront/orders/${orderId}/payment`, { method: 'POST', body: payload });
  },
  // PATCH19D-UNDO-PAYMENT
  undoStorefrontPayment(orderId, payload) {
    return apiRequest(`/storefront/orders/${orderId}/payment/undo`, { method: 'POST', body: payload });
  },
  listStorefrontOrdersPage(limit, offset) {
    return apiRequest(`/storefront/orders?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`);
  },
  listStorefrontCoupons() {
    return apiRequest('/storefront/coupons');
  },
  generateStorefrontCoupons(payload) {
    return apiRequest('/storefront/coupons', { method: 'POST', body: payload });
  },
  voidStorefrontCoupon(couponId) {
    return apiRequest(`/storefront/coupons/${couponId}/void`, { method: 'POST' });
  },
  pendingRefundCount(branchId) {
    const qs = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : '';
    return apiRequest(`/sales/refunds/pending-count${qs}`);
  },
  markRefundProcessed(refundId, payload = {}) {
    return apiRequest(`/sales/refunds/${refundId}/mark-processed`, { method: 'POST', body: payload });
  },
  // Full itemized refund record for the printable refund receipt
  // (Sales & Receipt → Refund → Details).
  getRefundDetail(refundId) {
    return apiRequest(`/sales/refunds/${refundId}`);
  },
  // Manager-level accountability trail (see /sales/audit-log). Used by
  // the "Open booking history" tab to show every booking_* event --
  // created, payment added, item swapped, completed, cancelled -- in
  // one place, since a booking's own row only ever reflects its
  // *current* state once it leaves status='booking'.
  auditLog({ entityType, limit, branchId } = {}) {
    const params = new URLSearchParams();
    if (entityType) params.set('entity_type', entityType);
    if (branchId) params.set('branch_id', branchId);
    if (limit) params.set('limit', limit);
    const qs = params.toString();
    return apiRequest(`/sales/audit-log${qs ? `?${qs}` : ''}`);
  },
  uploadRefundPhoto(file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload('/sales/refund-photo', formData);
  },
  listCoupons() {
    return apiRequest('/coupons');
  },
  issueCoupon(payload) {
    return apiRequest('/coupons/issue', { method: 'POST', body: payload });
  },
  voidCoupon(code) {
    return apiRequest(`/coupons/${encodeURIComponent(code)}/void`, { method: 'POST' });
  },
  redeemCoupon(payload) {
    return apiRequest('/coupons/redeem', { method: 'POST', body: payload });
  },
  couponHistory(issuedBy, branchId) {
    const params = new URLSearchParams();
    if (issuedBy) params.set('issued_by', issuedBy);
    if (branchId) params.set('branch_id', branchId);
    const qs = params.toString();
    return apiRequest(`/coupons/history${qs ? `?${qs}` : ''}`);
  },
  listRoles() {
    return apiRequest('/roles');
  },
  createRole(payload) {
    return apiRequest('/roles', { method: 'POST', body: payload });
  },
  updateRole(name, payload) {
    return apiRequest(`/roles/${encodeURIComponent(name)}`, { method: 'PATCH', body: payload });
  },
  deleteRole(name) {
    return apiRequest(`/roles/${encodeURIComponent(name)}`, { method: 'DELETE' });
  },
  listChatMessages(peerId) {
    const qs = peerId ? `?peer_id=${encodeURIComponent(peerId)}` : '';
    return apiRequest(`/chat/messages${qs}`);
  },
  sendChatMessage(payload) {
    return apiRequest('/chat/messages', { method: 'POST', body: payload });
  },
  markChatRead(peerId) {
    return apiRequest('/chat/mark-read', { method: 'POST', body: { peer_id: peerId || null } });
  },
  chatUnread() {
    return apiRequest('/chat/unread');
  },
  listAnnouncements() {
    return apiRequest('/chat/announcements');
  },
  createAnnouncement(payload) {
    return apiRequest('/chat/announcements', { method: 'POST', body: payload });
  },
  markAnnouncementsRead() {
    return apiRequest('/chat/announcements/mark-read', { method: 'POST' });
  },
  announcementsUnreadCount() {
    return apiRequest('/chat/announcements/unread-count');
  },
  listChatContacts() {
    return apiRequest('/chat/contacts');
  },
  uploadChatAttachment(kind, file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload(`/chat/attachments?kind=${encodeURIComponent(kind)}`, formData);
  },
  exportSales() {
    return apiDownload('/sales/export', 'sales.xlsx');
  },
  downloadSalesTemplate() {
    return apiDownload('/sales/import-template', 'sales-import-template.xlsx');
  },
  importSales(file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload('/sales/import', formData);
  },
  // ----- Store branding settings -----
  getStoreSettings() {
    return apiRequest('/settings/store');
  },
  updateStoreSettings(payload) {
    return apiRequest('/settings/store', { method: 'PUT', body: payload });
  },
  uploadStoreLogo(file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload('/settings/store/logo', formData);
  },
  // ----- Shift history (admin) -----
  listShifts({ branchId, status } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (status) params.set('status_filter', status);
    const qs = params.toString();
    return apiRequest(`/shifts${qs ? `?${qs}` : ''}`);
  },
  unclosedShifts({ branchId, sinceDays } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (sinceDays) params.set('since_days', sinceDays);
    const qs = params.toString();
    return apiRequest(`/shifts/reports/unclosed${qs ? `?${qs}` : ''}`);
  },
  // ----- Shift compliance (staff accountability layer) -----
  complianceSummary({ branchId, userId } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (userId) params.set('user_id', userId);
    const qs = params.toString();
    return apiRequest(`/shifts/compliance/summary${qs ? `?${qs}` : ''}`);
  },
  complianceEvents({ branchId, userId, status, sinceDays } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (userId) params.set('user_id', userId);
    if (status) params.set('status_filter', status);
    if (sinceDays) params.set('since_days', sinceDays);
    const qs = params.toString();
    return apiRequest(`/shifts/compliance/events${qs ? `?${qs}` : ''}`);
  },
  myPendingComplianceEvents() {
    return apiRequest('/shifts/compliance/my-pending');
  },
  acknowledgeComplianceEvent(eventId) {
    return apiRequest(`/shifts/compliance/events/${eventId}/acknowledge`, { method: 'POST' });
  },
  // Item 15: separate admin-side "I've informed this staff member"
  // follow-up, distinct from the staff member's own self-ack above.
  adminAcknowledgeComplianceEvent(eventId, note) {
    return apiRequest(`/shifts/compliance/events/${eventId}/admin-acknowledge`, { method: 'POST', body: { note: note || null } });
  },
  staffShiftBehavior(userId) {
    return apiRequest(`/shifts/compliance/staff/${userId}/behavior`);
  },
  exemptShiftViolation(shiftId, reason) {
    return apiRequest(`/shifts/${shiftId}/exempt`, { method: 'PATCH', body: { reason } });
  },
  getShiftSettings(branchId) {
    return apiRequest(`/branches/${branchId}/shift-settings`);
  },
  updateShiftSettings(branchId, payload) {
    return apiRequest(`/branches/${branchId}/shift-settings`, { method: 'PUT', body: payload });
  },
  // ----- Purchase invoices (supplier invoices, super admin/branch manager) -----
  listPurchaseInvoices({ branchId, year, month, supplierId, status, excludeTransfers } = {}) {
    const params = new URLSearchParams();
    if (branchId) params.set('branch_id', branchId);
    if (year) params.set('year', year);
    if (month) params.set('month', month);
    if (supplierId) params.set('supplier_id', supplierId);
    if (status) params.set('status', status);
    if (excludeTransfers) params.set('exclude_transfers', 'true');
    const qs = params.toString();
    return apiRequest(`/purchase-invoices${qs ? `?${qs}` : ''}`);
  },
  createPurchaseInvoice(payload) {
    return apiRequest('/purchase-invoices', { method: 'POST', body: payload });
  },
  updatePurchaseInvoice(invoiceId, payload) {
    return apiRequest(`/purchase-invoices/${invoiceId}`, { method: 'PATCH', body: payload });
  },
  // TRASH2: invoice Trash (delete / restore / permanent delete)
  trashPurchaseInvoice(invoiceId, reason) {
    return apiRequest(`/purchase-invoices/${invoiceId}/trash`, { method: 'POST', body: { reason } });
  },
  restorePurchaseInvoice(invoiceId, reason) {
    return apiRequest(`/purchase-invoices/${invoiceId}/restore`, { method: 'POST', body: reason ? { reason } : {} });
  },
  deletePurchaseInvoicePermanently(invoiceId, reason) {
    return apiRequest(`/purchase-invoices/${invoiceId}/delete-permanently`, { method: 'POST', body: { reason } });
  },
  listTrashedInvoices(branchId) {
    return apiRequest(`/purchase-invoices/trash${branchId ? `?branch_id=${encodeURIComponent(branchId)}` : ''}`);
  },
  listInvoicesForReview(branchId) {
    return apiRequest(`/purchase-invoices/trash/review${branchId ? `?branch_id=${encodeURIComponent(branchId)}` : ''}`);
  },
  listInvoiceTrashActivity(branchId) {
    return apiRequest(`/purchase-invoices/trash/activity${branchId ? `?branch_id=${encodeURIComponent(branchId)}` : ''}`);
  },
  listFinalDeletions() {
    return apiRequest('/purchase-invoices/final-deletions');
  },
  getInvoiceTrashHistory(invoiceId) {
    return apiRequest(`/purchase-invoices/${invoiceId}/history`);
  },
  uploadPurchaseInvoiceImage(file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiUpload('/purchase-invoices/upload-image', formData);
  },
  // ----- Suppliers (spec section 14: autofill from a shared registry) -----
  listSuppliers(search) {
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    const qs = params.toString();
    return apiRequest(`/suppliers${qs ? `?${qs}` : ''}`);
  },
  updateSupplier(supplierId, payload) {
    return apiRequest(`/suppliers/${supplierId}`, { method: 'PATCH', body: payload });
  },
  // ----- Restock List -----
  restockList: {
    list(branchId) {
      return apiRequest(`/restock-list?branch_id=${encodeURIComponent(branchId)}`);
    },
    addItem({ productId, branchId, quantity, unitLabel, isManual, customName, customImageUrl, note }) {
      return apiRequest('/restock-list/items', {
        method: 'POST',
        body: {
          product_id: productId || null,
          custom_name: customName || null,
          custom_image_url: customImageUrl || null,
          branch_id: branchId,
          quantity,
          unit_label: unitLabel,
          is_manual: isManual,
          note: note === undefined ? null : note,
        },
      });
    },
    uploadImage(file) {
      const formData = new FormData();
      formData.append('file', file);
      return apiUpload('/restock-list/upload-image', formData);
    },
    updateItem(entryId, { quantity, unitLabel, note }) {
      return apiRequest(`/restock-list/items/${entryId}`, {
        method: 'PATCH',
        body: { quantity, unit_label: unitLabel, note },
      });
    },
    removeItem(entryId) {
      return apiRequest(`/restock-list/items/${entryId}`, { method: 'DELETE' });
    },
    markReceived(entryId) {
      return apiRequest(`/restock-list/items/${entryId}/received`, { method: 'POST' });
    },
    markSendReceived(sendId) {
      return apiRequest(`/restock-list/sends/${sendId}/received`, { method: 'POST' });
    },
    listRequests(branchId, q) {
      const params = new URLSearchParams({ branch_id: branchId });
      if (q) params.set('q', q);
      return apiRequest(`/restock-list/requests?${params.toString()}`);
    },
    downloadRequest(requestId, requestNo) {
      return apiDownload(`/restock-list/requests/${requestId}/pdf`, `${requestNo}.pdf`);
    },
    deleteRequest(requestId) {
      return apiRequest(`/restock-list/requests/${requestId}`, { method: 'DELETE' });
    },
    async download(entryIds, supplierName) {
      const headers = { 'Content-Type': 'application/json' };
      const token = Auth.getToken();
      if (token) headers.Authorization = `Bearer ${token}`;

      let res;
      try {
        res = await fetch(`${API_BASE}/restock-list/download`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ entry_ids: entryIds, supplier_name: supplierName }),
        });
      } catch (err) {
        throw new ApiError('Could not reach the server. Check that the API is running on 127.0.0.1:8000.', 0, null);
      }

      if (res.status === 401) {
        const outcome = await Auth.trySilentRefresh();
        if (outcome === 'ok') return this.download(entryIds, supplierName);
        Auth.clearSession();
        window.location.href = 'index.html';
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new ApiError((data && data.detail) || 'Download failed.', res.status, null);
      }

      const blob = await res.blob();
      const disposition = res.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = (match && match[1]) || `restock-${(supplierName || 'list').toLowerCase().replace(/\s+/g, '-')}.pdf`;

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
  },

  // ----- Account statements -----
  downloadStatement({ staffId, branchId, dateFrom, dateTo, format = 'csv' } = {}) {
    const params = new URLSearchParams();
    if (staffId) params.set('staff_id', staffId);
    if (branchId) params.set('branch_id', branchId);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    params.set('format', format);
    const qs = params.toString();
    return apiDownload(`/statements/export${qs ? `?${qs}` : ''}`, `statement.${format}`);
  },
};
