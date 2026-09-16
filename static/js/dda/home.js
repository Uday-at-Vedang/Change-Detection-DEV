/** Dashboard (Home) — system-wide overview, region/village breakdowns,
 * change comparison, reports feed, and charts. Replaces the old
 * greeting+3-cards Home page in place. */

// ---------------------------------------------------------------- Tabs ----
// Overview / Charts & Graphs / Breakdown / Image Library, one visible at a
// time via small card-style tabs, so the dashboard doesn't force one long
// scroll through every section and sub-section.
const DASH_TABS = ['overview', 'charts', 'breakdown', 'library'];
function activateDashTab(tab) {
  DASH_TABS.forEach((t) => {
    const panel = document.getElementById(`dash-tab-${t}`);
    if (panel) panel.hidden = t !== tab;
  });
  document.querySelectorAll('#dda-dash-tab-switch .dda-dash-tab-card').forEach((card) => {
    const active = card.dataset.tab === tab;
    card.classList.toggle('is-active', active);
    card.setAttribute('aria-selected', String(active));
  });
  // Chart.js lays a canvas out at 0x0 if it was created while its container
  // had `hidden` set; force every existing chart to recompute now that its
  // panel is actually visible. Covers both the Charts & Graphs canvases and
  // the Image Library's format doughnut.
  if (tab === 'charts' || tab === 'library') {
    Object.values(dashCharts).forEach((chart) => {
      try { chart.resize(); } catch (_) { /* chart may have been destroyed/replaced */ }
    });
  }
}
document.querySelectorAll('#dda-dash-tab-switch .dda-dash-tab-card').forEach((card) => {
  card.addEventListener('click', () => activateDashTab(card.dataset.tab));
});

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

// Fixed taxonomy every region is already classified into (see
// app/dda/change_type_map.py) — same colors/order used everywhere a change
// type shows up, so a color always means the same thing across the page.
const CHANGE_TYPE_ORDER = ['New Construction', 'Demolition', 'Extension', 'Vegetation Change', 'Other'];
const CHANGE_TYPE_COLORS = {
  'New Construction': '#f97316',
  'Demolition': '#dc2626',
  'Extension': '#2563eb',
  'Vegetation Change': '#16a34a',
  'Other': '#64748b',
};
function changeTypeSlug(t) {
  return String(t || 'Other').replace(/\s+/g, '-');
}
/** Small colored pill for the single most common change type in a
 * confirmed set of counts — a lightweight way to surface "what kind of
 * change" without adding a full breakdown column to already-dense tables. */
function topChangeTypePill(changeTypes) {
  const entries = Object.entries(changeTypes || {});
  if (!entries.length) return '<span class="dim">—</span>';
  entries.sort((a, b) => b[1] - a[1]);
  const [type, count] = entries[0];
  const total = entries.reduce((sum, [, c]) => sum + c, 0);
  return `<span class="dda-changetype-pill dda-changetype-${changeTypeSlug(type)}" title="${escapeHtml(type)}: ${count} of ${total} region(s)">${escapeHtml(type)}${entries.length > 1 ? ` +${entries.length - 1}` : ''}</span>`;
}

/** Every change type present, each its own pill with a count — used for a
 * single report (Detection History), where "per report" detail matters more
 * than the compact single-pill summary that's appropriate when a row is
 * already an aggregate across several reports (Region-wise/Village-wise). */
function allChangeTypePills(changeTypes) {
  const entries = Object.entries(changeTypes || {});
  if (!entries.length) return '<span class="dim">No regions classified</span>';
  entries.sort((a, b) => b[1] - a[1]);
  return `<div class="dda-changetype-pill-group">${entries.map(([type, count]) =>
    `<span class="dda-changetype-pill dda-changetype-${changeTypeSlug(type)}">${escapeHtml(type)}: ${count}</span>`
  ).join('')}</div>`;
}

/** Stacked bar — one bar per region, one colored segment per change type —
 * the only change-type chart on the dashboard: a separate system-wide
 * doughnut was dropped since its totals are just these bars summed, and
 * showing both read as the same information twice. */
