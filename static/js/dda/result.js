/** DDA result modal — compare slider, view modes, region review (FR-07, FR-08). */

let ddaCurrentResult = null;
let ddaRegionRows = [];
let ddaRegionList = [];
let ddaRegionPage = 0;
let ddaSelectedRegionId = null;
let ddaViewMode = 'slider';
let ddaViewUrls = { before: '', after: '', overlay: '' };
const DDA_REGIONS_PER_PAGE = 10;
let ddaZoom = 1;
const DDA_ZOOM_MIN = 0.5;
const DDA_ZOOM_MAX = 3;
const DDA_ZOOM_STEP = 0.25;

function formatCompact(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 10_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
  return n.toLocaleString();
}

function reviewBadgeClass(status) {
  const s = (status || 'pending').toLowerCase();
  return `review-badge review-${s.replace('_', '-')}`;
}

function setDdaViewMode(mode) {
  ddaViewMode = mode;
  document.querySelectorAll('.dda-view-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === mode);
  });
  const slider = document.getElementById('compare-slider');
  const handle = document.getElementById('compare-handle');
  const beforeImg = document.getElementById('compare-before-img');
  const afterImg = document.getElementById('compare-after-img');
  const afterClip = document.getElementById('compare-after-clip');
  if (!slider || !beforeImg || !afterImg) return;

  slider.classList.remove('dda-mode-t1', 'dda-mode-t2', 'dda-mode-overlay', 'dda-mode-slider');
  slider.classList.add(`dda-mode-${mode}`);

  const overlaySrc = ddaViewUrls.overlay;
  const beforeSrc = ddaViewUrls.before;
  const afterSrc = ddaViewUrls.after || overlaySrc;

  if (mode === 'slider') {
    beforeImg.src = beforeSrc;
    afterImg.src = overlaySrc;
    if (afterClip) afterClip.style.clipPath = 'inset(0 0 0 50%)';
    if (handle) { handle.style.display = ''; handle.style.left = '50%'; }
  } else if (mode === 't1') {
    beforeImg.src = beforeSrc;
    afterImg.src = overlaySrc;
    if (afterClip) afterClip.style.clipPath = 'inset(0 0 0 100%)';
    if (handle) handle.style.display = 'none';
  } else if (mode === 't2') {
    beforeImg.src = beforeSrc;
    afterImg.src = afterSrc;
    if (afterClip) afterClip.style.clipPath = 'inset(0 0 0 0%)';
    if (handle) handle.style.display = 'none';
  } else if (mode === 'overlay') {
    beforeImg.src = beforeSrc;
    afterImg.src = overlaySrc;
    if (afterClip) afterClip.style.clipPath = 'inset(0 0 0 0%)';
    if (handle) handle.style.display = 'none';
  }
}

function updateDdaReviewSummary(regions) {
  const el = document.getElementById('dda-review-summary');
  if (!el) return;
  const counts = { pending: 0, confirmed: 0, false_positive: 0, submitted: 0 };
  (regions || []).forEach((r) => {
    const s = r.reviewStatus || 'pending';
    counts[s] = (counts[s] || 0) + 1;
  });
  el.textContent = `Review: ${counts.confirmed} confirmed · ${counts.false_positive} false positive · ${counts.submitted || 0} submitted · ${counts.pending} pending`;
}

function setupDdaReviewBar(runId, regions) {
  const exportAll = document.getElementById('dda-export-csv');
  const exportConfirmed = document.getElementById('dda-export-confirmed');
  const submitBtn = document.getElementById('dda-submit-dept');
  if (exportAll) exportAll.href = `/api/dda/reports/${runId}/export.csv`;
  if (exportConfirmed) exportConfirmed.href = `/api/dda/reports/${runId}/export.csv?confirmed=1`;
  updateDdaReviewSummary(regions);

  submitBtn?.replaceWith(submitBtn.cloneNode(true));
  document.getElementById('dda-submit-dept')?.addEventListener('click', async () => {
    try {
      const res = await ddaApi('POST', `/api/dda/reports/${runId}/submit`);
      if (typeof showDdaSuccess === 'function') showDdaSuccess(res.message || 'Submitted.');
      if (res.downloadUrl && res.mode === 'file') {
        window.open(res.downloadUrl, '_blank');
      }
      const data = await ddaApi('GET', `/api/history/${runId}`);
      showDdaResult(data);
    } catch (err) {
      if (typeof showDdaError === 'function') showDdaError(err.message || 'Submit failed');
    }
  });
}

