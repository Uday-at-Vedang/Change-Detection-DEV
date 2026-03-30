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

function getDetectionTypeFromPath() {
  const p = (window.location.pathname || '').toLowerCase();
  if (p.includes('/detect/landslide')) return 'landslide_detection';
  if (p.includes('/detect/change')) return 'change_detection';
  return null;
}

function applyDetectionTypeToUI(type) {
  const typeSel = document.getElementById('detect-type');
  if (!typeSel || !type) return;
  typeSel.value = type;
  typeSel.dispatchEvent(new Event('change'));
}

function pathForDetectionType(type) {
  return type === 'landslide_detection' ? '/detect/landslide' : '/detect/change';
}

function navigateToDetectionType(type, replace = false) {
  applyDetectionTypeToUI(type);
  const targetPath = pathForDetectionType(type);
  if ((window.location.pathname || '') !== targetPath) {
    const fn = replace ? 'replaceState' : 'pushState';
    window.history[fn]({}, '', targetPath);
  }
  showView('dashboard');
  loadHistory();
}

// ---- Detection type selection buttons ----
document.getElementById('btn-type-change')?.addEventListener('click', () => {
  navigateToDetectionType('change_detection');
});
document.getElementById('btn-type-landslide')?.addEventListener('click', () => {
  navigateToDetectionType('landslide_detection');
});

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

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((email || '').trim());
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
    handlePostAuthNavigation();
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
    handlePostAuthNavigation();
  } catch (err) { showError('register-error', err.message); }
});

function handlePostAuthNavigation() {
  const preferred = getDetectionTypeFromPath();
  if (preferred) applyDetectionTypeToUI(preferred);
  showView('detection-type');

  // If user already chose a URL (/detect/change or /detect/landslide),
  // then auto-redirect after the menu is shown.
  if (preferred) {
    setTimeout(() => {
      navigateToDetectionType(preferred, true);
    }, 150);
  }
}

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
    // Always show the selection menu first.
    // If the user already landed on /detect/change or /detect/landslide, we
    // pre-select the corresponding detection type in the dropdown for convenience,
    // but we still show the menu page before redirecting.
    const preferred = getDetectionTypeFromPath();
    if (preferred) applyDetectionTypeToUI(preferred);
    showView('detection-type');
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

// ---- Detection menu (General vs Landslide) ----
(function initDetectionMenu() {
  const typeSel = document.getElementById('detect-type');
  const landslideGroup = document.getElementById('landslide-model-group');
  const methodGroup = document.getElementById('detect-method')?.closest('.form-group');
  const regGroup = document.getElementById('detect-registration')?.closest('.form-group');
  const normGroup = document.getElementById('detect-normalization')?.closest('.form-group');
  if (!typeSel) return;

  function refresh() {
    const isLandslide = typeSel.value === 'landslide_detection';
    if (landslideGroup) landslideGroup.classList.toggle('hidden', !isLandslide);
    if (methodGroup) methodGroup.classList.toggle('hidden', isLandslide);
    if (regGroup) regGroup.classList.toggle('hidden', isLandslide);
    if (normGroup) normGroup.classList.toggle('hidden', isLandslide);
  }

  typeSel.addEventListener('change', refresh);
  refresh();
})();

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
    updateNotifyActionState();
  });
})();

function updateNotifyActionState() {
  const cb = document.getElementById('detect-notify');
  const btn = document.getElementById('notify-send-btn');
  const help = document.getElementById('notify-help');
  if (!btn || !cb) return;
  btn.disabled = !cb.checked;
  if (currentResultData?.id) {
    btn.textContent = 'Send Report';
    if (help) help.textContent = 'Send the currently open result report to this email address, or run a new detection to auto-send.';
  } else {
    btn.textContent = 'Send Test';
    if (help) help.textContent = 'Use Send Test to verify email delivery, or run detection to send the report automatically.';
  }
}

