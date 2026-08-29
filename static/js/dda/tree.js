/** Unlimited-depth tree library UI + admin management. */

let treeData = null;
let imageTypes = [];

const librarySidebar = {
  containerId: 'lib-tree',
  searchId: 'lib-tree-search',
  allBtnId: 'btn-tree-all',
  getSelectedNode: () => window.ddaState?.selectedNode,
  onNodeSelect: (node) => {
    window.ddaState.setNode(node);
    window.ddaState.refreshImages();
    syncUploadNodeSelect();
  },
  onClearNode: () => {
    window.ddaState.clearNode();
    window.ddaState.refreshImages();
    syncUploadNodeSelect();
  },
};

let compareSidebar = null;

function isAdmin() {
  return window.ddaState?.userRole === 'admin';
}

function getSidebars() {
  const list = [librarySidebar];
  if (compareSidebar) list.push(compareSidebar);
  return list;
}

function renderTreeNodes(nodes, sidebar, depth = 0) {
  if (!nodes || !nodes.length) return '';
  const filter = (document.getElementById(sidebar.searchId)?.value || '').toLowerCase();
  const sel = sidebar.getSelectedNode?.();

  return nodes.map((node) => {
    const name = node.name || node.nodeName;
    const children = node.children || [];
    const matches = !filter ||
      name.toLowerCase().includes(filter) ||
      (node.nodePath || '').toLowerCase().includes(filter) ||
      children.some((c) => JSON.stringify(c).toLowerCase().includes(filter));

    if (!matches && filter) return '';

    const active = sel?.id === node.id;
    const hasKids = children.length > 0;
    const count = node.imageCount ? ` <span class="dim">(${node.imageCount})</span>` : '';
    const indent = depth > 0 ? ` style="padding-left:${depth * 0.5}rem"` : '';

    if (hasKids) {
      return `
        <details class="dda-tree-node-wrap" open>
          <summary class="dda-tree-node-summary ${active ? 'active' : ''}" data-node-id="${node.id}">
            <button type="button" class="dda-tree-node-btn ${active ? 'active' : ''}" data-node-id="${node.id}"
              data-node-path="${escapeHtml(node.nodePath || name)}">${escapeHtml(name)}${count}</button>
          </summary>
          <div class="dda-tree-children">${renderTreeNodes(children, sidebar, depth + 1)}</div>
        </details>`;
    }
    return `
      <button type="button" class="dda-tree-node-btn dda-tree-leaf ${active ? 'active' : ''}" data-node-id="${node.id}"
        data-node-path="${escapeHtml(node.nodePath || name)}"${indent}>${escapeHtml(name)}${count}</button>`;
  }).join('');
}

function renderTreeSidebar(sidebar, tree, types) {
  const el = document.getElementById(sidebar.containerId);
  if (!el) return;
  if (types) imageTypes = types;

  const nodes = tree?.tree || tree || [];
  const sel = sidebar.getSelectedNode?.();
  const allActive = !sel?.id;
  const allBtnId = sidebar.allBtnId || `btn-tree-all-${sidebar.containerId}`;
  let html = `<button type="button" class="dda-tree-all ${allActive ? 'active' : ''}" id="${allBtnId}">All images</button>`;
  html += renderTreeNodes(nodes, sidebar);
  if (!nodes.length) {
    html += '<p class="dim">No folders yet. Use Folders master to add zones and areas, or drop a folder onto Upload.</p>';
  }
  el.innerHTML = html;

  document.getElementById(allBtnId)?.addEventListener('click', () => {
    sidebar.onClearNode?.();
    renderAllTrees(treeData, imageTypes);
  });

  el.querySelectorAll('.dda-tree-node-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      sidebar.onNodeSelect?.({
        id: parseInt(btn.dataset.nodeId, 10),
        path: btn.dataset.nodePath,
      });
      renderAllTrees(treeData, imageTypes);
    });
  });
  if (sidebar.containerId === 'lib-tree') bindLibraryTreeExtras(el);
}

function renderAllTrees(tree, types) {
  if (tree) treeData = tree;
  getSidebars().forEach((sidebar) => renderTreeSidebar(sidebar, treeData, types));
}

function renderTree(tree, types) {
  renderAllTrees(tree, types);
}

function registerCompareTreeSidebar(config) {
  compareSidebar = config;
  if (treeData) renderAllTrees(treeData, imageTypes);
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (treeData) renderAllTrees(treeData, imageTypes);
});