async function patchRegionReview(runId, regionId, reviewStatus) {
  const res = await ddaApi('PATCH', `/api/dda/reports/${runId}/regions/${regionId}`, {
    body: JSON.stringify({ reviewStatus }),
  });
  return res.region;
}

function showDdaResult(data) {
  const modal = document.getElementById('result-modal');
  const statsEl = document.getElementById('result-stats');
  const titleEl = document.getElementById('result-modal-title');
  if (!modal || !statsEl) return;

  ddaCurrentResult = data;
  ddaSelectedRegionId = null;
  document.getElementById('dda-locate-btn')?.setAttribute('disabled', 'disabled');

  if (titleEl) titleEl.textContent = data.title || 'Detection Result';

  const stats = data.statistics || {};
  const pct = (stats.changePercentage ?? 0).toFixed(2);
  const chPx = stats.changedPixels ?? 0;
  const totPx = stats.totalPixels ?? 0;
  const regOk = stats.registrationOk;
  const alignWarn = stats.alignmentWarning;
  const thrDbg = stats.thresholdDebug || {};
  const fusionPx = thrDbg.fused_changed_px != null
    ? `DL ${thrDbg.dl_changed_px ?? '—'} / fused ${thrDbg.fused_changed_px}`
    : '';
  const tileSkip = thrDbg.tileSkip;
  const tileSkipHtml = (tileSkip && tileSkip.totalTiles)
    ? `<div class="stat-box"><div class="value value-sm">${tileSkip.skippedTiles}/${tileSkip.totalTiles}</div><div class="label">Tiles skipped</div></div>`
    : '';

  let warnHtml = '';
  if (alignWarn) {
    warnHtml = `<div class="result-warning" role="alert">${alignWarn}</div>`;
  } else if (regOk === false) {
    warnHtml = '<div class="result-warning" role="alert">Image alignment was weak — results may include false detections.</div>';
  }

  const resHint = data.detectionMaxSide
    ? `<div class="stat-box"><div class="value value-sm">${data.detectionMaxSide}px</div><div class="label">Detection res</div></div>`
    : '';

  statsEl.innerHTML = warnHtml + `
    <div class="stat-box"><div class="value">${pct}%</div><div class="label">Changed</div></div>
    <div class="stat-box"><div class="value" title="${chPx.toLocaleString()}">${formatCompact(chPx)}</div><div class="label">Changed px</div></div>
    <div class="stat-box"><div class="value" title="${totPx.toLocaleString()}">${formatCompact(totPx)}</div><div class="label">Total px</div></div>
    <div class="stat-box"><div class="value">${(data.regions || []).length}</div><div class="label">Regions</div></div>
    ${resHint}
    ${fusionPx ? `<div class="stat-box stat-box-wide"><div class="value value-sm">${fusionPx}</div><div class="label">Fusion px</div></div>` : ''}
    <div class="stat-box"><div class="value value-sm">${regOk === true ? 'OK' : regOk === false ? 'Weak' : '—'}</div><div class="label">Alignment</div></div>
    ${tileSkipHtml}
  `;

  const overlaySrc = data.overlayBase64Png
    ? 'data:image/png;base64,' + data.overlayBase64Png
    : (data.overlayUrl || '');
  const beforeSrc = data.beforeFullUrl || data.beforeThumbUrl || '';
  const afterSrc = data.afterFullUrl || data.afterThumbUrl || beforeSrc;

  ddaViewUrls = { before: beforeSrc, after: afterSrc, overlay: overlaySrc };
  setDdaViewMode('slider');

  const beforeImg = document.getElementById('compare-before-img');
  const afterImg = document.getElementById('compare-after-img');
  if (!beforeImg || !afterImg) return;

  let loaded = 0;
  const onReady = () => {
    if (++loaded >= 2) {
      resetDdaCompareSlider();
      resetDdaZoom();
    }
  };
  afterImg.onload = onReady;
  beforeImg.onload = onReady;
  setTimeout(() => { resetDdaCompareSlider(); resetDdaZoom(); }, 500);

  const regions = data.regions || [];
  ddaRegionList = regions;
  ddaRegionRows = regions.map((r) => {
    const tr = document.createElement('tr');
    tr.dataset.regionId = r.id;
    const subType = r.subType || '—';
    const ddaType = r.ddaChangeType || '—';
    const mapsUrl = r.latLng
      ? `https://www.google.com/maps/search/?api=1&query=${r.latLng.lat},${r.latLng.lng}`
      : null;
    const latLng = r.latLng
      ? `<a href="${mapsUrl}" target="_blank" rel="noopener" title="Open in Google Maps">${r.latLng.lat}, ${r.latLng.lng}</a>`
      : '—';
    const severity = (r.severity || 'minor').toLowerCase();
    const stories = r.estimatedStories != null ? r.estimatedStories : '—';
    const height = r.estimatedHeightM != null ? r.estimatedHeightM + ' m' : '—';
    const stage = r.constructionStage && r.constructionStage !== 'Unknown' ? r.constructionStage : '—';
    const reviewStatus = r.reviewStatus || 'pending';
    const locked = reviewStatus === 'submitted';
    tr.innerHTML = `
      <td>${r.id}</td>
      <td>${r.objectType}</td>
      <td>${ddaType}</td>
      <td>${latLng}</td>
      <td>${subType}</td>
      <td><span class="severity-badge ${severity}">${severity}</span></td>
      <td>${(r.confidence * 100).toFixed(1)}%</td>
      <td>${r.areaSqM != null ? r.areaSqM.toLocaleString() + ' m²' : r.area.toLocaleString()}</td>
      <td>(${r.center.x}, ${r.center.y})</td>
      <td>${stories}</td>
      <td>${height}</td>
      <td>${stage}</td>
      <td><span class="${reviewBadgeClass(reviewStatus)}">${reviewStatus.replace('_', ' ')}</span></td>
      <td class="dda-review-btns">
        <button type="button" class="btn btn-secondary btn-sm btn-review-ok" data-action="confirmed" ${locked ? 'disabled' : ''} title="Confirm">✓</button>
        <button type="button" class="btn btn-secondary btn-sm btn-review-fp" data-action="false_positive" ${locked ? 'disabled' : ''} title="False positive">✗</button>
        <button type="button" class="btn btn-secondary btn-sm btn-review-locate" title="Locate">◎</button>
        ${mapsUrl ? `<a class="btn btn-secondary btn-sm" href="${mapsUrl}" target="_blank" rel="noopener" title="Open in Google Maps">Map</a>` : ''}
      </td>
    `;
    return tr;
  });

  ddaRegionPage = 0;
  renderDdaRegionPage();
  if (data.id) setupDdaReviewBar(data.id, regions);
  openDdaResultModal();
}

