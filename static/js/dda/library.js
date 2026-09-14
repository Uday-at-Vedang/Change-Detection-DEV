/** Image Library tab — upload, view, delete. */

const libraryViewerState = { image: null, dragActive: false, objectUrl: null, mode: 'preview', map: null, mapBound: false, mapLastPath: '' };

function thumbUrlFor(path) {
  return `/api/dda/local/thumb?path=${encodeURIComponent(path)}`;
}

function previewUrlFor(path, maxSide) {
  const max = maxSide || ((libraryViewerState.image?.fileSizeBytes || 0) > 500 * 1024 * 1024 ? 1024 : 1600);
  return `/api/dda/local/preview?path=${encodeURIComponent(path)}&max=${max}`;
}

function revokeViewerObjectUrl() {
  if (libraryViewerState.objectUrl) {
    URL.revokeObjectURL(libraryViewerState.objectUrl);
    libraryViewerState.objectUrl = null;
  }
}

function setViewerStatus(msg, isError) {
  const el = document.getElementById('dda-image-viewer-status');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('dda-viewer-error', !!isError);
}

function decodeLibraryPath(encoded) {
  try {
    return decodeURIComponent(encoded || '');
  } catch (_) {
    return encoded || '';
  }
}

function findLibraryItem(path) {
  const items = window.ddaState?.libraryItems || [];
  return items.find((img) => img.path === path) || null;
}

function setViewerMode(mode) {
  libraryViewerState.mode = mode === 'map' ? 'map' : 'preview';
  const previewPane = document.getElementById('dda-image-viewer-preview-pane');
  const mapPane = document.getElementById('dda-image-viewer-map-pane');
  document.getElementById('dda-viewer-mode-preview')?.classList.toggle('active', libraryViewerState.mode === 'preview');
  document.getElementById('dda-viewer-mode-map')?.classList.toggle('active', libraryViewerState.mode === 'map');
  previewPane?.classList.toggle('hidden', libraryViewerState.mode !== 'preview');
  mapPane?.classList.toggle('hidden', libraryViewerState.mode !== 'map');
  if (libraryViewerState.mode === 'map' && libraryViewerState.image) {
    openLibraryMap(libraryViewerState.image);
  }
}

function setLibraryMapStatus(msg, isError) {
  const el = document.getElementById('dda-library-map-status');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('dda-viewer-error', !!isError);
}

async function openLibraryMap(img) {
  const status = (msg, err) => setLibraryMapStatus(msg, err);
  if (typeof DdaMapViewer !== 'function') {
    status('Map library failed to load. Check your network connection.', true);
    return;
  }
  if (!libraryViewerState.map) {
    libraryViewerState.map = new DdaMapViewer('dda-library-map');
  }
  if (!libraryViewerState.mapBound && typeof bindDdaMapToolbar === 'function') {
    bindDdaMapToolbar(libraryViewerState.map, {
      basemap: 'dda-lib-basemap',
      xyz: 'dda-lib-xyz',
      overlay: 'dda-lib-overlay',
      opacity: 'dda-lib-opacity',
      opacityVal: 'dda-lib-opacity-val',
      fit: 'dda-lib-fit',
    });
    libraryViewerState.mapBound = true;
  }
  libraryViewerState.map.ensureMap();
  libraryViewerState.map.invalidate();
  requestAnimationFrame(() => libraryViewerState.map.invalidate());
  if (libraryViewerState.mapLastPath === img.path && libraryViewerState.map.rasterLayers.tif) {
    status('Overlay on the basemap. Toggle the TIF overlay to compare.', false);
    return;
  }
  status('Loading georeferenced overlay…', false);
  try {
    const result = await libraryViewerState.map.loadRaster(img.path);
    libraryViewerState.mapLastPath = img.path;
    if (!result.ok) {
      status(
        'This image has no georeferencing (CRS / world file / bounds), so it cannot be placed on the map. Upload a GeoTIFF or enter W,S,E,N bounds.',
        true,
      );
      return;
    }
    const src = result.info?.georefSource || 'georef';
    const crs = result.info?.crs ? ` · ${result.info.crs}` : '';
    status(`Overlay on ${result.info?.canTile ? 'XYZ tiles' : 'preview'} (${src}${crs}). Toggle the TIF overlay to compare with the basemap.`, false);
  } catch (err) {
    console.warn('Library map failed:', err);
    status(err.message || 'Could not load map overlay.', true);
  }
}

function openLibraryImageViewer(img) {
  const modal = document.getElementById('dda-image-viewer-modal');
  const title = document.getElementById('dda-image-viewer-title');
  const meta = document.getElementById('dda-image-viewer-meta');
  const imageEl = document.getElementById('dda-image-viewer-img');
  if (!modal || !title || !meta || !imageEl || !img) return;

  revokeViewerObjectUrl();
  libraryViewerState.image = img;
  title.textContent = img.filename || img.imageName || 'Image';
  meta.innerHTML = typeof buildImageMetaHtml === 'function'
    ? buildImageMetaHtml(img)
    : [
        img.breadcrumb || img.nodePath || '',
        [img.villageName, img.zoneName].filter(Boolean).join(', '),
        img.captureDate ? `Capture ${String(img.captureDate).slice(0, 10)}` : '',
        img.year != null ? `Year ${img.year}` : '',
        img.imageType || '',
        img.source || '',
        typeof formatBytes === 'function' ? formatBytes(img.fileSizeBytes) : '',
      ].filter(Boolean).join(' · ');
  imageEl.alt = img.filename || 'Library image';
  imageEl.removeAttribute('src');
  setViewerStatus('Loading preview…', false);
  setViewerMode('preview');
  modal.classList.remove('hidden');
  loadPreviewIntoViewer(img, imageEl);
}

