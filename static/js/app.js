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

// ---- Delhi Zone → Village cascading dropdowns ----
const DELHI_ZONES = {
  "Central Delhi": [
    "Karol Bagh", "Paharganj", "Daryaganj", "Rajinder Nagar", "Patel Nagar",
    "Anand Parbat", "Bapa Nagar", "Prasad Nagar", "Dev Nagar", "Old Rajinder Nagar"
  ],
  "New Delhi": [
    "Connaught Place", "Chanakyapuri", "Lodhi Road", "Mandi House",
    "India Gate", "Khan Market", "Barakhamba", "Gole Market", "Sansad Marg"
  ],
  "North Delhi": [
    "Civil Lines", "Model Town", "Sadar Bazaar", "Timarpur", "Gulabi Bagh",
    "Kamla Nagar", "Shakti Nagar", "Roop Nagar", "Vijay Nagar", "Mukherjee Nagar",
    "GTB Nagar", "Adarsh Nagar", "Azadpur", "Wazirabad"
  ],
  "North West Delhi": [
    "Rohini", "Narela", "Bawana", "Alipur", "Shalimar Bagh",
    "Pitampura", "Kanjhawala", "Mundka", "Sultanpuri", "Mangolpuri",
    "Begumpur", "Pooth Kalan", "Holambi Kalan", "Bankner", "Siraspur"
  ],
  "North East Delhi": [
    "Seelampur", "Jafrabad", "Mustafabad", "Babarpur", "Gokulpuri",
    "Yamuna Vihar", "Karawal Nagar", "Dayalpur", "Khajuri Khas",
    "Bhajanpura", "Harsh Vihar", "Brahmpuri", "Ghonda"
  ],
  "East Delhi": [
    "Preet Vihar", "Laxmi Nagar", "Mayur Vihar Phase I", "Mayur Vihar Phase II",
    "Mayur Vihar Phase III", "Patparganj", "Pandav Nagar", "Shakarpur",
    "Mandawali", "Kalyanpuri", "Trilokpuri", "Kondli", "Gharoli",
    "Khichripur", "Anand Vihar"
  ],
  "Shahdara": [
    "Shahdara", "Vivek Vihar", "Dilshad Garden", "Seema Puri", "New Seelampur",
    "Nand Nagri", "Harsh Vihar", "Jhilmil Colony", "Mansarovar Park"
  ],
  "South Delhi": [
    "Hauz Khas", "Mehrauli", "Saket", "Kalkaji", "Greater Kailash",
    "Malviya Nagar", "Vasant Kunj", "Chattarpur", "Lado Sarai",
    "Fatehpur Beri", "Mandi Village", "Dera Village", "Aaya Nagar",
    "Sultanpur", "Ghitorni", "Satbari", "Jonapur", "Asola"
  ],
  "South East Delhi": [
    "Defence Colony", "Okhla", "Jamia Nagar", "Badarpur", "Jaitpur",
    "Madanpur Khadar", "Sarita Vihar", "Jasola", "Sukhdev Vihar",
    "Tughlakabad", "Sangam Vihar", "Mithapur", "Pul Pehlad"
  ],
  "South West Delhi": [
    "Dwarka", "Najafgarh", "Kapashera", "Palam", "Dabri",
    "Mahavir Enclave", "Bindapur", "Uttam Nagar", "Nasirpur",
    "Chhawla", "Dichaon Kalan", "Ghumanhera", "Jhatikara",
    "Rawta", "Pochanpur", "Bijwasan", "Sarangpur", "Paprawat"
  ],
  "West Delhi": [
    "Rajouri Garden", "Janakpuri", "Tilak Nagar", "Vikaspuri",
    "Hari Nagar", "Subhash Nagar", "Tagore Garden", "Moti Nagar",
    "Kirti Nagar", "Punjabi Bagh", "Nangloi Jat", "Nilothi",
    "Mundka", "Madipur", "Paschim Vihar"
  ]
};

