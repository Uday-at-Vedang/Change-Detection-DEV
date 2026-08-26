// Generic helpers (API, ddaApi, showDdaError, escapeHtml, formatBytes, etc.)
// now live in shared.js, loaded before this file — also used standalone by
// login_dda.html. Don't add page-load side effects there; this file is
// where the main SPA's init logic (initDda(), below) belongs instead.

let ddaConfig = null;
const selectedNode = { id: null, path: '' };

window.ddaState = {
  get config() { return ddaConfig; },
  get localCfg() { return window._localCfg; },
  get selectedNode() { return { ...selectedNode }; },
  get userRole() { return window._ddaUserRole || 'analyst'; },
  set userRole(r) { window._ddaUserRole = r; },
  setNode(n) { selectedNode.id = n.id; selectedNode.path = n.path || ''; },
  clearNode() { selectedNode.id = null; selectedNode.path = ''; },
  refreshImages: () => loadLibraryImages(),
  rescan: () => rescanLibrary(),
};

async function rescanLibrary() {
  const data = await ddaApi('POST', '/api/dda/local/rescan');
  if (typeof renderAllTrees === 'function') renderAllTrees({ tree: data.tree }, []);
  else if (typeof renderTree === 'function') renderTree({ tree: data.tree }, []);
  if (typeof populateManageNodeSelect === 'function') populateManageNodeSelect();
  await loadLibraryImages();
  if (typeof loadCompareLibraryGrid === 'function') await loadCompareLibraryGrid();
  return data;
}

async function initDda() {
  hideDdaError();
  try {
    try {
      const me = await ddaApi('GET', '/api/dda/me');
      window.ddaState.userRole = me.role || 'analyst';
    } catch (_) {
      window.ddaState.userRole = 'analyst';
    }

    ddaConfig = await ddaApi('GET', '/api/dda/config');
    const localCfg = await ddaApi('GET', '/api/dda/local/config');
    window._localCfg = localCfg;


    const pathEl = document.getElementById('lib-path-display');
    if (pathEl) {
      pathEl.textContent = [
        'Storage root:',
        localCfg.storageRoot || localCfg.writablePath || '',
        'Layout: {zone}/{area}/…/Images/file.tif',
      ].filter(Boolean).join('\n');
    }

    const folderPath = document.getElementById('lib-folder-path');
    if (folderPath) {
      folderPath.textContent = localCfg.isHosted
        ? 'Hugging Face — tree library'
        : `Storage: ${localCfg.storageRoot || ''}`;
    }

    const instr = document.getElementById('lib-instructions');
    if (instr && localCfg.instructions) instr.textContent = localCfg.instructions;

    const uploadLimit = document.getElementById('upload-limit-hint');
    if (uploadLimit && localCfg.maxUploadGb) {
      uploadLimit.textContent = `Select a folder, then drop files or a directory here. Max ${localCfg.maxUploadGb} GB per file.`;
    }

    const resHint = document.getElementById('dda-detect-res-hint');
    if (resHint && localCfg.detectionMaxSide) {
      resHint.textContent = `Detection runs at up to ${localCfg.detectionMaxSide}px per side.`;
    }

    if (typeof loadTree === 'function') await loadTree();
    await loadLibraryImages();
  } catch (err) {
    showDdaError(err.message || 'Failed to load library');
  }
}

function selectionTitle() {
  return selectedNode.path || 'All images';
}

let libGridPage = 0;
const LIB_GRID_PER_PAGE = 16;
let libGridItems = [];

function renderLibGrid() {
  const grid = document.getElementById('lib-grid');
  if (!grid) return;
  const items = libGridItems;
  if (!items.length) {
    grid.innerHTML = `<p class="dim">No images in <strong>${escapeHtml(selectionTitle())}</strong>. Select a node and upload, or use Manage to create folders.</p>`;
    document.getElementById('lib-grid-pagination')?.replaceChildren();
    return;
  }

  const totalPages = Math.max(1, Math.ceil(items.length / LIB_GRID_PER_PAGE));
  libGridPage = Math.max(0, Math.min(libGridPage, totalPages - 1));
  const start = libGridPage * LIB_GRID_PER_PAGE;
  const pageItems = items.slice(start, start + LIB_GRID_PER_PAGE);

  grid.innerHTML = pageItems.map((img) => {
    const thumb = img.thumbUrl || '';
    const crumb = img.breadcrumb || img.nodePath || img.filename;
    const encPath = encodeURIComponent(img.path);
    return `
    <div class="dda-card-img" draggable="true" data-image-path="${encPath}" data-image-id="${img.id}" title="Click to view — ${escapeHtml(img.filename)}">
      <button type="button" class="dda-card-delete-btn" title="Delete image" aria-label="Delete image" data-requires-permission="library:delete">×</button>
      ${thumb ? `<img src="${thumb}" alt="" loading="lazy" />` : '<div class="meta">No preview</div>'}
      <div class="meta">
        <span class="dim dda-crumb">${escapeHtml(crumb)}</span><br/>
        <span class="dim">${img.captureDate ? String(img.captureDate).slice(0, 10) + ' · ' : ''}${img.imageType || ''} · ${formatBytes(img.fileSizeBytes)}</span>
      </div>
    </div>`;
  }).join('');
  if (typeof bindLibraryGridCards === 'function') bindLibraryGridCards(grid);
  if (typeof applyPermissionGating === 'function') applyPermissionGating(window.ddaPermissions || {}, grid);

  if (typeof renderPaginationControls === 'function') {
    renderPaginationControls(document.getElementById('lib-grid-pagination'), libGridPage, totalPages, (p) => {
      libGridPage = p;
      renderLibGrid();
    });
  }
}

