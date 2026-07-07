/** Image Library tab — upload, view, delete. */

const libraryViewerState = { image: null, dragActive: false, objectUrl: null };

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
  ].filter(Boolean);
  meta.textContent = parts.join(' · ');
  imageEl.alt = img.filename || 'Library image';
  imageEl.removeAttribute('src');
  setViewerStatus('Loading preview…', false);
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
  libraryViewerState.image = null;
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
      else reject(new Error(data?.detail || xhr.statusText || 'Upload failed'));
    });
    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.send(formData);
  });
}

document.getElementById('form-tree-upload')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError?.();

  const nodeId = document.getElementById('upload-node')?.value;
  const fileInput = document.getElementById('upload-file');
  const file = fileInput?.files?.[0];
  if (!nodeId) return showDdaError?.('Select a tree node.');
  if (!file) return showDdaError?.('Select a file.');

  const ext = (file.name.split('.').pop() || '').toLowerCase();
  const isTiff = ext === 'tif' || ext === 'tiff';
  const cfg = window.ddaState?.localCfg || {};
  const maxBytes = isTiff
    ? (cfg.maxGeotiffBytes || (cfg.maxGeotiffMb || 15360) * 1024 * 1024)
    : (cfg.maxImageBytes || (cfg.maxImageMb || 15360) * 1024 * 1024);
  if (file.size > maxBytes) {
    return showDdaError?.(`File is ${formatBytes(file.size)} — max ${formatBytes(maxBytes)}.`);
  }

  const form = new FormData();
  form.append('file', file);
  form.append('image_type', document.getElementById('upload-image-type')?.value || 'GeoTIFF');
  form.append('capture_date', document.getElementById('upload-capture-date')?.value || '');
  const manualBounds = document.getElementById('upload-manual-bounds')?.value?.trim();
  if (manualBounds) form.append('manual_bounds', manualBounds);

  const btn = document.getElementById('btn-tree-upload');
  const progWrap = document.getElementById('upload-progress');
  const progFill = document.getElementById('upload-progress-fill');
  const progLabel = document.getElementById('upload-progress-label');

  btn.disabled = true;
  progWrap?.classList.remove('hidden');
  if (progFill) progFill.style.width = '0%';

  try {
    await uploadWithProgress(`/api/dda/tree/nodes/${nodeId}/images/upload`, form, (loaded, total) => {
      const pct = total ? Math.round((loaded / total) * 100) : 0;
      if (progFill) progFill.style.width = pct + '%';
      if (progLabel) progLabel.textContent = `Uploading… ${pct}%`;
    });
    showDdaSuccess?.('Uploaded. Refreshing…');
    fileInput.value = '';
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError?.(err.message || 'Upload failed.');
  } finally {
    btn.disabled = false;
    setTimeout(() => progWrap?.classList.add('hidden'), 2000);
  }
});
