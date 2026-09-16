/** Reports page — jobs, history, filters, PDF export (FR-05). */

function formatReportDate(iso) {
  return typeof formatDateIst === 'function' ? formatDateIst(iso) : (iso || '—');
}

let allReportRows = [];
let reportsPage = 0;
const REPORTS_PER_PAGE = 25;
let reportsTotal = 0;
let reportsServerPaged = true;

function reportFilterState() {
  return {
    q: (document.getElementById('reports-filter')?.value || '').trim().toLowerCase(),
    status: document.getElementById('reports-filter-status')?.value || '',
    source: document.getElementById('reports-filter-source')?.value || '',
    zone: document.getElementById('reports-filter-zone')?.value || '',
    from: document.getElementById('reports-filter-from')?.value || '',
    to: document.getElementById('reports-filter-to')?.value || '',
    sort: document.getElementById('reports-filter-sort')?.value || 'newest',
  };
}

function filteredReportRows() {
  const f = reportFilterState();
  let rows = allReportRows.filter((r) => {
    if (f.q) {
      const hay = `${r.title || ''} ${r.zone || ''} ${r.village || ''} ${r.status || ''}`.toLowerCase();
      if (!hay.includes(f.q)) return false;
    }
    if (f.status && r.status !== f.status) return false;
    if (f.source === 'auto' && !r.autoScheduled) return false;
    if (f.source === 'manual' && r.autoScheduled) return false;
    if (f.zone && (r.zone || '') !== f.zone) return false;
    const day = typeof reportDayKey === 'function' ? reportDayKey(r.createdAt) : (r.createdAt || '').slice(0, 10);
    if (f.from && day && day < f.from) return false;
    if (f.to && day && day > f.to) return false;
    return true;
  });
  rows.sort((a, b) => {
    if (f.sort === 'oldest') return String(a.createdAt || '').localeCompare(String(b.createdAt || ''));
    if (f.sort === 'change-desc') return (b.changePct ?? -1) - (a.changePct ?? -1);
    if (f.sort === 'change-asc') return (a.changePct ?? 1e9) - (b.changePct ?? 1e9);
    if (f.sort === 'title') return String(a.title || '').localeCompare(String(b.title || ''));
    return String(b.createdAt || '').localeCompare(String(a.createdAt || ''));
  });
  return rows;
}

function syncReportZoneOptions() {
  const sel = document.getElementById('reports-filter-zone');
  if (!sel) return;
  const prev = sel.value;
  const zones = [...new Set(allReportRows.map((r) => (r.zone || '').trim()).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All areas</option>' +
    zones.map((z) => `<option value="${escapeHtml(z)}">${escapeHtml(z)}</option>`).join('');
  if (zones.includes(prev)) sel.value = prev;
}

function updateReportsMeta(shown, total) {
  const el = document.getElementById('reports-filter-meta');
  if (!el) return;
  if (!total) {
    el.textContent = '';
    return;
  }
  el.textContent = shown === total
    ? `${total} report${total === 1 ? '' : 's'}`
    : `Showing ${shown} of ${total}`;
}

function renderReportsTable(rows) {
  const el = document.getElementById('reports-list');
  if (!el) return;
  const totalForMeta = reportsServerPaged ? reportsTotal : allReportRows.length;
  updateReportsMeta(rows.length, totalForMeta || rows.length);

  if (!allReportRows.length && !reportsTotal) {
    el.innerHTML = '<p class="dim">No detection reports yet. Run a comparison on the Change Detection page.</p>';
    document.getElementById('reports-pagination')?.replaceChildren();
    return;
  }
  if (!rows.length) {
    el.innerHTML = '<p class="dim">No reports match these filters on this page. Clear filters or try another page.</p>';
    document.getElementById('reports-pagination')?.replaceChildren();
    return;
  }

  // Server already returned one page; client filters apply within that page.
  const pageRows = rows;

  el.innerHTML = `
      <table class="dda-reports-table">
        <thead>
          <tr>
            <th>Date (IST)</th>
            <th>Title</th>
            <th>Area</th>
            <th>Status</th>
            <th>Change %</th>
            <th>Regions</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${pageRows.map((r) => `
            <tr>
              <td><time class="dda-date" datetime="${escapeHtml(r.createdAt || '')}">${formatReportDate(r.createdAt)}</time></td>
              <td>${escapeHtml(r.title)}</td>
              <td>${escapeHtml(r.zone || r.village || '—')}</td>
              <td><span class="dda-status dda-status-${r.status}">${r.autoScheduled ? 'auto · ' : ''}${r.status}</span></td>
              <td>${r.changePct != null ? r.changePct.toFixed(2) + '%' : '—'}</td>
              <td>${r.regions ?? '—'}</td>
              <td class="dda-report-actions-cell">
                ${r.status === 'completed' && r.runId
                  ? `<a class="btn btn-secondary btn-sm" href="/dda/reports/${r.runId}">View</a>
                     <a class="btn btn-secondary btn-sm" href="/dda/reports/${r.runId}" target="_blank" rel="noopener">Report</a>
                     <a class="btn btn-secondary btn-sm" href="/api/dda/reports/${r.runId}/pdf" download>PDF</a>`
                  : (r.error ? `<span class="dim" title="${String(r.error).replace(/"/g, '')}">Error</span>` : '—')}
              </td>
            </tr>`).join('')}
        </tbody>
      </table>`;

  const totalPages = Math.max(1, Math.ceil((reportsTotal || rows.length) / REPORTS_PER_PAGE));
  if (typeof renderPaginationControls === 'function') {
    renderPaginationControls(document.getElementById('reports-pagination'), reportsPage, totalPages, (p) => {
      reportsPage = p;
      loadReportsList({ keepFilters: true });
    });
  }
}

function applyReportsFilter() {
  reportsPage = 0;
  renderReportsTable(filteredReportRows());
}

function clearReportsFilters() {
  const ids = [
    ['reports-filter', ''],
    ['reports-filter-status', ''],
    ['reports-filter-source', ''],
    ['reports-filter-zone', ''],
    ['reports-filter-from', ''],
    ['reports-filter-to', ''],
    ['reports-filter-sort', 'newest'],
  ];
  ids.forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (el) el.value = val;
  });
  applyReportsFilter();
}

