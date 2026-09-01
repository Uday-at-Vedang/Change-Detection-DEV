/** Change Detection tab — pick library images and run comparison. */

const compareState = { t1: null, t2: null, pickingSlot: null, selectedNode: null, allLibraryItems: [], roi: null, shapeMode: 'polygon', mode: 'automatic' };

// --- Region shape toggle (polygon footprints vs classic bounding boxes) -----
// Detection itself is identical either way; this only picks how regions are
// drawn. Regions always carry both polygon and bbox, so the run can be
// re-rendered in the other style later without re-detecting.
function initShapeToggle() {
  const wrap = document.querySelector('.dda-shape-toggle');
  if (!wrap || wrap.dataset.bound) return;
  wrap.dataset.bound = '1';
  const saved = localStorage.getItem('ddaShapeMode');
  if (saved === 'polygon' || saved === 'bbox') compareState.shapeMode = saved;
  const apply = () => {
    wrap.querySelectorAll('.dda-shape-btn').forEach((b) => {
      const on = b.dataset.shape === compareState.shapeMode;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  };
  wrap.addEventListener('click', (e) => {
    const btn = e.target.closest('.dda-shape-btn');
    if (!btn) return;
    compareState.shapeMode = btn.dataset.shape === 'bbox' ? 'bbox' : 'polygon';
    localStorage.setItem('ddaShapeMode', compareState.shapeMode);
    apply();
  });
  apply();
}

function compareFormatBytes(n) {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
}

function thumbUrlFor(path) {
  return `/api/dda/local/thumb?path=${encodeURIComponent(path)}`;
}

function encodePath(path) {
  return encodeURIComponent(path || '');
}

function decodePath(encoded) {
  try {
    return decodeURIComponent(encoded || '');
  } catch (_) {
    return encoded || '';
  }
}

function ensureDdaState() {
  window.ddaState = window.ddaState || {};
  return window.ddaState;
}

function runButtonLabel() {
  if (compareState.roi) return 'Run on selection';
  return compareState.mode === 'automatic' ? 'Run automatic detection' : 'Run Detection';
}

function forEachRunButton(fn) {
  document.querySelectorAll('.btn-run-detection').forEach(fn);
}

function updateRunButton() {
  const hasPair = !!(compareState.t1 && compareState.t2);
  // Fails closed until permissions are known — otherwise this runs before
  // app.js's permission fetch resolves (it hits the DB) and would re-enable
  // the button for a beat before the gate catches up, or worse, permanently
  // — this function runs on every pair selection, which would undo a
  // permission-denied disable the moment a pair is auto-selected.
  const permitted = window.ddaPermissions
    ? hasPermission(window.ddaPermissions, 'detect', 'create')
    : false;
  const deniedTitle = "Your role doesn't have permission to run detections.";
  const disabled = !hasPair || !permitted;
  const label = runButtonLabel();
  forEachRunButton((btn) => {
    btn.disabled = disabled;
    btn.textContent = label;
    btn.title = permitted ? '' : deniedTitle;
  });
  const hint = document.getElementById('dda-run-bar-hint');
  if (hint) {
    hint.textContent = !permitted
      ? deniedTitle
      : !hasPair
        ? (compareState.mode === 'automatic'
          ? 'Pick an area with Before and After images, or switch to Manual.'
          : 'Select both Before and After images, then run detection.')
        : 'Detection will use the options above (method, sensitivity, shape).';
  }
  updateRoiUi();
}

function getDetectMode() {
  return compareState.mode === 'manual' ? 'manual' : 'automatic';
}

function setDetectMode(mode, { applyPair } = { applyPair: true }) {
  compareState.mode = mode === 'manual' ? 'manual' : 'automatic';
  localStorage.setItem('ddaDetectMode', compareState.mode);
  const tab = document.getElementById('tab-detect');
  tab?.classList.toggle('dda-mode-automatic', compareState.mode === 'automatic');
  tab?.classList.toggle('dda-mode-manual', compareState.mode === 'manual');
  document.querySelectorAll('#dda-detect-mode .dda-mode-btn').forEach((btn) => {
    const on = btn.dataset.mode === compareState.mode;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-checked', on ? 'true' : 'false');
  });
  const help = document.getElementById('dda-detect-mode-help');
  if (help) {
    help.innerHTML = compareState.mode === 'automatic'
      ? `Automatic: the system selects the oldest image as <strong>Before</strong> and the newest as <strong>After</strong> for the chosen area. Set the interval and time, then click <strong>Start schedule</strong> — it does not run when the app launches. Use <strong>Run automatic detection</strong> for a one-off run.`
      : 'Manual: pick the Before and After images yourself (dropdowns, library cards, or drag-and-drop), then run change detection. Manual runs are not scheduled.';
  }
  if (compareState.mode === 'automatic' && applyPair !== false && typeof applySelectedAutoPair === 'function') {
    applySelectedAutoPair();
  }
  updateRunButton();
}

function initDetectMode() {
  const wrap = document.getElementById('dda-detect-mode');
  if (!wrap || wrap.dataset.bound) {
    setDetectMode(compareState.mode, { applyPair: false });
    return;
  }
  wrap.dataset.bound = '1';
  const saved = localStorage.getItem('ddaDetectMode');
  if (saved === 'manual' || saved === 'automatic') compareState.mode = saved;
  wrap.addEventListener('click', (e) => {
    const btn = e.target.closest('.dda-mode-btn');
    if (!btn) return;
    setDetectMode(btn.dataset.mode);
  });
  document.getElementById('dda-auto-area')?.addEventListener('change', () => {
    if (typeof applySelectedAutoPair === 'function') applySelectedAutoPair();
  });
  if (typeof initAutoScheduleControls === 'function') initAutoScheduleControls();
  setDetectMode(compareState.mode, { applyPair: false });
}

function renderSlotPreview(slotKey, selection) {
  const wrap = document.getElementById(slotKey === 't1' ? 'slot-t1-preview' : 'slot-t2-preview');
  const slot = document.getElementById(slotKey === 't1' ? 'slot-t1' : 'slot-t2');
  if (!wrap || !slot) return;

  if (!selection) {
    wrap.innerHTML = '<p class="dim">No image selected</p>';
    slot.classList.remove('filled');
    return;
  }

  slot.classList.add('filled');
  const thumb = selection.thumbUrl || thumbUrlFor(selection.path);
  wrap.innerHTML = `
    <img class="dda-slot-preview" src="${thumb}" alt="" />
    <div class="dda-slot-meta">${escapeHtml(selection.label || selection.filename)}${selection.captureDate ? `<br/><span class="dim">${escapeHtml(String(selection.captureDate).slice(0, 10))}</span>` : ''}</div>`;
  if (slotKey === 't2') setupRoiDraw();
}

// --- ROI (region-of-interest) draw tool -----------------------------------
// Drag a rectangle on the New (T2) preview to detect only that fractional
// window. Coordinates are stored as fractions [0,1] so they map to the full
// image regardless of the thumbnail's display size.
const _roiDraw = { drawing: false, startX: 0, startY: 0, img: null, wrap: null };
let _roiListenersBound = false;

function roiHintText(roi) {
  if (!roi) return 'Tip: drag a box on the New (T2) preview to detect only that area (faster).';
  return `Selection: ${Math.round(roi.w * 100)}% × ${Math.round(roi.h * 100)}% of the image will be detected.`;
}

function updateRoiUi() {
  const ctr = document.getElementById('dda-roi-controls');
  const hint = document.getElementById('dda-roi-hint');
  const clr = document.getElementById('btn-clear-roi');
  if (hint) hint.textContent = roiHintText(compareState.roi);
  if (ctr) ctr.classList.toggle('hidden', !compareState.t2);
  if (clr) clr.classList.toggle('hidden', !compareState.roi);
}

function clearRoi(refreshUi = true) {
  compareState.roi = null;
  document.getElementById('dda-roi-rect')?.remove();
  if (refreshUi) updateRoiUi();
}

function _positionRoiRect(x0, y0, x1, y1) {
  const wrap = _roiDraw.wrap;
  const img = _roiDraw.img;
  if (!wrap || !img) return;
  let rect = document.getElementById('dda-roi-rect');
  if (!rect) {
    rect = document.createElement('div');
    rect.id = 'dda-roi-rect';
    rect.className = 'dda-roi-rect';
    wrap.appendChild(rect);
  }
  const b = img.getBoundingClientRect();
  const wb = wrap.getBoundingClientRect();
  rect.style.left = `${Math.min(x0, x1) * b.width + (b.left - wb.left)}px`;
  rect.style.top = `${Math.min(y0, y1) * b.height + (b.top - wb.top)}px`;
  rect.style.width = `${Math.abs(x1 - x0) * b.width}px`;
  rect.style.height = `${Math.abs(y1 - y0) * b.height}px`;
}

function _roiFrac(clientX, clientY) {
  const b = _roiDraw.img.getBoundingClientRect();
  return {
    x: Math.min(Math.max((clientX - b.left) / b.width, 0), 1),
    y: Math.min(Math.max((clientY - b.top) / b.height, 0), 1),
  };
}

function setupRoiDraw() {
  const wrap = document.getElementById('slot-t2-preview');
  const img = wrap?.querySelector('img.dda-slot-preview');
  if (!wrap || !img) return;
  _roiDraw.wrap = wrap;
  _roiDraw.img = img;

  img.addEventListener('mousedown', (e) => {
    e.preventDefault();
    _roiDraw.drawing = true;
    const p = _roiFrac(e.clientX, e.clientY);
    _roiDraw.startX = p.x;
    _roiDraw.startY = p.y;
    _positionRoiRect(p.x, p.y, p.x, p.y);
  });

  // Window-level move/up so a drag that leaves the thumbnail still works.
  // Bind once; handlers read the shared _roiDraw state.
  if (!_roiListenersBound) {
    _roiListenersBound = true;
    window.addEventListener('mousemove', (e) => {
      if (!_roiDraw.drawing) return;
      const p = _roiFrac(e.clientX, e.clientY);
      _positionRoiRect(_roiDraw.startX, _roiDraw.startY, p.x, p.y);
    });
    window.addEventListener('mouseup', (e) => {
      if (!_roiDraw.drawing) return;
      _roiDraw.drawing = false;
      const p = _roiFrac(e.clientX, e.clientY);
      const x = Math.min(_roiDraw.startX, p.x);
      const y = Math.min(_roiDraw.startY, p.y);
      const w = Math.abs(p.x - _roiDraw.startX);
      const h = Math.abs(p.y - _roiDraw.startY);
      if (w < 0.02 || h < 0.02) { clearRoi(); return; }  // too small → treat as clear
      compareState.roi = {
        x: +x.toFixed(4), y: +y.toFixed(4), w: +w.toFixed(4), h: +h.toFixed(4),
      };
      _positionRoiRect(x, y, x + w, y + h);
      updateRoiUi();
    });
  }

  // Redraw an existing ROI (e.g., after a re-render); reposition once loaded.
  const redraw = () => {
    if (compareState.roi) {
      const r = compareState.roi;
      _positionRoiRect(r.x, r.y, r.x + r.w, r.y + r.h);
    }
  };
  if (img.complete) redraw(); else img.addEventListener('load', redraw, { once: true });
  updateRoiUi();
}

function populateSelects(items) {
  const sorted = [...(items || [])].sort((a, b) => {
    const da = a.captureDate ? String(a.captureDate).slice(0, 10) : '';
    const db = b.captureDate ? String(b.captureDate).slice(0, 10) : '';
    if (da && db && da !== db) return da.localeCompare(db);
    if (da && !db) return -1;
    if (!da && db) return 1;
    return String(a.filename || a.path || '').localeCompare(String(b.filename || b.path || ''));
  });
  ['select-t1', 'select-t2'].forEach((id) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const current = sel.value;
    const label = id === 'select-t1' ? '— Choose old image —' : '— Choose new image —';
    sel.innerHTML = `<option value="">${label}</option>` +
      sorted.map((img) => {
        const date = img.captureDate ? String(img.captureDate).slice(0, 10) + ' · ' : '';
        return `<option value="${encodePath(img.path)}">${escapeHtml(date + (img.breadcrumb || img.nodePath || img.filename))}</option>`;
      }).join('');
    if (current) sel.value = current;
  });
}

function setSlot(slotKey, img, options) {
  if (!img || !img.path) return;
  const opts = options || {};
  const date = img.captureDate ? String(img.captureDate).slice(0, 10) : '';
  const item = {
    path: img.path,
    label: img.breadcrumb || img.nodePath || img.filename,
    filename: img.filename,
    thumbUrl: img.thumbUrl || thumbUrlFor(img.path),
    captureDate: img.captureDate || null,
  };
  if (slotKey === 't1') {
    compareState.t1 = item;
    const sel = document.getElementById('select-t1');
    if (sel) sel.value = encodePath(item.path);
    renderSlotPreview('t1', item);
  } else {
    compareState.t2 = item;
    clearRoi(false);  // a new T2 invalidates any previous selection
    const sel = document.getElementById('select-t2');
    if (sel) sel.value = encodePath(item.path);
    renderSlotPreview('t2', item);
  }
  updateRunButton();
  refreshCompareLibrarySelection();
  if (!opts.silent && typeof showDdaSuccess === 'function') {
    const when = date ? ` (${date})` : '';
    showDdaSuccess(`${slotKey === 't1' ? 'Before' : 'After'} set: ${item.filename}${when}`);
  }
}

function applyAreaPairToCompare(before, after, options) {
  const opts = options || {};
  if (opts.onlyIfEmpty && (compareState.t1 || compareState.t2)) return false;
  if (!before?.path || !after?.path) return false;
  if (before.path === after.path) {
    if (!opts.silent && typeof showDdaError === 'function') {
      showDdaError('Before and After must be two different images. Pick another date in the dropdown.');
    }
    return false;
  }
  setSlot('t1', before, { silent: true });
  setSlot('t2', after, { silent: true });
  if (!opts.silent && typeof showDdaSuccess === 'function') {
    showDdaSuccess(`Before: ${before.filename} → After: ${after.filename}`);
  }
  return true;
}
window.applyAreaPairToCompare = applyAreaPairToCompare;

function findLibraryItem(path) {
  const norm = decodePath(path);
  const all = compareState.allLibraryItems || [];
  const fromAll = all.find((i) => i.path === norm);
  if (fromAll) return fromAll;
  const items = ensureDdaState().libraryItems || [];
  return items.find((i) => i.path === norm) || {
    path: norm,
    label: norm.split('/').pop(),
    filename: norm.split('/').pop(),
    thumbUrl: thumbUrlFor(norm),
  };
}

function compareSelectionTitle() {
  return compareState.selectedNode?.path || 'All images';
}

function updateCompareFolderPath() {
  const el = document.getElementById('compare-folder-path');
  if (el) el.textContent = compareSelectionTitle();
}

let compareGridPage = 0;
const COMPARE_GRID_PER_PAGE = 12;

function renderCompareGrid(allItems) {
  const grid = document.getElementById('compare-lib-grid');
  if (!grid) return;

  if (!allItems.length) {
    grid.innerHTML = `<p class="dim">No images in <strong>${escapeHtml(compareSelectionTitle())}</strong>. Select another folder or click Refresh to sync from disk.</p>`;
    document.getElementById('compare-lib-pagination')?.replaceChildren();
    return;
  }

  const totalPages = Math.max(1, Math.ceil(allItems.length / COMPARE_GRID_PER_PAGE));
  compareGridPage = Math.max(0, Math.min(compareGridPage, totalPages - 1));
  const start = compareGridPage * COMPARE_GRID_PER_PAGE;
  const items = allItems.slice(start, start + COMPARE_GRID_PER_PAGE);

  grid.innerHTML = items.map((img) => {
    const thumb = img.thumbUrl || thumbUrlFor(img.path);
    const enc = encodePath(img.path);
    const t1Sel = compareState.t1?.path === img.path ? ' selected-t1' : '';
    const t2Sel = compareState.t2?.path === img.path ? ' selected-t2' : '';
    const safeName = img.filename.replace(/</g, '&lt;');
    return `
      <div class="dda-compare-card dda-card-img${t1Sel}${t2Sel}" data-image-path="${enc}" draggable="true">
        ${thumb ? `<img src="${thumb}" alt="" loading="lazy" draggable="false" />` : '<div class="meta">No preview</div>'}
        <div class="meta">
          <span class="dim">${escapeHtml(img.breadcrumb || img.nodePath || '')}</span><br/>
          ${safeName}<br/>
          <span class="dim">${compareFormatBytes(img.fileSizeBytes)}</span>
        </div>
        <div class="dda-compare-assign">
          <button type="button" class="btn btn-secondary btn-sm" data-assign="t1" data-path="${enc}">T1</button>
          <button type="button" class="btn btn-secondary btn-sm" data-assign="t2" data-path="${enc}">T2</button>
        </div>
      </div>`;
  }).join('');

  grid.querySelectorAll('.dda-compare-card').forEach((card) => {
    card.addEventListener('dragstart', (e) => {
      const path = decodePath(card.dataset.imagePath);
      e.dataTransfer.setData('application/x-dda-image-path', path);
      e.dataTransfer.setData('text/plain', path);
    });
  });
  refreshCompareLibrarySelection();

  if (typeof renderPaginationControls === 'function') {
    renderPaginationControls(document.getElementById('compare-lib-pagination'), compareGridPage, totalPages, (p) => {
      compareGridPage = p;
      renderCompareGrid(allItems);
    });
  }
}

function refreshCompareLibrarySelection() {
  const grid = document.getElementById('compare-lib-grid');
  if (!grid) return;
  grid.querySelectorAll('.dda-compare-card').forEach((card) => {
    const path = decodePath(card.dataset.imagePath || '');
    card.classList.toggle('selected-t1', !!(compareState.t1 && compareState.t1.path === path));
    card.classList.toggle('selected-t2', !!(compareState.t2 && compareState.t2.path === path));
  });
}

async function loadCompareLibraryGrid() {
  const grid = document.getElementById('compare-lib-grid');
  const title = document.getElementById('compare-grid-title');
  if (!grid) return;

  grid.innerHTML = '<p class="dim">Loading library images…</p>';
  if (title) title.textContent = `Pick from library — ${compareSelectionTitle()}`;
  updateCompareFolderPath();

  try {
    const allItems = await ddaApi('GET', '/api/dda/local/images');
    compareState.allLibraryItems = allItems;
    populateSelects(allItems);

    const params = new URLSearchParams();
    if (compareState.selectedNode?.id) params.set('node_id', String(compareState.selectedNode.id));
    const gridItems = compareState.selectedNode?.id
      ? await ddaApi('GET', '/api/dda/local/images?' + params.toString())
      : allItems;

    ensureDdaState().libraryItems = gridItems;
    renderCompareGrid(gridItems);
    if (typeof loadAreaPairs === 'function') {
      loadAreaPairs({
        nodeId: compareState.selectedNode?.id || null,
        autoFillCompare: true,
      });
    }
  } catch (err) {
    grid.innerHTML = `<p class="dim">Could not load images: ${err.message}</p>`;
    if (typeof showDdaError === 'function') showDdaError(err.message);
  }
}

async function openPicker(slotKey) {
  compareState.pickingSlot = slotKey;
  const modal = document.getElementById('dda-picker-modal');
  const list = document.getElementById('dda-picker-list');
  const title = document.getElementById('dda-picker-title');
  if (!modal || !list) return;

  if (title) {
    title.textContent = slotKey === 't1' ? 'Select Old Image (T1)' : 'Select New Image (T2)';
  }
  list.innerHTML = '<p class="dim">Loading library…</p>';
  modal.classList.remove('hidden');

  let items = ensureDdaState().libraryItems || [];
  try {
    if (compareState.selectedNode?.id) {
      const params = new URLSearchParams();
      params.set('node_id', String(compareState.selectedNode.id));
      items = await ddaApi('GET', '/api/dda/local/images?' + params.toString());
    } else {
      items = compareState.allLibraryItems.length
        ? compareState.allLibraryItems
        : await ddaApi('GET', '/api/dda/local/images');
      compareState.allLibraryItems = items;
    }
    populateSelects(compareState.allLibraryItems.length
      ? compareState.allLibraryItems
      : items);
  } catch (err) {
    list.innerHTML = `<p class="dim">Could not load images: ${err.message}</p>`;
    return;
  }

  if (!items.length) {
    list.innerHTML = '<p class="dim">No images in library. Add files under library_sources/YEAR/ and refresh.</p>';
  } else {
    list.innerHTML = items.map((img) => {
      const enc = encodePath(img.path);
      return `
      <button type="button" class="dda-picker-item" data-path="${enc}">
        <img src="${img.thumbUrl || thumbUrlFor(img.path)}" alt="" loading="lazy" />
        <span>${escapeHtml(img.breadcrumb || img.filename)}</span>
      </button>`;
    }).join('');
    list.querySelectorAll('.dda-picker-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        const slot = compareState.pickingSlot;
        if (!slot) return;
        setSlot(slot, findLibraryItem(btn.dataset.path));
        closePicker();
      });
    });
  }
}

