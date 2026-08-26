/** Roles & Users admin page — role hierarchy, permission matrix, user CRUD. */

let allRoles = [];
let allUsers = [];
let userPage = 0;
const USERS_PER_PAGE = 10;

function roleModuleActionLabel(action) {
  return { canView: 'View', canCreate: 'Create', canEdit: 'Edit', canDelete: 'Delete' }[action] || action;
}

// ---------------------------------------------------------------- Roles ---

async function loadRoles() {
  const el = document.getElementById('role-list');
  try {
    const data = await ddaApi('GET', '/api/dda/rbac/roles');
    allRoles = data.roles || [];
    renderRoleList();
    populateUserRoleSelect();
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load roles: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function renderRoleList() {
  const el = document.getElementById('role-list');
  if (!allRoles.length) {
    el.innerHTML = '<p class="dim">No roles yet.</p>';
    return;
  }
  el.innerHTML = allRoles.map((r) => `
    <div class="dda-role-card" data-role-id="${r.id}">
      <div class="dda-role-card-info">
        <div class="dda-role-card-name">${escapeHtml(r.name)}${r.isSystem ? '<span class="dda-badge-system">Built-in</span>' : ''}</div>
        <div class="dda-role-card-desc">${escapeHtml(r.description || '')}</div>
      </div>
      <div class="dda-role-card-actions">
        <button type="button" class="btn btn-secondary btn-sm" data-perm-role="${r.id}">Permission Matrix</button>
        <button type="button" class="btn btn-secondary btn-sm" data-edit-role="${r.id}">Edit</button>
        <button type="button" class="btn btn-danger btn-sm" data-del-role="${r.id}"${r.isSystem ? ' disabled title="Built-in roles cannot be deleted"' : ''}>Del</button>
      </div>
    </div>`).join('');

  el.querySelectorAll('[data-perm-role]').forEach((btn) => {
    btn.addEventListener('click', () => openPermMatrix(Number(btn.dataset.permRole)));
  });
  el.querySelectorAll('[data-edit-role]').forEach((btn) => {
    btn.addEventListener('click', () => openRoleModal(Number(btn.dataset.editRole)));
  });
  el.querySelectorAll('[data-del-role]').forEach((btn) => {
    btn.addEventListener('click', () => deleteRole(Number(btn.dataset.delRole)));
  });
}

function openRoleModal(roleId) {
  const modal = document.getElementById('role-modal');
  const title = document.getElementById('role-modal-title');
  const role = allRoles.find((r) => r.id === roleId);
  document.getElementById('role-id').value = role ? role.id : '';
  document.getElementById('role-name').value = role ? role.name : '';
  document.getElementById('role-description').value = role ? role.description || '' : '';
  document.getElementById('role-rank').value = role ? role.rank : 0;
  title.textContent = role ? 'Edit Role' : 'New Role';
  modal.classList.remove('hidden');
}

document.getElementById('btn-new-role')?.addEventListener('click', () => openRoleModal(null));
document.getElementById('role-modal-close')?.addEventListener('click', () => document.getElementById('role-modal').classList.add('hidden'));
document.getElementById('role-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'role-modal') document.getElementById('role-modal').classList.add('hidden');
});

document.getElementById('role-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError();
  const id = document.getElementById('role-id').value;
  const body = {
    name: document.getElementById('role-name').value.trim(),
    description: document.getElementById('role-description').value.trim(),
    rank: Number(document.getElementById('role-rank').value) || 0,
  };
  try {
    if (id) {
      await ddaApi('PUT', `/api/dda/rbac/roles/${id}`, { body: JSON.stringify(body) });
    } else {
      await ddaApi('POST', '/api/dda/rbac/roles', { body: JSON.stringify(body) });
    }
    document.getElementById('role-modal').classList.add('hidden');
    showDdaSuccess('Role saved.');
    await loadRoles();
  } catch (err) {
    showDdaError(err.message);
  }
});

async function deleteRole(roleId) {
  if (!confirm('Delete this role? Users assigned to it must be reassigned first.')) return;
  try {
    await ddaApi('DELETE', `/api/dda/rbac/roles/${roleId}`);
    showDdaSuccess('Role deleted.');
    await loadRoles();
  } catch (err) {
    showDdaError(err.message);
  }
}

