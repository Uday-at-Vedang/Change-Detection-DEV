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
let ddaShapeMode = 'polygon';
// Detection working size for region/polygon coords (legacy compare PNG mismatch guard).
let ddaDetSize = null;

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

function setupDdaReviewBar(runId, regions, geo) {
  const exportAll = document.getElementById('dda-export-csv');
  const exportConfirmed = document.getElementById('dda-export-confirmed');
  const submitBtn = document.getElementById('dda-submit-dept');
  if (exportAll) exportAll.href = `/api/dda/reports/${runId}/export.csv`;
  if (exportConfirmed) exportConfirmed.href = `/api/dda/reports/${runId}/export.csv?confirmed=1`;
  const exportGeo = document.getElementById('dda-export-geojson');
  if (exportGeo) exportGeo.href = `/api/dda/reports/${runId}/export.geojson`;
  updateDdaReviewSummary(regions);
  setupTrainingPackButton(runId, geo);

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

// "Export training pack": write a paint-ready GT-labeling pack for the current
// pair (and ROI). Only available when the pair/ROI is known from the Compare tab
// (i.e. a fresh detection); hidden when viewing an old report from history.
function setupTrainingPackButton(runId, geo) {
  const btn = document.getElementById('dda-export-pack');
  if (!btn) return;
  const cs = (typeof compareState !== 'undefined') ? compareState : null;
  const havePair = cs && cs.t1 && cs.t2;
  btn.classList.toggle('hidden', !havePair);
  if (!havePair) return;

  btn.replaceWith(btn.cloneNode(true));
  const fresh = document.getElementById('dda-export-pack');
  fresh.addEventListener('click', async () => {
    fresh.disabled = true;
    const original = fresh.textContent;
    fresh.textContent = 'Exporting…';
    try {
      const form = new FormData();
      form.append('base_path', cs.t1.path);
      form.append('comparison_path', cs.t2.path);
      form.append('run_id', String(runId));
      if (geo && geo.detectionWidth && geo.detectionHeight) {
        form.append('det_w', String(geo.detectionWidth));
        form.append('det_h', String(geo.detectionHeight));
      }
      if (cs.roi) form.append('roi', JSON.stringify(cs.roi));
      const res = await ddaApi('POST', '/api/dda/training/pack', { body: form });
      if (typeof showDdaSuccess === 'function') {
        showDdaSuccess(`Training pack ready: ${res.dir} — paint gt_mask.png then run: ${res.ingestCmd}`);
      }
    } catch (err) {
      if (typeof showDdaError === 'function') showDdaError(err.message || 'Pack export failed');
    } finally {
      fresh.disabled = false;
      fresh.textContent = original;
    }
  });
}

async function patchRegionReview(runId, regionId, reviewStatus) {
  const res = await ddaApi('PATCH', `/api/dda/reports/${runId}/regions/${regionId}`, {
    body: JSON.stringify({ reviewStatus }),
  });
  return res.region;
}

/** Format polygon/mask area for the regions table (m² preferred when georeferenced). */
function formatRegionAreaCell(r) {
  const polyPx = r.polygonAreaPx != null ? Number(r.polygonAreaPx) : null;
  const maskPx = r.area != null ? Number(r.area) : null;
  const tipParts = [];
  if (polyPx != null) tipParts.push(`Polygon ${Math.round(polyPx).toLocaleString()} px`);
  if (maskPx != null) tipParts.push(`Mask ${Math.round(maskPx).toLocaleString()} px`);
  const tip = tipParts.join(' · ');
  if (r.areaSqM != null && !Number.isNaN(Number(r.areaSqM))) {
    // Unit lives in the column header ("Area (m²)") now, not repeated per row.
    return `<td class="region-area" title="${tip || 'Polygon footprint area'}">${Number(r.areaSqM).toLocaleString()}</td>`;
  }
  const px = polyPx != null ? polyPx : maskPx;
  if (px == null || Number.isNaN(px)) return '<td class="region-area">—</td>';
  const unit = polyPx != null ? 'px (polygon)' : 'px';
  return `<td class="region-area" title="${tip}">${Math.round(px).toLocaleString()} ${unit}</td>`;
}

function buildDdaRegionRow(r) {
  const tr = document.createElement('tr');
  tr.dataset.regionId = r.id;
  const subType = r.subType || '—';
  const ddaType = r.ddaChangeType || '—';
  const mapsUrl = r.latLng
    ? `https://www.google.com/maps?q=${r.latLng.lat},${r.latLng.lng}&ll=${r.latLng.lat},${r.latLng.lng}&z=19`
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
  const okActive = reviewStatus === 'confirmed' ? ' is-active' : '';
  const fpActive = reviewStatus === 'false_positive' ? ' is-active' : '';
  tr.innerHTML = `
      <td>${r.id}</td>
      <td><span class="severity-badge ${severity}">${severity}</span></td>
      <td>${r.objectType}</td>
      <td>${ddaType}</td>
      <td>${latLng}</td>
      <td>${subType}</td>
      <td>${(r.confidence * 100).toFixed(1)}%</td>
      ${formatRegionAreaCell(r)}
      <td>(${r.center.x}, ${r.center.y})</td>
      <td>${stories}</td>
      <td>${height}</td>
      <td>${stage}</td>
      <td><span class="${reviewBadgeClass(reviewStatus)}">${reviewStatus.replace('_', ' ')}</span></td>
      <td class="dda-review-btns">
        <button type="button" class="btn btn-secondary btn-sm btn-review-ok${okActive}" data-action="confirmed" ${locked ? 'disabled' : ''} title="Confirm">✓</button>
        <button type="button" class="btn btn-secondary btn-sm btn-review-fp${fpActive}" data-action="false_positive" ${locked ? 'disabled' : ''} title="False positive">✗</button>
        <button type="button" class="btn btn-secondary btn-sm btn-review-locate" title="Locate">◎</button>
        ${mapsUrl ? `<a class="btn btn-secondary btn-sm" href="${mapsUrl}" target="_blank" rel="noopener" title="Open in Google Maps">Map</a>` : ''}
      </td>
    `;
  return tr;
}

function rebuildDdaRegionRows() {
  ddaRegionRows = (ddaRegionList || []).map((r) => buildDdaRegionRow(r));
}

function applyRegionReviewLocally(regionId, reviewStatus, extra = {}) {
  const idx = ddaRegionList.findIndex((x) => x.id === regionId);
  if (idx < 0) return null;
  const prev = ddaRegionList[idx];
  ddaRegionList[idx] = { ...prev, ...extra, reviewStatus };
  ddaRegionRows[idx] = buildDdaRegionRow(ddaRegionList[idx]);
  renderDdaRegionPage();
  updateDdaReviewSummary(ddaRegionList);
  return prev;
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
  const pctStruct = stats.changePercentageStructural != null
    ? Number(stats.changePercentageStructural).toFixed(2)
    : null;
  const pctAll = stats.changePercentageAll != null
    ? Number(stats.changePercentageAll).toFixed(2)
    : null;
  const shadowPx = stats.shadowPixels ?? 0;
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
  const structHint = (pctStruct != null && pctAll != null && pctStruct !== pctAll)
    ? `<div class="stat-box"><div class="value">${pctStruct}%</div><div class="label">Structural</div></div>
       <div class="stat-box"><div class="value">${pctAll}%</div><div class="label">All (+shadow)</div></div>`
    : '';
  const shadowHint = shadowPx > 0
    ? `<div class="stat-box"><div class="value" title="${shadowPx.toLocaleString()}">${formatCompact(shadowPx)}</div><div class="label">Shadow px</div></div>`
    : '';

  statsEl.innerHTML = warnHtml + `
    <div class="stat-box"><div class="value">${pct}%</div><div class="label">Changed</div></div>
    ${structHint}
    <div class="stat-box"><div class="value" title="${chPx.toLocaleString()}">${formatCompact(chPx)}</div><div class="label">Changed px</div></div>
    ${shadowHint}
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
  rebuildDdaRegionRows();

  ddaRegionPage = 0;
  renderDdaRegionPage();
  if (data.id) setupDdaReviewBar(data.id, regions, data.statistics && data.statistics.geo);
  // Polygons need the image laid out to scale correctly; draw once it's ready
  // and again on resize so footprints stay locked to the image.
  ddaShapeMode = (data.statistics && data.statistics.params
    && data.statistics.params.shape_mode) || 'polygon';
  const geoStats = data.statistics && data.statistics.geo;
  ddaDetSize = (geoStats && geoStats.detectionWidth && geoStats.detectionHeight)
    ? { w: Number(geoStats.detectionWidth), h: Number(geoStats.detectionHeight) }
    : null;
  const drawPolys = () => renderRegionPolygons(regions, ddaShapeMode);
  const beforeImgEl = document.getElementById('compare-before-img');
  if (beforeImgEl) {
    if (beforeImgEl.complete && beforeImgEl.naturalWidth) drawPolys();
    beforeImgEl.addEventListener('load', drawPolys, { once: true });
  }
  setTimeout(drawPolys, 550);  // after resetDdaCompareSlider/resetDdaZoom settle
  if (!window._ddaPolyResizeBound) {
    window._ddaPolyResizeBound = true;
    window.addEventListener('resize', () => renderRegionPolygons(ddaRegionList));
  }
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
      const prev = applyRegionReviewLocally(id, 'confirmed');
      try {
        const updated = await patchRegionReview(runId, id, 'confirmed');
        applyRegionReviewLocally(id, updated?.reviewStatus || 'confirmed', updated || {});
      } catch (err) {
        if (prev) applyRegionReviewLocally(id, prev.reviewStatus || 'pending', prev);
        if (typeof showDdaError === 'function') showDdaError(err.message);
      }
    });
    tr.querySelector('.btn-review-fp')?.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = parseInt(tr.dataset.regionId, 10);
      const prev = applyRegionReviewLocally(id, 'false_positive');
      try {
        const updated = await patchRegionReview(runId, id, 'false_positive');
        applyRegionReviewLocally(id, updated?.reviewStatus || 'false_positive', updated || {});
      } catch (err) {
        if (prev) applyRegionReviewLocally(id, prev.reviewStatus || 'pending', prev);
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
  highlightRegionPolygon(r && r.id);
  locateRegionOnViewer(r);
}

/** Layout metrics for the before image (drives compare-slider size). */
function getCompareImageMetrics() {
  const beforeImg = document.getElementById('compare-before-img');
  if (!beforeImg || !beforeImg.naturalWidth) return null;
  const dispW = beforeImg.clientWidth;
  const dispH = beforeImg.clientHeight;
  if (dispW <= 0 || dispH <= 0) return null;
  // Prefer detection working size when available so polygon/bbox coords stay locked
  // even if a legacy compare PNG was saved at a different resolution.
  const coordW = (ddaDetSize && ddaDetSize.w > 0) ? ddaDetSize.w : beforeImg.naturalWidth;
  const coordH = (ddaDetSize && ddaDetSize.h > 0) ? ddaDetSize.h : beforeImg.naturalHeight;
  return {
    scaleX: dispW / coordW,
    scaleY: dispH / coordH,
    offsetX: beforeImg.offsetLeft || 0,
    offsetY: beforeImg.offsetTop || 0,
    dispW,
    dispH,
    imgW: coordW,
    imgH: coordH,
  };
}

function placeRegionHighlight(r, { pulse = false } = {}) {
  const overlay = document.getElementById('region-highlight-overlay');
  if (!overlay || !r || !r.bbox) return null;
  const m = getCompareImageMetrics();
  if (!m) return null;
  overlay.innerHTML = '';
  const box = document.createElement('div');
  box.className = pulse ? 'highlight-box highlight-pulse' : 'highlight-box';
  box.style.left = `${m.offsetX + r.bbox.x * m.scaleX}px`;
  box.style.top = `${m.offsetY + r.bbox.y * m.scaleY}px`;
  box.style.width = `${Math.max(2, r.bbox.w * m.scaleX)}px`;
  box.style.height = `${Math.max(2, r.bbox.h * m.scaleY)}px`;
  overlay.appendChild(box);
  const label = document.createElement('div');
  label.className = 'region-area-label';
  label.textContent = regionAreaLabel(r);
  label.style.left = `${m.offsetX + r.bbox.x * m.scaleX}px`;
  label.style.top = `${Math.max(0, m.offsetY + r.bbox.y * m.scaleY - 22)}px`;
  overlay.appendChild(label);
  return m;
}

// --- Polygon footprint layer -----------------------------------------------
// Regions may carry `polygon` ([[x,y],...] in detection-image px). Draw them as
// one SVG layer over the compare image, reusing getCompareImageMetrics() so the
// footprints track the image's zoom/pan exactly like the bbox highlight does.
// Regions without a polygon simply aren't drawn (the bbox highlight still works).
const DDA_POLYGON_COLORS = {
  'New Construction': '#ff8c1a',
  Demolition: '#e03131',
  Extension: '#2f7ed8',
  'Vegetation Change': '#2f9e44',
  Other: '#9e9e9e',
};

function polygonFillFor(r) {
  return DDA_POLYGON_COLORS[r.ddaChangeType] || DDA_POLYGON_COLORS.Other;
}

function renderRegionPolygons(regions, shapeMode) {
  const layer = document.getElementById('region-polygon-layer');
  if (!layer) return;
  // Match the baked overlay: a run detected in "bbox" mode shows rectangles
  // here too, even though every region still carries its polygon.
  const mode = shapeMode || ddaShapeMode || 'polygon';
  const asBox = (r) => {
    const b = r.bbox; if (!b) return null;
    return [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]];
  };
  const withPoly = (regions || [])
    .map((r) => {
      const ring = mode === 'bbox'
        ? asBox(r)
        : (Array.isArray(r.polygon) && r.polygon.length >= 3 ? r.polygon : asBox(r));
      return ring ? { ...r, _ring: ring } : null;
    })
    .filter(Boolean);
  if (!withPoly.length) { layer.innerHTML = ''; layer.style.display = 'none'; return; }
  const m = getCompareImageMetrics();
  if (!m) return;  // image not laid out yet; re-called on load/resize

  layer.style.display = '';
  layer.setAttribute('width', m.dispW);
  layer.setAttribute('height', m.dispH);
  layer.style.left = `${m.offsetX}px`;
  layer.style.top = `${m.offsetY}px`;

  const svgNs = 'http://www.w3.org/2000/svg';
  layer.innerHTML = '';
  withPoly.forEach((r) => {
    const pts = r._ring
      .map((p) => `${(p[0] * m.scaleX).toFixed(1)},${(p[1] * m.scaleY).toFixed(1)}`)
      .join(' ');
    const poly = document.createElementNS(svgNs, 'polygon');
    poly.setAttribute('points', pts);
    poly.setAttribute('fill', polygonFillFor(r));
    // Outline-first: fill is a light cue; stroke carries the footprint.
    poly.setAttribute('fill-opacity', '0.12');
    poly.setAttribute('stroke', polygonFillFor(r));
    poly.setAttribute('stroke-width', '2');
    poly.setAttribute('stroke-opacity', '0.95');
    poly.dataset.regionId = r.id;
    const tip = document.createElementNS(svgNs, 'title');
    tip.textContent = regionAreaLabel(r);
    poly.appendChild(tip);
    layer.appendChild(poly);
  });
}

/** Human-readable area for tooltips / highlight labels. */
function regionAreaLabel(r) {
  if (!r) return 'Region';
  if (r.areaSqM != null && !Number.isNaN(Number(r.areaSqM))) {
    return `#${r.id}: ${Number(r.areaSqM).toLocaleString()} m²`;
  }
  if (r.polygonAreaPx != null) {
    return `#${r.id}: ${Math.round(Number(r.polygonAreaPx)).toLocaleString()} px (polygon)`;
  }
  if (r.area != null) return `#${r.id}: ${Number(r.area).toLocaleString()} px`;
  return `#${r.id}`;
}