function renderDdaRegionPage() {
  const tbody = document.getElementById('regions-tbody');
  const pag = document.getElementById('regions-pagination');
  if (!tbody) return;

  const totalPages = Math.max(1, Math.ceil(ddaRegionRows.length / DDA_REGIONS_PER_PAGE));
  ddaRegionPage = Math.max(0, Math.min(ddaRegionPage, totalPages - 1));
  const start = ddaRegionPage * DDA_REGIONS_PER_PAGE;
  const pageRows = ddaRegionRows.slice(start, start + DDA_REGIONS_PER_PAGE);
  const pageData = ddaRegionList.slice(start, start + DDA_REGIONS_PER_PAGE);

  tbody.innerHTML = '';
  pageRows.forEach((tr) => tbody.appendChild(tr));
  setupDdaRegionHover(tbody, pageData);
  setupDdaReviewButtons(tbody, pageData);

  if (!pag) return;
  pag.innerHTML = '';
  if (totalPages <= 1) return;

  const prev = document.createElement('button');
  prev.textContent = '‹';
  prev.disabled = ddaRegionPage === 0;
  prev.addEventListener('click', () => { ddaRegionPage--; renderDdaRegionPage(); });
  pag.appendChild(prev);

  for (let i = 0; i < totalPages; i++) {
    const btn = document.createElement('button');
    btn.textContent = i + 1;
    if (i === ddaRegionPage) btn.classList.add('active');
    btn.addEventListener('click', () => { ddaRegionPage = i; renderDdaRegionPage(); });
    pag.appendChild(btn);
  }

  const next = document.createElement('button');
  next.textContent = '›';
  next.disabled = ddaRegionPage >= totalPages - 1;
  next.addEventListener('click', () => { ddaRegionPage++; renderDdaRegionPage(); });
  pag.appendChild(next);
}

