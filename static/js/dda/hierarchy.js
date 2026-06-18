/** Zone → folder → year library tree and admin management. */

let libraryTreeData = null;

function isAdminRole() {
  const role = window.ddaState?.userRole || '';
  return role === 'admin';
}

function canUploadRole() {
  const rank = { viewer: 0, uploader: 1, analyst: 2, admin: 3 };
  const role = window.ddaState?.userRole || 'analyst';
  return (rank[role] || 0) >= rank.uploader;
}

function selectionLabel() {
  const s = window.ddaState?.selection || {};
  const parts = [];
  if (s.zoneName) parts.push(s.zoneName);
  if (s.folderName) parts.push(s.folderName);
  if (s.year) parts.push(String(s.year));
  return parts.length ? parts.join(' / ') : 'All images';
}

function renderLibraryTree(tree) {
  const el = document.getElementById('lib-tree');
  if (!el) return;
  libraryTreeData = tree;
  const filter = (document.getElementById('lib-tree-search')?.value || '').toLowerCase();
  const sel = window.ddaState?.selection || {};

  const allActive = !sel.zoneId && !sel.legacy && !sel.year;
  const allBtn = `
    <button type="button" class="dda-tree-node dda-tree-all ${allActive ? 'active' : ''}">
      All images
    </button>`;

  let html = allBtn;

  const zones = (tree?.zones || []).filter((z) => {
    if (!filter) return true;
    const zMatch = z.name.toLowerCase().includes(filter);
    const fMatch = (z.folders || []).some((f) =>
      f.name.toLowerCase().includes(filter) ||
      (f.years || []).some((y) => String(y.year).includes(filter))
    );
    return zMatch || fMatch;
  });

  zones.forEach((zone) => {
    const zoneOpen = sel.zoneId === zone.id || !filter;
    html += `<details class="dda-tree-zone" ${zoneOpen ? 'open' : ''} data-zone-id="${zone.id}">`;
    html += `<summary class="dda-tree-zone-summary">${escapeHtml(zone.name)}</summary>`;
    html += '<div class="dda-tree-folders">';

    (zone.folders || []).forEach((folder) => {
      if (filter && !zone.name.toLowerCase().includes(filter) &&
          !folder.name.toLowerCase().includes(filter) &&
          !(folder.years || []).some((y) => String(y.year).includes(filter))) {
        return;
      }
      const folderOpen = sel.folderId === folder.id || filter;
      html += `<details class="dda-tree-folder" ${folderOpen ? 'open' : ''} data-folder-id="${folder.id}">`;
      html += `<summary class="dda-tree-folder-summary">${escapeHtml(folder.name)}</summary>`;
      html += '<div class="dda-tree-years">';

      const years = folder.years || [];
      if (!years.length) {
        html += '<span class="dim dda-tree-empty">No year folders yet</span>';
      }
      years.forEach((y) => {
        if (filter && !String(y.year).includes(filter) &&
            !folder.name.toLowerCase().includes(filter) &&
            !zone.name.toLowerCase().includes(filter)) return;
        const active = sel.zoneId === zone.id && sel.folderId === folder.id && sel.year === y.year;
        html += `<button type="button" class="dda-tree-year ${active ? 'active' : ''}"
          data-zone-id="${zone.id}" data-folder-id="${folder.id}" data-year="${y.year}">
          ${y.year} <span class="dim">(${y.imageCount})</span>
        </button>`;
      });
      html += '</div></details>';
    });

    html += '</div></details>';
  });

  const legacy = tree?.legacyYears || [];
  if (legacy.length) {
    html += '<details class="dda-tree-zone dda-tree-legacy" open>';
    html += '<summary class="dda-tree-zone-summary">Unassigned (legacy)</summary><div class="dda-tree-years">';
    legacy.forEach((y) => {
      const active = sel.legacy && sel.year === y.year;
      html += `<button type="button" class="dda-tree-year dda-tree-legacy-year ${active ? 'active' : ''}"
        data-legacy="1" data-year="${y.year}">
        ${y.year} <span class="dim">(${y.imageCount})</span>
      </button>`;
    });
    html += '</div></details>';
  }

  if (!zones.length && !legacy.length) {
    html += '<p class="dim">No zones yet. Admins can add zones under Manage library.</p>';
  }

  el.innerHTML = html;

  el.querySelector('.dda-tree-all')?.addEventListener('click', () => {
    window.ddaState.clearSelection();
    window.ddaState.refreshImages();
    renderLibraryTree(libraryTreeData);
  });

  el.querySelectorAll('.dda-tree-year').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.legacy) {
        window.ddaState.setSelection({ legacy: true, year: parseInt(btn.dataset.year, 10) });
      } else {
        const zoneId = parseInt(btn.dataset.zoneId, 10);
        const folderId = parseInt(btn.dataset.folderId, 10);
        const year = parseInt(btn.dataset.year, 10);
        let zoneName = '';
        let folderName = '';
        for (const z of (libraryTreeData?.zones || [])) {
          if (z.id === zoneId) {
            zoneName = z.name;
            const f = (z.folders || []).find((x) => x.id === folderId);
            if (f) folderName = f.name;
            break;
          }
        }
        window.ddaState.setSelection({
          zoneId, zoneName, folderId, folderName, year, legacy: false,
        });
      }
      if (typeof syncUploadPickers === 'function') syncUploadPickers();
      window.ddaState.refreshImages();
      renderLibraryTree(libraryTreeData);
    });
  });
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (libraryTreeData) renderLibraryTree(libraryTreeData);
});

