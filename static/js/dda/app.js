const API = '';

function escapeHtml(text) {
  if (text == null) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatApiError(detail) {
  if (!detail) return null;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && d.msg) || JSON.stringify(d)).join('; ');
  }
  return String(detail);
}

async function ddaApi(method, path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API + path, { method, headers, credentials: 'include', ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!res.ok) throw new Error(formatApiError(data?.detail) || res.statusText || 'Request failed');
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
let libraryTree = null;
const selection = { zoneId: null, zoneName: '', folderId: null, folderName: '', year: null, legacy: false };

window.ddaState = {
  get config() { return ddaConfig; },
  get localCfg() { return window._localCfg; },
  get tree() { return libraryTree; },
  get selection() { return { ...selection }; },
  get userRole() { return window._ddaUserRole || 'analyst'; },
  set userRole(r) { window._ddaUserRole = r; },
  setSelection(s) {
    selection.zoneId = s.zoneId ?? null;
    selection.zoneName = s.zoneName || '';
    selection.folderId = s.folderId ?? null;
    selection.folderName = s.folderName || '';
    selection.year = s.year ?? null;
    selection.legacy = !!s.legacy;
  },
  clearSelection() {
    selection.zoneId = null;
    selection.zoneName = '';
    selection.folderId = null;
    selection.folderName = '';
    selection.year = null;
    selection.legacy = false;
  },
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
    if (tab === 'detect' && typeof loadCompareLibraryGrid === 'function') {
      loadCompareLibraryGrid();
    }
  });
});

async function rescanLibrary() {
  const data = await ddaApi('POST', '/api/dda/local/rescan');
  libraryTree = data.tree || null;
  if (typeof renderLibraryTree === 'function' && libraryTree) renderLibraryTree(libraryTree);
  await loadLibraryImages();
  return data;
}

async function initDda() {
  hideDdaError();
  try {
    ddaConfig = await ddaApi('GET', '/api/dda/config');
    const localCfg = await ddaApi('GET', '/api/dda/local/config');
    window._localCfg = localCfg;

    try {
      const me = await ddaApi('GET', '/api/dda/me');
      window.ddaState.userRole = me.role || 'analyst';
    } catch (_) {
      window.ddaState.userRole = 'analyst';
    }

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
        'Layout: {zone}/{folder}/{year}/image.tif',
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

    const uploadTitle = document.getElementById('upload-card-title');
    const uploadHint = document.getElementById('upload-card-hint');
    if (uploadTitle) {
      uploadTitle.textContent = localCfg.isHosted ? 'Upload to Space storage' : 'Upload to library';
    }
    if (uploadHint) {
      uploadHint.textContent = localCfg.isHosted ? 'Required on Hugging Face' : 'Optional — or copy files manually';
    }

    const manageBtn = document.getElementById('btn-manage-library');
    if (manageBtn) manageBtn.classList.toggle('hidden', window.ddaState.userRole !== 'admin');

    const resHint = document.getElementById('dda-detect-res-hint');
    if (resHint && localCfg.detectionMaxSide) {
      resHint.textContent = `Detection runs at up to ${localCfg.detectionMaxSide}px per side for sharper results (set DETECTION_MAX_SIDE to change).`;
    }

    const uploadLimit = document.getElementById('hf-upload-limit');
    if (uploadLimit && localCfg.maxUploadGb) {
      uploadLimit.textContent = `Select zone, folder, and year. Upload .tif images (up to ${localCfg.maxUploadGb} GB each).`;
    }

    const appMode = ddaConfig.appMode || ddaConfig.mode || localCfg.appMode || 'dda';
    if (!localCfg.isHosted && appMode !== 'dda') {
      showDdaError('DDA mode is off. Run locally with: python run.py');
    }

    const urlTab = new URLSearchParams(window.location.search).get('tab');
    if (urlTab) {
      document.querySelector(`.dda-tab[data-tab="${urlTab}"]`)?.click();
    }

    libraryTree = await (typeof loadLibraryTree === 'function' ? loadLibraryTree() : null);
    if (typeof populateUploadZones === 'function') await populateUploadZones();
    await loadLibraryImages();
  } catch (err) {
    showDdaError(err.message || 'Failed to load library');
  }
}

function selectionTitle() {
  const s = window.ddaState.selection;
  if (s.legacy && s.year) return `Unassigned — ${s.year}`;
  const parts = [];
  if (s.zoneName) parts.push(s.zoneName);
  if (s.folderName) parts.push(s.folderName);
  if (s.year) parts.push(String(s.year));
  return parts.length ? parts.join(' / ') : 'All images';
}

async function loadLibraryImages() {
  const grid = document.getElementById('lib-grid');
  const title = document.getElementById('lib-grid-title');
  if (!grid) return;
  const q = document.getElementById('lib-filter')?.value?.trim() || '';
  const s = window.ddaState.selection;
  const params = new URLSearchParams();
  if (s.legacy && s.year) {
    params.set('year', String(s.year));
    params.set('legacy_only', 'true');
  } else {
    if (s.zoneId) params.set('zone_id', String(s.zoneId));
    if (s.folderId) params.set('folder_id', String(s.folderId));
    if (s.year) params.set('year', String(s.year));
  }
  if (q) params.set('q', q);
  if (title) title.textContent = `Images — ${selectionTitle()}`;
  try {
    const items = await ddaApi('GET', '/api/dda/local/images?' + params.toString());
    window.ddaState.libraryItems = items;
    if (!items.length) {
      grid.innerHTML = `<p class="dim">No images in ${escapeHtml(selectionTitle())}. Upload or copy files into <code>library_sources/zone/folder/year/</code>, then Refresh.</p>`;
      return;
    }
    grid.innerHTML = items.map((img) => {
      const thumb = img.thumbUrl ? img.thumbUrl.replace(/path=[^&]+/, 'path=' + encodeURIComponent(img.path)) : '';
      const crumb = img.breadcrumb || `${img.year} / ${img.filename}`;
      const assignBtn = img.legacy
        ? `<button type="button" class="btn btn-secondary btn-sm dda-assign-btn" data-path="${img.path.replace(/"/g, '&quot;')}" data-filename="${escapeHtml(img.filename)}">Assign location</button>`
        : '';
      return `
      <div class="dda-card-img" draggable="true" data-image-path="${img.path.replace(/"/g, '&quot;')}" title="${escapeHtml(img.filename)}">
        ${thumb ? `<img src="${thumb}" alt="" loading="lazy" />` : '<div class="meta">No preview</div>'}
        <div class="meta">
          <span class="dim dda-crumb">${escapeHtml(crumb)}</span><br/>
          <span class="dim">${formatBytes(img.fileSizeBytes)}</span>
          ${assignBtn}
        </div>
      </div>`;
    }).join('');
    grid.querySelectorAll('.dda-card-img').forEach((card) => {
      card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('application/x-dda-image-path', card.dataset.imagePath);
        e.dataTransfer.setData('text/plain', card.dataset.imagePath);
      });
    });
    grid.querySelectorAll('.dda-assign-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (typeof openReassignModal === 'function') {
          openReassignModal(btn.dataset.path, btn.dataset.filename);
        }
      });
    });
    if (typeof loadCompareLibraryGrid === 'function') loadCompareLibraryGrid();
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