(function initZoneVillage() {
  const zoneSel = document.getElementById('detect-zone');
  const villageSel = document.getElementById('detect-village');
  if (!zoneSel || !villageSel) return;

  Object.keys(DELHI_ZONES).sort().forEach(z => {
    const opt = document.createElement('option');
    opt.value = z;
    opt.textContent = z;
    zoneSel.appendChild(opt);
  });

  zoneSel.addEventListener('change', () => {
    const zone = zoneSel.value;
    villageSel.innerHTML = '';
    if (!zone) {
      villageSel.disabled = true;
      villageSel.innerHTML = '<option value="">— Select Zone first —</option>';
      return;
    }
    villageSel.disabled = false;
    villageSel.innerHTML = '<option value="">— Select Village / Area —</option>';
    (DELHI_ZONES[zone] || []).forEach(v => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      villageSel.appendChild(opt);
    });
  });
})();

// ---- Notify checkbox toggle ----
(function initNotifyToggle() {
  const cb = document.getElementById('detect-notify');
  const group = document.getElementById('notify-email-group');
  if (!cb || !group) return;

  cb.addEventListener('change', () => {
    if (cb.checked) {
      group.classList.remove('hidden');
      group.classList.add('visible');
    } else {
      group.classList.remove('visible');
      group.classList.add('hidden');
      document.getElementById('notify-email').value = '';
    }
  });
})();

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
  form.append('zone', document.getElementById('detect-zone').value || '');
  form.append('village', document.getElementById('detect-village').value || '');
  form.append('enable_registration', document.getElementById('detect-registration').checked);
  form.append('enable_normalization', document.getElementById('detect-normalization').checked);

  // Notify: validate and attach email if checkbox is checked
  const notifyCb = document.getElementById('detect-notify');
  const notifyInput = document.getElementById('notify-email');
  if (notifyCb?.checked) {
    const email = (notifyInput?.value || '').trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      showError('dashboard-error', 'Please enter a valid email address for notification.');
      btn.disabled = false;
      loading.classList.add('hidden');
      return;
    }
    form.append('notify_email', email);
  }

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
    const notifyMsg = data.notificationSent
      ? ' Notification email sent.'
      : '';
    showSuccess('dashboard-success', 'Detection complete!' + notifyMsg);
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
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function formatCompact(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 10_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
  return n.toLocaleString();
}

// Store current result for zoom and region hover (bbox in % for overlay)
let currentResultData = null;

function showResult(data) {
  const modal = document.getElementById('result-modal');
  const statsEl = document.getElementById('result-stats');
  const tbody = document.getElementById('regions-tbody');
  const titleEl = document.getElementById('result-modal-title');

  currentResultData = data;

  if (titleEl) titleEl.textContent = data.title || 'Result View';

  const locParts = [data.village, data.zone].filter(Boolean);
  const locLabel = locParts.length ? locParts.join(', ') : '—';
  const stats = data.statistics || {};
  const pct = (stats.changePercentage ?? 0).toFixed(2);
  const chPx = stats.changedPixels ?? 0;
  const totPx = stats.totalPixels ?? 0;

  statsEl.innerHTML = `
    <div class="stat-box"><div class="value">${pct}%</div><div class="label">Changed</div></div>
    <div class="stat-box"><div class="value" title="${chPx.toLocaleString()}">${formatCompact(chPx)}</div><div class="label">Changed px</div></div>
    <div class="stat-box"><div class="value" title="${totPx.toLocaleString()}">${formatCompact(totPx)}</div><div class="label">Total px</div></div>
    <div class="stat-box"><div class="value">${(data.regions || []).length}</div><div class="label">Regions</div></div>
    <div class="stat-box stat-box-wide"><div class="value value-sm" title="${locLabel}">${locLabel}</div><div class="label">Location</div></div>
  `;

  const beforeImg = document.getElementById('compare-before-img');
  const afterImg = document.getElementById('compare-after-img');

  // "Before" side = original before image (full-res), "After" side = result overlay
  if (data.overlayBase64Png) {
    // Fresh detection: overlay is base64, before from file picker or saved full-res
    afterImg.src = 'data:image/png;base64,' + data.overlayBase64Png;
    const beforeFile = document.getElementById('file-before').files?.[0];
    if (beforeFile) {
      readFileAsDataURL(beforeFile).then((url) => { beforeImg.src = url; });
    } else {
      beforeImg.src = data.beforeFullUrl || data.beforeThumbUrl || '';
    }
  } else {
    // Loading from history: use full-res before image for slider, overlay for changes
    afterImg.src = data.overlayUrl || '';
    beforeImg.src = data.beforeFullUrl || data.beforeThumbUrl || '';
  }

  // Wait for both images to fully load before positioning the slider handle
  let loaded = 0;
  const onReady = () => {
    if (++loaded >= 2) { resetCompareSlider(); resetZoom(); }
  };
  afterImg.onload = onReady;
  beforeImg.onload = onReady;
  setTimeout(() => { resetCompareSlider(); resetZoom(); }, 500);

  tbody.innerHTML = '';
  const regions = (data.regions || []).slice(0, 50);
  regions.forEach((r) => {
    const tr = document.createElement('tr');
    tr.dataset.regionId = r.id;
    const subType = r.subType || '—';
    const severity = (r.severity || 'minor').toLowerCase();
    const stories = r.estimatedStories != null ? r.estimatedStories : '—';
    const height = r.estimatedHeightM != null ? r.estimatedHeightM + ' m' : '—';
    const stage = r.constructionStage && r.constructionStage !== 'Unknown' ? r.constructionStage : '—';
    const coords = `(${r.center.x}, ${r.center.y})`;
    tr.innerHTML = `
      <td>${r.id}</td>
      <td>${r.objectType}</td>
      <td>${subType}</td>
      <td><span class="severity-badge ${severity}">${severity}</span></td>
      <td>${(r.confidence * 100).toFixed(1)}%</td>
      <td>${r.area.toLocaleString()}</td>
      <td>${coords}</td>
      <td>${stories}</td>
      <td>${height}</td>
      <td>${stage}</td>
    `;
    tbody.appendChild(tr);
  });

  setupRegionHover(tbody, regions);
  openResultModal();
}