function closePicker() {
  document.getElementById('dda-picker-modal')?.classList.add('hidden');
  compareState.pickingSlot = null;
}

// Shows the preflight result centered over the page so it can't be missed
// before a multi-minute detection run starts.
// - Hard fail: red item, single "Close" button, always resolves false.
// - Soft warnings: amber items, "Stop"/"Continue anyway" — resolves true only
//   if the user chooses to proceed anyway.
function showPreflightModal({ hardFail, failReason, warnings }) {
  return new Promise((resolve) => {
    const modal = document.getElementById('dda-preflight-modal');
    const title = document.getElementById('dda-preflight-title');
    const list = document.getElementById('dda-preflight-list');
    const continueBtn = document.getElementById('dda-preflight-continue');
    const cancelBtn = document.getElementById('dda-preflight-cancel');
    if (!modal || !list || !continueBtn || !cancelBtn) {
      resolve(!hardFail); // fail open — never silently block detection on a UI glitch
      return;
    }
    if (hardFail) {
      if (title) title.textContent = 'These images can’t be used for detection';
      list.innerHTML = `<li class="fail">${escapeHtml(failReason || 'These images are not suitable for change detection.')}</li>`;
      continueBtn.classList.add('hidden');
      cancelBtn.textContent = 'Close';
    } else {
      if (title) title.textContent = 'Check these images before running detection';
      list.innerHTML = warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
      continueBtn.classList.remove('hidden');
      cancelBtn.textContent = 'Stop';
    }
    modal.classList.remove('hidden');

    const cleanup = (result) => {
      modal.classList.add('hidden');
      continueBtn.removeEventListener('click', onContinue);
      cancelBtn.removeEventListener('click', onCancel);
      modal.removeEventListener('click', onBackdrop);
      resolve(result);
    };
    const onContinue = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onBackdrop = (e) => { if (e.target === modal) cleanup(false); };

    continueBtn.addEventListener('click', onContinue);
    cancelBtn.addEventListener('click', onCancel);
    modal.addEventListener('click', onBackdrop);
  });
}