document.getElementById('compare-tree-search')?.addEventListener('input', () => {
  if (treeData) renderAllTrees(treeData, imageTypes);
});

async function loadTree() {
  const data = await ddaApi('GET', '/api/dda/tree');
  renderAllTrees(data, data.imageTypes);
  populateNodeSelects(data.tree);
  if (typeof populateManageNodeSelect === 'function') populateManageNodeSelect();
  return data;
}

window.loadTree = loadTree;
window.renderTree = renderTree;
window.renderAllTrees = renderAllTrees;
window.registerCompareTreeSidebar = registerCompareTreeSidebar;

function flattenNodes(nodes, out = []) {
  (nodes || []).forEach((n) => {
    out.push(n);
    flattenNodes(n.children, out);
  });
  return out;
}

function populateNodeSelects(tree) {
  const flat = flattenNodes(tree || []);
  const pathOpts = flat.map((n) => `<option value="${n.id}">${escapeHtml(n.nodePath || n.name)}</option>`).join('');
  const uploadOpts = '<option value="">— Select folder —</option>' + pathOpts;
  const rootOpts = '<option value="">— Root —</option>' + pathOpts;

  const upload = document.getElementById('upload-node');
  if (upload) upload.innerHTML = uploadOpts;
  ['add-child-parent', 'move-parent'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = rootOpts;
  });

  const typeSel = document.getElementById('upload-image-type');
  if (typeSel && imageTypes.length) {
    typeSel.innerHTML = imageTypes.map((t) => `<option value="${t}">${t}</option>`).join('');
  }
  syncUploadNodeSelect();
}

function syncUploadNodeSelect() {
  const sel = document.getElementById('upload-node');
  const node = window.ddaState?.selectedNode;
  if (sel && node?.id) sel.value = String(node.id);
}

/* Manage modal */
function openManage() {
  document.getElementById('dda-manage-modal')?.classList.remove('hidden');
  if (treeData) populateNodeSelects(treeData.tree || treeData);
}

function closeManage() {
  document.getElementById('dda-manage-modal')?.classList.add('hidden');
}

document.getElementById('btn-manage-library')?.addEventListener('click', openManage);
document.getElementById('dda-manage-close')?.addEventListener('click', closeManage);