function openResultModal() {
  const modal = document.getElementById('result-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeResultModal() {
  const modal = document.getElementById('result-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  document.body.style.overflow = '';
}

document.getElementById('result-modal-close')?.addEventListener('click', closeResultModal);
document.getElementById('result-modal')?.addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeResultModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('result-modal');
    if (modal && !modal.classList.contains('hidden')) closeResultModal();
  }
});

function setupRegionHover(tbody, regions) {
  const overlay = document.getElementById('region-highlight-overlay');
  if (!overlay) return;
  overlay.innerHTML = '';
  tbody.querySelectorAll('tr[data-region-id]').forEach((tr) => {
    tr.addEventListener('mouseenter', () => {
      const id = parseInt(tr.dataset.regionId, 10);
      const r = regions.find((x) => x.id === id);
      if (!r || !r.bbox) return;
      tbody.querySelectorAll('tr').forEach((row) => row.classList.remove('region-hover'));
      tr.classList.add('region-hover');
      const box = document.createElement('div');
      box.className = 'highlight-box';
      const imgEl = document.getElementById('compare-after-img');
      if (!imgEl || !imgEl.offsetWidth) return;
      const slider = document.getElementById('compare-slider');
      if (!slider) return;
      const rw = slider.offsetWidth;
      const rh = slider.offsetHeight;
      const imgW = imgEl.naturalWidth || 1;
      const imgH = imgEl.naturalHeight || 1;
      const scaleX = rw / imgW;
      const scaleY = rh / imgH;
      const scale = Math.min(scaleX, scaleY);
      const drawW = imgW * scale;
      const drawH = imgH * scale;
      const offsetX = (rw - drawW) / 2;
      const offsetY = (rh - drawH) / 2;
      box.style.left = (offsetX + r.bbox.x * scale) + 'px';
      box.style.top = (offsetY + r.bbox.y * scale) + 'px';
      box.style.width = (r.bbox.w * scale) + 'px';
      box.style.height = (r.bbox.h * scale) + 'px';
      overlay.appendChild(box);
    });
    tr.addEventListener('mouseleave', () => {
      tr.classList.remove('region-hover');
      overlay.innerHTML = '';
    });
  });
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

// ---- Zoom (result and history view) ----
let currentZoom = 1;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 3;
const ZOOM_STEP = 0.25;

function applyZoom() {
  const slider = document.getElementById('compare-slider');
  const levelEl = document.getElementById('zoom-level');
  if (!slider) return;
  slider.style.transform = `scale(${currentZoom})`;
  slider.style.transformOrigin = 'center top';
  if (levelEl) levelEl.textContent = Math.round(currentZoom * 100) + '%';
}

function resetZoom() {
  currentZoom = 1;
  applyZoom();
}

function initZoom() {
  const zoomIn = document.getElementById('zoom-in');
  const zoomOut = document.getElementById('zoom-out');
  const wrapper = document.getElementById('zoom-wrapper');
  if (zoomIn) zoomIn.addEventListener('click', () => {
    currentZoom = Math.min(ZOOM_MAX, currentZoom + ZOOM_STEP);
    applyZoom();
  });
  if (zoomOut) zoomOut.addEventListener('click', () => {
    currentZoom = Math.max(ZOOM_MIN, currentZoom - ZOOM_STEP);
    applyZoom();
  });
  if (wrapper) {
    wrapper.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      if (e.deltaY < 0) currentZoom = Math.min(ZOOM_MAX, currentZoom + ZOOM_STEP);
      else currentZoom = Math.max(ZOOM_MIN, currentZoom - ZOOM_STEP);
      applyZoom();
    }, { passive: false });
  }
}
initZoom();