document.getElementById('notify-send-btn')?.addEventListener('click', async () => {
  hideError('dashboard-error');
  const cb = document.getElementById('detect-notify');
  const input = document.getElementById('notify-email');
  const btn = document.getElementById('notify-send-btn');
  const email = (input?.value || '').trim();

  if (!cb?.checked) {
    showError('dashboard-error', 'Enable "Notify via Email" first.');
    return;
  }
  if (!isValidEmail(email)) {
    showError('dashboard-error', 'Please enter a valid email address.');
    return;
  }

  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending...';
  try {
    const path = currentResultData?.id
      ? `/api/history/${currentResultData.id}/notify`
      : '/api/notify/test';
    const data = await api('POST', path, { body: JSON.stringify({ email }) });
    showSuccess('dashboard-success', data?.message || 'Email sent successfully.');
  } catch (err) {
    showError('dashboard-error', err.message || 'Failed to send email.');
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
    updateNotifyActionState();
  }
});

// ---- Run detection ----
let _detectProgressTimer = null;
let _detectProgressValue = 0;

function startDetectionProgress() {
  const loading = document.getElementById('run-loading');
  if (!loading) return;
  _detectProgressValue = 0;
  loading.querySelector('.spinner')?.classList.remove('hidden');
  function updateLabel() {
    const pct = Math.min(99, Math.max(1, Math.round(_detectProgressValue)));
    loading.childNodes.forEach((n) => {
      if (n.nodeType === Node.TEXT_NODE) {
        n.textContent = ` Analyzing images... ${pct}%`;
      }
    });
  }
  updateLabel();
  if (_detectProgressTimer) clearInterval(_detectProgressTimer);
  _detectProgressTimer = setInterval(() => {
    // Ease out: slow down as it approaches 95%
    if (_detectProgressValue < 95) {
      _detectProgressValue += Math.max(0.5, (100 - _detectProgressValue) * 0.03);
      updateLabel();
    }
  }, 400);
}

