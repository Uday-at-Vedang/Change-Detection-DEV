/** Exception Log + Application Logs (merged page). Exception Log shows system
 * errors captured by the global exception handlers in app/main.py.
 * Application Logs is the former standalone /logs page, folded in as a
 * second tab so both live under one "Configuration" entry. */
(function () {
  const PAGE_SIZE = 50;

  // ------------------------------------------------------------- Tabs ----
  const TITLES = {
    exceptions: ['💥 Exception Log', 'System errors captured by the global exception handler'],
    applogs: ['📝 Application Logs', 'Live view of recent server events. Filter, download, or clear the buffer.'],
  };

  function activateTab(tab) {
    document.getElementById('exceptions-tab').hidden = tab !== 'exceptions';
    document.getElementById('applogs-tab').hidden = tab !== 'applogs';
    document.querySelectorAll('#log-tab-switch .dda-mode-btn').forEach((btn) => {
      const active = btn.dataset.tab === tab;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-checked', String(active));
    });
    const title = document.getElementById('log-page-title');
    if (title) title.textContent = TITLES[tab][0];
    // Only the Exceptions tab has its own subtitle element carrying a live
    // record count; Application Logs' equivalent count lives in #logs-meta.
    if (tab === 'exceptions') {
      const sub = document.getElementById('exception-log-subtitle');
      if (sub && !sub.dataset.loaded) sub.textContent = TITLES.exceptions[1];
    }
    if (tab === 'applogs') {
      loadLogs();
      scheduleLive();
    } else {
      if (liveTimer) clearInterval(liveTimer);
      liveTimer = null;
    }
  }

  document.querySelectorAll('#log-tab-switch .dda-mode-btn').forEach((btn) => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  // ------------------------------------------------------- Exceptions ----
  const wrap = document.getElementById('exception-log-table-wrap');
  const meta = document.getElementById('exception-log-meta');
  const subtitle = document.getElementById('exception-log-subtitle');
  let exceptionPage = 0;

  function fillSelect(select, values, current) {
    if (!select) return;
    const placeholder = select.options[0];
    select.innerHTML = '';
    select.appendChild(placeholder);
    values.forEach((v) => {
      const opt = document.createElement('option');
      opt.value = String(v);
      opt.textContent = String(v);
      select.appendChild(opt);
    });
    if (current) select.value = current;
  }

  async function loadExceptionFilters() {
    try {
      const data = await ddaApi('GET', '/api/dda/exception-log/filters');
      fillSelect(document.getElementById('exception-filter-type'), data.types || []);
      fillSelect(document.getElementById('exception-filter-status'), data.statusCodes || []);
    } catch (_) { /* filter dropdowns are a nice-to-have; ignore failures */ }
  }

  function setStats(stats, total) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('exception-stat-500', stats?.errors500 ?? 0);
    set('exception-stat-other', stats?.errorsOther ?? 0);
    set('exception-stat-total', total ?? 0);
    set('exception-stat-unhandled', stats?.unhandled ?? 0);
  }

  async function loadExceptionLog() {
    if (!wrap) return;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(exceptionPage * PAGE_SIZE) });
    const type = document.getElementById('exception-filter-type')?.value || '';
    const status = document.getElementById('exception-filter-status')?.value || '';
    const from = document.getElementById('exception-filter-from')?.value || '';
    const to = document.getElementById('exception-filter-to')?.value || '';
    if (type) params.set('type', type);
    if (status) params.set('status_code', status);
    if (from) params.set('from', from);
    if (to) params.set('to', to);

    try {
      const data = await ddaApi('GET', '/api/dda/exception-log?' + params.toString());
      const rows = data.logs || [];
      if (subtitle) { subtitle.textContent = `System errors captured by the global exception handler — ${data.total || 0} total records`; subtitle.dataset.loaded = '1'; }
      if (meta) meta.textContent = `${rows.length ? exceptionPage * PAGE_SIZE + 1 : 0}–${exceptionPage * PAGE_SIZE + rows.length} of ${data.total || 0}`;
      setStats(data.stats, data.total);
      const totalPages = Math.max(1, Math.ceil((data.total || 0) / PAGE_SIZE));
      renderPaginationControls(document.getElementById('exception-log-pagination'), exceptionPage, totalPages, (p) => {
        exceptionPage = p;
        loadExceptionLog();
      });
      if (!rows.length) {
        wrap.innerHTML = '<p class="dim">No exceptions match these filters.</p>';
        return;
      }
      wrap.innerHTML = `
        <table class="dda-logs-table">
          <thead>
            <tr><th>Timestamp (IST)</th><th>Type</th><th>Message</th><th>Path</th><th>Code</th><th>Handled</th><th>Details</th></tr>
          </thead>
          <tbody>
            ${rows.map((r) => `
              <tr>
                <td><time class="dda-date">${typeof formatDateIst === 'function' ? formatDateIst(r.timestamp) : escapeHtml(r.timestamp || '')}</time></td>
                <td><span class="dda-badge ${r.statusCode >= 500 ? 'is-danger' : 'is-neutral'}">${escapeHtml(r.type)}</span></td>
                <td class="dda-log-msg">${escapeHtml(r.message || '')}</td>
                <td class="dda-action-cell">${escapeHtml(r.method || '')} ${escapeHtml(r.path || '')}</td>
                <td>${escapeHtml(String(r.statusCode ?? ''))}</td>
                <td><span class="dda-badge ${r.handled ? 'is-ok' : 'is-danger'}">${r.handled ? '✓ Yes' : '✗ No'}</span></td>
                <td><button type="button" class="btn btn-secondary btn-sm exception-view-btn" data-id="${r.id}">View</button></td>
              </tr>`).join('')}
          </tbody>
        </table>`;
      wrap.querySelectorAll('.exception-view-btn').forEach((btn) => {
        btn.addEventListener('click', () => showDetail(btn.dataset.id));
      });
    } catch (err) {
      wrap.innerHTML = `<p class="dim">${escapeHtml(err.message || 'Could not load the exception log.')}</p>`;
    }
  }

  async function showDetail(id) {
    const modal = document.getElementById('exception-detail-modal');
    const content = document.getElementById('exception-detail-content');
    if (!modal || !content) return;
    content.textContent = 'Loading…';
    modal.classList.remove('hidden');
    try {
      const entry = await ddaApi('GET', `/api/dda/exception-log/${id}`);
      content.textContent =
        `${entry.type} — ${entry.statusCode} (${entry.handled ? 'handled' : 'unhandled'})\n` +
        `${entry.method} ${entry.path}\n` +
        `User: ${entry.userEmail || '—'}\n` +
        `Time: ${entry.timestamp}\n\n${entry.message}\n\n${entry.traceback || ''}`;
    } catch (err) {
      content.textContent = err.message || 'Could not load exception details.';
    }
  }

  document.getElementById('btn-exception-detail-close')?.addEventListener('click', () => {
    document.getElementById('exception-detail-modal')?.classList.add('hidden');
  });
  document.getElementById('exception-detail-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'exception-detail-modal') e.target.classList.add('hidden');
  });

  document.getElementById('btn-exception-refresh')?.addEventListener('click', loadExceptionLog);
  document.getElementById('btn-exception-filter')?.addEventListener('click', () => { exceptionPage = 0; loadExceptionLog(); });
  document.getElementById('btn-exception-reset')?.addEventListener('click', () => {
    ['exception-filter-type', 'exception-filter-status', 'exception-filter-from', 'exception-filter-to'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    exceptionPage = 0;
    loadExceptionLog();
  });
  document.getElementById('btn-exception-clear-old')?.addEventListener('click', async () => {
    if (!confirm('Permanently delete exception log entries older than 30 days?')) return;
    try {
      const res = await ddaApi('DELETE', '/api/dda/exception-log/old?days=30');
      showDdaSuccess?.(`Cleared ${res.deleted || 0} old entries.`);
      exceptionPage = 0;
      await loadExceptionLog();
    } catch (err) {
      showDdaError?.(err.message || 'Could not clear old entries.');
    }
  });

  // --------------------------------------------------- Application logs --
  const logsWrap = document.getElementById('logs-table-wrap');
  const logsMeta = document.getElementById('logs-meta');
  let logsPage = 0;
  let liveTimer = null;

  function levelClass(level) {
    const l = (level || '').toUpperCase();
    if (l === 'ERROR' || l === 'CRITICAL') return 'is-error';
    if (l === 'WARNING') return 'is-warn';
    if (l === 'DEBUG') return 'is-debug';
    return 'is-info';
  }

  async function loadLogs() {
    if (!logsWrap) return;
    const q = document.getElementById('logs-q')?.value?.trim() || '';
    const level = document.getElementById('logs-level')?.value || '';
    const loggerName = document.getElementById('logs-logger')?.value?.trim() || '';
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(logsPage * PAGE_SIZE) });
    if (q) params.set('q', q);
    if (level) params.set('level', level);
    if (loggerName) params.set('logger', loggerName);
    try {
      const data = await ddaApi('GET', '/api/dda/logs?' + params.toString());
      const rows = data.logs || [];
      if (logsMeta) logsMeta.textContent = `${data.total || 0} event${data.total === 1 ? '' : 's'}`;
      const totalPages = Math.max(1, Math.ceil((data.total || 0) / PAGE_SIZE));
      renderPaginationControls(document.getElementById('logs-pagination'), logsPage, totalPages, (p) => {
        logsPage = p;
        loadLogs();
      });
      if (!rows.length) {
        logsWrap.innerHTML = '<p class="dim">No log events match these filters.</p>';
        return;
      }
      logsWrap.innerHTML = `
        <table class="dda-logs-table">
          <thead>
            <tr><th>Time (IST)</th><th>Level</th><th>Logger</th><th>Message</th></tr>
          </thead>
          <tbody>
            ${rows.map((r) => `
              <tr class="dda-log-row ${levelClass(r.level)}">
                <td><time class="dda-date">${typeof formatDateIst === 'function' ? formatDateIst(r.ts) : escapeHtml(r.ts || '')}</time></td>
                <td><span class="dda-log-level">${escapeHtml(r.level)}</span></td>
                <td class="dda-log-logger">${escapeHtml(r.logger || '')}</td>
                <td class="dda-log-msg">${escapeHtml(r.message || '')}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    } catch (err) {
      logsWrap.innerHTML = `<p class="dim">${escapeHtml(err.message || 'Could not load logs.')}</p>`;
    }
  }

  function scheduleLive() {
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = null;
    if (document.getElementById('applogs-tab')?.hidden) return;
    if (document.getElementById('logs-live')?.checked) {
      liveTimer = setInterval(loadLogs, 4000);
    }
  }

  document.getElementById('btn-logs-refresh')?.addEventListener('click', loadLogs);
  document.getElementById('logs-live')?.addEventListener('change', scheduleLive);
  ['logs-q', 'logs-level', 'logs-logger'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
      clearTimeout(window._logsFilterTimer);
      window._logsFilterTimer = setTimeout(() => { logsPage = 0; loadLogs(); }, 250);
    });
  });
  document.getElementById('btn-logs-clear')?.addEventListener('click', async () => {
    if (!confirm('Clear the in-memory log buffer and truncate the log file?')) return;
    try {
      await ddaApi('DELETE', '/api/dda/logs');
      showDdaSuccess?.('Logs cleared.');
      logsPage = 0;
      await loadLogs();
    } catch (err) {
      showDdaError?.(err.message || 'Could not clear logs.');
    }
  });

  // ------------------------------------------------------------- Init ----
  const initialTab = new URLSearchParams(window.location.search).get('tab') === 'applogs' ? 'applogs' : 'exceptions';
  loadExceptionFilters().then(loadExceptionLog);
  activateTab(initialTab);
})();
