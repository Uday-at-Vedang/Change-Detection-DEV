/** Zone / District and Village / Location master pages. */
(function () {
  const kind = document.body.getAttribute('data-masters');
  if (!kind) return;

  const isZones = kind === 'zones';
  let rows = [];
  let zones = [];

  const wrap = document.getElementById('masters-table-wrap');
  const modal = document.getElementById('masters-modal');
  const form = document.getElementById('masters-form');
  const idEl = document.getElementById('masters-id');
  const nameEl = document.getElementById('masters-name');
  const zoneEl = document.getElementById('masters-zone');
  const titleEl = document.getElementById('masters-modal-title');

  function openModal(edit) {
    idEl.value = edit ? String(edit.id) : '';
    nameEl.value = edit ? edit.name : '';
    if (titleEl) {
      titleEl.textContent = edit
        ? (isZones ? 'Edit zone' : 'Edit location')
        : (isZones ? 'Add zone' : 'Add location');
    }
    if (zoneEl) {
      zoneEl.innerHTML = zones.map((z) =>
        `<option value="${z.id}">${escapeHtml(z.name)}</option>`).join('');
      if (edit?.zoneId) zoneEl.value = String(edit.zoneId);
    }
    modal?.classList.remove('hidden');
    nameEl?.focus();
  }

  function closeModal() {
    modal?.classList.add('hidden');
    form?.reset();
  }

  function filtered() {
    const q = (document.getElementById('masters-filter')?.value || '').trim().toLowerCase();
    const zoneId = document.getElementById('masters-zone-filter')?.value || '';
    return rows.filter((r) => {
      if (q && !`${r.name} ${r.zoneName || ''}`.toLowerCase().includes(q)) return false;
      if (zoneId && String(r.zoneId) !== zoneId) return false;
      return true;
    });
  }

  function render() {
    const list = filtered();
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = `<p class="dim">No ${isZones ? 'zones' : 'locations'} yet. Add one to get started.</p>`;
      return;
    }
    if (!list.length) {
      wrap.innerHTML = '<p class="dim">No rows match this search.</p>';
      return;
    }
    wrap.innerHTML = `
      <table class="dda-reports-table">
        <thead>
          <tr>
            <th>Name</th>
            ${isZones ? '<th>Locations</th>' : '<th>Zone / District</th>'}
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${list.map((r) => `
            <tr>
              <td>${escapeHtml(r.name)}</td>
              <td>${isZones ? (r.villageCount ?? 0) : escapeHtml(r.zoneName || '—')}</td>
              <td class="dda-report-actions-cell">
                <button type="button" class="btn btn-secondary btn-sm" data-edit="${r.id}" data-requires-permission="masters:edit">Edit</button>
                <button type="button" class="btn btn-danger btn-sm" data-del="${r.id}" data-requires-permission="masters:delete">Remove</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>`;
    wrap.querySelectorAll('[data-edit]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const row = rows.find((r) => String(r.id) === btn.dataset.edit);
        if (row) openModal(row);
      });
    });
    wrap.querySelectorAll('[data-del]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const row = rows.find((r) => String(r.id) === btn.dataset.del);
        if (!row) return;
        if (!confirm(`Remove “${row.name}”?`)) return;
        try {
          await ddaApi('DELETE', isZones ? `/api/dda/masters/zones/${row.id}` : `/api/dda/masters/villages/${row.id}`);
          showDdaSuccess?.('Removed.');
          await load();
        } catch (err) {
          showDdaError?.(err.message || 'Could not remove.');
        }
      });
    });
    if (typeof applyPermissionGating === 'function') applyPermissionGating(window.ddaPermissions || {}, wrap);
  }

  async function load() {
    if (!wrap) return;
    wrap.innerHTML = '<p class="dim">Loading…</p>';
    try {
      if (isZones) {
        const data = await ddaApi('GET', '/api/dda/masters/zones');
        rows = data.zones || [];
      } else {
        const data = await ddaApi('GET', '/api/dda/masters/villages');
        rows = data.villages || [];
        zones = data.zones || [];
        const filter = document.getElementById('masters-zone-filter');
        if (filter) {
          const prev = filter.value;
          filter.innerHTML = '<option value="">All zones</option>' +
            zones.map((z) => `<option value="${z.id}">${escapeHtml(z.name)}</option>`).join('');
          if ([...filter.options].some((o) => o.value === prev)) filter.value = prev;
        }
      }
      render();
    } catch (err) {
      wrap.innerHTML = `<p class="dim">${escapeHtml(err.message || 'Could not load.')}</p>`;
    }
  }

  document.getElementById('btn-masters-new')?.addEventListener('click', () => openModal(null));
  document.getElementById('masters-modal-close')?.addEventListener('click', closeModal);
  modal?.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.getElementById('masters-filter')?.addEventListener('input', render);
  document.getElementById('masters-zone-filter')?.addEventListener('change', render);

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = idEl.value;
    const name = nameEl.value.trim();
    if (!name) return;
    const body = isZones
      ? { name }
      : { name, zone_id: Number(zoneEl.value) };
    try {
      if (id) {
        await ddaApi('PUT', isZones ? `/api/dda/masters/zones/${id}` : `/api/dda/masters/villages/${id}`, {
          body: JSON.stringify(body),
        });
        showDdaSuccess?.('Updated.');
      } else {
        await ddaApi('POST', isZones ? '/api/dda/masters/zones' : '/api/dda/masters/villages', {
          body: JSON.stringify(body),
        });
        showDdaSuccess?.('Added.');
      }
      closeModal();
      await load();
    } catch (err) {
      showDdaError?.(err.message || 'Could not save.');
    }
  });

  load();
})();
