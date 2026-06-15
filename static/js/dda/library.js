function renderYearTree(years) {
  const tree = document.getElementById('lib-tree');
  if (!tree) return;
  const filter = (document.getElementById('lib-tree-search')?.value || '').toLowerCase();

  const allBtn = `
    <button type="button" class="dda-tree-year ${window.ddaState.selectedYear === null ? 'active' : ''}" data-year="">
      All years
    </button>`;

  const yearBtns = (years || [])
    .filter((y) => !filter || String(y.year).includes(filter))
    .map((y) => `
      <button type="button" class="dda-tree-year ${window.ddaState.selectedYear === y.year ? 'active' : ''}" data-year="${y.year}">
        ${y.year} <span class="dim">(${y.imageCount})</span>
      </button>`).join('');

  tree.innerHTML = allBtn + yearBtns;

  tree.querySelectorAll('.dda-tree-year').forEach((btn) => {
    btn.addEventListener('click', () => {
      tree.querySelectorAll('.dda-tree-year').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const raw = btn.dataset.year;
      window.ddaState.setYear(raw ? parseInt(raw, 10) : null);
      window.ddaState.refreshImages();
    });
  });
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (window.ddaState?.years) renderYearTree(window.ddaState.years);
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

function formatBytes(n) {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
}

document.getElementById('form-hf-upload')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError?.();
  const fileInput = document.getElementById('hf-file');
  const file = fileInput?.files?.[0];
  if (!file) {
    showDdaError?.('Select a .tif file.');
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
  form.append('year', document.getElementById('hf-year').value);

  const btn = document.getElementById('btn-hf-upload');
  const progWrap = document.getElementById('hf-upload-progress');
  const progFill = document.getElementById('hf-upload-progress-fill');
  const progLabel = document.getElementById('hf-upload-progress-label');

  btn.disabled = true;
  progWrap?.classList.remove('hidden');
  if (progFill) progFill.style.width = '0%';

  try {
    await uploadWithProgress('/api/dda/local/upload', form, (loaded, total) => {
      const pct = total ? Math.round((loaded / total) * 100) : 0;
      if (progFill) progFill.style.width = pct + '%';
      if (progLabel) progLabel.textContent = `Uploading… ${pct}% (${formatBytes(loaded)} / ${formatBytes(total)})`;
    });
    showDdaSuccess?.('Uploaded to Space library. Click Refresh if images do not appear.');
    fileInput.value = '';
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError?.(err.message || 'Upload failed. Large files may exceed HF timeout — try a smaller file or run locally.');
  } finally {
    btn.disabled = false;
    setTimeout(() => progWrap?.classList.add('hidden'), 2000);
  }
});
