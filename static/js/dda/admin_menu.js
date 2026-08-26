/** Menu Management admin page — hierarchical menu item list + up/down
 * reorder within each sibling group, icon + parent (one level of nesting). */

let allMenuItems = [];
let allMenuModules = [];

async function loadMenuModules() {
  try {
    const data = await ddaApi('GET', '/api/dda/rbac/modules');
    allMenuModules = data.modules || [];
    const sel = document.getElementById('menu-item-module');
    if (sel) {
      sel.innerHTML = '<option value="">— No module (always visible) —</option>' +
        allMenuModules.map((m) => `<option value="${m.id}">${escapeHtml(m.name)}</option>`).join('');
    }
    const keysList = document.getElementById('module-keys-list');
    if (keysList) {
      keysList.innerHTML = allMenuModules.length
        ? allMenuModules.map((m) => `<code>${escapeHtml(m.key)}</code>`).join('')
        : '<p class="dim">No modules yet.</p>';
    }
  } catch (_) {
    // Module select / keys list just stay at their defaults if this fails.
  }
}

async function loadMenuItems() {
  const el = document.getElementById('menu-item-list');
  try {
    const data = await ddaApi('GET', '/api/dda/rbac/menu-items');
    allMenuItems = (data.menuItems || []).sort((a, b) => a.sortOrder - b.sortOrder);
    renderMenuItemList();
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load menu items: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function moduleNameFor(moduleId) {
  const m = allMenuModules.find((x) => x.id === moduleId);
  return m ? m.name : null;
}

function siblingGroup(parentId) {
  return allMenuItems.filter((i) => i.parentId === parentId).sort((a, b) => a.sortOrder - b.sortOrder);
}

function renderItemRow(item, isChild) {
  const group = siblingGroup(item.parentId);
  const idx = group.findIndex((i) => i.id === item.id);
  return `
    <div class="dda-menu-item-row${isChild ? ' is-child' : ''}" data-item-id="${item.id}">
      <div class="dda-menu-item-order">
        <button type="button" data-move="up" ${idx === 0 ? 'disabled' : ''} title="Move up">▲</button>
        <button type="button" data-move="down" ${idx === group.length - 1 ? 'disabled' : ''} title="Move down">▼</button>
      </div>
      <div class="dda-menu-item-info">
        <div class="dda-menu-item-label">
          ${item.icon ? `<span class="dda-menu-item-icon">${escapeHtml(item.icon)}</span>` : ''}
          ${escapeHtml(item.label)}
          <span class="dda-status-badge ${item.isActive ? 'is-active' : 'is-inactive'}">${item.isActive ? 'Active' : 'Hidden'}</span>
        </div>
        <div class="dda-menu-item-meta">
          <code>${escapeHtml(item.url)}</code>
          ${moduleNameFor(item.moduleId) ? ' · module: ' + escapeHtml(moduleNameFor(item.moduleId)) : ' · no module (always visible)'}
        </div>
      </div>
      <div class="dda-menu-item-actions">
        <button type="button" class="btn btn-secondary btn-sm" data-edit-item="${item.id}">Edit</button>
        <button type="button" class="btn btn-danger btn-sm" data-del-item="${item.id}">Delete</button>
      </div>
    </div>`;
}

function renderMenuItemList() {
  const el = document.getElementById('menu-item-list');
  if (!allMenuItems.length) {
    el.innerHTML = '<p class="dim">No menu items yet.</p>';
    return;
  }
  const topLevel = siblingGroup(null);
  el.innerHTML = topLevel.map((item) => {
    const children = siblingGroup(item.id);
    return renderItemRow(item, false) + children.map((c) => renderItemRow(c, true)).join('');
  }).join('');

  el.querySelectorAll('[data-move]').forEach((btn) => {
    btn.addEventListener('click', () => moveMenuItem(Number(btn.closest('.dda-menu-item-row').dataset.itemId), btn.dataset.move));
  });
  el.querySelectorAll('[data-edit-item]').forEach((btn) => {
    btn.addEventListener('click', () => openMenuItemModal(Number(btn.dataset.editItem)));
  });
  el.querySelectorAll('[data-del-item]').forEach((btn) => {
    btn.addEventListener('click', () => deleteMenuItem(Number(btn.dataset.delItem)));
  });
}

async function moveMenuItem(itemId, direction) {
  const item = allMenuItems.find((i) => i.id === itemId);
  const group = siblingGroup(item.parentId);
  const idx = group.findIndex((i) => i.id === itemId);
  const swapWith = direction === 'up' ? idx - 1 : idx + 1;
  if (swapWith < 0 || swapWith >= group.length) return;
  [group[idx], group[swapWith]] = [group[swapWith], group[idx]];
  renderMenuItemList();
  try {
    await ddaApi('POST', '/api/dda/rbac/menu-items/reorder', {
      body: JSON.stringify({ orderedIds: group.map((i) => i.id) }),
    });
    group.forEach((i, order) => { i.sortOrder = order; });
  } catch (err) {
    showDdaError(err.message);
    await loadMenuItems();
  }
}

function populateParentSelect(excludeId) {
  const sel = document.getElementById('menu-item-parent');
  if (!sel) return;
  const topLevel = allMenuItems.filter((i) => i.parentId === null && i.id !== excludeId);
  sel.innerHTML = '<option value="">— Top Level —</option>' +
    topLevel.map((i) => `<option value="${i.id}">${escapeHtml(i.label)}</option>`).join('');
}

function openMenuItemModal(itemId) {
  const modal = document.getElementById('menu-item-modal');
  const title = document.getElementById('menu-item-modal-title');
  const item = allMenuItems.find((i) => i.id === itemId);
  populateParentSelect(itemId);
  document.getElementById('menu-item-id').value = item ? item.id : '';
  document.getElementById('menu-item-label').value = item ? item.label : '';
  document.getElementById('menu-item-icon').value = item ? item.icon || '' : '';
  document.getElementById('menu-item-url').value = item ? item.url : '';
  document.getElementById('menu-item-module').value = item && item.moduleId ? item.moduleId : '';
  document.getElementById('menu-item-parent').value = item && item.parentId ? item.parentId : '';
  document.getElementById('menu-item-active').checked = item ? item.isActive : true;
  title.textContent = item ? 'Edit Menu Item' : 'Add Menu Item';
  modal.classList.remove('hidden');
}

document.getElementById('btn-new-menu-item')?.addEventListener('click', () => openMenuItemModal(null));
document.getElementById('menu-item-modal-close')?.addEventListener('click', () => document.getElementById('menu-item-modal').classList.add('hidden'));
document.getElementById('menu-item-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'menu-item-modal') document.getElementById('menu-item-modal').classList.add('hidden');
});

document.getElementById('menu-item-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError();
  const id = document.getElementById('menu-item-id').value;
  const moduleVal = document.getElementById('menu-item-module').value;
  const parentVal = document.getElementById('menu-item-parent').value;
  const body = {
    label: document.getElementById('menu-item-label').value.trim(),
    icon: document.getElementById('menu-item-icon').value.trim(),
    url: document.getElementById('menu-item-url').value.trim(),
    moduleId: moduleVal ? Number(moduleVal) : null,
    parentId: parentVal ? Number(parentVal) : null,
    isActive: document.getElementById('menu-item-active').checked,
  };
  try {
    if (id) {
      await ddaApi('PUT', `/api/dda/rbac/menu-items/${id}`, { body: JSON.stringify(body) });
    } else {
      await ddaApi('POST', '/api/dda/rbac/menu-items', { body: JSON.stringify(body) });
    }
    document.getElementById('menu-item-modal').classList.add('hidden');
    showDdaSuccess('Menu item saved.');
    await loadMenuItems();
  } catch (err) {
    showDdaError(err.message);
  }
});

async function deleteMenuItem(itemId) {
  if (!confirm('Delete this menu item?')) return;
  try {
    await ddaApi('DELETE', `/api/dda/rbac/menu-items/${itemId}`);
    showDdaSuccess('Menu item deleted.');
    await loadMenuItems();
  } catch (err) {
    showDdaError(err.message);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  await loadMenuModules();
  await loadMenuItems();
});