// ---------------------------------------------------- Permission matrix ---

async function openPermMatrix(roleId) {
  const modal = document.getElementById('perm-modal');
  const wrap = document.getElementById('perm-matrix-wrap');
  const title = document.getElementById('perm-modal-title');
  wrap.innerHTML = '<p class="dim">Loading…</p>';
  modal.classList.remove('hidden');
  modal.dataset.roleId = roleId;
  try {
    const data = await ddaApi('GET', `/api/dda/rbac/roles/${roleId}/permissions`);
    title.textContent = `Permission Matrix — ${data.role.name}`;
    const actions = ['canView', 'canCreate', 'canEdit', 'canDelete'];
    wrap.innerHTML = `
      <table class="dda-perm-matrix">
        <thead><tr><th>Module</th>${actions.map((a) => `<th>${roleModuleActionLabel(a)}</th>`).join('')}</tr></thead>
        <tbody>
          ${data.permissions.map((p) => `
            <tr data-module-id="${p.moduleId}">
              <td>${escapeHtml(p.moduleName)}</td>
              ${actions.map((a) => `<td><input type="checkbox" data-action="${a}" ${p[a] ? 'checked' : ''} /></td>`).join('')}
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (err) {
    wrap.innerHTML = `<p class="dim">Could not load permissions: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

document.getElementById('perm-modal-close')?.addEventListener('click', () => document.getElementById('perm-modal').classList.add('hidden'));
document.getElementById('perm-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'perm-modal') document.getElementById('perm-modal').classList.add('hidden');
});

function setAllPermCheckboxes(checked) {
  document.querySelectorAll('#perm-matrix-wrap input[type="checkbox"]').forEach((cb) => { cb.checked = checked; });
}
document.getElementById('btn-grant-all')?.addEventListener('click', () => setAllPermCheckboxes(true));
document.getElementById('btn-revoke-all')?.addEventListener('click', () => setAllPermCheckboxes(false));

document.getElementById('btn-save-perms')?.addEventListener('click', async () => {
  const modal = document.getElementById('perm-modal');
  const roleId = modal.dataset.roleId;
  const rows = modal.querySelectorAll('.dda-perm-matrix tbody tr');
  const permissions = [...rows].map((row) => {
    const get = (a) => row.querySelector(`input[data-action="${a}"]`)?.checked || false;
    return {
      moduleId: Number(row.dataset.moduleId),
      canView: get('canView'), canCreate: get('canCreate'),
      canEdit: get('canEdit'), canDelete: get('canDelete'),
    };
  });
  try {
    await ddaApi('PUT', `/api/dda/rbac/roles/${roleId}/permissions`, { body: JSON.stringify({ permissions }) });
    showDdaSuccess('Permissions saved.');
    modal.classList.add('hidden');
  } catch (err) {
    showDdaError(err.message);
  }
});

// ---------------------------------------------------------------- Users ---

function populateUserRoleSelect() {
  const sel = document.getElementById('user-role-select');
  if (!sel) return;
  sel.innerHTML = allRoles.map((r) => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join('');
}

async function loadUsers() {
  const el = document.getElementById('user-table-wrap');
  try {
    const data = await ddaApi('GET', '/api/dda/rbac/users');
    allUsers = data.users || [];
    userPage = 0;
    applyUserFilter();
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load users: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

function applyUserFilter() {
  const q = (document.getElementById('user-filter')?.value || '').trim().toLowerCase();
  const rows = q
    ? allUsers.filter((u) => u.email.toLowerCase().includes(q) || (u.fullName || '').toLowerCase().includes(q))
    : allUsers;
  renderUserTable(rows);
}

function renderUserTable(rows) {
  const el = document.getElementById('user-table-wrap');
  if (!allUsers.length) {
    el.innerHTML = '<p class="dim">No users yet.</p>';
    document.getElementById('user-pagination')?.replaceChildren();
    return;
  }
  if (!rows.length) {
    el.innerHTML = '<p class="dim">No users match your search.</p>';
    document.getElementById('user-pagination')?.replaceChildren();
    return;
  }

  const totalPages = Math.max(1, Math.ceil(rows.length / USERS_PER_PAGE));
  userPage = Math.max(0, Math.min(userPage, totalPages - 1));
  const start = userPage * USERS_PER_PAGE;
  const pageRows = rows.slice(start, start + USERS_PER_PAGE);

  el.innerHTML = `
    <table class="dda-reports-table">
      <thead>
        <tr><th>Name</th><th>Email</th><th>Role</th><th>Actions</th></tr>
      </thead>
      <tbody>
        ${pageRows.map((u) => `
          <tr>
            <td>${escapeHtml(u.fullName || '—')}</td>
            <td>${escapeHtml(u.email)}</td>
            <td>${escapeHtml(u.role || '—')}</td>
            <td class="dda-report-actions-cell">
              <button type="button" class="btn btn-secondary btn-sm" data-edit-user="${u.id}">Edit</button>
              <button type="button" class="btn btn-danger btn-sm" data-del-user="${u.id}">Del</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;

  el.querySelectorAll('[data-edit-user]').forEach((btn) => {
    btn.addEventListener('click', () => openUserModal(Number(btn.dataset.editUser)));
  });
  el.querySelectorAll('[data-del-user]').forEach((btn) => {
    btn.addEventListener('click', () => deleteUser(Number(btn.dataset.delUser)));
  });

  if (typeof renderPaginationControls === 'function') {
    renderPaginationControls(document.getElementById('user-pagination'), userPage, totalPages, (p) => {
      userPage = p;
      renderUserTable(rows);
    });
  }
}

function openUserModal(userId) {
  const modal = document.getElementById('user-modal');
  const title = document.getElementById('user-modal-title');
  const user = allUsers.find((u) => u.id === userId);
  document.getElementById('user-id').value = user ? user.id : '';
  document.getElementById('user-email').value = user ? user.email : '';
  document.getElementById('user-email').disabled = !!user;
  document.getElementById('user-full-name').value = user ? user.fullName || '' : '';
  document.getElementById('user-role-select').value = user ? user.roleId : (allRoles[0]?.id || '');
  document.getElementById('user-password').value = '';
  document.getElementById('user-password').required = !user;
  document.getElementById('user-password-hint').textContent = user ? '(leave blank to keep current password)' : '(min 8 characters)';
  title.textContent = user ? 'Edit User' : 'New User';
  modal.classList.remove('hidden');
}

document.getElementById('btn-new-user')?.addEventListener('click', () => openUserModal(null));
document.getElementById('user-modal-close')?.addEventListener('click', () => document.getElementById('user-modal').classList.add('hidden'));
document.getElementById('user-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'user-modal') document.getElementById('user-modal').classList.add('hidden');
});
document.getElementById('user-filter')?.addEventListener('input', applyUserFilter);

document.getElementById('user-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError();
  const id = document.getElementById('user-id').value;
  const password = document.getElementById('user-password').value;
  try {
    if (id) {
      const body = {
        fullName: document.getElementById('user-full-name').value.trim(),
        roleId: Number(document.getElementById('user-role-select').value),
      };
      if (password) body.password = password;
      await ddaApi('PUT', `/api/dda/rbac/users/${id}`, { body: JSON.stringify(body) });
    } else {
      const body = {
        email: document.getElementById('user-email').value.trim(),
        fullName: document.getElementById('user-full-name').value.trim(),
        roleId: Number(document.getElementById('user-role-select').value),
        password,
      };
      await ddaApi('POST', '/api/dda/rbac/users', { body: JSON.stringify(body) });
    }
    document.getElementById('user-modal').classList.add('hidden');
    showDdaSuccess('User saved.');
    await loadUsers();
  } catch (err) {
    showDdaError(err.message);
  }
});

async function deleteUser(userId) {
  if (!confirm('Delete this user? This cannot be undone.')) return;
  try {
    await ddaApi('DELETE', `/api/dda/rbac/users/${userId}`);
    showDdaSuccess('User deleted.');
    await loadUsers();
  } catch (err) {
    showDdaError(err.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadRoles();
  loadUsers();
});