function _historyToRows(history) {
  return (history || []).map((r) => ({
    kind: 'run',
    id: r.id,
    runId: r.id,
    title: r.title,
    status: 'completed',
    autoScheduled: String(r.title || '').startsWith('[Auto]'),
    zone: r.zone || '',
    village: r.village || '',
    changePct: r.changePercentage,
    regions: r.regionsCount,
    createdAt: r.createdAt,
  }));
}

function _jobsToRows(jobs) {
  return (jobs || []).map((j) => ({
    kind: 'job',
    id: j.id,
    runId: j.runId,
    title: j.title || `${j.basePath} vs ${j.comparisonPath}`,
    status: j.status,
    autoScheduled: !!j.autoScheduled,
    zone: j.zone || j.report?.zone || '',
    village: j.village || j.report?.village || '',
    changePct: j.report?.changePercentage,
    regions: j.report?.regionsCount,
    createdAt: j.createdAt,
    error: j.errorMessage,
  }));
}

async function loadReportsList(opts = {}) {
  const el = document.getElementById('reports-list');
  if (!el) return;
  if (!opts.keepFilters) reportsPage = 0;
  el.innerHTML = '<p class="dim">Loading reports…</p>';
  try {
    const offset = reportsPage * REPORTS_PER_PAGE;
    const [jobsData, historyPage] = await Promise.all([
      ddaApi('GET', '/api/dda/jobs?limit=25').catch(() => ({ jobs: [] })),
      ddaApi('GET', `/api/history?limit=${REPORTS_PER_PAGE}&offset=${offset}&paged=1`),
    ]);
    const jobs = jobsData.jobs || [];
    const history = historyPage.items || [];
    reportsTotal = historyPage.total != null ? historyPage.total : history.length;
    reportsServerPaged = true;

    const rows = [];
    // Active/recent jobs only on first page so they stay visible without a huge merge.
    if (reportsPage === 0) {
      _jobsToRows(jobs).forEach((row) => {
        if (row.status === 'completed' && row.runId) return;
        rows.push(row);
      });
    }
    _historyToRows(history).forEach((row) => {
      if (rows.some((x) => x.runId === row.runId)) return;
      rows.push(row);
    });

    allReportRows = rows;
    syncReportZoneOptions();
    renderReportsTable(filteredReportRows());
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load reports: ${err.message}</p>`;
  }
}

window.loadReportsList = loadReportsList;
window.applyReportsFilter = applyReportsFilter;

document.getElementById('btn-reports-refresh')?.addEventListener('click', () => loadReportsList());
document.getElementById('btn-reports-clear')?.addEventListener('click', clearReportsFilters);
['reports-filter', 'reports-filter-status', 'reports-filter-source', 'reports-filter-zone',
  'reports-filter-from', 'reports-filter-to', 'reports-filter-sort'].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('input', () => applyReportsFilter());
  el.addEventListener('change', () => applyReportsFilter());
});

if (document.getElementById('reports-list')) {
  loadReportsList();
}
