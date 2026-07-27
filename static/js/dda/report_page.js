/** Standalone report page at /dda/reports/{runId} (FR-05). */

function parseReportRunId() {
  const m = window.location.pathname.match(/\/dda\/reports\/(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

function showReportError(msg) {
  const el = document.getElementById('report-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}

function formatCoord(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(5) : '—';
}

function regionLatLng(r) {
  const ll = r.latLng || {};
  const lat = r.latitude ?? ll.lat;
  const lng = r.longitude ?? ll.lng;
  return { lat, lng };
}

async function loadReportPage() {
  const runId = parseReportRunId();
  const loading = document.getElementById('report-loading');
  const content = document.getElementById('report-content');
  if (!runId) {
    if (loading) loading.innerHTML = '<p class="dim">Invalid report URL.</p>';
    return;
  }

  try {
    const data = await ddaApi('GET', `/api/dda/reports/${runId}`);
    if (loading) loading.classList.add('hidden');
    if (content) content.classList.remove('hidden');

    document.getElementById('report-title').textContent = data.title || `Run #${runId}`;
    const loc = [data.village, data.zone].filter(Boolean).join(', ') || '—';
    document.getElementById('report-meta').textContent =
      `${data.method || '—'} · ${loc} · ${data.createdAt || ''}`;

    const stats = data.statistics || {};
    document.getElementById('report-stats').innerHTML = `
      <div class="dda-stat"><span class="label">Change</span><span class="value">${(stats.changePercentage ?? 0).toFixed(2)}%</span></div>
      <div class="dda-stat"><span class="label">Changed px</span><span class="value">${(stats.changedPixels ?? 0).toLocaleString()}</span></div>
      <div class="dda-stat"><span class="label">Regions</span><span class="value">${data.regionsCount ?? (data.regions || []).length}</span></div>`;

    const overlay = document.getElementById('report-overlay');
    if (overlay) {
      if (data.overlayBase64Png) {
        overlay.src = `data:image/png;base64,${data.overlayBase64Png}`;
      } else if (data.overlayUrl) {
        overlay.src = data.overlayUrl;
      } else {
        overlay.alt = 'No overlay available';
      }
    }

    const tbody = document.getElementById('report-regions-body');
    const regions = data.regions || [];
    if (tbody) {
      tbody.innerHTML = regions.length
        ? regions.map((r) => {
            const { lat, lng } = regionLatLng(r);
            const hasCoords = Number.isFinite(Number(lat)) && Number.isFinite(Number(lng));
            const mapsUrl = hasCoords
              ? `https://www.google.com/maps?q=${lat},${lng}&ll=${lat},${lng}&z=19`
              : null;
            return `
            <tr>
              <td>${r.id ?? ''}</td>
              <td>${r.ddaChangeType || r.objectType || '—'}</td>
              <td>${r.internalObjectType || r.objectType || '—'}</td>
              <td>${((r.confidence ?? 0) * 100).toFixed(0)}%</td>
              <td>${(r.area ?? 0).toLocaleString()}</td>
              <td>${formatCoord(lat)}</td>
              <td>${formatCoord(lng)}</td>
              <td>${mapsUrl ? `<a class="btn btn-secondary btn-sm" href="${mapsUrl}" target="_blank" rel="noopener">Map</a>` : '—'}</td>
            </tr>`;
          }).join('')
        : '<tr><td colspan="8" class="dim">No regions detected.</td></tr>';
    }

    const pdfBtn = document.getElementById('report-pdf-btn');
    if (pdfBtn) {
      pdfBtn.disabled = false;
      pdfBtn.onclick = () => { window.location.href = `/api/dda/reports/${runId}/pdf`; };
    }

    const viewBtn = document.getElementById('report-view-btn');
    if (viewBtn) {
      viewBtn.disabled = false;
      viewBtn.onclick = () => {
        try { sessionStorage.setItem('dda_open_run', String(runId)); } catch (_) {}
        window.location.href = '/?tab=reports';
      };
    }

    window._reportPageData = data;
  } catch (err) {
    if (loading) loading.classList.add('hidden');
    showReportError(err.message || 'Could not load report.');
  }
}

document.getElementById('report-email-btn')?.addEventListener('click', async () => {
  const runId = parseReportRunId();
  const input = document.getElementById('report-email');
  const msg = document.getElementById('report-email-msg');
  const email = (input?.value || '').trim();
  if (!email) {
    if (msg) msg.textContent = 'Enter an email address.';
    return;
  }
  try {
    const res = await ddaApi('POST', `/api/dda/reports/${runId}/notify`, {
      body: JSON.stringify({ email }),
    });
    if (msg) msg.textContent = res.message || 'Sent.';
  } catch (err) {
    if (msg) msg.textContent = err.message || 'Send failed.';
  }
});

loadReportPage();