async function loadPreviewIntoViewer(img, imageEl) {
  const thumb = img.thumbUrl || thumbUrlFor(img.path);
  imageEl.onerror = () => {
    setViewerStatus('Could not load image preview. The file may be missing or still processing.', true);
  };

  // Show thumbnail immediately while the larger preview is generated server-side
  imageEl.src = thumb;

  const previewUrl = img.previewUrl || previewUrlFor(img.path);
  try {
    const res = await fetch(previewUrl, { credentials: 'include' });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || res.statusText);
    }
    const blob = await res.blob();
    if (!blob.type.startsWith('image/')) {
      throw new Error('Preview response was not an image');
    }
    revokeViewerObjectUrl();
    libraryViewerState.objectUrl = URL.createObjectURL(blob);
    imageEl.src = libraryViewerState.objectUrl;
    setViewerStatus('', false);
  } catch (err) {
    console.warn('Library preview failed:', err);
    setViewerStatus(
      (img.fileSizeBytes || 0) > 500 * 1024 * 1024
        ? 'Large file — showing thumbnail. Full preview may take a minute on first load.'
        : 'Showing thumbnail — full preview unavailable.',
      false,
    );
  }
}

function closeLibraryImageViewer() {
  document.getElementById('dda-image-viewer-modal')?.classList.add('hidden');
  const imageEl = document.getElementById('dda-image-viewer-img');
  if (imageEl) {
    imageEl.removeAttribute('src');
    imageEl.onerror = null;
  }
  revokeViewerObjectUrl();
  setViewerStatus('', false);
  setLibraryMapStatus('', false);
  libraryViewerState.image = null;
  libraryViewerState.mapLastPath = '';
  if (libraryViewerState.map) {
    libraryViewerState.map.clearRasters();
    libraryViewerState.map.clearGeoJson();
  }
  setViewerMode('preview');
}

async function deleteLibraryImage(img) {
  if (!img?.path) return;
  const label = img.filename || img.imageName || img.path;
  if (!confirm(`Delete "${label}" from the library? This removes the file from disk.`)) return;

  hideDdaError?.();
  setViewerStatus('Deleting…', false);
  const deleteBtn = document.getElementById('dda-image-delete-btn');
  if (deleteBtn) deleteBtn.disabled = true;
  try {
    await ddaApi('POST', '/api/dda/local/images/delete', {
      body: JSON.stringify({ path: img.path }),
    });
    showDdaSuccess?.('Image deleted.');
    closeLibraryImageViewer();
    if (typeof loadLibraryImages === 'function') await loadLibraryImages();
    else if (window.ddaState?.rescan) await window.ddaState.rescan();
  } catch (err) {
    const msg = err.message || 'Could not delete image.';
    showDdaError?.(msg);
    setViewerStatus(msg, true);
  } finally {
    if (deleteBtn) deleteBtn.disabled = false;
  }
}

function bindLibraryGridCards(grid) {
  if (!grid) return;
  grid.querySelectorAll('.dda-card-img').forEach((card) => {
    card.addEventListener('dragstart', (e) => {
      libraryViewerState.dragActive = true;
      const path = decodeLibraryPath(card.dataset.imagePath);
      e.dataTransfer.setData('application/x-dda-image-path', path);
      e.dataTransfer.setData('text/plain', path);
    });
    card.addEventListener('dragend', () => {
      setTimeout(() => { libraryViewerState.dragActive = false; }, 0);
    });

    card.querySelector('.dda-card-delete-btn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      const path = decodeLibraryPath(card.dataset.imagePath);
      const img = findLibraryItem(path) || { path, filename: path.split('/').pop() };
      deleteLibraryImage(img);
    });

    card.addEventListener('click', () => {
      if (libraryViewerState.dragActive) return;
      const path = decodeLibraryPath(card.dataset.imagePath);
      const img = findLibraryItem(path) || { path, filename: path.split('/').pop() };
      openLibraryImageViewer(img);
    });
  });
}

document.getElementById('dda-image-viewer-close')?.addEventListener('click', closeLibraryImageViewer);
document.getElementById('dda-viewer-mode-preview')?.addEventListener('click', () => setViewerMode('preview'));
document.getElementById('dda-viewer-mode-map')?.addEventListener('click', () => setViewerMode('map'));
document.getElementById('dda-image-delete-btn')?.addEventListener('click', () => {
  if (libraryViewerState.image) deleteLibraryImage(libraryViewerState.image);
});
document.getElementById('dda-image-viewer-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'dda-image-viewer-modal') closeLibraryImageViewer();
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const modal = document.getElementById('dda-image-viewer-modal');
  if (modal && !modal.classList.contains('hidden')) closeLibraryImageViewer();
});

function uploadWithProgress(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    });
    xhr.addEventListener('load', () => {
      let data = null;
      try { data = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(formatApiError?.(data?.detail) || data?.detail || xhr.statusText || 'Upload failed'));
    });
    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.send(formData);
  });
}

const IMAGE_TYPE_EXTENSIONS = {
  GeoTIFF: ['.tif', '.tiff'],
  PNG: ['.png'],
  JPEG: ['.jpg', '.jpeg'],
};

let pendingUploadFiles = [];
/** @type {{file: File, serverName: string, selected: boolean}[]} */
let pendingUploadEntries = [];

function currentUploadImageType() {
  return document.getElementById('upload-image-type')?.value || 'GeoTIFF';
}

function allowedExtsForType(imageType) {
  return IMAGE_TYPE_EXTENSIONS[imageType] || IMAGE_TYPE_EXTENSIONS.GeoTIFF;
}

function fileExtension(name) {
  const n = String(name || '');
  const i = n.lastIndexOf('.');
  if (i < 0) return '';
  return n.slice(i).toLowerCase();
}

