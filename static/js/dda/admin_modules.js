/** App Modules admin page — module CRUD. */

let allModules = [];
let modulePage = 0;
let modulesPerPage = 10;

async function loadModules() {
  const el = document.getElementById('module-table-wrap');
  try {
    const data = await ddaApi('GET', '/api/dda/rbac/modules');
    allModules = data.modules || [];
    modulePage = 0;
    applyModuleFilter();
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load modules: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function applyModuleFilter() {
  const q = (document.getElementById('module-filter')?.value || '').trim().toLowerCase();
  const rows = q ? allModules.filter((m) => m.name.toLowerCase().includes(q) || m.key.toLowerCase().includes(q)) : allModules;
  renderModuleTable(rows);
}

function renderModuleTable(rows) {
  const el = document.getElementById('module-table-wrap');
  if (!allModules.length) {
    el.innerHTML = '<p class="dim">No modules yet.</p>';
    document.getElementById('module-pagination')?.replaceChildren();
    return;
  }
  if (!rows.length) {
    el.innerHTML = '<p class="dim">No modules match your search.</p>';
    document.getElementById('module-pagination')?.replaceChildren();
    return;
  }

  const totalPages = Math.max(1, Math.ceil(rows.length / modulesPerPage));
  modulePage = Math.max(0, Math.min(modulePage, totalPages - 1));
  const start = modulePage * modulesPerPage;
  const pageRows = rows.slice(start, start + modulesPerPage);

  el.innerHTML = `
    <table class="dda-reports-table">
      <thead><tr><th>Name</th><th>Key</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody>
        ${pageRows.map((m) => `
          <tr>
            <td>${escapeHtml(m.name)}</td>
            <td><code>${escapeHtml(m.key)}</code></td>
            <td><span class="dda-status-badge ${m.status === 'in_use' ? 'is-active' : 'is-inactive'}">${m.status === 'in_use' ? 'In Use' : 'Deprecated'}</span></td>
            <td class="dda-report-actions-cell">
              <button type="button" class="btn btn-secondary btn-sm" data-view-module="${m.id}">View</button>
              <button type="button" class="btn btn-secondary btn-sm" data-edit-module="${m.id}">Edit</button>
              <button type="button" class="btn btn-danger btn-sm" data-del-module="${m.id}">Delete</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;

  el.querySelectorAll('[data-view-module]').forEach((btn) => {
    btn.addEventListener('click', () => openModuleViewModal(Number(btn.dataset.viewModule)));
  });
  el.querySelectorAll('[data-edit-module]').forEach((btn) => {
    btn.addEventListener('click', () => openModuleModal(Number(btn.dataset.editModule)));
  });
  el.querySelectorAll('[data-del-module]').forEach((btn) => {
    btn.addEventListener('click', () => deleteModule(Number(btn.dataset.delModule)));
  });

  if (typeof renderPaginationControls === 'function') {
    renderPaginationControls(document.getElementById('module-pagination'), modulePage, totalPages, (p) => {
      modulePage = p;
      renderModuleTable(rows);
    });
  }
}

function openModuleViewModal(moduleId) {
  const mod = allModules.find((m) => m.id === moduleId);
  if (!mod) return;
  document.getElementById('module-view-title').textContent = mod.name;
  document.getElementById('module-view-body').innerHTML = `
    <p><strong>Key:</strong> <code>${escapeHtml(mod.key)}</code></p>
    <p><strong>Status:</strong> ${mod.status === 'in_use' ? 'In Use' : 'Deprecated'}</p>
    <p><strong>Description:</strong> ${escapeHtml(mod.description || '—')}</p>`;
  document.getElementById('module-view-modal').classList.remove('hidden');
}
document.getElementById('module-view-close')?.addEventListener('click', () => document.getElementById('module-view-modal').classList.add('hidden'));
document.getElementById('module-view-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'module-view-modal') document.getElementById('module-view-modal').classList.add('hidden');
});

document.getElementById('module-page-size')?.addEventListener('change', (e) => {
  modulesPerPage = Number(e.target.value) || 10;
  modulePage = 0;
  applyModuleFilter();
});

function openModuleModal(moduleId) {
  const modal = document.getElementById('module-modal');
  const title = document.getElementById('module-modal-title');
  const mod = allModules.find((m) => m.id === moduleId);
  document.getElementById('module-id').value = mod ? mod.id : '';
  document.getElementById('module-key').value = mod ? mod.key : '';
  document.getElementById('module-key').disabled = !!mod;
  document.getElementById('module-name').value = mod ? mod.name : '';
  document.getElementById('module-description').value = mod ? mod.description || '' : '';
  document.getElementById('module-status').value = mod ? mod.status : 'in_use';
  title.textContent = mod ? 'Edit Module' : 'Add App Module';
  modal.classList.remove('hidden');
}

document.getElementById('btn-new-module')?.addEventListener('click', () => openModuleModal(null));
document.getElementById('module-modal-close')?.addEventListener('click', () => document.getElementById('module-modal').classList.add('hidden'));
document.getElementById('module-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'module-modal') document.getElementById('module-modal').classList.add('hidden');
});
document.getElementById('module-filter')?.addEventListener('input', applyModuleFilter);

document.getElementById('module-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError();
  const id = document.getElementById('module-id').value;
  const body = {
    key: document.getElementById('module-key').value.trim(),
    name: document.getElementById('module-name').value.trim(),
    description: document.getElementById('module-description').value.trim(),
    status: document.getElementById('module-status').value,
  };
  try {
    if (id) {
      await ddaApi('PUT', `/api/dda/rbac/modules/${id}`, { body: JSON.stringify(body) });
    } else {
      await ddaApi('POST', '/api/dda/rbac/modules', { body: JSON.stringify(body) });
    }
    document.getElementById('module-modal').classList.add('hidden');
    showDdaSuccess('Module saved.');
    await loadModules();
  } catch (err) {
    showDdaError(err.message);
  }
});

async function deleteModule(moduleId) {
  if (!confirm('Delete this module? It must not be linked to any menu item.')) return;
  try {
    await ddaApi('DELETE', `/api/dda/rbac/modules/${moduleId}`);
    showDdaSuccess('Module deleted.');
    await loadModules();
  } catch (err) {
    showDdaError(err.message);
  }
}

document.addEventListener('DOMContentLoaded', loadModules);