function renderChangeTypesByRegionChart(regions) {
  const top = regions.slice(0, 10);
  renderDashChart('dda-chart-change-types-region', {
    type: 'bar',
    data: {
      labels: top.map((r) => r.zone),
      datasets: CHANGE_TYPE_ORDER
        .filter((t) => top.some((r) => (r.changeTypes || {})[t]))
        .map((t) => ({
          label: t,
          data: top.map((r) => (r.changeTypes || {})[t] || 0),
          backgroundColor: CHANGE_TYPE_COLORS[t],
        })),
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
      scales: {
        x: { stacked: true },
        y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  });
}

const dashCharts = {};

/** Current From/To date filter, as a URLSearchParams-ready query string
 * fragment (without leading '?') — shared by every dashboard fetch except
 * the trend chart, which has its own fixed month window. */
function dashDateParams() {
  const from = document.getElementById('dash-filter-from')?.value || '';
  const to = document.getElementById('dash-filter-to')?.value || '';
  const params = new URLSearchParams();
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);
  return params.toString();
}

function dashUrl(path) {
  const qs = dashDateParams();
  if (!qs) return path;
  return path + (path.includes('?') ? '&' : '?') + qs;
}

/** Every dashboard chart goes through here so animation and hover behavior
 * stay consistent without repeating options at each call site. Pass
 * `onClick(index)` in config to make chart segments clickable — the cursor
 * switches to a pointer only where a click actually does something. */
function renderDashChart(canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return;
  if (dashCharts[canvasId]) {
    dashCharts[canvasId].destroy();
  }
  const onClick = config.onClick;
  delete config.onClick;

  // Rounded, hover-highlighted bars and a pop-out hover on doughnut slices —
  // applied to every dataset that doesn't already set these, so each chart
  // call site doesn't have to repeat the same styling.
  (config.data?.datasets || []).forEach((ds) => {
    if (config.type === 'bar' && ds.borderRadius === undefined) {
      ds.borderRadius = 6;
      ds.borderSkipped = false;
      ds.hoverBackgroundColor = ds.hoverBackgroundColor || _dashHoverShade(ds.backgroundColor);
    }
    if (config.type === 'doughnut' && ds.hoverOffset === undefined) {
      ds.hoverOffset = 14;
    }
    if (config.type === 'line' && ds.pointHoverRadius === undefined) {
      ds.pointRadius = ds.pointRadius ?? 3;
      ds.pointHoverRadius = 7;
    }
  });

  const multiDataset = (config.data?.datasets || []).length > 1;
  const incomingOptions = config.options || {};
  const incomingPlugins = incomingOptions.plugins || {};
  config.options = {
    animation: { duration: 700, easing: 'easeOutQuart' },
    interaction: { intersect: true, mode: 'nearest' },
    ...incomingOptions,
    plugins: {
      ...incomingPlugins,
      tooltip: {
        padding: 10,
        cornerRadius: 8,
        titleFont: { weight: '600' },
        displayColors: config.type === 'doughnut' || multiDataset,
        ...incomingPlugins.tooltip,
      },
    },
  };

  if (onClick) {
    config.options.onClick = (evt, elements) => {
      if (elements.length) onClick(elements[0].index);
    };
    config.options.onHover = (evt, elements) => {
      evt.native.target.style.cursor = elements.length ? 'pointer' : 'default';
    };
  }
  dashCharts[canvasId] = new Chart(canvas, config);
}

/** Darken/lighten a hex color a touch for a bar's hover state — cheap
 * "interactive" affordance without needing a second color per dataset. */
function _dashHoverShade(hex) {
  if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) return hex;
  const amt = -24;
  const num = parseInt(hex.slice(1), 16);
  const clamp = (v) => Math.max(0, Math.min(255, v));
  const r = clamp(((num >> 16) & 0xff) + amt);
  const g = clamp(((num >> 8) & 0xff) + amt);
  const b = clamp((num & 0xff) + amt);
  return `rgb(${r}, ${g}, ${b})`;
}