function setupCompareInteractions() {
  const slotsWrap = document.querySelector('.dda-compare-slots');
  if (slotsWrap) {
    slotsWrap.addEventListener('click', (e) => {
      const pickBtn = e.target.closest('.dda-slot-pick');
      if (pickBtn) {
        e.preventDefault();
        openPicker(pickBtn.dataset.pick);
      }
    });
  }

  document.getElementById('select-t1')?.addEventListener('change', (e) => {
    const path = e.target.value;
    if (!path) {
      compareState.t1 = null;
      renderSlotPreview('t1', null);
      updateRunButton();
      refreshCompareLibrarySelection();
      return;
    }
    setSlot('t1', findLibraryItem(path));
  });

  document.getElementById('select-t2')?.addEventListener('change', (e) => {
    const path = e.target.value;
    if (!path) {
      compareState.t2 = null;
      renderSlotPreview('t2', null);
      updateRunButton();
      refreshCompareLibrarySelection();
      return;
    }
    setSlot('t2', findLibraryItem(path));
  });

  const grid = document.getElementById('compare-lib-grid');
  if (grid) {
    grid.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-assign]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      setSlot(btn.dataset.assign, findLibraryItem(btn.dataset.path));
    });
  }

  ['slot-t1', 'slot-t2'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('drag-over'); });
    el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
    el.addEventListener('drop', (e) => {
      e.preventDefault();
      el.classList.remove('drag-over');
      const path = e.dataTransfer.getData('application/x-dda-image-path')
        || e.dataTransfer.getData('text/plain');
      if (!path) return;
      setSlot(id === 'slot-t1' ? 't1' : 't2', findLibraryItem(path));
    });
  });

  document.getElementById('btn-compare-refresh')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-compare-refresh');
    btn.disabled = true;
    try {
      const data = await window.ddaState.rescan();
      const sync = data.sync || {};
      const parts = [`${data.totalImages || 0} image(s)`];
      if (sync.nodesCreated) parts.push(`${sync.nodesCreated} folder(s) imported`);
      if (sync.imagesIndexed) parts.push(`${sync.imagesIndexed} image(s) indexed`);
      if (typeof showDdaSuccess === 'function') showDdaSuccess(`Library synced — ${parts.join(', ')}.`);
      await loadCompareLibraryGrid();
    } catch (err) {
      if (typeof showDdaError === 'function') showDdaError(err.message);
    } finally {
      btn.disabled = false;
    }
  });
  document.getElementById('dda-picker-close')?.addEventListener('click', closePicker);
  document.getElementById('dda-picker-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'dda-picker-modal') closePicker();
  });
  document.querySelectorAll('.btn-run-detection').forEach((btn) => {
    btn.addEventListener('click', runLibraryDetection);
  });
  document.getElementById('btn-clear-roi')?.addEventListener('click', () => clearRoi());
  // updateRunButton() may have run (and failed closed) before app.js's
  // permission fetch resolved — re-check once it's actually known so a
  // permitted user's button doesn't stay stuck disabled.
  document.addEventListener('dda:permissions-ready', updateRunButton);

  const notifyCb = document.getElementById('dda-detect-notify');
  const notifyEmail = document.getElementById('dda-detect-notify-email');
  notifyCb?.addEventListener('change', () => {
    if (notifyEmail) notifyEmail.classList.toggle('hidden', !notifyCb.checked);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const picker = document.getElementById('dda-picker-modal');
    if (picker && !picker.classList.contains('hidden')) closePicker();
  });
}

