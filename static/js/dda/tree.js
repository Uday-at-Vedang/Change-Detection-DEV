/** Unlimited-depth tree library UI + admin management. */

let treeData = null;
let imageTypes = [];

function isAdmin() {
  return window.ddaState?.userRole === 'admin';
}

function renderTreeNodes(nodes, depth = 0) {
  if (!nodes || !nodes.length) return '';
  const filter = (document.getElementById('lib-tree-search')?.value || '').toLowerCase();
  const sel = window.ddaState?.selectedNode;

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
        <details class="dda-tree-node-wrap" open${indent ? '' : ''}>
          <summary class="dda-tree-node-summary ${active ? 'active' : ''}" data-node-id="${node.id}">
            <button type="button" class="dda-tree-node-btn ${active ? 'active' : ''}" data-node-id="${node.id}"
              data-node-path="${escapeHtml(node.nodePath || name)}">${escapeHtml(name)}${count}</button>
          </summary>
          <div class="dda-tree-children">${renderTreeNodes(children, depth + 1)}</div>
        </details>`;
    }
    return `
      <button type="button" class="dda-tree-node-btn dda-tree-leaf ${active ? 'active' : ''}" data-node-id="${node.id}"
        data-node-path="${escapeHtml(node.nodePath || name)}"${indent}>${escapeHtml(name)}${count}</button>`;
  }).join('');
}

function renderTree(tree, types) {
  const el = document.getElementById('lib-tree');
  if (!el) return;
  treeData = tree;
  if (types) imageTypes = types;

  const nodes = tree?.tree || tree || [];
  const allActive = !window.ddaState?.selectedNode?.id;
  let html = `<button type="button" class="dda-tree-all ${allActive ? 'active' : ''}" id="btn-tree-all">All images</button>`;
  html += renderTreeNodes(nodes);
  if (!nodes.length) {
    html += '<p class="dim">No nodes yet. Admins can use Manage to add zones.</p>';
  }
  el.innerHTML = html;

  document.getElementById('btn-tree-all')?.addEventListener('click', () => {
    window.ddaState.clearNode();
    window.ddaState.refreshImages();
    renderTree(treeData, imageTypes);
    syncUploadNodeSelect();
  });

  el.querySelectorAll('.dda-tree-node-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      window.ddaState.setNode({
        id: parseInt(btn.dataset.nodeId, 10),
        path: btn.dataset.nodePath,
      });
      window.ddaState.refreshImages();
      renderTree(treeData, imageTypes);
      syncUploadNodeSelect();
    });
  });
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (treeData) renderTree(treeData, imageTypes);
});

async function loadTree() {
  const data = await ddaApi('GET', '/api/dda/tree');
  renderTree(data, data.imageTypes);
  populateNodeSelects(data.tree);
  if (typeof populateManageNodeSelect === 'function') populateManageNodeSelect();
  return data;
}

window.loadTree = loadTree;
window.renderTree = renderTree;

function flattenNodes(nodes, out = []) {
  (nodes || []).forEach((n) => {
    out.push(n);
    flattenNodes(n.children, out);
  });
  return out;
}

function populateNodeSelects(tree) {
  const flat = flattenNodes(tree || []);
  const opts = '<option value="">— Select node —</option>' +
    flat.map((n) => `<option value="${n.id}">${escapeHtml(n.nodePath || n.name)}</option>`).join('');

  ['upload-node', 'manage-parent', 'move-parent', 'add-child-parent'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = id === 'manage-parent' ? '<option value="">— Root —</option>' + flat.map((n) =>
      `<option value="${n.id}">${escapeHtml(n.nodePath || n.name)}</option>`).join('') : opts;
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
  if (!name) return showDdaError('Enter a node name.');
  const body = { node_name: name, node_type: type, parent_id: parentVal ? parseInt(parentVal, 10) : null };
  try {
    await ddaApi('POST', '/api/dda/tree/nodes', { body: JSON.stringify(body) });
    document.getElementById('add-node-name').value = '';
    showDdaSuccess('Node created.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

document.getElementById('btn-rename-node')?.addEventListener('click', async () => {
  const id = parseInt(document.getElementById('manage-node-select')?.value || '0', 10);
  const name = document.getElementById('rename-node-name')?.value?.trim();
  if (!id || !name) return showDdaError('Select a node and enter a new name.');
  try {
    await ddaApi('PUT', `/api/dda/tree/nodes/${id}/rename`, { body: JSON.stringify({ node_name: name }) });
    showDdaSuccess('Node renamed.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

document.getElementById('btn-delete-node')?.addEventListener('click', async () => {
  const id = parseInt(document.getElementById('manage-node-select')?.value || '0', 10);
  if (!id || !confirm('Delete this node? Check delete files if removing images.')) return;
  const deleteFiles = document.getElementById('delete-files-check')?.checked;
  try {
    await ddaApi('DELETE', `/api/dda/tree/nodes/${id}`, { body: JSON.stringify({ delete_files: !!deleteFiles }) });
    showDdaSuccess('Node deleted.');
    await window.ddaState.rescan();
  } catch (err) {
    showDdaError(err.message);
  }
});

document.getElementById('btn-move-node')?.addEventListener('click', async () => {
  const id = parseInt(document.getElementById('manage-node-select')?.value || '0', 10);
  const parentVal = document.getElementById('move-parent')?.value;
  if (!id) return showDdaError('Select a node to move.');
  try {
    await ddaApi('POST', `/api/dda/tree/nodes/${id}/move`, {
      body: JSON.stringify({ parent_id: parentVal ? parseInt(parentVal, 10) : null }),
    });
    showDdaSuccess('Node moved.');
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

document.getElementById('dda-manage-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'dda-manage-modal') closeManage();
});

window.populateManageNodeSelect = populateManageNodeSelect;