function stopDetectionProgress(success) {
  const loading = document.getElementById('run-loading');
  if (_detectProgressTimer) {
    clearInterval(_detectProgressTimer);
    _detectProgressTimer = null;
  }
  if (!loading) return;
  if (success) {
    _detectProgressValue = 100;
    loading.childNodes.forEach((n) => {
      if (n.nodeType === Node.TEXT_NODE) {
        n.textContent = ' Analyzing images... 100%';
      }
    });
  }
}

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
   startDetectionProgress();

  const token = getToken();
  const form = new FormData();
  form.append('before', before);
  form.append('after', after);
  const detectionType = document.getElementById('detect-type')?.value || 'change_detection';
  form.append('detection_type', detectionType);
  form.append('method', document.getElementById('detect-method').value);
  if (detectionType === 'landslide_detection') {
    form.append('landslide_model', document.getElementById('landslide-model')?.value || 'Rule-Based v1');
  }
  form.append('title', document.getElementById('detect-title').value || 'Untitled run');
  form.append('zone', document.getElementById('detect-zone').value || '');
  form.append('village', document.getElementById('detect-village').value || '');
  form.append('enable_registration', document.getElementById('detect-registration').checked);
  form.append('enable_normalization', document.getElementById('detect-normalization').checked);
  const sensitivityInput = document.getElementById('detect-sensitivity');
  const minAreaInput = document.getElementById('detect-min-area');
  const sensitivity = Number(sensitivityInput?.value ?? 0.5);
  if (Number.isNaN(sensitivity) || sensitivity < 0 || sensitivity > 1) {
    showError('dashboard-error', 'Detection sensitivity must be between 0 and 1.');
    btn.disabled = false;
    loading.classList.add('hidden');
    stopDetectionProgress(false);
    return;
  }
  form.append('detection_sensitivity', String(sensitivity));
  const minAreaRaw = (minAreaInput?.value || '').trim();
  if (minAreaRaw) {
    const minArea = Number(minAreaRaw);
    if (Number.isNaN(minArea) || minArea < 50) {
      showError('dashboard-error', 'Min region area must be at least 50.');
      btn.disabled = false;
      loading.classList.add('hidden');
      stopDetectionProgress(false);
      return;
    }
    form.append('min_region_area', String(Math.round(minArea)));
  }

  // Notify: validate and attach email if checkbox is checked
  const notifyCb = document.getElementById('detect-notify');
  const notifyInput = document.getElementById('notify-email');
  if (notifyCb?.checked) {
    const email = (notifyInput?.value || '').trim();
    if (!isValidEmail(email)) {
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
    const notifyCbDone = document.getElementById('detect-notify');
    let notifyMsg = '';
    if (notifyCbDone?.checked) {
      notifyMsg = data.notificationSent
        ? ' Notification email sent.'
        : ` Email notification failed${data.notificationError ? `: ${data.notificationError}` : '.'}`;
    }
    const thrInfo = data?.statistics?.thresholdDebug?.threshold_used;
    const thrMsg = typeof thrInfo === 'number' ? ` Threshold: ${thrInfo}.` : '';
    showSuccess('dashboard-success', 'Detection complete!' + thrMsg + notifyMsg);
    loadHistory();
  } catch (err) {
    showError('dashboard-error', err.message);
  } finally {
    btn.disabled = false;
    loading.classList.add('hidden');
    stopDetectionProgress(true);
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
let _regionRows = [];       // all region <tr> elements for pagination
let _regionList = [];        // matching data objects
const REGIONS_PER_PAGE = 10;
let _regionPage = 0;

updateNotifyActionState();

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

  const regions = (data.regions || []).slice(0, 60);
  _regionList = regions;
  _regionRows = regions.map((r) => {
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
    return tr;
  });

  _regionPage = 0;
  renderRegionPage();
  updateNotifyActionState();
  openResultModal();
}

function renderRegionPage() {
  const tbody = document.getElementById('regions-tbody');
  const pag = document.getElementById('regions-pagination');
  if (!tbody) return;

  const totalPages = Math.max(1, Math.ceil(_regionRows.length / REGIONS_PER_PAGE));
  _regionPage = Math.max(0, Math.min(_regionPage, totalPages - 1));
  const start = _regionPage * REGIONS_PER_PAGE;
  const pageRows = _regionRows.slice(start, start + REGIONS_PER_PAGE);
  const pageData = _regionList.slice(start, start + REGIONS_PER_PAGE);

  tbody.innerHTML = '';
  pageRows.forEach((tr) => tbody.appendChild(tr));
  setupRegionHover(tbody, pageData);

  if (pag) {
    pag.innerHTML = '';
    if (totalPages <= 1) return;

    const prev = document.createElement('button');
    prev.textContent = '‹';
    prev.disabled = _regionPage === 0;
    prev.addEventListener('click', () => { _regionPage--; renderRegionPage(); });
    pag.appendChild(prev);

    const maxButtons = 7;
    let rangeStart = Math.max(0, _regionPage - Math.floor(maxButtons / 2));
    let rangeEnd = Math.min(totalPages, rangeStart + maxButtons);
    if (rangeEnd - rangeStart < maxButtons) rangeStart = Math.max(0, rangeEnd - maxButtons);

    for (let i = rangeStart; i < rangeEnd; i++) {
      const btn = document.createElement('button');
      btn.textContent = i + 1;
      if (i === _regionPage) btn.classList.add('active');
      btn.addEventListener('click', () => { _regionPage = i; renderRegionPage(); });
      pag.appendChild(btn);
    }

    const next = document.createElement('button');
    next.textContent = '›';
    next.disabled = _regionPage >= totalPages - 1;
    next.addEventListener('click', () => { _regionPage++; renderRegionPage(); });
    pag.appendChild(next);

    const info = document.createElement('span');
    info.className = 'page-info';
    info.textContent = `${start + 1}–${Math.min(start + REGIONS_PER_PAGE, _regionRows.length)} of ${_regionRows.length}`;
    pag.appendChild(info);
  }
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
  const tz = 'Asia/Kolkata';
  return d.toLocaleDateString('en-IN', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-IN', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: true });
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