function setupDdaReviewButtons(tbody, regions) {
  const runId = ddaCurrentResult?.id;
  if (!runId) return;

  tbody.querySelectorAll('tr[data-region-id]').forEach((tr) => {
    tr.querySelector('.btn-review-ok')?.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = parseInt(tr.dataset.regionId, 10);
      try {
        const updated = await patchRegionReview(runId, id, 'confirmed');
        const idx = ddaRegionList.findIndex((x) => x.id === id);
        if (idx >= 0) ddaRegionList[idx] = { ...ddaRegionList[idx], ...updated };
        renderDdaRegionPage();
        updateDdaReviewSummary(ddaRegionList);
      } catch (err) {
        if (typeof showDdaError === 'function') showDdaError(err.message);
      }
    });
    tr.querySelector('.btn-review-fp')?.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = parseInt(tr.dataset.regionId, 10);
      try {
        const updated = await patchRegionReview(runId, id, 'false_positive');
        const idx = ddaRegionList.findIndex((x) => x.id === id);
        if (idx >= 0) ddaRegionList[idx] = { ...ddaRegionList[idx], ...updated };
        renderDdaRegionPage();
        updateDdaReviewSummary(ddaRegionList);
      } catch (err) {
        if (typeof showDdaError === 'function') showDdaError(err.message);
      }
    });
    tr.querySelector('.btn-review-locate')?.addEventListener('click', (e) => {
      e.stopPropagation();
      selectAndLocateRegion(tr, regions);
    });
  });
}

function selectAndLocateRegion(tr, regions) {
  const id = parseInt(tr.dataset.regionId, 10);
  const r = regions.find((x) => x.id === id);
  ddaSelectedRegionId = id;
  document.getElementById('dda-locate-btn')?.removeAttribute('disabled');
  tr.closest('tbody')?.querySelectorAll('tr').forEach((row) => row.classList.remove('region-selected'));
  tr.classList.add('region-selected');
  locateRegionOnViewer(r);
}

function locateRegionOnViewer(r) {
  const overlay = document.getElementById('region-highlight-overlay');
  if (!r || !r.bbox || !overlay) return;
  overlay.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'highlight-box highlight-pulse';
  const imgEl = document.getElementById('compare-after-img');
  const slider = document.getElementById('compare-slider');
  const wrapper = document.getElementById('zoom-wrapper');
  if (!imgEl || !slider || !imgEl.naturalWidth) return;
  const rw = slider.offsetWidth;
  const rh = slider.offsetHeight;
  const imgW = imgEl.naturalWidth || 1;
  const imgH = imgEl.naturalHeight || 1;
  const scale = Math.min(rw / imgW, rh / imgH);
  const drawW = imgW * scale;
  const drawH = imgH * scale;
  const offsetX = (rw - drawW) / 2;
  const offsetY = (rh - drawH) / 2;
  box.style.left = (offsetX + r.bbox.x * scale) + 'px';
  box.style.top = (offsetY + r.bbox.y * scale) + 'px';
  box.style.width = (r.bbox.w * scale) + 'px';
  box.style.height = (r.bbox.h * scale) + 'px';
  overlay.appendChild(box);

  if (wrapper && r.bbox.w > 0 && r.bbox.h > 0) {
    const cx = r.bbox.x + r.bbox.w / 2;
    const cy = r.bbox.y + r.bbox.h / 2;
    ddaZoom = Math.min(DDA_ZOOM_MAX, Math.max(1.5, Math.min(drawW / (r.bbox.w * scale * 2.5), drawH / (r.bbox.h * scale * 2.5))));
    applyDdaZoom();
    wrapper.scrollLeft = Math.max(0, (offsetX + cx * scale) * ddaZoom - wrapper.clientWidth / 2);
    wrapper.scrollTop = Math.max(0, (offsetY + cy * scale) * ddaZoom - wrapper.clientHeight / 2);
  }
}

function setupDdaRegionHover(tbody, regions) {
  const overlay = document.getElementById('region-highlight-overlay');
  if (!overlay) return;

  function showRegionHighlight(r, zoomTo) {
    if (!r || !r.bbox) return;
    if (zoomTo) locateRegionOnViewer(r);
    else {
      overlay.innerHTML = '';
      const box = document.createElement('div');
      box.className = 'highlight-box';
      const imgEl = document.getElementById('compare-after-img');
      const slider = document.getElementById('compare-slider');
      if (!imgEl || !slider || !imgEl.naturalWidth) return;
      const rw = slider.offsetWidth;
      const rh = slider.offsetHeight;
      const imgW = imgEl.naturalWidth || 1;
      const imgH = imgEl.naturalHeight || 1;
      const scale = Math.min(rw / imgW, rh / imgH);
      const drawW = imgW * scale;
      const drawH = imgH * scale;
      const offsetX = (rw - drawW) / 2;
      const offsetY = (rh - drawH) / 2;
      box.style.left = (offsetX + r.bbox.x * scale) + 'px';
      box.style.top = (offsetY + r.bbox.y * scale) + 'px';
      box.style.width = (r.bbox.w * scale) + 'px';
      box.style.height = (r.bbox.h * scale) + 'px';
      overlay.appendChild(box);
    }
  }

  tbody.querySelectorAll('tr[data-region-id]').forEach((tr) => {
    tr.style.cursor = 'pointer';
    tr.title = 'Click to select and locate';
    tr.addEventListener('mouseenter', () => {
      const id = parseInt(tr.dataset.regionId, 10);
      const r = regions.find((x) => x.id === id);
      tbody.querySelectorAll('tr').forEach((row) => row.classList.remove('region-hover'));
      tr.classList.add('region-hover');
      showRegionHighlight(r, false);
    });
    tr.addEventListener('mouseleave', () => {
      tr.classList.remove('region-hover');
      if (!tr.classList.contains('region-selected')) overlay.innerHTML = '';
    });
    tr.addEventListener('click', () => selectAndLocateRegion(tr, regions));
  });
}