async function loadLibraryImages() {
  const grid = document.getElementById('lib-grid');
  const title = document.getElementById('lib-grid-title');
  if (!grid) return;
  const q = document.getElementById('lib-filter')?.value?.trim() || '';
  const params = new URLSearchParams();
  if (selectedNode.id) params.set('node_id', String(selectedNode.id));
  if (q) params.set('q', q);
  if (title) title.textContent = `Images — ${selectionTitle()}`;

  try {
    const items = await ddaApi('GET', '/api/dda/local/images?' + params.toString());
    window.ddaState.libraryItems = items;
    libGridItems = items;
    libGridPage = 0;
    renderLibGrid();
    if (typeof loadCompareLibraryGrid === 'function') loadCompareLibraryGrid();
    if (typeof loadAreaPairs === 'function') {
      loadAreaPairs({ nodeId: selectedNode.id || null });
    }
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
    const sync = data.sync || {};
    const parts = [`${data.totalImages || 0} image(s)`];
    if (sync.nodesCreated) parts.push(`${sync.nodesCreated} folder(s) imported`);
    if (sync.imagesIndexed) parts.push(`${sync.imagesIndexed} image(s) indexed`);
    showDdaSuccess(`Library synced — ${parts.join(', ')}.`);
  } catch (err) {
    showDdaError(err.message);
  } finally {
    btn.disabled = false;
  }
});

// Current-user chip + sign out (login is required to reach this page at all).
(async function initUserMenu() {
  const emailEl = document.getElementById('dda-user-email');
  if (!emailEl) return;
  try {
    const me = await ddaApi('GET', '/api/me');
    emailEl.textContent = me.email || '';
    const avatarEl = document.getElementById('dda-user-avatar');
    if (avatarEl) avatarEl.textContent = (me.full_name || me.email || '?').trim().charAt(0).toUpperCase();
  } catch (_) {
    // Not authenticated — the page-load redirect to /login should already
    // have caught this; leave the chip blank rather than erroring loudly.
  }
})();

// Dynamic role permissions — fetched once per page load, cached on
// window.ddaPermissions for page-specific JS (e.g. per-row Delete buttons
// rendered by library.js/reports.js) to check directly, and also applied
// declaratively to any static element carrying data-requires-permission.
(async function initPermissionGating() {
  const permissions = await loadMyPermissions();
  window.ddaPermissions = permissions;
  applyPermissionGating(permissions);
  document.dispatchEvent(new CustomEvent('dda:permissions-ready', { detail: permissions }));
})();

// Sidebar collapse/expand toggle — persisted so it stays collapsed across
// page loads, same as the LMS reference sidebar.
(function initSidebarToggle() {
  const sidebar = document.querySelector('.dda-sidebar-nav');
  const toggleBtn = document.getElementById('dda-sidebar-toggle');
  if (!sidebar || !toggleBtn) return;

  const STORAGE_KEY = 'ddaSidebarCollapsed';
  const applyState = (collapsed) => {
    sidebar.classList.toggle('collapsed', collapsed);
    toggleBtn.setAttribute('aria-expanded', String(!collapsed));
    toggleBtn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  };

  applyState(localStorage.getItem(STORAGE_KEY) === 'true');

  toggleBtn.addEventListener('click', () => {
    const collapsed = !sidebar.classList.contains('collapsed');
    applyState(collapsed);
    localStorage.setItem(STORAGE_KEY, String(collapsed));
  });
})();

document.getElementById('dda-logout-btn')?.addEventListener('click', async () => {
  try {
    await ddaApi('POST', '/api/auth/logout');
  } catch (_) {
    // Even if the request fails, still send the user to /login below.
  } finally {
    window.location.href = '/login';
  }
});

initDda();
