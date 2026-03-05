const API_BASE = '';

function getToken() { return localStorage.getItem('token'); }
function setToken(token) {
  if (token) localStorage.setItem('token', token);
  else localStorage.removeItem('token');
}

function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const el = document.getElementById('view-' + id);
  if (el) el.classList.add('active');
}

function showError(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}
function hideError(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}
function showSuccess(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

async function api(method, path, options = {}) {
  const headers = { ...options.headers };
  const token = getToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API_BASE + path, { method, headers, credentials: 'include', ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!res.ok) throw new Error(data?.detail || res.statusText || 'Request failed');
  return data;
}

// ---- Auth ----
document.getElementById('form-login')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError('login-error');
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  try {
    const data = await api('POST', '/api/auth/login', { body: JSON.stringify({ email, password }) });
    setToken(data.access_token);
    document.getElementById('user-email').textContent = data.user.email;
    showView('dashboard');
    loadHistory();
  } catch (err) { showError('login-error', err.message); }
});

document.getElementById('form-register')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError('register-error');
  const full_name = document.getElementById('register-name').value.trim();
  const email = document.getElementById('register-email').value.trim();
  const password = document.getElementById('register-password').value;
  try {
    const data = await api('POST', '/api/auth/register', { body: JSON.stringify({ email, password, full_name }) });
    setToken(data.access_token);
    document.getElementById('user-email').textContent = data.user.email;
    showView('dashboard');
    loadHistory();
  } catch (err) { showError('register-error', err.message); }
});

// ---- Forgot password ----
document.getElementById('form-forgot')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError('forgot-error');
  hideError('forgot-success');
  const email = document.getElementById('forgot-email').value.trim();
  const new_password = document.getElementById('forgot-password').value;
  try {
    const data = await api('POST', '/api/auth/reset-password', { body: JSON.stringify({ email, new_password }) });
    showSuccess('forgot-success', data.message || 'Password reset! You can now sign in.');
    document.getElementById('form-forgot').reset();
  } catch (err) { showError('forgot-error', err.message); }
});

// ---- Password visibility toggle ----
document.querySelectorAll('.password-toggle').forEach((btn) => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    const showing = input.type !== 'password';
    input.type = showing ? 'password' : 'text';
    btn.style.opacity = showing ? '' : '0.9';
  });
});

document.querySelectorAll('[data-view]').forEach((a) => {
  a.addEventListener('click', (e) => {
    e.preventDefault();
    showView(a.getAttribute('data-view'));
    hideError('login-error');
    hideError('register-error');
    hideError('forgot-error');
    hideError('forgot-success');
  });
});

document.getElementById('btn-logout')?.addEventListener('click', async () => {
  try { await fetch(API_BASE + '/api/auth/logout', { method: 'POST', credentials: 'include' }); } catch (_) {}
  setToken(null);
  document.getElementById('nav-dropdown')?.classList.add('hidden');
  showView('login');
});

// ---- Avatar dropdown toggle ----
document.getElementById('btn-avatar')?.addEventListener('click', (e) => {
  e.stopPropagation();
  document.getElementById('nav-dropdown')?.classList.toggle('hidden');
});
document.addEventListener('click', (e) => {
  const dd = document.getElementById('nav-dropdown');
  if (dd && !dd.classList.contains('hidden') && !e.target.closest('.nav-user')) {
    dd.classList.add('hidden');
  }
});

async function init() {
  const token = getToken();
  if (!token) { showView('login'); return; }
  try {
    const user = await api('GET', '/api/me');
    document.getElementById('user-email').textContent = user.email;
    showView('dashboard');
    loadHistory();
  } catch (_) { setToken(null); showView('login'); }
}

