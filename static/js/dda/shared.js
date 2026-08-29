// Generic helpers shared by the main SPA (app.js) and standalone pages
// (login_dda.html). Date-picker enhancement is the only DOM init here and
// is a no-op when no .dda-date-input fields exist.
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

/** Calendar day in the viewer's local timezone, YYYY-MM-DD. */
function reportDayKey(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Dates shown in IST so reports match the schedule clock. */
function formatDateIst(iso, { withTime = true } = {}) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const opts = withTime
    ? { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Kolkata' }
    : { dateStyle: 'medium', timeZone: 'Asia/Kolkata' };
  try {
    const formatted = new Intl.DateTimeFormat(undefined, opts).format(d);
    return withTime ? `${formatted} IST` : formatted;
  } catch (_) {
    return d.toLocaleString();
  }
}

window.formatDateIst = formatDateIst;
window.reportDayKey = reportDayKey;

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

const _DATE_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const _DATE_MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const _DATE_DOW = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

function _parseIsoDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (d.getFullYear() !== Number(m[1]) || d.getMonth() !== Number(m[2]) - 1 || d.getDate() !== Number(m[3])) return null;
  return d;
}

function _fmtIsoDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function _fmtPrettyDate(iso) {
  const d = _parseIsoDate(iso);
  if (!d) return '';
  return `${d.getDate()} ${_DATE_MONTHS_SHORT[d.getMonth()]} ${d.getFullYear()}`;
}

function _closeAllDatePickers(except) {
  document.querySelectorAll('.dda-datewrap.is-open').forEach((wrap) => {
    if (wrap !== except) wrap.classList.remove('is-open');
  });
}

function enhanceDateInput(input) {
  if (!input || input.dataset.dateEnhanced === '1') return;
  if (input.closest('.dda-datewrap')) return;
  input.dataset.dateEnhanced = '1';

  const wrap = document.createElement('div');
  wrap.className = 'dda-datewrap';
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  input.classList.add('dda-date-native');
  input.setAttribute('autocomplete', 'off');

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'dda-date-trigger';
  trigger.setAttribute('aria-haspopup', 'dialog');
  wrap.appendChild(trigger);

  const pop = document.createElement('div');
  pop.className = 'dda-cal';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Choose date');
  wrap.appendChild(pop);

  let view = _parseIsoDate(input.value) || new Date();
  view = new Date(view.getFullYear(), view.getMonth(), 1);

  function syncTrigger() {
    const pretty = _fmtPrettyDate(input.value);
    trigger.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>
      <span class="${pretty ? '' : 'dda-date-placeholder'}">${pretty || (input.getAttribute('placeholder') || 'Select date')}</span>
      ${input.value ? '<span class="dda-date-clear" title="Clear" aria-label="Clear date">×</span>' : ''}`;
  }

  function renderCal() {
    const selected = _parseIsoDate(input.value);
    const today = new Date();
    const year = view.getFullYear();
    const month = view.getMonth();
    const first = new Date(year, month, 1);
    const startDow = (first.getDay() + 6) % 7;
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    let cells = '';
    for (let i = 0; i < startDow; i++) cells += '<span class="dda-cal-day is-empty"></span>';
    for (let day = 1; day <= daysInMonth; day++) {
      const iso = _fmtIsoDate(new Date(year, month, day));
      const isSel = selected && selected.getFullYear() === year && selected.getMonth() === month && selected.getDate() === day;
      const isToday = today.getFullYear() === year && today.getMonth() === month && today.getDate() === day;
      cells += `<button type="button" class="dda-cal-day${isSel ? ' is-selected' : ''}${isToday ? ' is-today' : ''}" data-date="${iso}">${day}</button>`;
    }
    pop.innerHTML = `
      <div class="dda-cal-head">
        <button type="button" class="dda-cal-nav" data-cal-nav="-1" aria-label="Previous month">‹</button>
        <span class="dda-cal-title">${_DATE_MONTHS[month]} ${year}</span>
        <button type="button" class="dda-cal-nav" data-cal-nav="1" aria-label="Next month">›</button>
      </div>
      <div class="dda-cal-dow">${_DATE_DOW.map((d) => `<span>${d}</span>`).join('')}</div>
      <div class="dda-cal-grid">${cells}</div>
      <div class="dda-cal-foot">
        <button type="button" class="dda-cal-today" data-cal-today>Today</button>
      </div>`;
  }

  function openCal() {
    const cur = _parseIsoDate(input.value);
    view = new Date((cur || new Date()).getFullYear(), (cur || new Date()).getMonth(), 1);
    renderCal();
    _closeAllDatePickers(wrap);
    wrap.classList.add('is-open');
  }

  function setValue(iso) {
    input.value = iso || '';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    syncTrigger();
    wrap.classList.remove('is-open');
  }

  trigger.addEventListener('click', (e) => {
    if (e.target.closest('.dda-date-clear')) {
      e.preventDefault();
      e.stopPropagation();
      setValue('');
      return;
    }
    if (wrap.classList.contains('is-open')) wrap.classList.remove('is-open');
    else openCal();
  });

  pop.addEventListener('click', (e) => {
    const nav = e.target.closest('[data-cal-nav]');
    if (nav) {
      view = new Date(view.getFullYear(), view.getMonth() + Number(nav.dataset.calNav), 1);
      renderCal();
      return;
    }
    if (e.target.closest('[data-cal-today]')) {
      setValue(_fmtIsoDate(new Date()));
      return;
    }
    const day = e.target.closest('[data-date]');
    if (day) setValue(day.dataset.date);
  });

  input.addEventListener('change', syncTrigger);
  syncTrigger();
}

function initDatePickers(root = document) {
  root.querySelectorAll('input.dda-date-input[type="date"]').forEach(enhanceDateInput);
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.dda-datewrap')) _closeAllDatePickers();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') _closeAllDatePickers();
});
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => initDatePickers());
} else {
  initDatePickers();
}
window.initDatePickers = initDatePickers;
window.enhanceDateInput = enhanceDateInput;