/** Emphasize one region's polygon (called alongside the bbox highlight). */
function highlightRegionPolygon(regionId) {
  const layer = document.getElementById('region-polygon-layer');
  if (!layer) return;
  layer.querySelectorAll('polygon').forEach((p) => {
    const on = String(p.dataset.regionId) === String(regionId);
    p.setAttribute('fill-opacity', on ? '0.6' : '0.35');
    p.setAttribute('stroke-width', on ? '3' : '2');
  });
}

function scrollViewerToRegion(r) {
  const wrapper = document.getElementById('zoom-wrapper');
  const m = getCompareImageMetrics();
  if (!wrapper || !m || !r?.bbox) return;
  const cx = m.offsetX + (r.bbox.x + r.bbox.w / 2) * m.scaleX;
  const cy = m.offsetY + (r.bbox.y + r.bbox.h / 2) * m.scaleY;
  wrapper.scrollTo({
    left: Math.max(0, cx - wrapper.clientWidth / 2),
    top: Math.max(0, cy - wrapper.clientHeight / 2),
    behavior: 'smooth',
  });
}

function locateRegionOnViewer(r) {
  if (!r || !r.bbox) return;
  const beforeImg = document.getElementById('compare-before-img');
  const wrapper = document.getElementById('zoom-wrapper');
  if (!beforeImg || !wrapper) return;

  const runLocate = () => {
    const m0 = getCompareImageMetrics();
    if (!m0) return;
    // Size region to ~40% of the shorter viewport edge (natural→display at zoom 1).
    const basePx = Math.max(r.bbox.w, r.bbox.h) * (wrapper.clientWidth / m0.imgW);
    const desired = Math.min(wrapper.clientWidth, wrapper.clientHeight) * 0.4;
    const nextZoom = desired / Math.max(basePx, 1);
    ddaZoom = Math.min(DDA_ZOOM_MAX, Math.max(1.25, nextZoom));
    applyDdaZoom();
    requestAnimationFrame(() => {
      placeRegionHighlight(r, { pulse: true });
      scrollViewerToRegion(r);
    });
  };

  if (!beforeImg.naturalWidth) {
    beforeImg.addEventListener('load', runLocate, { once: true });
    return;
  }
  runLocate();
}

