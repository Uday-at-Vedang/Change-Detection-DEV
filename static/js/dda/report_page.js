/** Standalone report page at /dda/reports/{runId} — interactive viewer in-window. */

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

async function loadReportPage() {
  const runId = parseReportRunId();
  const loading = document.getElementById('report-loading');
  if (!runId) {
    if (loading) loading.innerHTML = '<p class="dim">Invalid report URL.</p>';
    return;
  }

  try {
    if (typeof loadMyPermissions === 'function') {
      window.ddaPermissions = await loadMyPermissions();
      if (typeof applyPermissionGating === 'function') applyPermissionGating(window.ddaPermissions);
    }
    const data = await ddaApi('GET', `/api/history/${runId}`);
    if (loading) loading.classList.add('hidden');

    const pdfBtn = document.getElementById('report-pdf-btn');
    if (pdfBtn) {
      pdfBtn.disabled = false;
      pdfBtn.onclick = () => { window.location.href = `/api/dda/reports/${runId}/pdf`; };
    }

    if (typeof showDdaResult === 'function') {
      showDdaResult(data);
      const beforeImg = document.getElementById('compare-before-img');
      const afterImg = document.getElementById('compare-after-img');
      const waitLoad = (img) => new Promise((resolve) => {
        if (!img) return resolve();
        if (img.complete && img.naturalWidth) return resolve();
        img.addEventListener('load', () => resolve(), { once: true });
        img.addEventListener('error', () => resolve(), { once: true });
        setTimeout(resolve, 8000);
      });
      await Promise.all([waitLoad(beforeImg), waitLoad(afterImg)]);
      requestAnimationFrame(() => {
        if (typeof applyDdaZoom === 'function') applyDdaZoom();
      });
    } else {
      showReportError('Interactive viewer failed to load.');
    }
  } catch (err) {
    if (loading) loading.classList.add('hidden');
    showReportError(err.message || 'Could not load report.');
  }
}

loadReportPage();
