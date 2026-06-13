function renderHierarchy(data) {
  const tree = document.getElementById('lib-tree');
  if (!tree || !data?.zones) return;
  const filter = (document.getElementById('lib-tree-search')?.value || '').toLowerCase();

  tree.innerHTML = data.zones
    .filter((z) => !filter || z.name.toLowerCase().includes(filter) ||
      z.villages.some((v) => v.name.toLowerCase().includes(filter)))
    .map((zone) => `
      <div class="dda-tree-zone" data-zone-id="${zone.id}">
        <button type="button" class="dda-zone-toggle">${zone.name}</button>
        <div class="dda-tree-villages">
          ${zone.villages
            .filter((v) => !filter || v.name.toLowerCase().includes(filter) || zone.name.toLowerCase().includes(filter))
            .map((v) => `
            <button type="button" class="dda-tree-village" data-zone-id="${zone.id}" data-village-id="${v.id}">
              ${v.name}${v.imageCount ? ` (${v.imageCount})` : ''}
            </button>`).join('')}
        </div>
      </div>`).join('');

  tree.querySelectorAll('.dda-zone-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const zoneEl = btn.closest('.dda-tree-zone');
      zoneEl?.classList.toggle('open');
      const zoneId = parseInt(zoneEl?.dataset.zoneId, 10);
      window.ddaState.setSelection(zoneId, null);
      window.ddaState.refreshImages();
    });
  });

  tree.querySelectorAll('.dda-tree-village').forEach((btn) => {
    btn.addEventListener('click', () => {
      tree.querySelectorAll('.dda-tree-village').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      btn.closest('.dda-tree-zone')?.classList.add('open');
      window.ddaState.setSelection(
        parseInt(btn.dataset.zoneId, 10),
        parseInt(btn.dataset.villageId, 10),
      );
      window.ddaState.refreshImages();
    });
  });
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (window.ddaState?.hierarchy) renderHierarchy(window.ddaState.hierarchy);
});

function populateUploadSelects(data) {
  const zoneSel = document.getElementById('up-zone');
  const villageSel = document.getElementById('up-village');
  if (!zoneSel || !villageSel || !data?.zones) return;

  zoneSel.innerHTML = '<option value="">— Select —</option>';
  data.zones.forEach((z) => {
    const opt = document.createElement('option');
    opt.value = z.id;
    opt.textContent = z.name;
    zoneSel.appendChild(opt);
  });

  if (!zoneSel.dataset.bound) {
    zoneSel.dataset.bound = '1';
    zoneSel.addEventListener('change', () => {
      const hierarchy = window.ddaState?.hierarchy;
      const zid = parseInt(zoneSel.value, 10);
      villageSel.innerHTML = '<option value="">— Select —</option>';
      villageSel.disabled = !zid;
      if (!zid || !hierarchy) return;
      const zone = hierarchy.zones.find((z) => z.id === zid);
      (zone?.villages || []).forEach((v) => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = v.name;
        villageSel.appendChild(opt);
      });
    });
  }
}

function formatBytes(n) {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
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
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));
    xhr.send(formData);
  });
}

document.getElementById('form-upload')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError?.();
  const fileInput = document.getElementById('up-file');
  const file = fileInput?.files?.[0];
  if (!file) {
    showDdaError?.('Select a file to upload.');
    return;
  }

  const form = new FormData();
  form.append('file', file);
  form.append('zone_id', document.getElementById('up-zone').value);
  form.append('village_id', document.getElementById('up-village').value);
  form.append('area_name', document.getElementById('up-area').value || '');
  form.append('year', document.getElementById('up-year').value);
  form.append('capture_date', document.getElementById('up-date').value);
  form.append('source', document.getElementById('up-source').value);
  form.append('manual_bounds_json', document.getElementById('up-manual-bounds').value || '');

  const btn = document.getElementById('btn-upload');
  const progWrap = document.getElementById('upload-progress');
  const progFill = document.getElementById('upload-progress-fill');
  const progLabel = document.getElementById('upload-progress-label');

  btn.disabled = true;
  btn.textContent = 'Uploading…';
  progWrap?.classList.remove('hidden');
  if (progFill) progFill.style.width = '0%';
  if (progLabel) progLabel.textContent = `Uploading ${file.name} (${formatBytes(file.size)})… 0%`;

  try {
    const data = await uploadWithProgress('/api/dda/images/upload', form, (loaded, total) => {
      const pct = total ? Math.round((loaded / total) * 100) : 0;
      if (progFill) progFill.style.width = pct + '%';
      if (progLabel) progLabel.textContent = `Uploading… ${pct}% (${formatBytes(loaded)} / ${formatBytes(total)})`;
    });
    if (progFill) progFill.style.width = '100%';
    showDdaSuccess?.(data?.status === 'success' ? 'Image uploaded to library.' : 'Upload complete.');
    document.getElementById('form-upload').reset();
    document.getElementById('up-year').value = '2025';
    fileInput.value = '';
    const dateInput = document.getElementById('up-date');
    if (dateInput) dateInput.value = new Date().toISOString().slice(0, 10);
    window.ddaState.hierarchy = await ddaApi('GET', '/api/dda/hierarchy');
    renderHierarchy(window.ddaState.hierarchy);
    populateUploadSelects(window.ddaState.hierarchy);
    await window.ddaState.refreshImages();
  } catch (err) {
    showDdaError?.(err.message || 'Upload failed');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Upload to Library';
    setTimeout(() => progWrap?.classList.add('hidden'), 1500);
  }
});

// Default capture date = today
const dateInput = document.getElementById('up-date');
if (dateInput && !dateInput.value) {
  dateInput.value = new Date().toISOString().slice(0, 10);
}