function showDetectResult(data) {
  if (typeof showDdaResult === 'function') showDdaResult(data);
  else if (typeof showDdaError === 'function') showDdaError('Result viewer failed to load.');
}

function setDetectProgress(pct, stage) {
  const fill = document.getElementById('detect-progress-fill');
  const label = document.getElementById('detect-progress-label');
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  if (fill) fill.style.width = `${clamped}%`;
  const text = stage ? `${stage} — ${clamped}%` : `${clamped}%`;
  if (label) label.textContent = text;
}

function showDetectProgress() {
  const wrap = document.getElementById('detect-progress');
  wrap?.classList.remove('hidden');
  setDetectProgress(0, 'Starting detection');
}

function hideDetectProgress(delayMs = 0) {
  const hide = () => document.getElementById('detect-progress')?.classList.add('hidden');
  if (delayMs > 0) setTimeout(hide, delayMs);
  else hide();
}

async function runDetectionWithFallback(form) {
  setDetectProgress(2, 'Queuing detection job');
  try {
    const queued = await ddaApi('POST', '/api/dda/jobs', { body: form });
    return await pollJobUntilDone(queued.jobId);
  } catch (err) {
    const msg = String(err.message || '');
    // Busy / queue conflict: never fall back to sync (that fights the running job).
    if (msg.includes('409') || msg.includes('busy') || msg.includes('already running')) {
      throw new Error(
        'Another detection is still running. Wait for it to finish (Reports tab), then try again.',
      );
    }
    const useSync = msg.includes('Not Found') || msg.includes('404')
      || msg.includes('503')
      || msg.includes('Internal Server Error') || msg.includes('NOT NULL');
    if (!useSync) throw err;
    return runSyncDetectionWithProgress(form);
  }
}