document.getElementById('btn-add-node')?.addEventListener('click', async () => {
  const parentVal = document.getElementById('add-child-parent')?.value;
  const name = document.getElementById('add-node-name')?.value?.trim();
  const type = document.getElementById('add-node-type')?.value || 'Folder';
  if (!name) return showDdaError('Enter a folder name.');
  const body = { node_name: name, node_type: type, parent_id: parentVal ? parseInt(parentVal, 10) : null };
  try {
    await ddaApi('POST', '/api/dda/tree/nodes', { body: JSON.stringify(body) });
    document.getElementById('add-node-name').value = '';
    showDdaSuccess('Folder created.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

document.getElementById('btn-rename-node')?.addEventListener('click', async () => {
  const id = parseInt(document.getElementById('manage-node-select')?.value || '0', 10);
  const name = document.getElementById('rename-node-name')?.value?.trim();
  if (!id || !name) return showDdaError('Select a folder and enter a new name.');
  try {
    await ddaApi('PUT', `/api/dda/tree/nodes/${id}/rename`, { body: JSON.stringify({ node_name: name }) });
    showDdaSuccess('Folder renamed.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

document.getElementById('btn-delete-node')?.addEventListener('click', async () => {
  const id = parseInt(document.getElementById('manage-node-select')?.value || '0', 10);
  if (!id || !confirm('Remove this folder from the masters library? Images inside it will no longer be listed.')) return;
  const deleteFiles = document.getElementById('delete-files-check')?.checked;
  try {
    await ddaApi('DELETE', `/api/dda/tree/nodes/${id}`, { body: JSON.stringify({ delete_files: !!deleteFiles }) });
    showDdaSuccess('Folder removed.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

document.getElementById('btn-move-node')?.addEventListener('click', async () => {
  const id = parseInt(document.getElementById('manage-node-select')?.value || '0', 10);
  const parentVal = document.getElementById('move-parent')?.value;
  if (!id) return showDdaError('Select a folder to move.');
  try {
    await ddaApi('POST', `/api/dda/tree/nodes/${id}/move`, {
      body: JSON.stringify({ parent_id: parentVal ? parseInt(parentVal, 10) : null }),
    });
    showDdaSuccess('Folder moved.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

function populateManageNodeSelect() {
  if (!treeData) return;
  const flat = flattenNodes(treeData.tree || treeData || []);
  const sel = document.getElementById('manage-node-select');
  if (sel) sel.innerHTML = flat.map((n) =>
    `<option value="${n.id}">${escapeHtml(n.nodePath || n.name)}</option>`).join('');
}

let treeMenuNodeId = null;

function hideTreeMenu() {
  document.getElementById('dda-tree-menu')?.classList.add('hidden');
  treeMenuNodeId = null;
}

function showTreeMenu(x, y, nodeId) {
  const menu = document.getElementById('dda-tree-menu');
  if (!menu) return;
  treeMenuNodeId = nodeId;
  menu.querySelectorAll('[data-requires-permission]').forEach((el) => {
    const [mod, action] = (el.getAttribute('data-requires-permission') || ':').split(':');
    const allowed = (typeof hasPermission === 'function' && window.ddaPermissions)
      ? hasPermission(window.ddaPermissions, mod, action)
      : isAdmin();
    el.classList.toggle('hidden', !allowed);
  });
  menu.classList.remove('hidden');
  const pad = 8;
  const w = menu.offsetWidth || 180;
  const h = menu.offsetHeight || 160;
  menu.style.left = `${Math.min(x, window.innerWidth - w - pad)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - h - pad)}px`;
}

function bindLibraryTreeExtras(el) {
  el.querySelectorAll('.dda-tree-node-btn').forEach((btn) => {
    btn.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showTreeMenu(e.clientX, e.clientY, parseInt(btn.dataset.nodeId, 10));
    });
    ['dragenter', 'dragover'].forEach((ev) => {
      btn.addEventListener(ev, (e) => {
        if (![...e.dataTransfer.types].includes('Files')) return;
        e.preventDefault();
        e.stopPropagation();
        btn.classList.add('is-drop-target');
      });
    });
    btn.addEventListener('dragleave', () => btn.classList.remove('is-drop-target'));
    btn.addEventListener('drop', async (e) => {
      btn.classList.remove('is-drop-target');
      if (![...e.dataTransfer.types].includes('Files')) return;
      e.preventDefault();
      e.stopPropagation();
      const nodeId = parseInt(btn.dataset.nodeId, 10);
      if (typeof setUploadTargetNode === 'function') setUploadTargetNode(nodeId);
      try {
        const files = typeof window.readDroppedEntries === 'function'
          ? await window.readDroppedEntries(e.dataTransfer)
          : Array.from(e.dataTransfer.files || []);
        if (!files.length) return;
        if (typeof uploadLibraryFiles === 'function') {
          await uploadLibraryFiles(files, { nodeId, preserveFolders: true });
        }
      } catch (err) {
        showDdaError(err.message || 'Upload failed.');
      }
    });
  });
}

document.getElementById('dda-tree-menu')?.addEventListener('click', (e) => {
  const act = e.target.closest('[data-tree-act]')?.dataset.treeAct;
  if (!act || !treeMenuNodeId) return;
  const nodeId = treeMenuNodeId;
  hideTreeMenu();
  if (act === 'upload') {
    if (typeof setUploadTargetNode === 'function') setUploadTargetNode(nodeId);
    document.getElementById('upload-file')?.click();
    return;
  }
  if (act === 'manage') {
    openManage();
    const sel = document.getElementById('manage-node-select');
    if (sel) sel.value = String(nodeId);
    return;
  }
  if (act === 'add') {
    openManage();
    const parent = document.getElementById('add-child-parent');
    if (parent) parent.value = String(nodeId);
    document.getElementById('add-node-name')?.focus();
    return;
  }
  if (act === 'rename') {
    openManage();
    const sel = document.getElementById('manage-node-select');
    if (sel) sel.value = String(nodeId);
    document.getElementById('rename-node-name')?.focus();
    return;
  }
  if (act === 'delete') {
    openManage();
    const sel = document.getElementById('manage-node-select');
    if (sel) sel.value = String(nodeId);
    document.getElementById('btn-delete-node')?.focus();
  }
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('#dda-tree-menu')) hideTreeMenu();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') hideTreeMenu();
});

document.getElementById('dda-manage-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'dda-manage-modal') closeManage();
});

window.populateManageNodeSelect = populateManageNodeSelect;
window.openManage = openManage;
