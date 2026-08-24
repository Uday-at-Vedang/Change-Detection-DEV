/** Home dashboard — greeting + recent detection runs. */

function formatHomeDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch (_) {
    return iso;
  }
}

async function loadHomeGreeting() {
  const el = document.getElementById('dda-home-greeting');
  if (!el) return;
  try {
    const me = await ddaApi('GET', '/api/me');
    el.textContent = me.full_name ? `Welcome, ${me.full_name}` : 'Welcome';
  } catch (_) {
    // Not authenticated — the page-load redirect to /login should already
    // have caught this; leave the default greeting rather than erroring loudly.
  }
}

async function loadHomeRecent() {
  const el = document.getElementById('dda-home-recent');
  if (!el) return;
  try {
    const history = await ddaApi('GET', '/api/history');
    const rows = (history || []).slice(0, 5);
    if (!rows.length) {
      el.innerHTML = '<p class="dim">No detection runs yet. Start one from the Change Detection page.</p>';
      return;
    }
    el.innerHTML = `
      <table class="dda-reports-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Title</th>
            <th>Change %</th>
            <th>Regions</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${formatHomeDate(r.createdAt)}</td>
              <td>${escapeHtml(r.title)}</td>
              <td>${r.changePercentage != null ? r.changePercentage.toFixed(2) + '%' : '—'}</td>
              <td>${r.regionsCount ?? '—'}</td>
              <td><a class="btn btn-secondary btn-sm" href="/dda/reports/${r.id}" target="_blank" rel="noopener">Report</a></td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (err) {
    el.innerHTML = `<p class="dim">Could not load recent activity: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadHomeGreeting();
  loadHomeRecent();
});