initCompareSlider();

// ---- History table: load and row click to open result ----
async function loadHistory() {
  const tbody = document.getElementById('history-tbody');
  const emptyEl = document.getElementById('history-empty');
  const tableWrap = document.querySelector('.history-table-wrap');
  if (!tbody) return;
  try {
    const items = await api('GET', '/api/history');
    if (!items || items.length === 0) {
      if (tableWrap) tableWrap.classList.add('hidden');
      if (emptyEl) { emptyEl.classList.remove('hidden'); emptyEl.textContent = 'No detection runs yet. Upload images above to get started.'; }
      return;
    }
    if (tableWrap) tableWrap.classList.remove('hidden');
    if (emptyEl) emptyEl.classList.add('hidden');
    tbody.innerHTML = items.map((r) => {
      const beforeThumb = r.beforeThumbUrl ? `<img src="${r.beforeThumbUrl}" alt="Before" loading="lazy" />` : '<span class="dim">—</span>';
      const afterThumb = r.afterThumbUrl ? `<img src="${r.afterThumbUrl}" alt="After" loading="lazy" />` : '<span class="dim">—</span>';
      const resultThumb = r.overlayUrl ? `<img src="${r.overlayUrl}" alt="Result" loading="lazy" />` : '<span class="dim">—</span>';
      return `
      <tr data-run-id="${r.id}">
        <td class="timestamp-cell">${formatDate(r.createdAt)}</td>
        <td class="thumb-cell">${beforeThumb}</td>
        <td class="thumb-cell">${afterThumb}</td>
        <td class="thumb-cell">${resultThumb}</td>
        <td class="stats-cell">${r.regionsCount} regions</td>
        <td class="stats-cell">${(r.changePercentage ?? 0).toFixed(2)}%</td>
        <td class="actions-cell">
          <button type="button" class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); openRunFromHistory(${r.id})">View</button>
          <button type="button" class="btn-icon" title="Delete" onclick="event.stopPropagation(); confirmDelete(${r.id})">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
        </td>
      </tr>`;
    }).join('');

    tbody.querySelectorAll('tr[data-run-id]').forEach((tr) => {
      tr.addEventListener('click', (e) => {
        if (e.target.closest('.actions-cell')) return;
        const id = parseInt(tr.dataset.runId, 10);
        openRunFromHistory(id);
      });
    });
  } catch (_) {
    if (tableWrap) tableWrap.classList.add('hidden');
    if (emptyEl) { emptyEl.classList.remove('hidden'); emptyEl.textContent = 'Could not load history.'; }
  }
}

async function openRunFromHistory(runId) {
  try {
    const data = await api('GET', '/api/history/' + runId);
    showResult(data);
  } catch (err) {
    showError('dashboard-error', err.message || 'Failed to load run.');
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
    const row = document.querySelector(`tr[data-run-id="${id}"]`);
    if (row) {
      row.style.transition = 'all 0.3s ease';
      row.style.opacity = '0';
      setTimeout(() => loadHistory(), 300);
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
