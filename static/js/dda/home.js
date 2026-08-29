/** Dashboard (Home) — system-wide overview, region/village breakdowns,
 * change comparison, reports feed, and charts. Replaces the old
 * greeting+3-cards Home page in place. */

function formatHomeDate(iso) {
  return typeof formatDateIst === 'function' ? formatDateIst(iso) : (iso || '—');
}

function dashPct(v) {
  return v != null ? v.toFixed(2) + '%' : '—';
}

function dashDelta(v, suffix) {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${typeof v === 'number' && suffix === '%' ? v.toFixed(2) : v}${suffix}`;
}

const dashCharts = {};

function renderDashChart(canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return;
  if (dashCharts[canvasId]) {
    dashCharts[canvasId].destroy();
  }
  dashCharts[canvasId] = new Chart(canvas, config);
}

async function loadHomeGreeting() {
  const el = document.getElementById('dda-home-greeting');
  if (!el) return;
  try {
    const me = await ddaApi('GET', '/api/me');
    el.textContent = me.full_name ? `Welcome, ${me.full_name}` : 'Welcome';
  } catch (_) {
    // Not authenticated — the page-load redirect to /login should already
    // have caught this; leave the default greeting rather than erroring loudly.
  }
}

let dashSummaryCache = null;

function renderDashKpis(summary) {
  const el = document.getElementById('dda-dash-kpis');
  if (!el) return;
  const tiles = [
    ['Total Detection Runs', summary.totalRuns],
    ['Total Detected Changes', summary.totalDetectedChanges],
    ['Total Unclassified', summary.totalUnclassified],
    ['Avg Change %', dashPct(summary.avgChangePercentage)],
    ['Regions Covered', summary.totalRegions],
    ['Villages Covered', summary.totalVillages],
  ];
  el.innerHTML = tiles.map(([label, value]) => `
    <a class="stat-box dda-dash-kpi" href="/reports" title="Open Reports">
      <span class="value">${escapeHtml(String(value ?? '—'))}</span>
      <span class="label">${escapeHtml(label)}</span>
    </a>`).join('');
}

function renderDashLatest(run) {
  const el = document.getElementById('dda-dash-latest');
  if (!el) return;
  if (!run) {
    el.innerHTML = '<p class="dim">No detection runs yet. Start one from the Change Detection page.</p>';
    return;
  }
  const thumb = run.afterThumbUrl || run.overlayUrl;
  el.innerHTML = `
    <div class="dda-dash-latest-row">
      ${thumb ? `<img class="dda-dash-latest-thumb" src="${escapeHtml(thumb)}" alt="Latest detection thumbnail" />` : ''}
      <div class="dda-dash-latest-info">
        <h4>${escapeHtml(run.title)}</h4>
        <p class="dim">${escapeHtml(run.zone)} &rsaquo; ${escapeHtml(run.village)} · <time datetime="${escapeHtml(run.createdAt || '')}">${formatHomeDate(run.createdAt)}</time></p>
        <div class="dda-dash-latest-stats">
          <span><strong>${dashPct(run.changePercentage)}</strong> change</span>
          <span><strong>${run.regionsCount ?? '—'}</strong> regions</span>
          <span><strong>${run.unclassified ?? '—'}</strong> unclassified</span>
        </div>
        <a class="btn btn-secondary btn-sm" href="/dda/reports/${run.id}" target="_blank" rel="noopener">View Report</a>
      </div>
    </div>`;
}

async function loadDashboardSummary() {
  try {
    const summary = await ddaApi('GET', '/api/dda/dashboard/summary');
    dashSummaryCache = summary;
    const empty = document.getElementById('dda-dash-empty');
    const content = document.getElementById('dda-dash-content');
    if (!summary.totalRuns) {
      empty?.classList.remove('hidden');
      content?.classList.add('hidden');
      return summary;
    }
    empty?.classList.add('hidden');
    content?.classList.remove('hidden');
    renderDashKpis(summary);
    renderDashLatest(summary.latestRun);
    renderDashChart('dda-chart-status', {
      type: 'doughnut',
      data: {
        labels: ['Confirmed', 'False Positive', 'Submitted', 'Unclassified'],
        datasets: [{
          data: [summary.totalConfirmed, summary.totalFalsePositive, summary.totalSubmitted, summary.totalUnclassified],
          backgroundColor: ['#16a34a', '#dc2626', '#6358d4', '#ca8a04'],
        }],
      },
      options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
    });
    return summary;
  } catch (err) {
    document.getElementById('dda-dash-kpis').innerHTML = `<p class="dim">Could not load overview: ${escapeHtml(err.message || 'error')}</p>`;
    return null;
  }
}

function roleLabel(role) {
  const r = String(role || '').trim();
  return r ? r.charAt(0).toUpperCase() + r.slice(1) : 'Unknown';
}

function renderDashRolesTable(roles) {
  const el = document.getElementById('dda-dash-roles');
  if (!el) return;
  if (!roles.length) {
    el.innerHTML = '<p class="dim">No role activity to show yet.</p>';
    return;
  }
  el.innerHTML = `
    <table class="dda-reports-table">
      <thead>
        <tr>
          <th>Role</th>
          <th>Users</th>
          <th>Runs</th>
          <th>Detected Changes</th>
          <th>Unclassified</th>
          <th>Avg Change %</th>
          <th>Last Detection</th>
        </tr>
      </thead>
      <tbody>
        ${roles.map((r) => `
          <tr>
            <td>${escapeHtml(roleLabel(r.role))}</td>
            <td>${r.usersCount}</td>
            <td>${r.runsCount}</td>
            <td>${r.detectedChanges}</td>
            <td>${r.unclassified}</td>
            <td>${dashPct(r.avgChangePercentage)}</td>
            <td><time datetime="${escapeHtml(r.lastDetectionAt || '')}">${formatHomeDate(r.lastDetectionAt)}</time></td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

async function loadDashboardRoles() {
  const el = document.getElementById('dda-dash-roles');
  try {
    const data = await ddaApi('GET', '/api/dda/dashboard/roles');
    const roles = data.roles || [];
    renderDashRolesTable(roles);
    renderDashChart('dda-chart-roles', {
      type: 'bar',
      data: {
        labels: roles.map((r) => roleLabel(r.role)),
        datasets: [{ label: 'Detections', data: roles.map((r) => r.runsCount), backgroundColor: '#16a34a' }],
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    });
  } catch (err) {
    if (el) el.innerHTML = `<p class="dim">Could not load role breakdown: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

let dashRegionsCache = [];
let dashSelectedZone = '';

function renderDashRegionsTable(regions) {
  const el = document.getElementById('dda-dash-regions');
  if (!el) return;
  if (!regions.length) {
    el.innerHTML = '<p class="dim">No regions detected yet.</p>';
    return;
  }
  el.innerHTML = `
    <table class="dda-reports-table">
      <thead>
        <tr>
          <th>Region</th>
          <th>Runs</th>
          <th>Detected Changes</th>
          <th>Unclassified</th>
          <th>Villages</th>
          <th>Avg Change %</th>
          <th>Last Detection</th>
        </tr>
      </thead>
      <tbody>
        ${regions.map((r) => `
          <tr class="dda-dash-row-clickable${r.zone === dashSelectedZone ? ' dda-dash-row-active' : ''}" data-zone="${escapeHtml(r.zone)}">
            <td>${escapeHtml(r.zone)}</td>
            <td>${r.runsCount}</td>
            <td>${r.detectedChanges}</td>
            <td>${r.unclassified}</td>
            <td>${r.villagesCount}</td>
            <td>${dashPct(r.avgChangePercentage)}</td>
            <td><time datetime="${escapeHtml(r.lastDetectionAt || '')}">${formatHomeDate(r.lastDetectionAt)}</time></td>
          </tr>`).join('')}
      </tbody>
    </table>`;
  el.querySelectorAll('[data-zone]').forEach((row) => {
    row.addEventListener('click', () => selectDashRegion(row.dataset.zone));
  });
}

function selectDashRegion(zone) {
  dashSelectedZone = dashSelectedZone === zone ? '' : zone;
  renderDashRegionsTable(dashRegionsCache);
  loadDashboardVillages(dashSelectedZone);
  const label = document.getElementById('dda-dash-village-filter-label');
  const clearBtn = document.getElementById('btn-dash-village-clear');
  if (dashSelectedZone) {
    label.textContent = `Filtered to region: ${dashSelectedZone}`;
    label.classList.remove('hidden');
    clearBtn.classList.remove('hidden');
  } else {
    label.classList.add('hidden');
    clearBtn.classList.add('hidden');
  }
}

async function loadDashboardRegions() {
  try {
    const data = await ddaApi('GET', '/api/dda/dashboard/regions');
    dashRegionsCache = data.regions || [];
    renderDashRegionsTable(dashRegionsCache);
    renderDashChart('dda-chart-regions', {
      type: 'bar',
      data: {
        labels: dashRegionsCache.map((r) => r.zone),
        datasets: [{ label: 'Detections', data: dashRegionsCache.map((r) => r.runsCount), backgroundColor: '#7a6aed' }],
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    });
  } catch (err) {
    document.getElementById('dda-dash-regions').innerHTML = `<p class="dim">Could not load regions: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function renderDashVillagesTable(villages) {
  const el = document.getElementById('dda-dash-villages');
  if (!el) return;
  if (!villages.length) {
    el.innerHTML = '<p class="dim">No villages match this filter.</p>';
    return;
  }
  el.innerHTML = `
    <table class="dda-reports-table">
      <thead>
        <tr>
          <th>Region</th>
          <th>Village</th>
          <th>Runs</th>
          <th>Detected Changes</th>
          <th>Unclassified</th>
          <th>Avg Change %</th>
          <th>Last Detection</th>
        </tr>
      </thead>
      <tbody>
        ${villages.map((v) => `
          <tr class="dda-dash-row-clickable" data-zone="${escapeHtml(v.zone)}" data-village="${escapeHtml(v.village)}">
            <td>${escapeHtml(v.zone)}</td>
            <td>${escapeHtml(v.village)}</td>
            <td>${v.runsCount}</td>
            <td>${v.detectedChanges}</td>
            <td>${v.unclassified}</td>
            <td>${dashPct(v.avgChangePercentage)}</td>
            <td><time datetime="${escapeHtml(v.lastDetectionAt || '')}">${formatHomeDate(v.lastDetectionAt)}</time></td>
          </tr>`).join('')}
      </tbody>
    </table>`;
  el.querySelectorAll('[data-village]').forEach((row) => {
    row.addEventListener('click', () => {
      loadDashboardLocation(row.dataset.zone, row.dataset.village);
      document.getElementById('dda-dash-location-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

async function loadDashboardVillages(zone) {
  const el = document.getElementById('dda-dash-villages');
  if (el) el.innerHTML = '<p class="dim">Loading villages…</p>';
  try {
    const qs = zone ? `?zone=${encodeURIComponent(zone)}` : '';
    const data = await ddaApi('GET', `/api/dda/dashboard/villages${qs}`);
    const villages = data.villages || [];
    renderDashVillagesTable(villages);
    const top = villages.slice(0, 15);
    renderDashChart('dda-chart-villages', {
      type: 'bar',
      data: {
        labels: top.map((v) => v.village),
        datasets: [{ label: 'Detections', data: top.map((v) => v.runsCount), backgroundColor: '#6358d4' }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  } catch (err) {
    if (el) el.innerHTML = `<p class="dim">Could not load villages: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function renderDashLocation(data) {
  const el = document.getElementById('dda-dash-location');
  if (!el) return;
  if (!data.latest) {
    el.innerHTML = '<p class="dim">No detection history for this location yet.</p>';
    return;
  }
  const cmp = data.comparison;
  el.innerHTML = `
    <h4>${escapeHtml(data.zone)} &rsaquo; ${escapeHtml(data.village)}</h4>
    <p class="sub dim">${data.runsCount} detection run${data.runsCount === 1 ? '' : 's'} recorded for this location.</p>
    <div class="dda-dash-compare-grid">
      <div class="dda-dash-compare-col">
        <span class="dim">Current</span>
        <h4>${escapeHtml(data.latest.title)}</h4>
        <p>${dashPct(data.latest.changePercentage)} change · ${data.latest.regionsCount} regions</p>
        <time class="dim" datetime="${escapeHtml(data.latest.createdAt || '')}">${formatHomeDate(data.latest.createdAt)}</time><br/>
        <a class="btn btn-secondary btn-sm" href="/dda/reports/${data.latest.id}" target="_blank" rel="noopener">View Report</a>
      </div>
      <div class="dda-dash-compare-col">
        <span class="dim">Previous</span>
        ${data.previous ? `
          <h4>${escapeHtml(data.previous.title)}</h4>
          <p>${dashPct(data.previous.changePercentage)} change · ${data.previous.regionsCount} regions</p>
          <time class="dim" datetime="${escapeHtml(data.previous.createdAt || '')}">${formatHomeDate(data.previous.createdAt)}</time><br/>
          <a class="btn btn-secondary btn-sm" href="/dda/reports/${data.previous.id}" target="_blank" rel="noopener">View Report</a>
        ` : '<p class="dim">No earlier run to compare against.</p>'}
      </div>
      <div class="dda-dash-compare-col">
        <span class="dim">Change vs previous</span>
        ${cmp ? `
          <p class="dda-dash-delta">${dashDelta(cmp.changePercentageDelta, '%')} change %</p>
          <p class="dda-dash-delta">${dashDelta(cmp.regionsCountDelta, '')} regions</p>
        ` : '<p class="dim">—</p>'}
      </div>
    </div>
    <h4 class="dda-dash-history-title">Detection History</h4>
    <table class="dda-reports-table">
      <thead><tr><th>Date</th><th>Title</th><th>Change %</th><th>Regions</th><th>Unclassified</th><th></th></tr></thead>
      <tbody>
        ${data.history.map((r) => `
          <tr>
            <td><time datetime="${escapeHtml(r.createdAt || '')}">${formatHomeDate(r.createdAt)}</time></td>
            <td>${escapeHtml(r.title)}</td>
            <td>${dashPct(r.changePercentage)}</td>
            <td>${r.regionsCount}</td>
            <td>${r.unclassified}</td>
            <td><a class="btn btn-secondary btn-sm" href="/dda/reports/${r.id}" target="_blank" rel="noopener">Report</a></td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

async function loadDashboardLocation(zone, village) {
  const el = document.getElementById('dda-dash-location');
  if (el) el.innerHTML = '<p class="dim">Loading location history…</p>';
  try {
    const data = await ddaApi('GET', `/api/dda/dashboard/location?zone=${encodeURIComponent(zone)}&village=${encodeURIComponent(village)}`);
    renderDashLocation(data);
  } catch (err) {
    if (el) el.innerHTML = `<p class="dim">Could not load location history: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

async function loadDashboardTrend() {
  try {
    const data = await ddaApi('GET', '/api/dda/dashboard/trend?months=6');
    const buckets = data.buckets || [];
    renderDashChart('dda-chart-trend', {
      type: 'line',
      data: {
        labels: buckets.map((b) => b.label),
        datasets: [{
          label: 'Detection runs',
          data: buckets.map((b) => b.runsCount),
          borderColor: '#7a6aed',
          backgroundColor: 'rgba(122,106,237,0.15)',
          tension: 0.3,
          fill: true,
        }],
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    });
  } catch (_) {
    // Chart stays absent — non-critical widget, other sections still work.
  }
}

async function loadDashboardReports() {
  const el = document.getElementById('dda-dash-reports');
  if (!el) return;
  try {
    const data = await ddaApi('GET', '/api/dda/dashboard/recent-reports?limit=8');
    const rows = data.reports || [];
    if (!rows.length) {
      el.innerHTML = '<p class="dim">No detection reports yet. Run a comparison on the Change Detection page.</p>';
      return;
    }
    el.innerHTML = `
      <table class="dda-reports-table">
        <thead>
          <tr>
            <th>Date (IST)</th>
            <th>Title</th>
            <th>Area</th>
            <th>Change %</th>
            <th>Regions</th>
            <th>Confirmed</th>
            <th>Unclassified</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td><time datetime="${escapeHtml(r.createdAt || '')}">${formatHomeDate(r.createdAt)}</time></td>
              <td>${escapeHtml(r.title)}</td>
              <td>${escapeHtml(r.zone)} &rsaquo; ${escapeHtml(r.village)}</td>
              <td>${dashPct(r.changePercentage)}</td>
              <td>${r.regionsCount}</td>
              <td>${r.confirmed}</td>
              <td>${r.unclassified}</td>
              <td><a class="btn btn-secondary btn-sm" href="/dda/reports/${r.id}" target="_blank" rel="noopener">Report</a></td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load recent reports: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function markDashUpdated() {
  const el = document.getElementById('dda-dash-updated');
  if (!el) return;
  el.textContent = `Updated ${formatHomeDate(new Date().toISOString())}`;
}

async function loadDashboard() {
  dashSelectedZone = '';
  document.getElementById('dda-dash-village-filter-label')?.classList.add('hidden');
  document.getElementById('btn-dash-village-clear')?.classList.add('hidden');
  document.getElementById('dda-dash-location').innerHTML =
    '<p class="dim">Select a village above to compare its latest and previous detection results.</p>';

  const summary = await loadDashboardSummary();
  if (!summary || !summary.totalRuns) {
    markDashUpdated();
    return;
  }
  await Promise.all([
    loadDashboardRoles(),
    loadDashboardRegions(),
    loadDashboardVillages(''),
    loadDashboardTrend(),
    loadDashboardReports(),
  ]);
  markDashUpdated();
}

document.getElementById('btn-dash-refresh')?.addEventListener('click', loadDashboard);
document.getElementById('btn-dash-village-clear')?.addEventListener('click', () => selectDashRegion(''));

document.addEventListener('DOMContentLoaded', () => {
  loadHomeGreeting();
  loadDashboard();
});