async function runSyncDetectionWithProgress(form) {
  let pct = 5;
  setDetectProgress(pct, 'Running detection (sync)');
  const timer = setInterval(() => {
    pct = Math.min(92, pct + (pct < 50 ? 4 : 2));
    setDetectProgress(pct, 'Running detection (sync)');
  }, 1500);
  try {
    const result = await ddaApi('POST', '/api/dda/detect/from-library', { body: form });
    setDetectProgress(100, 'Complete');
    return { result, jobId: null };
  } finally {
    clearInterval(timer);
  }
}

async function pollJobUntilDone(jobId) {
  // Fullres GeoTIFF pairs often take 20–90+ min. Do not hard-timeout the UI —
  // the backend job keeps running; we poll until completed/failed.
  const pollMs = 2000;
  const started = Date.now();
  let lastPct = -1;
  let lastStage = '';
  let consecutiveErrors = 0;
  for (;;) {
    let job;
    try {
      job = await ddaApi('GET', `/api/dda/jobs/${jobId}`);
      consecutiveErrors = 0;
    } catch (err) {
      consecutiveErrors += 1;
      const elapsedMin = Math.max(0, Math.round((Date.now() - started) / 60000));
      setDetectProgress(
        Math.max(lastPct, 1),
        `Waiting for job #${jobId}… (${elapsedMin} min, reconnecting)`,
      );
      if (consecutiveErrors > 30) {
        throw new Error(
          `Lost connection while job #${jobId} was running. `
          + 'Open the Reports tab — the job may still finish in the background.',
        );
      }
      await new Promise((r) => setTimeout(r, pollMs));
      continue;
    }
    const status = job.status;
    const pct = job.progressPct ?? (status === 'queued' ? 0 : 10);
    const elapsedMin = Math.max(0, Math.round((Date.now() - started) / 60000));
    let stage = job.progressStage || (status === 'queued' ? 'Queued' : 'Running');
    if (elapsedMin >= 1) stage = `${stage} (${elapsedMin} min)`;
    if (pct !== lastPct || stage !== lastStage) {
      setDetectProgress(pct, stage);
      lastPct = pct;
      lastStage = stage;
    }
    if (status === 'completed') {
      setDetectProgress(100, 'Complete');
      if (job.result) {
        if (typeof window.refreshDdaNotifications === 'function') window.refreshDdaNotifications();
        return { result: job.result, jobId };
      }
      if (job.runId) {
        const data = await ddaApi('GET', `/api/history/${job.runId}`);
        if (typeof window.refreshDdaNotifications === 'function') window.refreshDdaNotifications();
        return { result: data, jobId };
      }
      if (job.resultError) throw new Error(job.resultError);
    }
    if (status === 'failed') throw new Error(job.errorMessage || 'Detection job failed');
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

async function runLibraryDetection() {
  if (!compareState.t1 || !compareState.t2) {
    if (typeof showDdaError === 'function') showDdaError(
      compareState.mode === 'automatic'
        ? 'No Before/After pair for this area yet. Add two dates of the same place, or switch to Manual.'
        : 'Select both Before and After images first.',
    );
    return;
  }
  if (typeof hideDdaError === 'function') hideDdaError();
  forEachRunButton((btn) => { btn.disabled = true; });

  try {
    const preflightForm = new FormData();
    preflightForm.append('base_path', compareState.t1.path);
    preflightForm.append('comparison_path', compareState.t2.path);
    if (compareState.roi) {
      preflightForm.append('roi', JSON.stringify(compareState.roi));
    }
    const preflight = await ddaApi('POST', '/api/dda/detect/preflight', { body: preflightForm });
    if (preflight.hardFail) {
      await showPreflightModal(preflight);
      updateRunButton();
      return;
    }
    if (preflight.warnings && preflight.warnings.length) {
      const proceed = await showPreflightModal(preflight);
      if (!proceed) {
        updateRunButton();
        return;
      }
    }
  } catch (err) {
    // Preflight check itself failing (network hiccup etc.) shouldn't silently
    // block detection — fall through and let the real detect call surface it.
  }

  showDetectProgress();

  const form = new FormData();
  form.append('base_path', compareState.t1.path);
  form.append('comparison_path', compareState.t2.path);
  form.append('title', `${compareState.t1.filename} vs ${compareState.t2.filename}`);
  form.append('method', document.getElementById('dda-detect-method')?.value || 'AI-Based Deep Learning');
  form.append('enable_registration', String(document.getElementById('dda-detect-registration')?.checked !== false));
  form.append('enable_normalization', String(document.getElementById('dda-detect-normalization')?.checked !== false));
  form.append('detection_sensitivity', String(Math.max(0, Math.min(1, Number(document.getElementById('dda-detect-sensitivity')?.value ?? 0.45)))));
  const minArea = Number(document.getElementById('dda-detect-min-area')?.value ?? 150);
  if (!Number.isNaN(minArea) && minArea >= 50) form.append('min_region_area', String(Math.round(minArea)));
  const notifyCb = document.getElementById('dda-detect-notify');
  const notifyEmail = document.getElementById('dda-detect-notify-email');
  if (notifyCb?.checked && notifyEmail?.value?.trim()) {
    form.append('notify_email', notifyEmail.value.trim());
  }
  if (compareState.roi) {
    form.append('roi', JSON.stringify(compareState.roi));
  }
  form.append('shape_mode', compareState.shapeMode || 'polygon');

  try {
    const { result: data, jobId } = await runDetectionWithFallback(form);
    if (jobId && typeof window.markDdaJobSeen === 'function') window.markDdaJobSeen(jobId);
    showDetectResult(data);
    if (typeof showDdaSuccess === 'function') {
      let msg = 'Detection complete.';
      if (data.notificationSent) msg += ' Report email sent.';
      showDdaSuccess(msg);
    }
    if (typeof loadReportsList === 'function') loadReportsList();
  } catch (err) {
    if (typeof showDdaError === 'function') showDdaError(err.message || 'Detection failed');
  } finally {
    updateRunButton();
    hideDetectProgress(2000);
  }
}

let compareInitialized = false;

function initCompareTab() {
  initShapeToggle();
  initDetectMode();
  if (!compareInitialized) {
    if (typeof registerCompareTreeSidebar === 'function') {
      registerCompareTreeSidebar({
        containerId: 'compare-tree',
        searchId: 'compare-tree-search',
        allBtnId: 'btn-compare-tree-all',
        getSelectedNode: () => compareState.selectedNode,
        onNodeSelect: (node) => {
          compareState.selectedNode = node;
          loadCompareLibraryGrid();
        },
        onClearNode: () => {
          compareState.selectedNode = null;
          loadCompareLibraryGrid();
        },
      });
    }
    setupCompareInteractions();
    compareInitialized = true;
  }
  updateRunButton();
  if (typeof loadTree === 'function') {
    loadTree().then(() => loadCompareLibraryGrid()).catch(() => loadCompareLibraryGrid());
  } else {
    loadCompareLibraryGrid();
  }
}

window.loadCompareLibraryGrid = loadCompareLibraryGrid;
window.initCompareTab = initCompareTab;
window.getDetectMode = getDetectMode;
window.setDetectMode = setDetectMode;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCompareTab);
} else {
  initCompareTab();
}