function formatAllowedExts(exts) {
  if (exts.length === 1) return exts[0];
  if (exts.length === 2) return `${exts[0]} or ${exts[1]}`;
  return `${exts.slice(0, -1).join(', ')} or ${exts[exts.length - 1]}`;
}

function formatRejectedNames(names) {
  const list = names.slice(0, 3);
  const more = names.length - list.length;
  const joined = list.map((n) => `"${n}"`).join(', ');
  return more > 0 ? `${joined}, and ${more} more` : joined;
}

function setUploadTypeError(msg) {
  const el = document.getElementById('upload-type-error');
  if (el) {
    el.textContent = msg || '';
    el.classList.toggle('hidden', !msg);
  }
  if (msg) showDdaError?.(msg);
  else hideDdaError?.();
}

function clearUploadTypeError() {
  setUploadTypeError('');
}

function splitFilesForImageType(files, imageType) {
  const allowed = allowedExtsForType(imageType);
  const accepted = [];
  const rejected = [];
  Array.from(files || []).forEach((file) => {
    const name = file.name || '';
    if (!name || name.startsWith('.') || name.toLowerCase() === '.ds_store') {
      rejected.push(file);
      return;
    }
    if ((file._relativePath || file.webkitRelativePath || '').toLowerCase().includes('__macosx')) {
      rejected.push(file);
      return;
    }
    const ext = fileExtension(name);
    if (!ext || !allowed.includes(ext)) rejected.push(file);
    else accepted.push(file);
  });
  return { accepted, rejected };
}

function rejectMessage(imageType, rejected, { typeChanged = false } = {}) {
  const allowed = allowedExtsForType(imageType);
  const names = rejected.map((f) => f.name || 'file');
  const named = formatRejectedNames(names);
  const allow = formatAllowedExts(allowed);
  if (typeChanged) {
    return `Image type changed to ${imageType}. ${rejected.length} selected file${rejected.length === 1 ? ' is' : 's are'} not ${allow} and ${rejected.length === 1 ? 'was' : 'were'} removed: ${named}.`;
  }
  if (rejected.length === 1) {
    const ext = fileExtension(rejected[0].name);
    if (!ext) {
      return `Image type ${imageType} only allows ${allow}. "${rejected[0].name}" has no supported extension and was not added.`;
    }
    return `Image type ${imageType} only allows ${allow}. "${rejected[0].name}" was not added.`;
  }
  return `Image type ${imageType} only allows ${allow}. ${rejected.length} files were not added: ${named}.`;
}

function syncUploadAcceptAttribute() {
  const type = currentUploadImageType();
  const accept = allowedExtsForType(type).join(',');
  const fileInput = document.getElementById('upload-file');
  const folderInput = document.getElementById('upload-folder');
  if (fileInput) fileInput.accept = accept;
  if (folderInput) folderInput.accept = accept;
  const hint = document.getElementById('upload-accept-hint');
  if (hint) {
    hint.textContent = `${type} only: ${formatAllowedExts(allowedExtsForType(type))}. Multiple files and nested folders are supported.`;
  }
}

function isLibraryImageFile(file) {
  const name = (file.name || '').toLowerCase();
  if (!name || name.startsWith('.') || name === '.ds_store') return false;
  if ((file._relativePath || file.webkitRelativePath || '').includes('__macosx')) return false;
  const allowed = allowedExtsForType(currentUploadImageType());
  return allowed.includes(fileExtension(name));
}