async function loadLibraryTree() {
  try {
    const tree = await ddaApi('GET', '/api/dda/hierarchy/tree');
    renderLibraryTree(tree);
    return tree;
  } catch (_) {
    const el = document.getElementById('lib-tree');
    if (el) el.innerHTML = '<p class="dim">Could not load library tree.</p>';
    return null;
  }
}

window.loadLibraryTree = loadLibraryTree;
window.renderLibraryTree = renderLibraryTree;

/* ---- Manage library modal ---- */

function openManageModal() {
  document.getElementById('dda-manage-modal')?.classList.remove('hidden');
  populateManageZones();
}

function closeManageModal() {
  document.getElementById('dda-manage-modal')?.classList.add('hidden');
}

async function populateManageZones() {
  const sel = document.getElementById('manage-zone-select');
  if (!sel) return;
  try {
    const tree = await ddaApi('GET', '/api/dda/hierarchy/tree');
    libraryTreeData = tree;
    sel.innerHTML = (tree.zones || []).map((z) =>
      `<option value="${z.id}">${escapeHtml(z.name)}</option>`
    ).join('');
    populateManageFolders();
  } catch (err) {
    showDdaError?.(err.message);
  }
}

function populateManageFolders() {
  const zoneId = parseInt(document.getElementById('manage-zone-select')?.value || '0', 10);
  const folderSel = document.getElementById('manage-folder-select');
  if (!folderSel || !libraryTreeData) return;
  const zone = (libraryTreeData.zones || []).find((z) => z.id === zoneId);
  folderSel.innerHTML = (zone?.folders || []).map((f) =>
    `<option value="${f.id}">${escapeHtml(f.name)}</option>`
  ).join('');
}

document.getElementById('btn-manage-library')?.addEventListener('click', openManageModal);
document.getElementById('dda-manage-close')?.addEventListener('click', closeManageModal);
document.getElementById('manage-zone-select')?.addEventListener('change', populateManageFolders);