function openDdaResultModal() {
  const modal = document.getElementById('result-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeDdaResultModal() {
  const modal = document.getElementById('result-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  const picker = document.getElementById('dda-picker-modal');
  if (!picker || picker.classList.contains('hidden')) {
    document.body.style.overflow = '';
  }
}

function resetDdaCompareSlider() {
  if (ddaViewMode !== 'slider') return;
  const ac = document.getElementById('compare-after-clip');
  const h = document.getElementById('compare-handle');
  if (ac) ac.style.clipPath = 'inset(0 0 0 50%)';
  if (h) h.style.left = '50%';
}

function applyDdaZoom() {
  const slider = document.getElementById('compare-slider');
  const levelEl = document.getElementById('zoom-level');
  if (!slider) return;
  slider.style.transform = `scale(${ddaZoom})`;
  slider.style.transformOrigin = 'center top';
  if (levelEl) levelEl.textContent = Math.round(ddaZoom * 100) + '%';
}

function resetDdaZoom() {
  ddaZoom = 1;
  applyDdaZoom();
}

function initDdaCompareSlider() {
  const slider = document.getElementById('compare-slider');
  if (!slider) return;
  let isDragging = false;

  function updatePosition(clientX) {
    if (ddaViewMode !== 'slider') return;
    const rect = slider.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    document.getElementById('compare-after-clip').style.clipPath = `inset(0 0 0 ${pct}%)`;
    document.getElementById('compare-handle').style.left = pct + '%';
  }

  slider.addEventListener('mousedown', (e) => { if (ddaViewMode !== 'slider') return; e.preventDefault(); isDragging = true; updatePosition(e.clientX); });
  document.addEventListener('mousemove', (e) => { if (isDragging) updatePosition(e.clientX); });
  document.addEventListener('mouseup', () => { isDragging = false; });
  slider.addEventListener('touchstart', (e) => { if (ddaViewMode !== 'slider') return; isDragging = true; updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchmove', (e) => { if (isDragging) updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchend', () => { isDragging = false; });
}

function initDdaViewToolbar() {
  document.querySelectorAll('.dda-view-btn').forEach((btn) => {
    btn.addEventListener('click', () => setDdaViewMode(btn.dataset.view));
  });
  document.getElementById('dda-locate-btn')?.addEventListener('click', () => {
    const r = ddaRegionList.find((x) => x.id === ddaSelectedRegionId);
    if (r) locateRegionOnViewer(r);
  });
}

function initDdaZoom() {
  document.getElementById('zoom-in')?.addEventListener('click', () => {
    ddaZoom = Math.min(DDA_ZOOM_MAX, ddaZoom + DDA_ZOOM_STEP);
    applyDdaZoom();
  });
  document.getElementById('zoom-out')?.addEventListener('click', () => {
    ddaZoom = Math.max(DDA_ZOOM_MIN, ddaZoom - DDA_ZOOM_STEP);
    applyDdaZoom();
  });
  document.getElementById('zoom-wrapper')?.addEventListener('wheel', (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    if (e.deltaY < 0) ddaZoom = Math.min(DDA_ZOOM_MAX, ddaZoom + DDA_ZOOM_STEP);
    else ddaZoom = Math.max(DDA_ZOOM_MIN, ddaZoom - DDA_ZOOM_STEP);
    applyDdaZoom();
  }, { passive: false });
}

document.getElementById('result-modal-close')?.addEventListener('click', closeDdaResultModal);
document.getElementById('result-modal')?.addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeDdaResultModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('result-modal');
    if (modal && !modal.classList.contains('hidden')) closeDdaResultModal();
  }
});

initDdaCompareSlider();
initDdaZoom();
initDdaViewToolbar();