function relativeUploadPath(file) {
  return (file._relativePath || file.webkitRelativePath || '').replace(/\\/g, '/').replace(/^\//, '');
}

function fileTooLarge(file) {
  const ext = (file.name.split('.').pop() || '').toLowerCase();
  const isTiff = ext === 'tif' || ext === 'tiff';
  const cfg = window.ddaState?.localCfg || {};
  const maxBytes = isTiff
    ? (cfg.maxGeotiffBytes || (cfg.maxGeotiffMb || 15360) * 1024 * 1024)
    : (cfg.maxImageBytes || (cfg.maxImageMb || 15360) * 1024 * 1024);
  if (file.size > maxBytes) {
    return `${file.name} is ${formatBytes(file.size)} — max ${formatBytes(maxBytes)}.`;
  }
  return null;
}

function updateUploadSummary() {
  const el = document.getElementById('upload-file-summary');
  if (!el) return;
  const n = pendingUploadEntries.length;
  if (!n) {
    el.textContent = 'No files selected.';
    return;
  }
  el.textContent = `${n} image${n === 1 ? '' : 's'} ready for ${currentUploadImageType()}.`;
}

function renderUploadFileList() {
  const wrap = document.getElementById('upload-file-list-wrap');
  const tbody = document.getElementById('upload-file-tbody');
  if (!wrap || !tbody) return;
  if (!pendingUploadEntries.length) {
    wrap.classList.add('hidden');
    tbody.innerHTML = '';
    return;
  }
  wrap.classList.remove('hidden');
  const type = currentUploadImageType();
  tbody.innerHTML = pendingUploadEntries.map((entry, idx) => `
    <tr data-idx="${idx}">
      <td><input type="checkbox" class="upload-file-check" ${entry.selected ? 'checked' : ''} /></td>
      <td class="dda-upload-orig">${escapeHtml(entry.file.name)}</td>
      <td><input type="text" class="upload-server-name" value="${escapeHtml(entry.serverName)}" aria-label="Server filename" /></td>
      <td>${escapeHtml(type)}</td>
      <td>${typeof formatBytes === 'function' ? formatBytes(entry.file.size) : entry.file.size}</td>
    </tr>`).join('');

  tbody.querySelectorAll('.upload-file-check').forEach((cb) => {
    cb.addEventListener('change', () => {
      const idx = parseInt(cb.closest('tr').dataset.idx, 10);
      if (pendingUploadEntries[idx]) pendingUploadEntries[idx].selected = cb.checked;
    });
  });
  tbody.querySelectorAll('.upload-server-name').forEach((input) => {
    input.addEventListener('change', () => {
      const idx = parseInt(input.closest('tr').dataset.idx, 10);
      if (pendingUploadEntries[idx]) {
        const name = (input.value || '').trim() || pendingUploadEntries[idx].file.name;
        pendingUploadEntries[idx].serverName = name.split(/[/\\]/).pop();
        input.value = pendingUploadEntries[idx].serverName;
      }
    });
  });
}

function setPendingUploadFiles(files, { append = false } = {}) {
  const type = currentUploadImageType();
  const { accepted, rejected } = splitFilesForImageType(files, type);
  if (rejected.length) setUploadTypeError(rejectMessage(type, rejected));
  else clearUploadTypeError();

  const entries = accepted.map((file) => ({
    file,
    serverName: (file.name || 'upload').split(/[/\\]/).pop(),
    selected: true,
  }));
  pendingUploadEntries = append ? pendingUploadEntries.concat(entries) : entries;
  pendingUploadFiles = pendingUploadEntries.map((e) => e.file);
  updateUploadSummary();
  renderUploadFileList();
}

function revalidatePendingForImageType({ typeChanged = false } = {}) {
  const type = currentUploadImageType();
  syncUploadAcceptAttribute();
  if (!pendingUploadEntries.length) {
    clearUploadTypeError();
    updateUploadSummary();
    renderUploadFileList();
    suggestSourceFromType();
    return;
  }
  const files = pendingUploadEntries.map((e) => e.file);
  const { accepted, rejected } = splitFilesForImageType(files, type);
  if (rejected.length) {
    const keepNames = new Set(accepted.map((f) => f));
    pendingUploadEntries = pendingUploadEntries.filter((e) => keepNames.has(e.file));
    pendingUploadFiles = pendingUploadEntries.map((e) => e.file);
    setUploadTypeError(rejectMessage(type, rejected, { typeChanged: true }));
  } else {
    clearUploadTypeError();
  }
  updateUploadSummary();
  renderUploadFileList();
  suggestSourceFromType();
}

function suggestSourceFromType() {
  const sourceSel = document.getElementById('upload-source');
  if (!sourceSel || sourceSel.dataset.userPicked === '1') return;
  const type = currentUploadImageType();
  if (type === 'Satellite') sourceSel.value = 'satellite';
  else if (type === 'Drone') sourceSel.value = 'drone';
}

function populateUploadYearOptions() {
  const sel = document.getElementById('upload-year');
  const filterSel = document.getElementById('lib-filter-year');
  if (!sel) return;
  const current = new Date().getFullYear();
  const years = [];
  for (let y = current; y >= current - 15; y--) years.push(y);
  const prev = sel.value;
  sel.innerHTML = '<option value="">— Select year —</option>' +
    years.map((y) => `<option value="${y}">${y}</option>`).join('');
  if (prev) sel.value = prev;
  if (filterSel) {
    const fprev = filterSel.value;
    filterSel.innerHTML = '<option value="">All</option>' +
      years.map((y) => `<option value="${y}">${y}</option>`).join('');
    if (fprev) filterSel.value = fprev;
  }
}

const uploadLocations = { zones: [], villages: [] };

function renderUploadVillageOptions() {
  const villageSel = document.getElementById('upload-village');
  if (!villageSel) return;
  const zoneId = document.getElementById('upload-zone')?.value || '';
  const previous = villageSel.value;
  const list = zoneId
    ? uploadLocations.villages.filter((v) => String(v.zoneId) === String(zoneId))
    : uploadLocations.villages;
  const empty = zoneId ? '— Select village —' : '— Select village (or pick a zone first) —';
  villageSel.innerHTML = `<option value="">${empty}</option>` + list.map((v) => {
    const label = zoneId ? v.name : `${v.name} · ${v.zoneName || ''}`;
    return `<option value="${v.id}">${escapeHtml(label)}</option>`;
  }).join('');
  if (previous && list.some((v) => String(v.id) === String(previous))) villageSel.value = previous;
}

function renderLibFilterLocationOptions() {
  const zoneSel = document.getElementById('lib-filter-zone');
  const villageSel = document.getElementById('lib-filter-village');
  if (zoneSel) {
    const prev = zoneSel.value;
    zoneSel.innerHTML = '<option value="">All</option>' + uploadLocations.zones
      .map((z) => `<option value="${z.id}">${escapeHtml(z.name)}</option>`).join('');
    if (prev) zoneSel.value = prev;
  }
  if (villageSel) {
    const zoneId = zoneSel?.value || '';
    const prev = villageSel.value;
    const list = zoneId
      ? uploadLocations.villages.filter((v) => String(v.zoneId) === String(zoneId))
      : uploadLocations.villages;
    villageSel.innerHTML = '<option value="">All</option>' + list
      .map((v) => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join('');
    if (prev) villageSel.value = prev;
  }
}

function markUploadLocationPicked() {
  const zoneSel = document.getElementById('upload-zone');
  if (zoneSel) zoneSel.dataset.userPicked = '1';
}

function suggestUploadLocationFromFolder() {
  const zoneSel = document.getElementById('upload-zone');
  const villageSel = document.getElementById('upload-village');
  if (!zoneSel || zoneSel.dataset.userPicked === '1') return;
  const path = document.getElementById('upload-node')?.selectedOptions?.[0]?.textContent || '';
  const segments = path.split(/[/\\]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!segments.length) return;
  const zone = uploadLocations.zones.find((z) => segments.includes(String(z.name || '').toLowerCase()));
  if (zone) zoneSel.value = String(zone.id);
  renderUploadVillageOptions();
  const matches = uploadLocations.villages.filter((v) => {
    if (!segments.includes(String(v.name || '').toLowerCase())) return false;
    if (zone && String(v.zoneId) !== String(zone.id)) return false;
    return true;
  });
  if (matches.length === 1 && villageSel) {
    if (!zoneSel.value) zoneSel.value = String(matches[0].zoneId);
    renderUploadVillageOptions();
    villageSel.value = String(matches[0].id);
  }
  // Suggest year from folder path segments
  const yearSel = document.getElementById('upload-year');
  if (yearSel && !yearSel.value) {
    const yearSeg = segments.find((s) => /^\d{4}$/.test(s) && (s.startsWith('19') || s.startsWith('20')));
    if (yearSeg) yearSel.value = yearSeg;
  }
}

async function loadUploadLocations() {
  const zoneSel = document.getElementById('upload-zone');
  if (!zoneSel) return;
  try {
    const data = await ddaApi('GET', '/api/dda/masters/options');
    const zoneNames = new Map((data.zones || []).map((z) => [z.id, z.name]));
    uploadLocations.zones = data.zones || [];
    uploadLocations.villages = (data.villages || []).map((v) => ({
      ...v, zoneName: zoneNames.get(v.zoneId) || '',
    }));
  } catch (_) {
    return;
  }
  const previousZone = zoneSel.value;
  zoneSel.innerHTML = '<option value="">— Select zone —</option>' + uploadLocations.zones
    .map((z) => `<option value="${z.id}">${escapeHtml(z.name)}</option>`).join('');
  if (previousZone) zoneSel.value = previousZone;
  renderUploadVillageOptions();
  renderLibFilterLocationOptions();

  if (!zoneSel.dataset.bound) {
    zoneSel.dataset.bound = '1';
    zoneSel.addEventListener('change', () => {
      markUploadLocationPicked();
      renderUploadVillageOptions();
    });
    document.getElementById('upload-village')?.addEventListener('change', (e) => {
      markUploadLocationPicked();
      const village = uploadLocations.villages.find((v) => String(v.id) === String(e.target.value));
      if (village) zoneSel.value = String(village.zoneId);
      renderUploadVillageOptions();
      if (village) document.getElementById('upload-village').value = String(village.id);
    });
    document.getElementById('upload-node')?.addEventListener('change', suggestUploadLocationFromFolder);
  }
}

function setUploadTargetNode(nodeId) {
  const sel = document.getElementById('upload-node');
  if (sel && nodeId) {
    sel.value = String(nodeId);
    sel.dispatchEvent(new Event('change'));
  }
  document.getElementById('upload-card')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function ensureFolderPath(parentId, segments) {
  let current = parentId ? Number(parentId) : null;
  for (const raw of segments) {
    const name = String(raw || '').trim();
    if (!name || name === '.' || name === '..') continue;
    const res = await ddaApi('POST', '/api/dda/tree/nodes/ensure', {
      body: JSON.stringify({ parent_id: current, node_name: name, node_type: 'Folder' }),
    });
    current = res.node.id;
  }
  return current;
}

function appendSharedUploadFields(form) {
  form.append('image_type', currentUploadImageType());
  form.append('capture_date', document.getElementById('upload-capture-date')?.value || '');
  const manualBounds = document.getElementById('upload-manual-bounds')?.value?.trim();
  if (manualBounds) form.append('manual_bounds', manualBounds);
  const zoneId = document.getElementById('upload-zone')?.value || '';
  const villageId = document.getElementById('upload-village')?.value || '';
  if (zoneId) form.append('zone_id', zoneId);
  if (villageId) form.append('village_id', villageId);
  const year = document.getElementById('upload-year')?.value || '';
  if (year) form.append('year', year);
  const source = document.getElementById('upload-source')?.value || '';
  if (source) form.append('source', source);
}

async function uploadOneLibraryFile(nodeId, file, serverName, { onProgress } = {}) {
  const oversized = fileTooLarge(file);
  if (oversized) throw new Error(oversized);
  const form = new FormData();
  form.append('file', file, serverName || file.name);
  form.append('server_filename', serverName || file.name);
  appendSharedUploadFields(form);
  return uploadWithProgress(`/api/dda/tree/nodes/${nodeId}/images/upload`, form, onProgress);
}

async function uploadBulkLibraryFiles(nodeId, entries, { onProgress } = {}) {
  const form = new FormData();
  entries.forEach((entry) => {
    form.append('files', entry.file, entry.serverName || entry.file.name);
    form.append('filenames', entry.serverName || entry.file.name);
  });
  appendSharedUploadFields(form);
  return uploadWithProgress(`/api/dda/tree/nodes/${nodeId}/images/upload-bulk`, form, onProgress);
}

async function uploadLibraryFiles(files, { nodeId, preserveFolders = true, entries = null } = {}) {
  if (uploadLibraryFiles._busy) return 0;
  const type = currentUploadImageType();
  let workEntries = entries;
  if (!workEntries) {
    const { accepted, rejected } = splitFilesForImageType(files, type);
    if (rejected.length) {
      setUploadTypeError(rejectMessage(type, rejected));
      if (!accepted.length) throw new Error(rejectMessage(type, rejected));
    }
    workEntries = accepted.map((file) => ({
      file,
      serverName: (file.name || 'upload').split(/[/\\]/).pop(),
      selected: true,
    }));
  }
  workEntries = workEntries.filter((e) => e.selected !== false);
  if (!workEntries.length) throw new Error(`No ${type} files to upload.`);

  // Final gate before POST
  const recheck = splitFilesForImageType(workEntries.map((e) => e.file), type);
  if (recheck.rejected.length) {
    const msg = `Cannot upload. Image type ${type} only allows ${formatAllowedExts(allowedExtsForType(type))}.`;
    setUploadTypeError(msg);
    throw new Error(msg);
  }

  const dest = nodeId || document.getElementById('upload-node')?.value;
  const needsFolders = preserveFolders && workEntries.some((e) => relativeUploadPath(e.file).includes('/'));
  const canCreateFolders = typeof hasPermission === 'function'
    ? hasPermission(window.ddaPermissions, 'library', 'create')
    : window.ddaState?.userRole === 'admin';
  if (needsFolders && !canCreateFolders) {
    throw new Error('Creating subfolders from a dropped folder requires permission to create library folders. Upload files into an existing folder, or ask an admin.');
  }
  if (!dest && !needsFolders) throw new Error('Select a destination folder.');
  uploadLibraryFiles._busy = true;

  const btn = document.getElementById('btn-tree-upload');
  const progWrap = document.getElementById('upload-progress');
  const progFill = document.getElementById('upload-progress-fill');
  const progLabel = document.getElementById('upload-progress-label');
  if (btn) btn.disabled = true;
  progWrap?.classList.remove('hidden');

  const folderCache = new Map();
  let ok = 0;
  const errors = [];
  const storedNames = [];
  try {
    // Group by destination folder for bulk when no nested folders, or multiple files same dest
    const byTarget = new Map();
    for (const entry of workEntries) {
      const rel = relativeUploadPath(entry.file);
      const parts = rel.split('/').filter(Boolean);
      const dirParts = parts.length > 1 ? parts.slice(0, -1) : [];
      let targetId = dest ? Number(dest) : null;
      if (dirParts.length) {
        const key = `${targetId || 'root'}|${dirParts.join('/')}`;
        if (!folderCache.has(key)) {
          folderCache.set(key, await ensureFolderPath(targetId, dirParts));
        }
        targetId = folderCache.get(key);
      }
      if (!targetId) throw new Error('Select a destination folder.');
      if (!byTarget.has(targetId)) byTarget.set(targetId, []);
      byTarget.get(targetId).push(entry);
    }

    let done = 0;
    const total = workEntries.length;
    for (const [targetId, group] of byTarget.entries()) {
      if (group.length === 1) {
        const entry = group[0];
        if (progLabel) progLabel.textContent = `Uploading ${done + 1} of ${total}: ${entry.serverName}`;
        try {
          const res = await uploadOneLibraryFile(targetId, entry.file, entry.serverName, {
            onProgress: (loaded, tot) => {
              const filePct = tot ? loaded / tot : 1;
              if (progFill) progFill.style.width = `${Math.round(((done + filePct) / total) * 100)}%`;
            },
          });
          ok += 1;
          if (res?.image?.filename) storedNames.push(res.image.filename);
        } catch (err) {
          errors.push(`${entry.serverName}: ${err.message || 'failed'}`);
        }
        done += 1;
      } else {
        if (progLabel) progLabel.textContent = `Uploading ${group.length} files…`;
        try {
          const res = await uploadBulkLibraryFiles(targetId, group, {
            onProgress: (loaded, tot) => {
              const filePct = tot ? loaded / tot : 1;
              if (progFill) progFill.style.width = `${Math.round(((done + filePct * group.length) / total) * 100)}%`;
            },
          });
          ok += (res.uploaded || []).length;
          (res.uploaded || []).forEach((img) => {
            if (img.filename) storedNames.push(img.filename);
          });
          (res.failed || []).forEach((f) => errors.push(`${f.filename}: ${f.error}`));
        } catch (err) {
          errors.push(err.message || 'Bulk upload failed');
        }
        done += group.length;
      }
    }
    if (progFill) progFill.style.width = '100%';
    if (ok && typeof window.ddaState?.rescan === 'function') await window.ddaState.rescan();
    else if (ok && typeof loadLibraryImages === 'function') await loadLibraryImages({ resetPage: true });
    if (errors.length && ok) {
      showDdaError?.(`${ok} uploaded, ${errors.length} failed. ${errors[0]}`);
    } else if (errors.length) {
      throw new Error(errors[0]);
    } else {
      const nameHint = storedNames.length === 1 ? ` Saved as ${storedNames[0]}.` : '';
      showDdaSuccess?.(`Uploaded ${ok} image${ok === 1 ? '' : 's'}.${nameHint}`);
      clearUploadTypeError();
    }
    pendingUploadEntries = [];
    pendingUploadFiles = [];
    updateUploadSummary();
    renderUploadFileList();
    const fileInput = document.getElementById('upload-file');
    const folderInput = document.getElementById('upload-folder');
    if (fileInput) fileInput.value = '';
    if (folderInput) folderInput.value = '';
  } finally {
    if (btn) btn.disabled = false;
    uploadLibraryFiles._busy = false;
    setTimeout(() => progWrap?.classList.add('hidden'), 2000);
  }
  return ok;
}

function readDroppedEntries(dataTransfer) {
  const items = dataTransfer?.items;
  if (!items || !items.length || !items[0].webkitGetAsEntry) {
    return Promise.resolve(Array.from(dataTransfer?.files || []));
  }
  const readEntry = (entry, prefix = '') => new Promise((resolve) => {
    if (!entry) return resolve([]);
    if (entry.isFile) {
      entry.file((file) => {
        const rel = `${prefix}${entry.name}`.replace(/^\//, '');
        try { Object.defineProperty(file, 'webkitRelativePath', { value: rel }); } catch (_) {
          file._relativePath = rel;
        }
        resolve([file]);
      }, () => resolve([]));
      return;
    }
    if (!entry.isDirectory) return resolve([]);
    const reader = entry.createReader();
    const collected = [];
    const readBatch = () => {
      reader.readEntries(async (batch) => {
        if (!batch.length) {
          resolve(collected);
          return;
        }
        for (const child of batch) {
          const nextPrefix = `${prefix}${entry.name}/`;
          collected.push(...await readEntry(child, nextPrefix));
        }
        readBatch();
      }, () => resolve(collected));
    };
    readBatch();
  });

  return Promise.all(
    Array.from(items)
      .filter((it) => it.kind === 'file')
      .map((it) => readEntry(it.webkitGetAsEntry())),
  ).then((groups) => groups.flat());
}

function initLibraryDropzone() {
  const zone = document.getElementById('dda-upload-dropzone');
  if (!zone || zone.dataset.bound) return;
  zone.dataset.bound = '1';

  ['dragenter', 'dragover'].forEach((ev) => {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach((ev) => {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (ev === 'dragleave' && zone.contains(e.relatedTarget)) return;
      zone.classList.remove('is-dragover');
    });
  });
  zone.addEventListener('drop', async (e) => {
    const files = await readDroppedEntries(e.dataTransfer);
    if (!files.length) return;
    setPendingUploadFiles(files);
    if (!pendingUploadEntries.length) return;
    const dest = document.getElementById('upload-node')?.value;
    try {
      await uploadLibraryFiles(null, {
        nodeId: dest,
        preserveFolders: true,
        entries: pendingUploadEntries.slice(),
      });
    } catch (err) {
      showDdaError?.(err.message || 'Upload failed.');
    }
  });

  document.getElementById('upload-file')?.addEventListener('change', (e) => {
    setPendingUploadFiles(e.target.files);
  });
  document.getElementById('upload-folder')?.addEventListener('change', (e) => {
    setPendingUploadFiles(e.target.files);
  });
}

document.getElementById('form-tree-upload')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError?.();
  try {
    await uploadLibraryFiles(null, {
      nodeId: document.getElementById('upload-node')?.value,
      preserveFolders: true,
      entries: pendingUploadEntries.length ? pendingUploadEntries.slice() : null,
    });
  } catch (err) {
    showDdaError?.(err.message || 'Upload failed.');
  }
});

document.getElementById('upload-image-type')?.addEventListener('change', () => {
  revalidatePendingForImageType({ typeChanged: true });
});
document.getElementById('upload-source')?.addEventListener('change', (e) => {
  e.target.dataset.userPicked = '1';
});

function libFilterParams() {
  const params = new URLSearchParams();
  const year = document.getElementById('lib-filter-year')?.value || '';
  const type = document.getElementById('lib-filter-type')?.value || '';
  const zone = document.getElementById('lib-filter-zone')?.value || '';
  const village = document.getElementById('lib-filter-village')?.value || '';
  const from = document.getElementById('lib-filter-from')?.value || '';
  const to = document.getElementById('lib-filter-to')?.value || '';
  if (year) params.set('year', year);
  if (type) params.set('image_type', type);
  if (zone) params.set('zone_id', zone);
  if (village) params.set('village_id', village);
  if (from) params.set('capture_date_from', from);
  if (to) params.set('capture_date_to', to);
  return params;
}

function exportLibrary(format) {
  const params = libFilterParams();
  const q = document.getElementById('lib-filter')?.value?.trim() || '';
  if (typeof selectedNode !== 'undefined' && selectedNode?.id) {
    params.set('node_id', String(selectedNode.id));
  }
  if (q) params.set('q', q);
  params.set('format', format);
  window.location.href = `/api/dda/tree/images/export?${params.toString()}`;
}

document.getElementById('btn-lib-export-csv')?.addEventListener('click', () => exportLibrary('csv'));
document.getElementById('btn-lib-export-xlsx')?.addEventListener('click', () => exportLibrary('xlsx'));

document.getElementById('btn-lib-filter-clear')?.addEventListener('click', () => {
  ['lib-filter-year', 'lib-filter-type', 'lib-filter-zone', 'lib-filter-village', 'lib-filter-from', 'lib-filter-to'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  if (typeof loadLibraryImages === 'function') loadLibraryImages({ resetPage: true });
});

['lib-filter-year', 'lib-filter-type', 'lib-filter-zone', 'lib-filter-village', 'lib-filter-from', 'lib-filter-to'].forEach((id) => {
  document.getElementById(id)?.addEventListener('change', () => {
    if (id === 'lib-filter-zone') renderLibFilterLocationOptions();
    if (typeof loadLibraryImages === 'function') loadLibraryImages({ resetPage: true });
  });
});

document.getElementById('btn-lib-select-all')?.addEventListener('click', () => {
  const checks = document.querySelectorAll('.dda-card-select');
  const allOn = [...checks].every((c) => c.checked);
  checks.forEach((c) => { c.checked = !allOn; });
  updateLibDeleteSelectedBtn();
});

function updateLibDeleteSelectedBtn() {
  const btn = document.getElementById('btn-lib-delete-selected');
  if (!btn) return;
  const n = document.querySelectorAll('.dda-card-select:checked').length;
  btn.disabled = n === 0;
  btn.textContent = n ? `Delete selected (${n})` : 'Delete selected';
}

document.getElementById('btn-lib-delete-selected')?.addEventListener('click', async () => {
  const ids = [...document.querySelectorAll('.dda-card-select:checked')].map((c) => c.dataset.imageId).filter(Boolean);
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} selected image${ids.length === 1 ? '' : 's'}?`)) return;
  let ok = 0;
  const errors = [];
  for (const id of ids) {
    try {
      await ddaApi('DELETE', `/api/dda/tree/images/${id}`);
      ok += 1;
    } catch (err) {
      errors.push(err.message || 'failed');
    }
  }
  if (ok && typeof loadLibraryImages === 'function') await loadLibraryImages();
  if (errors.length) showDdaError?.(`${ok} deleted, ${errors.length} failed. ${errors[0]}`);
  else showDdaSuccess?.(`Deleted ${ok} image${ok === 1 ? '' : 's'}.`);
});

populateUploadYearOptions();
syncUploadAcceptAttribute();
suggestSourceFromType();
initLibraryDropzone();
loadUploadLocations();
window.loadUploadLocations = loadUploadLocations;
window.uploadLibraryFiles = uploadLibraryFiles;
window.setUploadTargetNode = setUploadTargetNode;
window.setPendingUploadFiles = setPendingUploadFiles;
window.readDroppedEntries = readDroppedEntries;
window.splitFilesForImageType = splitFilesForImageType;
window.libFilterParams = libFilterParams;
window.updateLibDeleteSelectedBtn = updateLibDeleteSelectedBtn;

// --- Bulk upload (Excel manifest + files) ---------------------------------

function openBulkUploadModal() {
  document.getElementById('bulk-upload-result')?.classList.add('hidden');
  document.getElementById('dda-bulk-upload-modal')?.classList.remove('hidden');
}
function closeBulkUploadModal() {
  document.getElementById('dda-bulk-upload-modal')?.classList.add('hidden');
}

document.getElementById('btn-open-bulk-upload')?.addEventListener('click', openBulkUploadModal);
document.getElementById('dda-bulk-upload-close')?.addEventListener('click', closeBulkUploadModal);
document.getElementById('dda-bulk-upload-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'dda-bulk-upload-modal') closeBulkUploadModal();
});

document.getElementById('bulk-upload-excel')?.addEventListener('change', (e) => {
  const f = e.target.files?.[0];
  document.getElementById('bulk-upload-excel-name').textContent = f ? f.name : 'No file selected.';
});
document.getElementById('bulk-upload-files')?.addEventListener('change', (e) => {
  const n = e.target.files?.length || 0;
  document.getElementById('bulk-upload-files-name').textContent = n
    ? `${n} file${n === 1 ? '' : 's'} selected.`
    : 'No files selected.';
});

function renderBulkUploadResult(data) {
  const wrap = document.getElementById('bulk-upload-result');
  const summaryEl = document.getElementById('bulk-upload-summary');
  const errorsEl = document.getElementById('bulk-upload-errors');
  if (!wrap || !summaryEl || !errorsEl) return;
  const s = data.summary || {};
  summaryEl.innerHTML = `
    <p><strong>${s.created || 0}</strong> created,
    <strong>${s.duplicates || 0}</strong> already existed (skipped),
    <strong>${s.errors || 0}</strong> error${(s.errors || 0) === 1 ? '' : 's'},
    <strong>${s.unmatchedFiles || 0}</strong> file${(s.unmatchedFiles || 0) === 1 ? '' : 's'} not listed in the sheet.</p>`;
  const rows = [
    ...(data.errors || []).map((e) => ({ row: e.row, file: e.file, reason: e.reason })),
    ...(data.unmatchedFiles || []).map((u) => ({ row: '—', file: u.file, reason: 'No matching row in the Excel sheet' })),
  ];
  errorsEl.innerHTML = rows.length
    ? `<table class="dda-logs-table"><thead><tr><th>Row</th><th>File</th><th>Reason</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td>${escapeHtml(String(r.row))}</td><td>${escapeHtml(r.file || '')}</td><td>${escapeHtml(r.reason || '')}</td></tr>`).join('')}
       </tbody></table>`
    : '';
  wrap.classList.remove('hidden');
}

document.getElementById('btn-bulk-upload-submit')?.addEventListener('click', async () => {
  const excelInput = document.getElementById('bulk-upload-excel');
  const filesInput = document.getElementById('bulk-upload-files');
  const excelFile = excelInput?.files?.[0];
  const imageFiles = Array.from(filesInput?.files || []);
  if (!excelFile) { showDdaError?.('Choose the filled-in Excel sheet first.'); return; }
  if (!imageFiles.length) { showDdaError?.('Choose the image files listed in the sheet.'); return; }

  const btn = document.getElementById('btn-bulk-upload-submit');
  const progWrap = document.getElementById('bulk-upload-progress');
  const progFill = document.getElementById('bulk-upload-progress-fill');
  const progLabel = document.getElementById('bulk-upload-progress-label');
  if (btn) btn.disabled = true;
  progWrap?.classList.remove('hidden');
  if (progLabel) progLabel.textContent = `Uploading ${imageFiles.length} file${imageFiles.length === 1 ? '' : 's'}…`;
  if (progFill) progFill.style.width = '0%';

  const form = new FormData();
  form.append('excel', excelFile);
  imageFiles.forEach((f) => form.append('files', f));

  try {
    const data = await uploadWithProgress('/api/dda/tree/bulk-upload', form, (loaded, total) => {
      if (progFill && total) progFill.style.width = `${Math.round((loaded / total) * 100)}%`;
    });
    if (progFill) progFill.style.width = '100%';
    renderBulkUploadResult(data);
    const created = data.summary?.created || 0;
    if (created) {
      showDdaSuccess?.(`Bulk upload complete — ${created} image${created === 1 ? '' : 's'} added.`);
      if (typeof window.ddaState?.rescan === 'function') await window.ddaState.rescan();
    } else {
      showDdaError?.('No images were created — see the results below.');
    }
  } catch (err) {
    showDdaError?.(err.message || 'Bulk upload failed.');
  } finally {
    if (btn) btn.disabled = false;
    setTimeout(() => progWrap?.classList.add('hidden'), 1500);
  }
});