function setupDdaRegionHover(tbody, regions) {
  const overlay = document.getElementById('region-highlight-overlay');
  if (!overlay) return;

  function showRegionHighlight(r, zoomTo) {
    if (!r || !r.bbox) return;
    if (zoomTo) locateRegionOnViewer(r);
    else placeRegionHighlight(r, { pulse: false });
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
  // Width-based zoom keeps overflow:auto scrollable (CSS transform does not).
  slider.style.transform = 'none';
  slider.style.transformOrigin = '';
  slider.style.width = `${Math.max(DDA_ZOOM_MIN, ddaZoom) * 100}%`;
  slider.style.maxWidth = 'none';
  if (levelEl) levelEl.textContent = Math.round(ddaZoom * 100) + '%';
  // Re-scale SVG footprints with the image (same metrics as bbox highlight).
  renderRegionPolygons(ddaRegionList);
  if (ddaSelectedRegionId != null) {
    const r = ddaRegionList.find((x) => x.id === ddaSelectedRegionId);
    if (r) placeRegionHighlight(r, { pulse: true });
    highlightRegionPolygon(ddaSelectedRegionId);
  }
}

function resetDdaZoom() {
  ddaZoom = 1;
  applyDdaZoom();
  const wrapper = document.getElementById('zoom-wrapper');
  if (wrapper) {
    wrapper.scrollLeft = 0;
    wrapper.scrollTop = 0;
  }
}

function initDdaCompareSlider() {
  const slider = document.getElementById('compare-slider');
  const handle = document.getElementById('compare-handle');
  if (!slider || !handle) return;
  let isDragging = false;

  function updatePosition(clientX) {
    if (ddaViewMode !== 'slider') return;
    const rect = slider.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    document.getElementById('compare-after-clip').style.clipPath = `inset(0 0 0 ${pct}%)`;
    handle.style.left = pct + '%';
  }

  // Only the handle adjusts the before/after clip — rest of image is free to pan.
  handle.addEventListener('mousedown', (e) => {
    if (ddaViewMode !== 'slider') return;
    e.preventDefault();
    e.stopPropagation();
    isDragging = true;
    updatePosition(e.clientX);
  });
  document.addEventListener('mousemove', (e) => { if (isDragging) updatePosition(e.clientX); });
  document.addEventListener('mouseup', () => { isDragging = false; });
  handle.addEventListener('touchstart', (e) => {
    if (ddaViewMode !== 'slider') return;
    e.stopPropagation();
    isDragging = true;
    updatePosition(e.touches[0].clientX);
  }, { passive: true });
  document.addEventListener('touchmove', (e) => { if (isDragging) updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchend', () => { isDragging = false; });
}

function initDdaMapPan() {
  const wrap = document.getElementById('zoom-wrapper');
  if (!wrap) return;
  let panning = false;
  let startX = 0;
  let startY = 0;
  let scrollLeft = 0;
  let scrollTop = 0;

  wrap.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.compare-handle')) return;
    if (e.target.closest('button, a, input, select, textarea')) return;
    panning = true;
    wrap.classList.add('is-panning');
    startX = e.clientX;
    startY = e.clientY;
    scrollLeft = wrap.scrollLeft;
    scrollTop = wrap.scrollTop;
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!panning) return;
    wrap.scrollLeft = scrollLeft - (e.clientX - startX);
    wrap.scrollTop = scrollTop - (e.clientY - startY);
  });
  document.addEventListener('mouseup', () => {
    if (!panning) return;
    panning = false;
    wrap.classList.remove('is-panning');
  });
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
    // Ctrl/Cmd+wheel zooms; plain wheel scrolls (native overflow).
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
initDdaMapPan();
initDdaZoom();
initDdaViewToolbar();
applyDdaZoom();