// ---- Upload zones with preview ----
function setupUploadZone(inputId, nameId, zoneId, previewId) {
  const input = document.getElementById(inputId);
  const nameEl = document.getElementById(nameId);
  const zone = document.getElementById(zoneId);
  const preview = document.getElementById(previewId);
  if (!input || !nameEl || !zone) return;

  function updatePreview() {
    const file = input.files?.[0];
    nameEl.textContent = file ? file.name : 'No file chosen';
    if (file && preview) {
      const reader = new FileReader();
      reader.onload = () => {
        preview.src = reader.result;
        preview.classList.remove('hidden');
      };
      reader.readAsDataURL(file);
    } else if (preview) {
      preview.classList.add('hidden');
    }
  }

  input.addEventListener('change', updatePreview);
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer?.files?.[0];
    if (file && file.type.startsWith('image/')) {
      input.files = e.dataTransfer.files;
      updatePreview();
    }
  });
}

setupUploadZone('file-before', 'name-before', 'zone-before', 'preview-before');
setupUploadZone('file-after', 'name-after', 'zone-after', 'preview-after');

// ---- Run detection ----
document.getElementById('form-detect')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError('dashboard-error');
  const before = document.getElementById('file-before').files?.[0];
  const after = document.getElementById('file-after').files?.[0];
  if (!before || !after) {
    showError('dashboard-error', 'Please select both before and after images.');
    return;
  }

  const btn = document.getElementById('btn-run');
  const loading = document.getElementById('run-loading');
  btn.disabled = true;
  loading.classList.remove('hidden');

  const token = getToken();
  const form = new FormData();
  form.append('before', before);
  form.append('after', after);
  form.append('method', document.getElementById('detect-method').value);
  form.append('title', document.getElementById('detect-title').value || 'Untitled run');
  form.append('enable_registration', document.getElementById('detect-registration').checked);
  form.append('enable_normalization', document.getElementById('detect-normalization').checked);
  if (token) form.append('access_token', token);

  try {
    if (!token) {
      showError('dashboard-error', 'Session expired. Please sign in again.');
      setToken(null);
      showView('login');
      return;
    }
    const data = await api('POST', '/api/detect', { body: form });
    showResult(data);
    showSuccess('dashboard-success', 'Detection complete!');
    loadHistory();
  } catch (err) {
    showError('dashboard-error', err.message);
  } finally {
    btn.disabled = false;
    loading.classList.add('hidden');
  }
});

// ---- Show result ----
function readFileAsDataURL(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(file);
  });
}

function showResult(data) {
  const card = document.getElementById('result-card');
  const statsEl = document.getElementById('result-stats');
  const tbody = document.getElementById('regions-tbody');

  statsEl.innerHTML = `
    <div class="stat-box"><div class="value">${data.statistics.changePercentage.toFixed(2)}%</div><div class="label">Changed</div></div>
    <div class="stat-box"><div class="value">${data.statistics.changedPixels.toLocaleString()}</div><div class="label">Changed px</div></div>
    <div class="stat-box"><div class="value">${data.statistics.totalPixels.toLocaleString()}</div><div class="label">Total px</div></div>
    <div class="stat-box"><div class="value">${(data.regions || []).length}</div><div class="label">Regions</div></div>
  `;

  const beforeImg = document.getElementById('compare-before-img');
  const afterImg = document.getElementById('compare-after-img');
  const beforeFile = document.getElementById('file-before').files?.[0];
  if (beforeFile) readFileAsDataURL(beforeFile).then((url) => { beforeImg.src = url; });

  afterImg.src = data.overlayBase64Png
    ? 'data:image/png;base64,' + data.overlayBase64Png
    : (data.overlayUrl || '');

  resetCompareSlider();

  tbody.innerHTML = '';
  (data.regions || []).slice(0, 50).forEach((r) => {
    const tr = document.createElement('tr');
    const subType = r.subType || '—';
    const stories = r.estimatedStories != null ? r.estimatedStories : '—';
    const height = r.estimatedHeightM != null ? r.estimatedHeightM + ' m' : '—';
    const stage = r.constructionStage && r.constructionStage !== 'Unknown' ? r.constructionStage : '—';
    tr.innerHTML = `
      <td>${r.id}</td>
      <td>${r.objectType}</td>
      <td>${subType}</td>
      <td>${(r.confidence * 100).toFixed(1)}%</td>
      <td>${r.area.toLocaleString()}</td>
      <td>${stories}</td>
      <td>${height}</td>
      <td>${stage}</td>
      <td>(${r.center.x}, ${r.center.y})</td>
    `;
    tbody.appendChild(tr);
  });

  card.classList.remove('hidden');
  card.scrollIntoView({ behavior: 'smooth' });
}

