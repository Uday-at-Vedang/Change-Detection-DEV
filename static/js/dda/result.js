/** DDA result modal — before/after compare slider (same as production). */

let ddaCurrentResult = null;
let ddaRegionRows = [];
let ddaRegionList = [];
let ddaRegionPage = 0;
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

function showDdaResult(data) {
  const modal = document.getElementById('result-modal');
  const statsEl = document.getElementById('result-stats');
  const titleEl = document.getElementById('result-modal-title');
  if (!modal || !statsEl) return;

  ddaCurrentResult = data;
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
  `;

  const beforeImg = document.getElementById('compare-before-img');
  const afterImg = document.getElementById('compare-after-img');
  if (!beforeImg || !afterImg) return;

  if (data.overlayBase64Png) {
    afterImg.src = 'data:image/png;base64,' + data.overlayBase64Png;
  } else {
    afterImg.src = data.overlayUrl || '';
  }
  beforeImg.src = data.beforeFullUrl || data.beforeThumbUrl || '';

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

  const regions = (data.regions || []).slice(0, 60);
  ddaRegionList = regions;
  ddaRegionRows = regions.map((r) => {
    const tr = document.createElement('tr');
    tr.dataset.regionId = r.id;
    const subType = r.subType || '—';
    const ddaType = r.ddaChangeType || '—';
    const latLng = r.latLng ? `${r.latLng.lat}, ${r.latLng.lng}` : '—';
    const severity = (r.severity || 'minor').toLowerCase();
    const stories = r.estimatedStories != null ? r.estimatedStories : '—';
    const height = r.estimatedHeightM != null ? r.estimatedHeightM + ' m' : '—';
    const stage = r.constructionStage && r.constructionStage !== 'Unknown' ? r.constructionStage : '—';
    tr.innerHTML = `
      <td>${r.id}</td>
      <td>${r.objectType}</td>
      <td>${ddaType}</td>
      <td>${latLng}</td>
      <td>${subType}</td>
      <td><span class="severity-badge ${severity}">${severity}</span></td>
      <td>${(r.confidence * 100).toFixed(1)}%</td>
      <td>${r.area.toLocaleString()}</td>
      <td>(${r.center.x}, ${r.center.y})</td>
      <td>${stories}</td>
      <td>${height}</td>
      <td>${stage}</td>
    `;
    return tr;
  });

  ddaRegionPage = 0;
  renderDdaRegionPage();
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

function setupDdaRegionHover(tbody, regions) {
  const overlay = document.getElementById('region-highlight-overlay');
  if (!overlay) return;
  overlay.innerHTML = '';
  tbody.querySelectorAll('tr[data-region-id]').forEach((tr) => {
    tr.addEventListener('mouseenter', () => {
      const id = parseInt(tr.dataset.regionId, 10);
      const r = regions.find((x) => x.id === id);
      if (!r || !r.bbox) return;
      tbody.querySelectorAll('tr').forEach((row) => row.classList.remove('region-hover'));
      tr.classList.add('region-hover');
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
    });
    tr.addEventListener('mouseleave', () => {
      tr.classList.remove('region-hover');
      overlay.innerHTML = '';
    });
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
    const rect = slider.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    document.getElementById('compare-after-clip').style.clipPath = `inset(0 0 0 ${pct}%)`;
    document.getElementById('compare-handle').style.left = pct + '%';
  }

  slider.addEventListener('mousedown', (e) => { e.preventDefault(); isDragging = true; updatePosition(e.clientX); });
  document.addEventListener('mousemove', (e) => { if (isDragging) updatePosition(e.clientX); });
  document.addEventListener('mouseup', () => { isDragging = false; });
  slider.addEventListener('touchstart', (e) => { isDragging = true; updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchmove', (e) => { if (isDragging) updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchend', () => { isDragging = false; });
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
