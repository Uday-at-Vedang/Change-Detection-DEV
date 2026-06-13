const API = '';

async function ddaApi(method, path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API + path, { method, headers, credentials: 'include', ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!res.ok) throw new Error(data?.detail || res.statusText || 'Request failed');
  return data;
}

function showDdaError(msg) {
  const el = document.getElementById('dda-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}
function hideDdaError() {
  document.getElementById('dda-error')?.classList.add('hidden');
}
function showDdaSuccess(msg) {
  const el = document.getElementById('dda-success');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

function formatBytes(n) {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
}

let ddaConfig = null;
let localYears = [];
let selectedYear = null;

window.ddaState = {
  get config() { return ddaConfig; },
  get localCfg() { return window._localCfg; },
  get years() { return localYears; },
  get selectedYear() { return selectedYear; },
  setYear(year) { selectedYear = year; },
  refreshImages: () => loadLibraryImages(),
  rescan: () => rescanLibrary(),
};

document.querySelectorAll('.dda-tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.dda-tab').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.dda-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    document.getElementById('tab-' + tab)?.classList.add('active');
  });
});

async function rescanLibrary() {
  const data = await ddaApi('POST', '/api/dda/local/rescan');
  localYears = data.years || [];
  if (typeof renderYearTree === 'function') renderYearTree(localYears);
  await loadLibraryImages();
  return data;
}

async function initDda() {
  hideDdaError();
  try {
    ddaConfig = await ddaApi('GET', '/api/dda/config');
    const localCfg = await ddaApi('GET', '/api/dda/local/config');
    window._localCfg = localCfg;

    const hint = document.getElementById('lib-config-hint');
    if (hint) {
      hint.textContent = localCfg.geotiffEnabled ? 'GeoTIFF ready' : 'GeoTIFF limited';
    }
    const paths = (localCfg.rootPaths || []).filter(Boolean);
    const pathEl = document.getElementById('lib-path-display');
    if (pathEl) {
      pathEl.textContent = [
        localCfg.isHosted ? 'HF writable storage:' : 'Local folders:',
        localCfg.writablePath || paths[0] || '',
        ...paths.filter((p) => p !== localCfg.writablePath),
      ].filter(Boolean).join('\n');
    }
    const folderPath = document.getElementById('lib-folder-path');
    if (folderPath) {
      folderPath.textContent = localCfg.isHosted
        ? 'Hugging Face — upload files below'
        : (paths[0] ? `Scanning: ${paths[0]}` : '');
    }
    const instr = document.getElementById('lib-instructions');
    if (instr && localCfg.instructions) instr.textContent = localCfg.instructions;

    const hfUpload = document.getElementById('hf-upload-card');
    if (hfUpload) hfUpload.classList.toggle('hidden', !localCfg.isHosted);

    if (!localCfg.isHosted && ddaConfig.appMode !== 'dda') {
      showDdaError('DDA mode is off. Run locally with: python run.py');
    }

    const yearsData = await ddaApi('GET', '/api/dda/local/years');
    localYears = yearsData.years || [];
    if (typeof renderYearTree === 'function') renderYearTree(localYears);
    await loadLibraryImages();
  } catch (err) {
    showDdaError(err.message || 'Failed to load library');
  }
}

async function loadLibraryImages() {
  const grid = document.getElementById('lib-grid');
  const title = document.getElementById('lib-grid-title');
  if (!grid) return;
  const q = document.getElementById('lib-filter')?.value?.trim() || '';
  const params = new URLSearchParams();
  if (selectedYear) params.set('year', String(selectedYear));
  if (q) params.set('q', q);
  if (title) {
    title.textContent = selectedYear ? `Images — ${selectedYear}` : 'Images — all years';
  }
  try {
    const items = await ddaApi('GET', '/api/dda/local/images?' + params.toString());
    if (!items.length) {
      const hf = window.ddaState?.localCfg?.isHosted;
      grid.innerHTML = hf
        ? `<p class="dim">No images on this Space yet. Use <strong>Upload to Space storage</strong> above (2025 / 2026), then click Refresh.</p>`
        : `<p class="dim">No images in ${selectedYear || 'library_sources'}. Copy .tif files into <code>library_sources/${selectedYear || 'YEAR'}/</code> and click Refresh.</p>`;
      return;
    }
    grid.innerHTML = items.map((img) => {
      const thumb = img.thumbUrl ? img.thumbUrl.replace(/path=[^&]+/, 'path=' + encodeURIComponent(img.path)) : '';
      return `
      <div class="dda-card-img" draggable="true" data-image-path="${img.path.replace(/"/g, '&quot;')}" title="${img.filename}">
        ${thumb ? `<img src="${thumb}" alt="" loading="lazy" />` : '<div class="meta">No preview</div>'}
        <div class="meta">
          <strong>${img.year}</strong><br/>
          ${img.filename}<br/>
          <span class="dim">${formatBytes(img.fileSizeBytes)}</span>
        </div>
      </div>`;
    }).join('');
    grid.querySelectorAll('.dda-card-img').forEach((card) => {
      card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('application/x-dda-image-path', card.dataset.imagePath);
        e.dataTransfer.setData('text/plain', card.dataset.imagePath);
      });
    });
  } catch (err) {
    grid.innerHTML = `<p class="dim">Could not load images: ${err.message}</p>`;
  }
}

document.getElementById('lib-filter')?.addEventListener('input', () => {
  clearTimeout(window._libFilterTimer);
  window._libFilterTimer = setTimeout(loadLibraryImages, 300);
});

document.getElementById('btn-refresh-lib')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-refresh-lib');
  btn.disabled = true;
  try {
    const data = await rescanLibrary();
    showDdaSuccess(`Library refreshed — ${data.totalImages || 0} image(s) found.`);
  } catch (err) {
    showDdaError(err.message);
  } finally {
    btn.disabled = false;
  }
});

initDda();
