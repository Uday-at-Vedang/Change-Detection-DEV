/** Application logs viewer. */
(function () {
  const wrap = document.getElementById('logs-table-wrap');
  const meta = document.getElementById('logs-meta');
  let timer = null;

  function levelClass(level) {
    const l = (level || '').toUpperCase();
    if (l === 'ERROR' || l === 'CRITICAL') return 'is-error';
    if (l === 'WARNING') return 'is-warn';
    if (l === 'DEBUG') return 'is-debug';
    return 'is-info';
  }

  async function loadLogs() {
    if (!wrap) return;
    const q = document.getElementById('logs-q')?.value?.trim() || '';
    const level = document.getElementById('logs-level')?.value || '';
    const logger = document.getElementById('logs-logger')?.value?.trim() || '';
    const params = new URLSearchParams({ limit: '300', offset: '0' });
    if (q) params.set('q', q);
    if (level) params.set('level', level);
    if (logger) params.set('logger', logger);
    try {
      const data = await ddaApi('GET', '/api/dda/logs?' + params.toString());
      const rows = data.logs || [];
      if (meta) meta.textContent = `${data.total || 0} event${data.total === 1 ? '' : 's'}`;
      if (!rows.length) {
        wrap.innerHTML = '<p class="dim">No log events match these filters.</p>';
        return;
      }
      wrap.innerHTML = `
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
      wrap.innerHTML = `<p class="dim">${escapeHtml(err.message || 'Could not load logs.')}</p>`;
    }
  }

  function scheduleLive() {
    if (timer) clearInterval(timer);
    timer = null;
    if (document.getElementById('logs-live')?.checked) {
      timer = setInterval(loadLogs, 4000);
    }
  }

  document.getElementById('btn-logs-refresh')?.addEventListener('click', loadLogs);
  document.getElementById('logs-live')?.addEventListener('change', scheduleLive);
  ['logs-q', 'logs-level', 'logs-logger'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
      clearTimeout(window._logsFilterTimer);
      window._logsFilterTimer = setTimeout(loadLogs, 250);
    });
  });
  document.getElementById('btn-logs-clear')?.addEventListener('click', async () => {
    if (!confirm('Clear the in-memory log buffer and truncate the log file?')) return;
    try {
      await ddaApi('DELETE', '/api/dda/logs');
      showDdaSuccess?.('Logs cleared.');
      await loadLogs();
    } catch (err) {
      showDdaError?.(err.message || 'Could not clear logs.');
    }
  });

  loadLogs();
  scheduleLive();
})();
