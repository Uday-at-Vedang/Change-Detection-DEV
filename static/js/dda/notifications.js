/** In-app notification bell — polls completed detection jobs (FR-05). */

const SEEN_JOBS_KEY = 'dda_seen_job_ids';
const POLL_MS = 30000;

function getSeenJobIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(SEEN_JOBS_KEY) || '[]'));
  } catch (_) {
    return new Set();
  }
}

function saveSeenJobIds(ids) {
  try {
    localStorage.setItem(SEEN_JOBS_KEY, JSON.stringify([...ids].slice(-200)));
  } catch (_) {}
}

function markJobSeen(jobId) {
  const seen = getSeenJobIds();
  seen.add(String(jobId));
  saveSeenJobIds(seen);
  refreshNotifications();
}

function formatNotifyTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
  } catch (_) {
    return iso;
  }
}

async function fetchNotifyJobs() {
  try {
    const data = await ddaApi('GET', '/api/dda/jobs?limit=25');
    return data.jobs || [];
  } catch (_) {
    return [];
  }
}

function updateNotifyBadge(jobs) {
  const badge = document.getElementById('dda-notify-badge');
  if (!badge) return;
  const seen = getSeenJobIds();
  const unread = jobs.filter((j) => j.status === 'completed' && j.runId && !seen.has(String(j.id)));
  if (unread.length) {
    badge.textContent = unread.length > 9 ? '9+' : String(unread.length);
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
  return unread;
}

function renderNotifyPanel(jobs) {
  const panel = document.getElementById('dda-notify-panel');
  if (!panel) return;
  const seen = getSeenJobIds();
  const items = jobs.filter((j) => ['completed', 'failed', 'running', 'queued'].includes(j.status));
  if (!items.length) {
    panel.innerHTML = '<p class="dim dda-notify-empty">No recent jobs.</p>';
    return;
  }
  panel.innerHTML = items.slice(0, 12).map((j) => {
    const unread = j.status === 'completed' && !seen.has(String(j.id));
    const title = j.title || `${j.basePath || 'Base'} vs ${j.comparisonPath || 'Comparison'}`;
    const pct = j.report?.changePercentage;
    const meta = j.status === 'completed' && pct != null ? `${pct.toFixed(2)}% change` : j.status;
    return `
      <button type="button" class="dda-notify-item${unread ? ' unread' : ''}" data-job-id="${j.id}" data-run-id="${j.runId || ''}" data-status="${j.status}">
        <span class="dda-notify-item-title">${title}</span>
        <span class="dda-notify-item-meta">${formatNotifyTime(j.completedAt || j.createdAt)} · ${meta}</span>
      </button>`;
  }).join('');

  panel.querySelectorAll('.dda-notify-item').forEach((btn) => {
    btn.addEventListener('click', async () => {
      markJobSeen(btn.dataset.jobId);
      const status = btn.dataset.status;
      const runId = btn.dataset.runId;
      closeNotifyPanel();
      if (status === 'completed' && runId) {
        if (typeof showDdaResult === 'function') {
          try {
            const data = await ddaApi('GET', `/api/history/${runId}`);
            showDdaResult(data);
          } catch (_) {
            window.open(`/dda/reports/${runId}`, '_blank');
          }
        } else {
          window.open(`/dda/reports/${runId}`, '_blank');
        }
      } else if (status === 'failed') {
        if (typeof showDdaError === 'function') showDdaError('Detection job failed. See Reports tab for details.');
        document.querySelector('.dda-tab[data-tab="reports"]')?.click();
      } else {
        document.querySelector('.dda-tab[data-tab="reports"]')?.click();
      }
    });
  });
}

function closeNotifyPanel() {
  document.getElementById('dda-notify-panel')?.classList.add('hidden');
}

async function refreshNotifications() {
  const jobs = await fetchNotifyJobs();
  updateNotifyBadge(jobs);
  const panel = document.getElementById('dda-notify-panel');
  if (panel && !panel.classList.contains('hidden')) {
    renderNotifyPanel(jobs);
  }
  return jobs;
}

function initNotifications() {
  const btn = document.getElementById('dda-notify-btn');
  if (!btn) return;

  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const panel = document.getElementById('dda-notify-panel');
    const wasHidden = panel?.classList.contains('hidden');
    if (wasHidden) {
      const jobs = await fetchNotifyJobs();
      renderNotifyPanel(jobs);
      panel?.classList.remove('hidden');
    } else {
      closeNotifyPanel();
    }
  });

  document.addEventListener('click', (e) => {
    if (e.target.closest('.dda-notify-wrap')) return;
    closeNotifyPanel();
  });

  refreshNotifications();
  setInterval(refreshNotifications, POLL_MS);
}

document.addEventListener('DOMContentLoaded', initNotifications);
window.markDdaJobSeen = markJobSeen;
window.refreshDdaNotifications = refreshNotifications;