async function loadHomeGreeting() {
  const el = document.getElementById('dda-home-greeting');
  if (!el) return;
  try {
    const me = typeof ensureDdaMe === 'function' ? await ensureDdaMe() : await ddaApi('GET', '/api/me');
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
    ['Detection Runs', summary.totalRuns],
    ['Regions Detected', summary.totalDetectedChanges],
    ['Confirmed', summary.totalConfirmed],
    ['False Positive', summary.totalFalsePositive],
    ['Submitted', summary.totalSubmitted],
    ['Avg Change %', dashPct(summary.avgChangePercentage)],
    ['Zones Covered', `${summary.totalRegions}${summary.zonesTotal ? `/${summary.zonesTotal}` : ''}`],
    ['Villages Covered', `${summary.totalVillages}${summary.villagesTotal ? `/${summary.villagesTotal}` : ''}`],
  ];
  el.innerHTML = tiles.map(([label, value]) => `
    <a class="stat-box dda-dash-kpi" href="/reports" title="Open Reports">
      <span class="value">${escapeHtml(String(value ?? '—'))}</span>
      <span class="label">${escapeHtml(label)}</span>
    </a>`).join('');
}

/** Ranked list of system-wide change-type totals — same numbers as the
 * "Change Types by Zone" stacked chart, just ranked instead of charted a
 * second time (a duplicate doughnut used to sit here and was dropped). */
