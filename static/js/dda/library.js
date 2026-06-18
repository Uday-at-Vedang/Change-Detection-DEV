let uploadTreeCache = null;

async function populateUploadZones() {
  const zoneSel = document.getElementById('upload-zone');
  if (!zoneSel) return;
  try {
    const tree = await ddaApi('GET', '/api/dda/hierarchy/tree');
    uploadTreeCache = tree;
    zoneSel.innerHTML = '<option value="">— Select zone —</option>' +
      (tree.zones || []).filter((z) => z.slug !== '_unassigned').map((z) =>
        `<option value="${z.id}">${escapeHtml(z.name)}</option>`
      ).join('');
    populateUploadFolders();
    syncUploadPickers();
  } catch (_) {}
}

function populateUploadFolders() {
  const zoneId = parseInt(document.getElementById('upload-zone')?.value || '0', 10);
  const folderSel = document.getElementById('upload-folder');
  if (!folderSel || !uploadTreeCache) return;
  const zone = (uploadTreeCache.zones || []).find((z) => z.id === zoneId);
  folderSel.innerHTML = '<option value="">— Select folder —</option>' +
    (zone?.folders || []).map((f) =>
      `<option value="${f.id}">${escapeHtml(f.name)}</option>`
    ).join('');
}

function syncUploadPickers() {
  const s = window.ddaState?.selection;
  if (!s) return;
  const zoneSel = document.getElementById('upload-zone');
  const folderSel = document.getElementById('upload-folder');
  const yearInput = document.getElementById('upload-year');
  if (s.zoneId && zoneSel) {
    zoneSel.value = String(s.zoneId);
    populateUploadFolders();
  }
  if (s.folderId && folderSel) folderSel.value = String(s.folderId);
  if (s.year && yearInput) yearInput.value = String(s.year);
}

document.getElementById('upload-zone')?.addEventListener('change', populateUploadFolders);

function bindUploadForm(formId, fileId, btnId) {
  document.getElementById(formId)?.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideDdaError?.();
    const fileInput = document.getElementById(fileId);
    const file = fileInput?.files?.[0];
    if (!file) {
      showDdaError?.('Select a .tif file.');
      return;
    }

    const zoneId = document.getElementById('upload-zone')?.value;
    const folderId = document.getElementById('upload-folder')?.value;
    const year = document.getElementById('upload-year')?.value;
    if (!zoneId || !folderId || !year) {
      showDdaError?.('Select zone, folder, and year.');
      return;
    }

    const maxBytes = window.ddaState?.localCfg?.maxGeotiffBytes
      || (window.ddaState?.localCfg?.maxGeotiffMb || 5120) * 1024 * 1024;
    if (file.size > maxBytes) {
      showDdaError?.(`File is ${formatBytes(file.size)} — maximum upload size is ${formatBytes(maxBytes)}.`);
      return;
    }

    const form = new FormData();
    form.append('file', file);
    form.append('zone_id', zoneId);
    form.append('folder_id', folderId);
    form.append('year', year);

    const btn = document.getElementById(btnId);
    const progWrap = document.getElementById('upload-progress');
    const progFill = document.getElementById('upload-progress-fill');
    const progLabel = document.getElementById('upload-progress-label');

    btn.disabled = true;
    progWrap?.classList.remove('hidden');
    if (progFill) progFill.style.width = '0%';

    try {
      await uploadWithProgress('/api/dda/local/upload', form, (loaded, total) => {
        const pct = total ? Math.round((loaded / total) * 100) : 0;
        if (progFill) progFill.style.width = pct + '%';
        if (progLabel) progLabel.textContent = `Uploading… ${pct}% (${formatBytes(loaded)} / ${formatBytes(total)})`;
      });
      showDdaSuccess?.('Uploaded to library. Refreshing…');
      fileInput.value = '';
      await window.ddaState.rescan();
    } catch (err) {
      showDdaError?.(err.message || 'Upload failed.');
    } finally {
      btn.disabled = false;
      setTimeout(() => progWrap?.classList.add('hidden'), 2000);
    }
  });
}

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

bindUploadForm('form-hf-upload', 'hf-file', 'btn-hf-upload');

window.populateUploadZones = populateUploadZones;
window.syncUploadPickers = syncUploadPickers;
