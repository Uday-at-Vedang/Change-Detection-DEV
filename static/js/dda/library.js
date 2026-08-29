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
  const parts = [
    img.breadcrumb || img.nodePath || '',
    img.imageType || '',
    typeof formatBytes === 'function' ? formatBytes(img.fileSizeBytes) : '',
    img.width && img.height ? `${img.width}×${img.height}` : '',
    img.hasGeoref ? 'georeferenced' : '',
  ].filter(Boolean);
  meta.textContent = parts.join(' · ');
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

const LIBRARY_IMAGE_EXTS = ['.tif', '.tiff', '.png', '.jpg', '.jpeg'];
let pendingUploadFiles = [];

function isLibraryImageFile(file) {
  const name = (file.name || '').toLowerCase();
  if (!name || name.startsWith('.') || name === '.ds_store') return false;
  if ((file._relativePath || file.webkitRelativePath || '').includes('__macosx')) return false;
  return LIBRARY_IMAGE_EXTS.some((ext) => name.endsWith(ext));
}

function guessImageType(file) {
  const ext = (file.name.split('.').pop() || '').toLowerCase();
  if (ext === 'tif' || ext === 'tiff') return 'GeoTIFF';
  if (ext === 'png') return 'PNG';
  if (ext === 'jpg' || ext === 'jpeg') return 'JPEG';
  return document.getElementById('upload-image-type')?.value || 'GeoTIFF';
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
  const images = pendingUploadFiles.filter(isLibraryImageFile);
  const skipped = pendingUploadFiles.length - images.length;
  if (!pendingUploadFiles.length) {
    el.textContent = 'No files selected.';
    return;
  }
  el.textContent = `${images.length} image${images.length === 1 ? '' : 's'} ready` +
    (skipped ? ` · ${skipped} skipped` : '');
}

function setPendingUploadFiles(files) {
  pendingUploadFiles = Array.from(files || []);
  updateUploadSummary();
}

function setUploadTargetNode(nodeId) {
  const sel = document.getElementById('upload-node');
  if (sel && nodeId) sel.value = String(nodeId);
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

async function uploadOneLibraryFile(nodeId, file, { onProgress } = {}) {
  const oversized = fileTooLarge(file);
  if (oversized) throw new Error(oversized);
  const form = new FormData();
  form.append('file', file);
  form.append('image_type', document.getElementById('upload-image-type')?.value || guessImageType(file));
  form.append('capture_date', document.getElementById('upload-capture-date')?.value || '');
  const manualBounds = document.getElementById('upload-manual-bounds')?.value?.trim();
  if (manualBounds) form.append('manual_bounds', manualBounds);
  return uploadWithProgress(`/api/dda/tree/nodes/${nodeId}/images/upload`, form, onProgress);
}

async function uploadLibraryFiles(files, { nodeId, preserveFolders = true } = {}) {
  if (uploadLibraryFiles._busy) return 0;
  const list = Array.from(files || []).filter(isLibraryImageFile);
  if (!list.length) throw new Error('No GeoTIFF, PNG, or JPEG files to upload.');
  const dest = nodeId || document.getElementById('upload-node')?.value;
  const needsFolders = preserveFolders && list.some((f) => relativeUploadPath(f).includes('/'));
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
  try {
    for (let i = 0; i < list.length; i++) {
      const file = list[i];
      const rel = relativeUploadPath(file);
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
      if (progLabel) {
        progLabel.textContent = `Uploading ${i + 1} of ${list.length}: ${file.name}`;
      }
      if (progFill) progFill.style.width = `${Math.round((i / list.length) * 100)}%`;
      try {
        await uploadOneLibraryFile(targetId, file, {
          onProgress: (loaded, total) => {
            const filePct = total ? loaded / total : 1;
            const overall = ((i + filePct) / list.length) * 100;
            if (progFill) progFill.style.width = `${Math.round(overall)}%`;
          },
        });
        ok += 1;
      } catch (err) {
        errors.push(`${file.name}: ${err.message || 'failed'}`);
      }
    }
    if (progFill) progFill.style.width = '100%';
    if (ok && typeof window.ddaState?.rescan === 'function') await window.ddaState.rescan();
    if (errors.length && ok) {
      showDdaError?.(`${ok} uploaded, ${errors.length} failed. ${errors[0]}`);
    } else if (errors.length) {
      throw new Error(errors[0]);
    } else {
      showDdaSuccess?.(`Uploaded ${ok} image${ok === 1 ? '' : 's'}.`);
    }
    setPendingUploadFiles([]);
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
    const dest = document.getElementById('upload-node')?.value;
    try {
      await uploadLibraryFiles(files, { nodeId: dest, preserveFolders: true });
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
  const files = pendingUploadFiles.length
    ? pendingUploadFiles
    : [
        ...Array.from(document.getElementById('upload-file')?.files || []),
        ...Array.from(document.getElementById('upload-folder')?.files || []),
      ];
  try {
    await uploadLibraryFiles(files, {
      nodeId: document.getElementById('upload-node')?.value,
      preserveFolders: true,
    });
  } catch (err) {
    showDdaError?.(err.message || 'Upload failed.');
  }
});

initLibraryDropzone();
window.uploadLibraryFiles = uploadLibraryFiles;
window.setUploadTargetNode = setUploadTargetNode;
window.setPendingUploadFiles = setPendingUploadFiles;
window.readDroppedEntries = readDroppedEntries;
