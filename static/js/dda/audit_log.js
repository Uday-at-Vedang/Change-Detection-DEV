/** Audit Log viewer — user activity trail (login/logout, uploads, detections,
 * reports, admin/master-data changes). */
(function () {
  const PAGE_SIZE = 50;
  const wrap = document.getElementById('audit-log-table-wrap');
  const meta = document.getElementById('audit-log-meta');
  const subtitle = document.getElementById('audit-log-subtitle');
  if (!wrap) return;

  let auditPage = 0;

  function fillSelect(select, values, current) {
    if (!select) return;
    const placeholder = select.options[0];
    select.innerHTML = '';
    select.appendChild(placeholder);
    values.forEach((v) => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      select.appendChild(opt);
    });
    if (current) select.value = current;
  }

  async function loadFilters() {
    try {
      const data = await ddaApi('GET', '/api/dda/audit-log/filters');
      fillSelect(document.getElementById('audit-filter-action'), data.actions || []);
      fillSelect(document.getElementById('audit-filter-module'), data.modules || []);
      fillSelect(document.getElementById('audit-filter-role'), data.roles || []);
    } catch (_) { /* filter dropdowns are a nice-to-have; ignore failures */ }
  }

  async function loadAuditLog() {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(auditPage * PAGE_SIZE) });
    const action = document.getElementById('audit-filter-action')?.value || '';
    const module = document.getElementById('audit-filter-module')?.value || '';
    const role = document.getElementById('audit-filter-role')?.value || '';
    const from = document.getElementById('audit-filter-from')?.value || '';
    const to = document.getElementById('audit-filter-to')?.value || '';
    if (action) params.set('action', action);
    if (module) params.set('module', module);
    if (role) params.set('role', role);
    if (from) params.set('from', from);
    if (to) params.set('to', to);

    try {
      const data = await ddaApi('GET', '/api/dda/audit-log?' + params.toString());
      const rows = data.logs || [];
      if (subtitle) subtitle.textContent = `User activity audit trail — ${data.total || 0} total records`;
      if (meta) meta.textContent = `${rows.length ? auditPage * PAGE_SIZE + 1 : 0}–${auditPage * PAGE_SIZE + rows.length} of ${data.total || 0}`;
      const totalPages = Math.max(1, Math.ceil((data.total || 0) / PAGE_SIZE));
      renderPaginationControls(document.getElementById('audit-log-pagination'), auditPage, totalPages, (p) => {
        auditPage = p;
        loadAuditLog();
      });
      if (!rows.length) {
        wrap.innerHTML = '<p class="dim">No audit events match these filters.</p>';
        return;
      }
      wrap.innerHTML = `
        <table class="dda-logs-table">
          <thead>
            <tr><th>Timestamp (IST)</th><th>User</th><th>Role</th><th>Action</th><th>Module</th><th>Details</th></tr>
          </thead>
          <tbody>
            ${rows.map((r) => `
              <tr>
                <td><time class="dda-date">${typeof formatDateIst === 'function' ? formatDateIst(r.timestamp) : escapeHtml(r.timestamp || '')}</time></td>
                <td>${escapeHtml(r.user || '—')}</td>
                <td>${escapeHtml(r.role || '—')}</td>
                <td class="dda-action-cell">${escapeHtml(r.action)}</td>
                <td>${escapeHtml(r.module || '—')}</td>
                <td class="dda-log-msg">${escapeHtml(r.newValue || r.oldValue || '')}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    } catch (err) {
      wrap.innerHTML = `<p class="dim">${escapeHtml(err.message || 'Could not load the audit log.')}</p>`;
    }
  }

  document.getElementById('btn-audit-refresh')?.addEventListener('click', loadAuditLog);
  document.getElementById('btn-audit-filter')?.addEventListener('click', () => { auditPage = 0; loadAuditLog(); });
  document.getElementById('btn-audit-reset')?.addEventListener('click', () => {
    ['audit-filter-action', 'audit-filter-module', 'audit-filter-role', 'audit-filter-from', 'audit-filter-to'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    auditPage = 0;
    loadAuditLog();
  });

  loadFilters().then(loadAuditLog);
})();