document.getElementById('btn-add-zone')?.addEventListener('click', async () => {
  const name = document.getElementById('manage-zone-name')?.value?.trim();
  if (!name) return showDdaError?.('Enter a zone name.');
  try {
    await ddaApi('POST', '/api/dda/hierarchy/zones', { body: JSON.stringify({ name }) });
    document.getElementById('manage-zone-name').value = '';
    showDdaSuccess?.('Zone created.');
    await loadLibraryTree();
    await populateManageZones();
    if (typeof populateUploadZones === 'function') populateUploadZones();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

document.getElementById('btn-rename-zone')?.addEventListener('click', async () => {
  const zoneId = parseInt(document.getElementById('manage-zone-select')?.value || '0', 10);
  const name = document.getElementById('manage-zone-rename')?.value?.trim();
  if (!zoneId || !name) return showDdaError?.('Select a zone and enter a new name.');
  try {
    await ddaApi('PATCH', `/api/dda/hierarchy/zones/${zoneId}`, { body: JSON.stringify({ name }) });
    document.getElementById('manage-zone-rename').value = '';
    showDdaSuccess?.('Zone renamed.');
    await loadLibraryTree();
    await populateManageZones();
    if (typeof populateUploadZones === 'function') populateUploadZones();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

document.getElementById('btn-add-folder')?.addEventListener('click', async () => {
  const zoneId = parseInt(document.getElementById('manage-zone-select')?.value || '0', 10);
  const name = document.getElementById('manage-folder-name')?.value?.trim();
  if (!zoneId || !name) return showDdaError?.('Select a zone and enter a folder name.');
  try {
    await ddaApi('POST', `/api/dda/hierarchy/zones/${zoneId}/folders`, { body: JSON.stringify({ name }) });
    document.getElementById('manage-folder-name').value = '';
    showDdaSuccess?.('Folder created.');
    await loadLibraryTree();
    await populateManageZones();
    if (typeof populateUploadZones === 'function') populateUploadZones();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

document.getElementById('btn-rename-folder')?.addEventListener('click', async () => {
  const folderId = parseInt(document.getElementById('manage-folder-select')?.value || '0', 10);
  const name = document.getElementById('manage-folder-rename')?.value?.trim();
  if (!folderId || !name) return showDdaError?.('Select a folder and enter a new name.');
  try {
    await ddaApi('PATCH', `/api/dda/hierarchy/folders/${folderId}`, { body: JSON.stringify({ name }) });
    document.getElementById('manage-folder-rename').value = '';
    showDdaSuccess?.('Folder renamed.');
    await loadLibraryTree();
    await populateManageZones();
    if (typeof populateUploadZones === 'function') populateUploadZones();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

document.getElementById('btn-create-year')?.addEventListener('click', async () => {
  const folderId = parseInt(document.getElementById('manage-folder-select')?.value || '0', 10);
  const year = parseInt(document.getElementById('manage-year')?.value || '0', 10);
  if (!folderId || !year) return showDdaError?.('Select a folder and enter a year.');
  try {
    await ddaApi('POST', `/api/dda/hierarchy/folders/${folderId}/years`, { body: JSON.stringify({ year }) });
    showDdaSuccess?.(`Year folder ${year} created.`);
    await loadLibraryTree();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

document.getElementById('btn-delete-folder')?.addEventListener('click', async () => {
  const folderId = parseInt(document.getElementById('manage-folder-select')?.value || '0', 10);
  if (!folderId || !confirm('Delete this empty folder?')) return;
  try {
    await ddaApi('DELETE', `/api/dda/hierarchy/folders/${folderId}`);
    showDdaSuccess?.('Folder deleted.');
    await loadLibraryTree();
    await populateManageZones();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

document.getElementById('btn-delete-zone')?.addEventListener('click', async () => {
  const zoneId = parseInt(document.getElementById('manage-zone-select')?.value || '0', 10);
  if (!zoneId || !confirm('Delete this empty zone and all empty folders?')) return;
  try {
    await ddaApi('DELETE', `/api/dda/hierarchy/zones/${zoneId}`);
    showDdaSuccess?.('Zone deleted.');
    await loadLibraryTree();
    await populateManageZones();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

/* ---- Reassign legacy images ---- */

function openReassignModal(imagePath, filename) {
  const modal = document.getElementById('dda-reassign-modal');
  if (!modal) return;
  modal.dataset.path = imagePath;
  document.getElementById('reassign-filename').textContent = filename || imagePath;
  populateReassignPickers();
  modal.classList.remove('hidden');
}

function closeReassignModal() {
  document.getElementById('dda-reassign-modal')?.classList.add('hidden');
}

async function populateReassignPickers() {
  const zoneSel = document.getElementById('reassign-zone');
  const folderSel = document.getElementById('reassign-folder');
  if (!zoneSel) return;
  try {
    const tree = await ddaApi('GET', '/api/dda/hierarchy/tree');
    zoneSel.innerHTML = (tree.zones || []).filter((z) => z.slug !== '_unassigned').map((z) =>
      `<option value="${z.id}">${escapeHtml(z.name)}</option>`
    ).join('');
    const syncFolders = () => {
      const zoneId = parseInt(zoneSel.value, 10);
      const zone = (tree.zones || []).find((z) => z.id === zoneId);
      folderSel.innerHTML = (zone?.folders || []).map((f) =>
        `<option value="${f.id}">${escapeHtml(f.name)}</option>`
      ).join('');
    };
    zoneSel.onchange = syncFolders;
    syncFolders();
  } catch (_) {}
}

document.getElementById('dda-reassign-close')?.addEventListener('click', closeReassignModal);
document.getElementById('btn-reassign-submit')?.addEventListener('click', async () => {
  const modal = document.getElementById('dda-reassign-modal');
  const path = modal?.dataset.path;
  const zoneId = parseInt(document.getElementById('reassign-zone')?.value || '0', 10);
  const folderId = parseInt(document.getElementById('reassign-folder')?.value || '0', 10);
  const year = parseInt(document.getElementById('reassign-year')?.value || '0', 10);
  if (!path || !zoneId || !folderId || !year) return;
  try {
    await ddaApi('POST', '/api/dda/local/reassign', {
      body: JSON.stringify({ path, zone_id: zoneId, folder_id: folderId, year }),
    });
    showDdaSuccess?.('Image reassigned.');
    closeReassignModal();
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError?.(err.message);
  }
});

window.openReassignModal = openReassignModal;
