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
