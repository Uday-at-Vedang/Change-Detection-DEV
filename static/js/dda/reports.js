/** Reports tab — detection jobs, history, PDF export (FR-05). */

function formatReportDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso;
  }
}

async function loadReportsList() {
  const el = document.getElementById('reports-list');
  if (!el) return;
  el.innerHTML = '<p class="dim">Loading reports…</p>';
  try {
    let jobs = [];
    try {
      const jobsData = await ddaApi('GET', '/api/dda/jobs?limit=30');
      jobs = jobsData.jobs || [];
    } catch (_) {
      /* jobs API optional — history still works */
    }
    const history = await ddaApi('GET', '/api/history');
    const rows = [];

    jobs.forEach((j) => {
      rows.push({
        kind: 'job',
        id: j.id,
        runId: j.runId,
        title: j.title || `${j.basePath} vs ${j.comparisonPath}`,
        status: j.status,
        changePct: j.report?.changePercentage,
        regions: j.report?.regionsCount,
        createdAt: j.createdAt,
        error: j.errorMessage,
      });
    });

    (history || []).forEach((r) => {
      if (rows.some((x) => x.runId === r.id)) return;
      rows.push({
        kind: 'run',
        id: r.id,
        runId: r.id,
        title: r.title,
        status: 'completed',
        changePct: r.changePercentage,
        regions: r.regionsCount,
        createdAt: r.createdAt,
      });
    });

    rows.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));

    if (!rows.length) {
      el.innerHTML = '<p class="dim">No detection reports yet. Run a comparison on the Change Detection tab.</p>';
      return;
    }

    el.innerHTML = `
      <table class="dda-reports-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Title</th>
            <th>Status</th>
            <th>Change %</th>
            <th>Regions</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${formatReportDate(r.createdAt)}</td>
              <td>${escapeHtml(r.title)}</td>
              <td><span class="dda-status dda-status-${r.status}">${r.status}</span></td>
              <td>${r.changePct != null ? r.changePct.toFixed(2) + '%' : '—'}</td>
              <td>${r.regions ?? '—'}</td>
              <td class="dda-report-actions-cell">
                ${r.status === 'completed' && r.runId
                  ? `<button type="button" class="btn btn-secondary btn-sm" data-view-run="${r.runId}">View</button>
                     <a class="btn btn-secondary btn-sm" href="/dda/reports/${r.runId}" target="_blank" rel="noopener">Report</a>
                     <a class="btn btn-secondary btn-sm" href="/api/dda/reports/${r.runId}/pdf" download>PDF</a>`
                  : (r.error ? `<span class="dim" title="${String(r.error).replace(/"/g, '')}">Error</span>` : '—')}
              </td>
            </tr>`).join('')}
        </tbody>
      </table>`;

    el.querySelectorAll('[data-view-run]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const runId = btn.dataset.viewRun;
        try {
          const data = await ddaApi('GET', `/api/history/${runId}`);
          if (typeof showDdaResult === 'function') showDdaResult(data);
        } catch (err) {
          if (typeof showDdaError === 'function') showDdaError(err.message);
        }
      });
    });
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load reports: ${err.message}</p>`;
  }
}

window.loadReportsList = loadReportsList;

document.getElementById('btn-reports-refresh')?.addEventListener('click', loadReportsList);

document.querySelectorAll('.dda-tab').forEach((btn) => {
  if (btn.dataset.tab === 'reports') {
    btn.addEventListener('click', () => loadReportsList());
  }
});

document.addEventListener('DOMContentLoaded', () => {
  try {
    const openRun = sessionStorage.getItem('dda_open_run');
    if (openRun) {
      sessionStorage.removeItem('dda_open_run');
      document.querySelector('.dda-tab[data-tab="reports"]')?.click();
      setTimeout(async () => {
        try {
          const data = await ddaApi('GET', `/api/history/${openRun}`);
          if (typeof showDdaResult === 'function') showDdaResult(data);
        } catch (_) {}
      }, 300);
    }
  } catch (_) {}

  if (document.getElementById('tab-reports')?.classList.contains('active')) {
    loadReportsList();
  }
});