function renderRankedChangeTypes(changeTypes) {
  const el = document.getElementById('dda-dash-ranked-changetypes');
  if (!el) return;
  const entries = Object.entries(changeTypes || {});
  if (!entries.length) {
    el.innerHTML = '<p class="dim">No classified changes yet.</p>';
    return;
  }
  entries.sort((a, b) => b[1] - a[1]);
  el.innerHTML = `<div class="dda-dash-ranked-list">${entries.map(([type, count]) => `
    <div class="dda-dash-ranked-row">
      <span class="dda-dash-ranked-swatch" style="background:${CHANGE_TYPE_COLORS[type] || '#64748b'}"></span>
      <span class="dda-dash-ranked-label">${escapeHtml(type)}</span>
      <span class="dda-dash-ranked-count">${count}</span>
    </div>`).join('')}</div>`;
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
        <div class="dda-dash-latest-changetypes">${allChangeTypePills(run.changeTypes)}</div>
        <a class="btn btn-secondary btn-sm" href="/dda/reports/${run.id}" target="_blank" rel="noopener">View Report</a>
      </div>
    </div>`;
}

async function loadDashboardSummary() {
  try {
    const summary = await ddaApi('GET', dashUrl('/api/dda/dashboard/summary'));
    return applyDashboardSummary(summary);
  } catch (err) {
    document.getElementById('dda-dash-kpis').innerHTML = `<p class="dim">Could not load overview: ${escapeHtml(err.message || 'error')}</p>`;
    return null;
  }
}

function applyDashboardSummary(summary) {
  dashSummaryCache = summary;
  const empty = document.getElementById('dda-dash-empty');
  // Only the Overview tab's KPI/Latest-result cards hide behind the empty
  // state — Charts & Graphs, Breakdown, and Image Library stay reachable.
  const overviewGrid = document.getElementById('dda-dash-overview-grid');
  if (!summary.totalRuns) {
    const filtered = !!dashDateParams();
    const heading = empty?.querySelector('h3');
    const body = empty?.querySelector('p');
    const cta = empty?.querySelector('a');
    if (heading) heading.textContent = filtered ? 'No runs in this date range' : 'No detections yet';
    if (body) {
      body.textContent = filtered
        ? 'Try clearing the From/To filter or picking a wider range.'
        : 'Run your first comparison to start populating the dashboard with detection statistics, region/village breakdowns, and reports.';
    }
    if (cta) cta.classList.toggle('hidden', filtered);
    empty?.classList.remove('hidden');
    overviewGrid?.classList.add('hidden');
    return summary;
  }
  empty?.classList.add('hidden');
  overviewGrid?.classList.remove('hidden');
  renderDashKpis(summary);
  renderDashLatest(summary.latestRun);
  renderRankedChangeTypes(summary.changeTypes);
  renderDashChart('dda-chart-status', {
    type: 'doughnut',
    data: {
      labels: ['Confirmed', 'False Positive', 'Submitted', 'Unclassified'],
      datasets: [{
        data: [summary.totalConfirmed, summary.totalFalsePositive, summary.totalSubmitted, summary.totalUnclassified],
        backgroundColor: ['#16a34a', '#dc2626', '#2563eb', '#ca8a04'],
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct = total ? Math.round((ctx.parsed / total) * 100) : 0;
              return ` ${ctx.label}: ${ctx.parsed} (${pct}%)`;
            },
          },
        },
      },
    },
  });
  return summary;
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
    const data = await ddaApi('GET', dashUrl('/api/dda/dashboard/roles'));
    applyDashboardRoles(data.roles || []);
  } catch (err) {
    if (el) el.innerHTML = `<p class="dim">Could not load role breakdown: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function applyDashboardRoles(roles) {
  const el = document.getElementById('dda-dash-roles');
  renderDashRolesTable(roles);
  renderDashChart('dda-chart-roles', {
    type: 'bar',
    data: {
      labels: roles.map((r) => roleLabel(r.role)),
      datasets: [{ label: 'Detections', data: roles.map((r) => r.runsCount), backgroundColor: '#2d2b2b' }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });
}

let dashRegionsCache = [];
let dashSelectedZone = '';

/** Small horizontal stacked bar visualizing Confirmed/False Positive/
 * Submitted/Unclassified as proportions of total regions — used wherever a
 * classification breakdown would otherwise be four more table columns. */
function classificationBar(r) {
  const confirmed = r.confirmed || 0;
  const falsePositive = r.falsePositive || 0;
  const submitted = r.submitted || 0;
  const unclassified = r.unclassified || 0;
  const total = confirmed + falsePositive + submitted + unclassified;
  if (!total) return '<span class="dim">—</span>';
  const seg = (val, cls) => (val > 0
    ? `<span class="dda-class-seg dda-class-${cls}" style="width:${(val / total * 100).toFixed(1)}%"></span>`
    : '');
  const title = `${confirmed} confirmed · ${falsePositive} false positive · ${submitted} submitted · ${unclassified} unclassified`;
  return `
    <div class="dda-class-bar" title="${escapeHtml(title)}">
      ${seg(confirmed, 'confirmed')}${seg(falsePositive, 'fp')}${seg(submitted, 'submitted')}${seg(unclassified, 'unclassified')}
    </div>
    <span class="dim dda-class-bar-caption">${confirmed}/${falsePositive}/${submitted}/${unclassified}</span>`;
}

function accuracyCell(rate) {
  if (rate == null) return '<span class="dim">Not yet reviewed</span>';
  const cls = rate >= 66 ? 'dda-accuracy-good' : rate >= 33 ? 'dda-accuracy-mid' : 'dda-accuracy-low';
  return `<span class="${cls}">${rate.toFixed(1)}%</span>`;
}

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
          <th>Zone</th>
          <th>Runs</th>
          <th>Images</th>
          <th>Detected Changes</th>
          <th>Top Change Type</th>
          <th>Classification (confirmed/FP/submitted/unclassified)</th>
          <th>Accuracy</th>
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
            <td>${r.imageCount ?? 0}</td>
            <td>${r.detectedChanges}</td>
            <td>${topChangeTypePill(r.changeTypes)}</td>
            <td>${classificationBar(r)}</td>
            <td>${accuracyCell(r.confirmedRate)}</td>
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
  dashSelectedVillageKey = '';
  renderDashRegionsTable(dashRegionsCache);
  loadDashboardVillages(dashSelectedZone);
  const label = document.getElementById('dda-dash-village-filter-label');
  const clearBtn = document.getElementById('btn-dash-village-clear');
  if (dashSelectedZone) {
    label.textContent = `Filtered to zone: ${dashSelectedZone}`;
    label.classList.remove('hidden');
    clearBtn.classList.remove('hidden');
  } else {
    label.classList.add('hidden');
    clearBtn.classList.add('hidden');
  }
}

async function loadDashboardRegions() {
  try {
    const data = await ddaApi('GET', dashUrl('/api/dda/dashboard/regions'));
    applyDashboardRegions(data.regions || []);
  } catch (err) {
    document.getElementById('dda-dash-regions').innerHTML = `<p class="dim">Could not load regions: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function applyDashboardRegions(regions) {
  dashRegionsCache = regions;
  renderDashRegionsTable(dashRegionsCache);
  renderDashChart('dda-chart-regions', {
    type: 'bar',
    data: {
      labels: dashRegionsCache.map((r) => r.zone),
      datasets: [{ label: 'Detections', data: dashRegionsCache.map((r) => r.runsCount), backgroundColor: '#ec3013' }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    onClick: (i) => selectDashRegion(dashRegionsCache[i]?.zone),
  });
  renderChangeTypesByRegionChart(dashRegionsCache);
}

let dashSelectedVillageKey = '';

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
          <th>Zone</th>
          <th>Village</th>
          <th>Runs</th>
          <th>Images</th>
          <th>Detected Changes</th>
          <th>Top Change Type</th>
          <th>Classification (confirmed/FP/submitted/unclassified)</th>
          <th>Accuracy</th>
          <th>Avg Change %</th>
          <th>Last Detection</th>
        </tr>
      </thead>
      <tbody>
        ${villages.map((v) => {
          const key = `${v.zone} ${v.village}`;
          return `
          <tr class="dda-dash-row-clickable${key === dashSelectedVillageKey ? ' dda-dash-row-active' : ''}"
              data-zone="${escapeHtml(v.zone)}" data-village="${escapeHtml(v.village)}">
            <td>${escapeHtml(v.zone)}</td>
            <td>${escapeHtml(v.village)}</td>
            <td>${v.runsCount}</td>
            <td>${v.imageCount ?? 0}</td>
            <td>${v.detectedChanges}</td>
            <td>${topChangeTypePill(v.changeTypes)}</td>
            <td>${classificationBar(v)}</td>
            <td>${accuracyCell(v.confirmedRate)}</td>
            <td>${dashPct(v.avgChangePercentage)}</td>
            <td><time datetime="${escapeHtml(v.lastDetectionAt || '')}">${formatHomeDate(v.lastDetectionAt)}</time></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  el.querySelectorAll('[data-village]').forEach((row) => {
    row.addEventListener('click', () => {
      dashSelectedVillageKey = `${row.dataset.zone} ${row.dataset.village}`;
      renderDashVillagesTable(villages);
      loadDashboardLocation(row.dataset.zone, row.dataset.village);
      document.getElementById('dda-dash-location-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

async function loadDashboardVillages(zone) {
  const el = document.getElementById('dda-dash-villages');
  if (el) el.innerHTML = '<p class="dim">Loading villages…</p>';
  try {
    const params = new URLSearchParams(dashDateParams());
    if (zone) params.set('zone', zone);
    const qs = params.toString();
    const data = await ddaApi('GET', `/api/dda/dashboard/villages${qs ? `?${qs}` : ''}`);
    applyDashboardVillages(data.villages || []);
  } catch (err) {
    if (el) el.innerHTML = `<p class="dim">Could not load villages: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function applyDashboardVillages(villages) {
  renderDashVillagesTable(villages);
  if (villages.length && !dashSelectedVillageKey) {
    dashSelectedVillageKey = `${villages[0].zone} ${villages[0].village}`;
    renderDashVillagesTable(villages);
    loadDashboardLocation(villages[0].zone, villages[0].village);
  }
  const top = villages.slice(0, 15);
  renderDashChart('dda-chart-villages', {
    type: 'bar',
    data: {
      labels: top.map((v) => v.village),
      datasets: [{ label: 'Detections', data: top.map((v) => v.runsCount), backgroundColor: '#e15b47' }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
    onClick: (i) => {
      const v = top[i];
      if (!v) return;
      dashSelectedVillageKey = `${v.zone} ${v.village}`;
      renderDashVillagesTable(villages);
      loadDashboardLocation(v.zone, v.village);
      document.getElementById('dda-dash-location-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
  });
}

function dashClassRow(label, r) {
  return `<p class="dda-dash-class-line"><span class="dim">${label}</span> ${r.confirmed}/${r.falsePositive}/${r.submitted}/${r.unclassified} &middot; ${accuracyCell(r.confirmedRate)}</p>`;
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
        <span class="dim">Recent Change Detection</span>
        <h4>${escapeHtml(data.latest.title)}</h4>
        <p>${dashPct(data.latest.changePercentage)} change &middot; ${data.latest.regionsCount} regions</p>
        ${dashClassRow('Confirmed/FP/Submitted/Unclassified', data.latest)}
        <time class="dim" datetime="${escapeHtml(data.latest.createdAt || '')}">${formatHomeDate(data.latest.createdAt)}</time><br/>
        <a class="btn btn-secondary btn-sm" href="/dda/reports/${data.latest.id}" target="_blank" rel="noopener">View Report</a>
      </div>
      <div class="dda-dash-compare-col">
        <span class="dim">Previous Change Detection</span>
        ${data.previous ? `
          <h4>${escapeHtml(data.previous.title)}</h4>
          <p>${dashPct(data.previous.changePercentage)} change &middot; ${data.previous.regionsCount} regions</p>
          ${dashClassRow('Confirmed/FP/Submitted/Unclassified', data.previous)}
          <time class="dim" datetime="${escapeHtml(data.previous.createdAt || '')}">${formatHomeDate(data.previous.createdAt)}</time><br/>
          <a class="btn btn-secondary btn-sm" href="/dda/reports/${data.previous.id}" target="_blank" rel="noopener">View Report</a>
        ` : '<p class="dim">No earlier run to compare against.</p>'}
      </div>
      <div class="dda-dash-compare-col">
        <span class="dim">Recent vs Previous</span>
        ${cmp ? `
          <p class="dda-dash-delta">${dashDelta(cmp.changePercentageDelta, '%')} change %</p>
          <p class="dda-dash-delta">${dashDelta(cmp.regionsCountDelta, '')} regions</p>
          <p class="dda-dash-delta">${dashDelta(cmp.confirmedRateDelta, '%')} accuracy</p>
          <p class="dda-dash-delta">${dashDelta(cmp.falsePositiveDelta, '')} false positives</p>
        ` : '<p class="dim">Run a second detection here to see a comparison.</p>'}
      </div>
    </div>
    ${data.summary ? `<p class="dda-dash-summary">${escapeHtml(data.summary)}</p>` : ''}
    ${data.previous ? '<div class="dda-dash-chart-box dda-dash-compare-chart"><canvas id="dda-chart-location-compare" height="140"></canvas></div>' : ''}
    <h4 class="dda-dash-history-title">Detection History</h4>
    <table class="dda-reports-table">
      <thead><tr><th>Date</th><th>Title</th><th>Change %</th><th>Regions</th><th>Change Types (this report)</th><th>Classification (confirmed/FP/submitted/unclassified)</th><th>Accuracy</th><th></th></tr></thead>
      <tbody>
        ${data.history.map((r) => `
          <tr>
            <td><time datetime="${escapeHtml(r.createdAt || '')}">${formatHomeDate(r.createdAt)}</time></td>
            <td>${escapeHtml(r.title)}</td>
            <td>${dashPct(r.changePercentage)}</td>
            <td>${r.regionsCount}</td>
            <td>${allChangeTypePills(r.changeTypes)}</td>
            <td>${classificationBar(r)}</td>
            <td>${accuracyCell(r.confirmedRate)}</td>
            <td><a class="btn btn-secondary btn-sm" href="/dda/reports/${r.id}" target="_blank" rel="noopener">Report</a></td>
          </tr>`).join('')}
      </tbody>
    </table>`;

  if (data.previous) {
    renderDashChart('dda-chart-location-compare', {
      type: 'bar',
      data: {
        labels: ['Confirmed', 'False Positive', 'Submitted', 'Unclassified'],
        datasets: [
          {
            label: 'Previous',
            data: [data.previous.confirmed, data.previous.falsePositive, data.previous.submitted, data.previous.unclassified],
            backgroundColor: '#d7d3d3',
          },
          {
            label: 'Recent',
            data: [data.latest.confirmed, data.latest.falsePositive, data.latest.submitted, data.latest.unclassified],
            backgroundColor: '#ec3013',
          },
        ],
      },
      options: {
        responsive: true,
        animation: { duration: 500, easing: 'easeOutQuart' },
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }
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
    applyDashboardTrend(data.buckets || []);
  } catch (_) {
    // Chart stays absent — non-critical widget, other sections still work.
  }
}

function applyDashboardTrend(buckets) {
  renderDashChart('dda-chart-trend', {
    type: 'line',
    data: {
      labels: buckets.map((b) => b.label),
      datasets: [{
        label: 'Detection runs',
        data: buckets.map((b) => b.runsCount),
        borderColor: '#ec3013',
        backgroundColor: 'rgba(236,48,19,0.12)',
        tension: 0.3,
        fill: true,
      }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    onClick: (i) => {
      const period = buckets[i]?.period; // "YYYY-MM"
      if (!period) return;
      const [y, m] = period.split('-').map(Number);
      const lastDay = new Date(y, m, 0).getDate();
      const from = document.getElementById('dash-filter-from');
      const to = document.getElementById('dash-filter-to');
      if (from) from.value = `${period}-01`;
      if (to) to.value = `${period}-${String(lastDay).padStart(2, '0')}`;
      loadDashboard();
    },
  });
}

let dashLiveJobTimer = null;

function dashElapsedLabel(startedAtIso) {
  if (!startedAtIso) return '';
  const started = new Date(startedAtIso).getTime();
  if (Number.isNaN(started)) return '';
  const secs = Math.max(0, Math.floor((Date.now() - started) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `started ${m}m ${s}s ago`;
}

/** Banner for a detection job that's actually running right now — real
 * progress/stage/elapsed time from GET /api/dda/jobs, not a placeholder.
 * Polls every 15s while a job is live so it doesn't look stuck, and stops
 * polling itself once nothing is running. */
function renderDashLiveBanner(jobs) {
  const el = document.getElementById('dda-dash-live-banner');
  if (!el) return;
  const running = jobs.filter((j) => j.status === 'running');
  const queued = jobs.filter((j) => j.status === 'queued');
  if (!running.length) {
    el.classList.add('hidden');
    el.innerHTML = '';
    if (dashLiveJobTimer) {
      clearInterval(dashLiveJobTimer);
      dashLiveJobTimer = null;
    }
    return;
  }
  const job = running[0];
  const loc = [job.zone, job.village].filter(Boolean).join(' · ');
  const pct = Math.max(0, Math.min(100, Math.round(job.progressPct || 0)));
  const meta = [job.method, job.progressStage, dashElapsedLabel(job.startedAt)].filter(Boolean).join(' · ');
  el.classList.remove('hidden');
  el.innerHTML = `
    <span class="dda-dash-live-dot"></span>
    <div class="dda-dash-live-info">
      <div class="dda-dash-live-title">Detection job running${loc ? ` — ${escapeHtml(loc)}` : ''}</div>
      <div class="dda-dash-live-meta">${escapeHtml(meta)}</div>
    </div>
    <div class="dda-dash-live-bar"><div class="dda-dash-live-bar-fill" style="width:${pct}%"></div></div>
    ${queued.length ? `<span class="dda-dash-live-queued">${queued.length} queued</span>` : ''}
    <a class="btn btn-ghost btn-sm" href="/detect">View progress</a>`;

  if (!dashLiveJobTimer) {
    dashLiveJobTimer = setInterval(loadDashboardLiveJob, 15000);
  }
}

async function loadDashboardLiveJob() {
  try {
    const jobs = typeof getDdaNotifyJobs === 'function'
      ? await getDdaNotifyJobs()
      : ((await ddaApi('GET', '/api/dda/jobs?limit=20')).jobs || []);
    renderDashLiveBanner((jobs || []).filter((j) => j.status === 'running' || j.status === 'queued'));
  } catch (_) {
    // Non-critical widget — leave banner as it was on a transient failure.
  }
}

function markDashUpdated() {
  const el = document.getElementById('dda-dash-updated');
  if (!el) return;
  el.textContent = `Updated ${formatHomeDate(new Date().toISOString())}`;
}

async function loadDashboardLibraryStats() {
  const kpiEl = document.getElementById('dda-dash-lib-kpis');
  const locEl = document.getElementById('dda-dash-lib-locations');
  try {
    const lib = await ddaApi('GET', '/api/dda/dashboard/library-stats');

    const tiles = [
      ['Total Images', lib.totalImages],
      ['Assigned to a Location', lib.assignedImages],
      ['Unassigned', lib.unassignedImages],
      ['Zones Covered', lib.zonesCovered],
      ['Villages Covered', lib.villagesCovered],
    ];
    if (kpiEl) {
      kpiEl.innerHTML = tiles.map(([label, value]) => `
        <a class="stat-box dda-dash-kpi" href="/library" title="Open Image Library">
          <span class="value">${escapeHtml(String(value ?? '—'))}</span>
          <span class="label">${escapeHtml(label)}</span>
        </a>`).join('');
    }

    renderDashChart('dda-chart-lib-format', {
      type: 'doughnut',
      data: {
        labels: lib.byFormat.map((f) => f.format),
        datasets: [{
          data: lib.byFormat.map((f) => f.count),
          backgroundColor: ['#2d2b2b', '#ec3013', '#d7d3d3', '#2563eb', '#16a34a', '#64748b'],
        }],
      },
      options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
    });

    if (locEl) {
      const zoneRows = lib.byZone.length
        ? lib.byZone.map((z) => `<tr><td>${escapeHtml(z.zone)}</td><td>${z.imageCount}</td></tr>`).join('')
        : '<tr><td colspan="2" class="dim">No images assigned to a zone yet.</td></tr>';
      const villageRows = lib.byVillage.length
        ? lib.byVillage.slice(0, 20).map((v) => `<tr><td>${escapeHtml(v.zone)}</td><td>${escapeHtml(v.village)}</td><td>${v.imageCount}</td></tr>`).join('')
        : '<tr><td colspan="3" class="dim">No images assigned to a village yet.</td></tr>';
      locEl.innerHTML = `
        <div class="dda-dash-overview-grid">
          <table class="dda-reports-table">
            <thead><tr><th>Zone</th><th>Images</th></tr></thead>
            <tbody>${zoneRows}</tbody>
          </table>
          <table class="dda-reports-table">
            <thead><tr><th>Zone</th><th>Village</th><th>Images</th></tr></thead>
            <tbody>${villageRows}</tbody>
          </table>
        </div>
        ${lib.unassignedImages ? `<p class="dim" style="margin-top:0.75rem">${lib.unassignedImages} image(s) have no zone/village tag yet — assign one from the Image Library upload form or an image's detail view.</p>` : ''}`;
    }
  } catch (err) {
    if (kpiEl) kpiEl.innerHTML = `<p class="dim">Could not load library stats: ${escapeHtml(err.message || 'error')}</p>`;
    if (locEl) locEl.innerHTML = '';
  }
}

async function loadDashboard() {
  dashSelectedZone = '';
  dashSelectedVillageKey = '';
  document.getElementById('dda-dash-village-filter-label')?.classList.add('hidden');
  document.getElementById('btn-dash-village-clear')?.classList.add('hidden');
  document.getElementById('dda-dash-location').innerHTML =
    '<p class="dim">Select a village above to compare its latest and previous detection results.</p>';

  // Independent of detection history — the library can (and usually does)
  // have images even with zero runs yet, so this isn't gated behind
  // summary.totalRuns like the sections below it.
  loadDashboardLibraryStats();
  loadDashboardLiveJob();

  try {
    const params = new URLSearchParams(dashDateParams());
    params.set('months', '6');
    const qs = params.toString();
    const data = await ddaApi('GET', `/api/dda/dashboard/bootstrap${qs ? `?${qs}` : ''}`);
    const summary = applyDashboardSummary(data.summary || {});
    if (!summary || !summary.totalRuns) {
      // Charts & Graphs / Breakdown stay reachable via tabs — clear placeholders.
      renderDashRolesTable([]);
      dashRegionsCache = [];
      renderDashRegionsTable([]);
      renderDashVillagesTable([]);
      markDashUpdated();
      return;
    }
    applyDashboardRoles(data.roles || []);
    applyDashboardRegions(data.regions || []);
    applyDashboardVillages(data.villages || []);
    applyDashboardTrend((data.trend && data.trend.buckets) || []);
    markDashUpdated();
  } catch (err) {
    document.getElementById('dda-dash-kpis').innerHTML = `<p class="dim">Could not load overview: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

document.getElementById('btn-dash-refresh')?.addEventListener('click', loadDashboard);
document.getElementById('btn-dash-village-clear')?.addEventListener('click', () => selectDashRegion(''));

// Date filter applies on an explicit Search click, not on every date pick —
// picking From then To would otherwise fire two reloads back to back.
document.getElementById('btn-dash-filter-apply')?.addEventListener('click', loadDashboard);
document.getElementById('btn-dash-filter-clear')?.addEventListener('click', () => {
  const from = document.getElementById('dash-filter-from');
  const to = document.getElementById('dash-filter-to');
  if (from) from.value = '';
  if (to) to.value = '';
  loadDashboard();
});

document.addEventListener('DOMContentLoaded', () => {
  loadHomeGreeting();
  loadDashboard();
});
