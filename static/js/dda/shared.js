// Generic helpers shared by the main SPA (app.js) and standalone pages
// (login_dda.html). Deliberately has NO page-load side effects — safe to
// include on any page. Page-specific initialization (e.g. app.js's
// initDda()) must NOT live here.
const API = '';

function escapeHtml(text) {
  if (text == null) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatApiError(detail) {
  if (!detail) return null;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && d.msg) || JSON.stringify(d)).join('; ');
  }
  return String(detail);
}

async function ddaApi(method, path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API + path, { method, headers, credentials: 'include', ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!res.ok) throw new Error(formatApiError(data?.detail) || res.statusText || 'Request failed');
  return data;
}

function showDdaError(msg) {
  const el = document.getElementById('dda-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}
function hideDdaError() {
  document.getElementById('dda-error')?.classList.add('hidden');
}
function showDdaSuccess(msg) {
  const el = document.getElementById('dda-success');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

function formatBytes(n) {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
}

// --- Dynamic role permissions (drives show/hide/disable in the UI so it
// always matches whatever's set in the Roles & Users permission matrix,
// without hardcoding any role name in a template or JS file). ---

let _ddaPermissionsPromise = null;

/** Fetch (once, cached for the page's lifetime) the current user's
 * per-module permissions: { moduleKey: { view, create, edit, delete } }.
 * Call this during page init, before hasPermission()/applyPermissionGating()
 * are used. Resolves to {} (nothing permitted) if the request fails, rather
 * than throwing — a permission check should fail closed, not break the page. */
function loadMyPermissions() {
  if (!_ddaPermissionsPromise) {
    _ddaPermissionsPromise = ddaApi('GET', '/api/dda/rbac/me/permissions').catch(() => ({}));
  }
  return _ddaPermissionsPromise;
}

/** Sync check against an already-resolved permissions map (from
 * loadMyPermissions()). */
function hasPermission(permissions, moduleKey, action) {
  return !!(permissions && permissions[moduleKey] && permissions[moduleKey][action]);
}

/** Declaratively hides/disables every element carrying
 * data-requires-permission="moduleKey:action" based on the resolved
 * permissions map, e.g. <button data-requires-permission="detect:create">.
 * By default the element is hidden when not permitted; add
 * data-permission-mode="disable" to keep it visible but disabled instead
 * (for controls where it should stay clear the feature exists). Safe to
 * call again after re-rendering a list/table to gate newly-added elements. */
function applyPermissionGating(permissions, root = document) {
  root.querySelectorAll('[data-requires-permission]').forEach((el) => {
    const [moduleKey, action] = el.getAttribute('data-requires-permission').split(':');
    const allowed = hasPermission(permissions, moduleKey, action);
    if (el.getAttribute('data-permission-mode') === 'disable') {
      // Only ever force it OFF for a denied permission — never force it back
      // ON, since the element may also be legitimately disabled by other
      // page logic (e.g. "no pair selected yet") that this must not override.
      if (!allowed) {
        el.disabled = true;
        el.classList.add('perm-disabled');
        el.title = el.getAttribute('data-permission-denied-title') || "You don't have permission for this action.";
      }
    } else {
      // Reuses the existing global .hidden utility (style.css) rather than a
      // separate class — plays correctly with any other code that also
      // toggles 'hidden' on the same element (e.g. an admin-only button).
      el.classList.toggle('hidden', !allowed);
    }
  });
}

/** Shared prev/numbered/next pagination control — same markup/behavior as
 * the region-review table's pagination (result.js), reused by any list/grid
 * that needs paging instead of an internal scrollbar. */
function renderPaginationControls(container, page, totalPages, onChange) {
  if (!container) return;
  container.innerHTML = '';
  if (totalPages <= 1) return;

  const prev = document.createElement('button');
  prev.type = 'button';
  prev.textContent = '‹';
  prev.disabled = page === 0;
  prev.addEventListener('click', () => onChange(page - 1));
  container.appendChild(prev);

  for (let i = 0; i < totalPages; i++) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = String(i + 1);
    if (i === page) btn.classList.add('active');
    btn.addEventListener('click', () => onChange(i));
    container.appendChild(btn);
  }

  const next = document.createElement('button');
  next.type = 'button';
  next.textContent = '›';
  next.disabled = page >= totalPages - 1;
  next.addEventListener('click', () => onChange(page + 1));
  container.appendChild(next);
}