// ---- Compare slider ----
function initCompareSlider() {
  const slider = document.getElementById('compare-slider');
  if (!slider) return;
  let isDragging = false;

  function updatePosition(clientX) {
    const rect = slider.getBoundingClientRect();
    let pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    document.getElementById('compare-after-clip').style.clipPath = `inset(0 0 0 ${pct}%)`;
    document.getElementById('compare-handle').style.left = pct + '%';
  }

  slider.addEventListener('mousedown', (e) => { e.preventDefault(); isDragging = true; updatePosition(e.clientX); });
  document.addEventListener('mousemove', (e) => { if (isDragging) updatePosition(e.clientX); });
  document.addEventListener('mouseup', () => { isDragging = false; });

  slider.addEventListener('touchstart', (e) => { isDragging = true; updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchmove', (e) => { if (isDragging) updatePosition(e.touches[0].clientX); }, { passive: true });
  document.addEventListener('touchend', () => { isDragging = false; });
}

function resetCompareSlider() {
  const ac = document.getElementById('compare-after-clip');
  const h = document.getElementById('compare-handle');
  if (ac) ac.style.clipPath = 'inset(0 0 0 50%)';
  if (h) h.style.left = '50%';
}

initCompareSlider();

// ---- History with delete ----
async function loadHistory() {
  const list = document.getElementById('history-list');
  if (!list) return;
  try {
    const items = await api('GET', '/api/history');
    if (!items || items.length === 0) {
      list.innerHTML = '<div class="history-empty">No detection runs yet. Upload images above to get started.</div>';
      return;
    }
    list.innerHTML = items.map((r) => `
      <div class="history-item" data-id="${r.id}">
        <div class="history-info">
          <div class="history-title">${escapeHtml(r.title)}</div>
          <div class="history-meta">
            <span class="tag">${r.method}</span>
            ${r.changePercentage.toFixed(2)}% changed &middot; ${r.regionsCount} regions &middot; ${formatDate(r.createdAt)}
          </div>
        </div>
        <div class="history-actions">
          ${r.overlayUrl ? `<a href="${r.overlayUrl}" target="_blank" class="btn btn-secondary btn-sm">View</a>` : ''}
          <button class="btn-icon" title="Delete this run" onclick="confirmDelete(${r.id})">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
        </div>
      </div>
    `).join('');
  } catch (_) {
    list.innerHTML = '<div class="history-empty">Could not load history.</div>';
  }
}

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

// ---- Delete modal ----
let pendingDeleteId = null;

function confirmDelete(id) {
  pendingDeleteId = id;
  document.getElementById('modal-delete').classList.remove('hidden');
}

document.getElementById('modal-cancel')?.addEventListener('click', () => {
  document.getElementById('modal-delete').classList.add('hidden');
  pendingDeleteId = null;
});

document.getElementById('modal-confirm')?.addEventListener('click', async () => {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  document.getElementById('modal-delete').classList.add('hidden');
  pendingDeleteId = null;
  try {
    await api('DELETE', `/api/history/${id}`);
    // Animate removal
    const item = document.querySelector(`.history-item[data-id="${id}"]`);
    if (item) {
      item.style.transition = 'all 0.3s ease';
      item.style.opacity = '0';
      item.style.transform = 'translateX(20px)';
      setTimeout(() => { item.remove(); loadHistory(); }, 300);
    } else {
      loadHistory();
    }
    showSuccess('dashboard-success', 'Run deleted.');
  } catch (err) {
    showError('dashboard-error', err.message);
  }
});

// Close modal on backdrop click
document.getElementById('modal-delete')?.addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.classList.add('hidden');
    pendingDeleteId = null;
  }
});

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

init();
