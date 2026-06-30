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
